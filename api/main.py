# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "anthropic>=0.80", "pydantic>=2"]
# ///
"""PRIDE Map API.  run:  uv run api/main.py   (http://localhost:8000/docs)

Everything reads data/pride.db (built by build_db.py / tag.py). Filters only apply to tagged datasets;
free-text q also searches untagged ones via FTS5.
"""
import json
import math
import os
import pathlib
import re
import sqlite3
import sys
import datetime as dt
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "pride.db"
from tag import VOCAB  # noqa: E402  (also loads .env)

app = FastAPI(
    title="PRIDE Map API",
    version="0.1.0",
    description="Find the right PRIDE proteomics dataset. LLM-tagged, filterable, explained. "
    "Every tag carries confidence + the evidence sentence it came from.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TAG_COLS = ["organism", "sample_type", "tissue", "cell_line", "disease", "enrichment", "acquisition",
            "labelling", "enzyme", "fractionation", "instrument_family", "search_engine"]
PRIDE_URL = "https://www.ebi.ac.uk/pride/archive/projects/"


def db():
    con = sqlite3.connect(DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def jl(s):
    try:
        return json.loads(s) if s else []
    except Exception:
        return []


# ----------------------------------------------------------------------------- models
class Filters(BaseModel):
    organism: Optional[str] = None
    sample_type: Optional[str] = None
    tissue: Optional[str] = None
    cell_line: Optional[str] = None
    disease: Optional[str] = None
    enrichment: Optional[str] = None
    acquisition: Optional[str] = None
    labelling: Optional[str] = None
    enzyme: Optional[str] = None
    fractionation: Optional[str] = None
    instrument_family: Optional[str] = None
    search_engine: Optional[str] = None
    since: Optional[int] = None
    q: Optional[str] = None
    # intent flags that change "watch" warnings
    wants_absolute_quant: bool = False
    low_abundance: bool = False
    wants_unmodified: bool = False


class AskIn(BaseModel):
    q: str


class CorrectIn(BaseModel):
    tag: str
    suggested_value: Optional[str] = None
    note: Optional[str] = None


# ----------------------------------------------------------------------------- ranking
def recency(pubdate: Optional[str]) -> float:
    try:
        y = int((pubdate or "2010")[:4])
    except ValueError:
        y = 2010
    return max(0.0, min(1.0, (y - 2010) / (dt.date.today().year - 2010)))


FUZZY = {"organism", "tissue", "cell_line", "disease"}  # substring match, case-insensitive
ORG_ALIASES = {"human": "homo sapiens", "mouse": "mus musculus", "rat": "rattus norvegicus",
               "yeast": "saccharomyces cerevisiae", "arabidopsis": "arabidopsis thaliana",
               "e. coli": "escherichia coli", "zebrafish": "danio rerio", "fly": "drosophila melanogaster"}


def norm_org(v: Optional[str]):
    if not v:
        return v
    return ORG_ALIASES.get(v.lower().strip(), v)


def search(con, f: Filters, limit=25, offset=0):
    where, params = [], []
    for k in TAG_COLS:
        v = getattr(f, k)
        if not v:
            continue
        if k == "organism":
            v = norm_org(v)
        if k in FUZZY:
            where.append(f"lower(t.{k}) LIKE ?")
            params.append(f"%{v.lower()}%")
        else:
            vals = [x.strip() for x in v.split(",") if x.strip()]
            where.append(f"t.{k} IN ({','.join('?' * len(vals))})")
            params += vals
    if f.since:
        where.append("p.publication_date >= ?")
        params.append(f"{f.since}-01-01")
    join_fts = ""
    if f.q:
        join_fts = " JOIN projects_fts fts ON fts.accession = p.accession"
        where.append("projects_fts MATCH ?")
        params.append(fts_query(f.q))
    sql = (
        "SELECT p.accession, p.title, p.publication_date, p.organic_downloads, p.downloads, p.pubmed_id, p.doi, "
        "t.* FROM tags t JOIN projects p ON p.accession = t.accession" + join_fts
        + (" WHERE " + " AND ".join(where) if where else "")
    )
    rows = [dict(r) for r in con.execute(sql, params)]
    # confidences for matched tags
    conf = {}
    if rows:
        accs = [r["accession"] for r in rows]
        for chunk in range(0, len(accs), 900):
            sub = accs[chunk:chunk + 900]
            for e in con.execute(
                f"SELECT accession, tag, confidence, evidence, source FROM tag_evidence WHERE accession IN ({','.join('?' * len(sub))})", sub
            ):
                conf.setdefault(e["accession"], {})[e["tag"]] = dict(e)
    results = []
    for r in rows:
        ev = conf.get(r["accession"], {})
        matched = [k for k in TAG_COLS if getattr(f, k)]
        avg_conf = sum(ev.get(k, {}).get("confidence", 0.5) for k in matched) / len(matched) if matched else 0.6
        depth = r.get("depth_proteins") or 0
        score = (
            1.0
            + 0.5 * math.log1p(r.get("organic_downloads") or 0) / 10
            + 0.3 * recency(r.get("publication_date"))
            + 0.4 * min(depth / 8000, 1.0)
            + 0.3 * avg_conf
        )
        fits, watch = reasons(r, ev, f)
        score -= 0.25 * sum(1 for w in watch if not w["text"].endswith("is a low-confidence tag"))  # real mismatches cost rank
        score -= 0.05 * sum(1 for w in watch if w["text"].endswith("is a low-confidence tag"))
        results.append({
            "accession": r["accession"], "title": r["title"], "summary": r.get("summary_one_line"),
            "publication_date": r.get("publication_date"), "downloads": r.get("organic_downloads"),
            "pubmed_id": r.get("pubmed_id"), "doi": r.get("doi"), "pride_url": PRIDE_URL + r["accession"],
            "tags": {k: r.get(k) for k in TAG_COLS + ["depth_proteins", "taxon_id"]},
            "fits": fits, "watch": watch, "score": round(score, 3),
        })
    results.sort(key=lambda x: -x["score"])
    total = len(results)
    return total, results[offset:offset + limit]


def fts_query(q: str) -> str:
    # quote each token so punctuation can't break FTS5 syntax; OR tokens for recall
    toks = [t for t in re.findall(r"[A-Za-z0-9\-]+", q) if len(t) > 2]
    return " OR ".join(f'"{t}"' for t in toks) or '""'


PRETTY = {"label_free": "label-free", "trypsin_lysc": "trypsin + Lys-C", "high_ph_rp": "high-pH RP fractionation",
          "ap_ms": "AP-MS / IP", "proximity_labelling": "proximity labelling", "PRM_SRM": "targeted PRM/SRM",
          "proteome_discoverer": "Proteome Discoverer", "maxquant": "MaxQuant", "fragpipe": "FragPipe", "diann": "DIA-NN",
          "spectronaut": "Spectronaut", "mascot": "Mascot", "orbitrap": "Orbitrap", "timstof": "timsTOF", "qtof": "Q-TOF",
          "triple_quad": "triple quadrupole", "cell_line": "cell line", "primary_cells": "primary cells"}


def pretty(v):
    return PRETTY.get(v, v) if v else v


def reasons(r, ev, f: Filters):
    fits, watch = [], []

    def item(tag, text):
        e = ev.get(tag, {})
        return {"tag": tag, "text": text, "confidence": e.get("confidence"), "evidence": e.get("evidence"), "source": e.get("source")}

    for k in TAG_COLS:
        if getattr(f, k) and r.get(k):
            fits.append(item(k, f"{k.replace('_', ' ')}: {pretty(r[k])}"))
        elif getattr(f, k) is None and k in ("enrichment", "acquisition", "labelling") and r.get(k):
            pass
    for k in TAG_COLS:
        e = ev.get(k)
        if e and e["confidence"] is not None and e["confidence"] < 0.6 and r.get(k):
            watch.append(item(k, f"{k.replace('_', ' ')} = {pretty(r[k])} is a low-confidence tag"))
    if f.wants_absolute_quant and r.get("labelling") in ("TMT", "iTRAQ", "SILAC"):
        watch.append(item("labelling", f"{r['labelling']} gives relative ratios, not absolute amounts"))
    if f.low_abundance and r.get("acquisition") == "DDA":
        watch.append(item("acquisition", "DDA skips low-abundance peptides; a DIA dataset would see more"))
    if f.wants_unmodified and r.get("enrichment") not in (None, "none"):
        watch.append(item("enrichment", f"{pretty(r['enrichment'])}-enriched: unmodified proteins are mostly discarded"))
    if r.get("enrichment") == "phospho" and not f.enrichment and not f.wants_unmodified:
        watch.append(item("enrichment", "phospho-enriched: good for phosphosites, poor for total protein"))
    if r.get("sample_type") == "biofluid" and r.get("tissue") and "plasma" in (r["tissue"] or "").lower() and not f.tissue:
        watch.append(item("tissue", "plasma: dominated by albumin etc.; low-abundance proteins are hard to see"))
    if r.get("depth_proteins"):
        fits.append(item("depth_proteins", f"{r['depth_proteins']:,} proteins identified"))
    return fits, watch


# ----------------------------------------------------------------------------- question → filters
RULES = [
    ("enrichment", "phospho", r"phospho"),
    ("enrichment", "ubiquitin", r"ubiquit"),
    ("enrichment", "glyco", r"glyco"),
    ("enrichment", "acetyl", r"acetyl"),
    ("enrichment", "ap_ms", r"interact|pull.?down|immunoprecip|\bIP\b|AP-?MS|co-?IP"),
    ("enrichment", "proximity_labelling", r"bioid|turboid|apex|proximity"),
    ("enrichment", "secretome", r"secret"),
    ("acquisition", "DIA", r"\bDIA\b|data.independent|swath|diapasef"),
    ("acquisition", "DDA", r"\bDDA\b|data.dependent"),
    ("acquisition", "PRM_SRM", r"\bPRM\b|\bSRM\b|\bMRM\b|targeted"),
    ("labelling", "TMT", r"\bTMT"),
    ("labelling", "SILAC", r"silac"),
    ("labelling", "label_free", r"label.free|\bLFQ\b"),
    ("sample_type", "biofluid", r"plasma|serum|urine|\bCSF\b|saliva"),
    ("sample_type", "cell_line", r"cell line|hela|hek293|a549|mcf-?7|jurkat|k562|u2os"),
    ("instrument_family", "timstof", r"timstof|bruker"),
    ("instrument_family", "orbitrap", r"orbitrap|exactive|lumos|exploris|astral"),
]
TISSUES = ["liver", "brain", "heart", "kidney", "lung", "plasma", "serum", "muscle", "skin", "colon", "breast", "pancreas",
           "blood", "bone", "retina", "spleen", "prostate", "ovary", "testis", "cortex", "hippocampus", "adipose", "tumor",
           "tumour", "urine", "csf", "saliva", "placenta", "intestine", "stomach", "leaf", "root", "seed"]
ORGS = [("mouse", "Mus musculus"), ("mice", "Mus musculus"), ("murine", "Mus musculus"), ("human", "Homo sapiens"),
        ("patient", "Homo sapiens"), ("rat ", "Rattus norvegicus"), ("rats", "Rattus norvegicus"), ("yeast", "Saccharomyces cerevisiae"),
        ("arabidopsis", "Arabidopsis thaliana"), ("e. coli", "Escherichia coli"), ("zebrafish", "Danio rerio"),
        ("drosophila", "Drosophila melanogaster"), ("c. elegans", "Caenorhabditis elegans")]


def rule_parse(q: str) -> Filters:
    ql = q.lower()
    f = Filters()
    for tag, val, pat in RULES:
        if getattr(f, tag) is None and re.search(pat, ql, re.I):
            setattr(f, tag, val)
    for t in TISSUES:
        if re.search(rf"\b{t}", ql):
            f.tissue = t
            break
    for word, org in ORGS:
        if word in ql:
            f.organism = org
            break
    m = re.search(r"(?:since|after|from)\s+(20\d\d)", ql)
    if m:
        f.since = int(m.group(1))
    f.low_abundance = bool(re.search(r"low.abundan|rare|hard to detect|low level", ql))
    f.wants_absolute_quant = bool(re.search(r"absolute|copy number|concentration", ql))
    f.wants_unmodified = bool(re.search(r"unmodified|total protein|whole proteome|global", ql))
    # anything not consumed becomes free text (protein names etc.)
    leftovers = [w for w in re.findall(r"[A-Za-z0-9\-]{3,}", q) if w.isupper() or re.match(r"^[A-Z][A-Za-z0-9]+\d", w)]
    f.q = " ".join(leftovers) or None
    return f


ASK_SYSTEM = f"""You turn a researcher's question about finding proteomics datasets into filters.
Allowed values: {json.dumps(VOCAB)}. organism is an NCBI scientific name. tissue is a short organ/tissue word.
since is a 4-digit year or null. q is free text for protein/gene names only (not tissues or methods), or null.
Set low_abundance=true if the protein is described as rare/low abundance; wants_absolute_quant=true if they need absolute amounts;
wants_unmodified=true if they want total/unmodified protein rather than a modification. Leave a filter null unless the question implies it.
Also write interpretation: one sentence saying how you read the question."""


class AskOut(BaseModel):
    """Flat, all-required schema: structured outputs reject many defaulted/optional fields as 'too complex'."""
    organism: Optional[str]
    sample_type: Optional[str]
    tissue: Optional[str]
    cell_line: Optional[str]
    disease: Optional[str]
    enrichment: Optional[str]
    acquisition: Optional[str]
    labelling: Optional[str]
    enzyme: Optional[str]
    fractionation: Optional[str]
    instrument_family: Optional[str]
    search_engine: Optional[str]
    since: Optional[int]
    q: Optional[str]
    low_abundance: bool
    wants_absolute_quant: bool
    wants_unmodified: bool
    interpretation: str


def llm_parse(q: str) -> Optional[AskOut]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(timeout=60, max_retries=1)
        msg = client.messages.parse(
            model=os.environ.get("ASK_MODEL", "claude-sonnet-5"), max_tokens=1000,
            output_config={"effort": "low"},
            system=ASK_SYSTEM, messages=[{"role": "user", "content": q}], output_format=AskOut,
        )
        out = msg.parsed_output
        # drop values outside the closed vocab rather than 500 on a bad filter
        for k, allowed in VOCAB.items():
            if getattr(out, k) not in (None, *allowed):
                setattr(out, k, None)
        return out
    except Exception as e:  # fall back to rules; never 500 the demo
        print("llm_parse failed:", e, file=sys.stderr)
        return None


# ----------------------------------------------------------------------------- routes
@app.get("/stats")
def stats():
    con = db()
    out = {
        "projects": con.execute("SELECT count(*) FROM projects").fetchone()[0],
        "papers_with_methods": con.execute("SELECT count(*) FROM papers WHERE length(methods_text) > 500").fetchone()[0],
        "tagged": con.execute("SELECT count(*) FROM tags").fetchone()[0],
        "corrections": con.execute("SELECT count(*) FROM corrections").fetchone()[0],
        "histograms": {},
    }
    for k in ["organism", "sample_type", "enrichment", "acquisition", "labelling", "enzyme", "fractionation", "instrument_family", "search_engine"]:
        out["histograms"][k] = {r[0] or "null": r[1] for r in con.execute(f"SELECT {k}, count(*) FROM tags GROUP BY {k} ORDER BY 2 DESC")}
    return out


@app.get("/vocab")
def vocab():
    return VOCAB


@app.get("/search")
def search_route(
    organism: Optional[str] = None, sample_type: Optional[str] = None, tissue: Optional[str] = None,
    cell_line: Optional[str] = None, disease: Optional[str] = None, enrichment: Optional[str] = None,
    acquisition: Optional[str] = None, labelling: Optional[str] = None, enzyme: Optional[str] = None,
    fractionation: Optional[str] = None, instrument_family: Optional[str] = None, search_engine: Optional[str] = None,
    since: Optional[int] = None, q: Optional[str] = None, limit: int = Query(25, le=200), offset: int = 0,
):
    f = Filters(**{k: v for k, v in locals().items() if k in Filters.model_fields})
    total, results = search(db(), f, limit, offset)
    return {"filters": f.model_dump(exclude_none=True), "total": total, "results": results}


@app.post("/ask")
def ask(body: AskIn):
    parsed = llm_parse(body.q)
    if parsed:
        f = Filters(**parsed.model_dump(exclude={"interpretation"}))
        interp, how = parsed.interpretation, "llm"
    else:
        f = rule_parse(body.q)
        how = "rules"
        bits = [f"{k}={v}" for k, v in f.model_dump(exclude_none=True).items() if v not in (False, None)]
        interp = "Read as: " + (", ".join(bits) if bits else "no specific filters; free-text search") + "."
    con = db()
    total, results = search(con, f)
    relaxed = []
    # relaxation ladder: free text (protein names) first, then tissue, then the rest, so the user always sees something
    for drop in ["q", "tissue", "cell_line", "disease", "since", "labelling", "acquisition", "instrument_family", "fractionation", "enzyme"]:
        if total:
            break
        if getattr(f, drop) in (None, False):
            continue
        setattr(f, drop, None)
        relaxed.append(drop)
        total, results = search(con, f)
    if relaxed:
        interp += " No exact match, so I dropped: " + ", ".join(relaxed) + "."
    return {"question": body.q, "parser": how, "interpretation": interp, "relaxed": relaxed,
            "filters": f.model_dump(exclude_none=True), "total": total, "results": results}


@app.get("/map")
def map_route(by: str = "organism,sample_type,enrichment,acquisition"):
    levels = [b for b in by.split(",") if b in TAG_COLS]
    if not levels:
        raise HTTPException(400, "by must be a comma list of tag names")
    con = db()
    rows = con.execute(f"SELECT {','.join(levels)}, count(*) n FROM tags GROUP BY {','.join(levels)}").fetchall()
    root = {"name": "PRIDE (tagged)", "children": []}
    for r in rows:
        node = root
        for i, lvl in enumerate(levels):
            key = r[i] or "unknown"
            nxt = next((c for c in node["children"] if c["name"] == key), None)
            if not nxt:
                nxt = {"name": key, "level": lvl, "children": []}
                node["children"].append(nxt)
            node = nxt
        node["value"] = node.get("value", 0) + r["n"]
    def fill(n):
        if n.get("children"):
            n["value"] = sum(fill(c) for c in n["children"])
            n["children"].sort(key=lambda c: -c["value"])
        return n.get("value", 0)
    fill(root)
    return {"levels": levels, "tree": root}


@app.get("/dataset/{accession}")
def dataset(accession: str):
    con = db()
    p = con.execute("SELECT * FROM projects WHERE accession=?", (accession,)).fetchone()
    if not p:
        raise HTTPException(404, "unknown accession")
    p = dict(p)
    raw = json.loads(p.pop("raw_json") or "{}")
    for k in ["organisms", "organism_parts", "diseases", "instruments", "experiment_types", "quant_methods", "softwares", "ptms", "keywords", "project_tags", "countries"]:
        p[k] = jl(p.get(k))
    t = con.execute("SELECT * FROM tags WHERE accession=?", (accession,)).fetchone()
    ev = [dict(r) for r in con.execute("SELECT tag,value,confidence,source,evidence FROM tag_evidence WHERE accession=?", (accession,))]
    paper = con.execute("SELECT pubmed_id,pmcid,is_open_access,title,length(methods_text) methods_chars FROM papers WHERE pubmed_id=?", (p["pubmed_id"],)).fetchone()
    files_est = sum(1 for _ in raw.get("references") or [])
    return {
        "project": p, "pride_url": PRIDE_URL + accession,
        "paper": dict(paper) if paper else None,
        "paper_url": f"https://europepmc.org/article/MED/{p['pubmed_id']}" if p.get("pubmed_id") else None,
        "tags": {k: (t[k] if t else None) for k in TAG_COLS + ["depth_proteins", "taxon_id", "summary_one_line", "model", "tagged_at"]},
        "evidence": ev, "tagged": bool(t),
        "corrections": [dict(r) for r in con.execute("SELECT * FROM corrections WHERE accession=? ORDER BY id DESC", (accession,))],
    }


@app.get("/dataset/{accession}/methods", response_class=PlainTextResponse)
def methods(accession: str):
    con = db()
    r = con.execute("SELECT pp.methods_text FROM projects p JOIN papers pp ON pp.pubmed_id=p.pubmed_id WHERE p.accession=?", (accession,)).fetchone()
    return (r[0] if r and r[0] else "")


@app.post("/dataset/{accession}/correct")
def correct(accession: str, body: CorrectIn):
    if body.tag not in TAG_COLS + ["depth_proteins", "summary_one_line"]:
        raise HTTPException(400, "unknown tag")
    con = db()
    con.execute("INSERT INTO corrections(accession,tag,suggested_value,note) VALUES (?,?,?,?)", (accession, body.tag, body.suggested_value, body.note))
    con.commit()
    return {"ok": True}


SDRF_MAP = {"organism": "characteristics[organism]", "tissue": "characteristics[organism part]", "cell_line": "characteristics[cell line]",
            "disease": "characteristics[disease]", "enrichment": "comment[enrichment process]", "labelling": "comment[label]",
            "enzyme": "comment[cleavage agent details]", "instrument_family": "comment[instrument]", "acquisition": "comment[proteomics data acquisition method]",
            "fractionation": "comment[fractionation method]"}


@app.get("/export.sdrf.tsv", response_class=PlainTextResponse)
def export_sdrf(request: Request):
    f = Filters(**{k: v for k, v in request.query_params.items() if k in Filters.model_fields})
    _, results = search(db(), f, limit=5000)
    cols = ["source name"] + list(SDRF_MAP.values()) + ["comment[pride accession]", "comment[summary]"]
    lines = ["\t".join(cols)]
    for r in results:
        t = r["tags"]
        lines.append("\t".join([r["accession"]] + [str(t.get(k) or "not available") for k in SDRF_MAP] + [r["pride_url"], (r["summary"] or "").replace("\t", " ")]))
    return "\n".join(lines) + "\n"


@app.get("/")
def root():
    return {"name": "PRIDE Map API", "docs": "/docs", "stats": "/stats", "search": "/search?organism=mouse&enrichment=phospho"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False, app_dir=str(pathlib.Path(__file__).parent))
