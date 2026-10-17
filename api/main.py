# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "anthropic>=0.80", "pydantic>=2", "pandas>=2", "numpy", "scipy", "openpyxl", "python-multipart"]
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


FACET_TAGS = ["organism", "sample_type", "enrichment", "acquisition", "labelling", "enzyme", "fractionation", "instrument_family"]


def where_for(f: Filters, exclude: str | None = None):
    """Same WHERE logic as search(), as SQL + params, optionally ignoring one tag (for facet counts)."""
    where, params = [], []
    for k in TAG_COLS:
        v = getattr(f, k)
        if not v or k == exclude:
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
    join = ""
    if f.q:
        join = " JOIN projects_fts fts ON fts.accession = p.accession"
        where.append("projects_fts MATCH ?")
        params.append(fts_query(f.q))
    return join, (" WHERE " + " AND ".join(where) if where else ""), params


def facets(con, f: Filters, top=8):
    """Value counts per tag within the current filter set, ignoring that tag's own filter (like a shop sidebar). Pure SQL."""
    out = {}
    for tag in FACET_TAGS:
        join, where, params = where_for(f, exclude=tag)
        rows = con.execute(f"SELECT COALESCE(t.{tag}, 'unknown'), count(*) n FROM tags t JOIN projects p ON p.accession = t.accession{join}{where} GROUP BY 1 ORDER BY n DESC LIMIT ?", params + [top]).fetchall()
        out[tag] = [[r[0], r[1]] for r in rows]
    return out


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


DROP_WHY = {
    "q": "protein and gene names almost never appear in a dataset's description, so this can't be filtered on; "
         "you check for the protein by reanalysing the dataset",
    "tissue": "no tagged dataset from that tissue matched the other filters",
    "since": "no match in that date range",
}


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
    con = db()
    total, results = search(con, f, limit, offset)
    return {"filters": f.model_dump(exclude_none=True), "total": total, "results": results, "facets": facets(con, f)}


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
    dropped = []
    # relaxation ladder: free text (protein names) first, then tissue, then the rest, so the user always sees something
    for drop in ["q", "tissue", "cell_line", "disease", "since", "labelling", "acquisition", "instrument_family", "fractionation", "enzyme"]:
        if total:
            break
        v = getattr(f, drop)
        if v in (None, False):
            continue
        setattr(f, drop, None)
        dropped.append({"key": drop, "value": v, "why": DROP_WHY.get(drop, "no tagged dataset matched it together with the other filters")})
        total, results = search(con, f)
    return {"question": body.q, "parser": how, "interpretation": interp, "dropped": dropped,
            "filters": f.model_dump(exclude_none=True), "total": total, "results": results, "facets": facets(con, f)}


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


# ----------------------------------------------------------------------------- compare
COMPARE_RULES = [
    # (tag, severity, why it matters for comparing quantities across datasets)
    ("labelling", "blocker", "different quantification chemistry: intensities aren't on the same scale"),
    ("acquisition", "blocker", "DDA and DIA sample different peptides; protein lists and quantities aren't comparable"),
    ("enrichment", "blocker", "different enrichment means a different population of peptides was measured"),
    ("organism", "blocker", "different organisms: different proteomes"),
    ("enzyme", "major", "different digestion gives different peptides for the same protein"),
    ("instrument_family", "major", "instrument sensitivity and dynamic range differ"),
    ("sample_type", "major", "tissue vs cell line vs biofluid have very different backgrounds"),
    ("fractionation", "minor", "depth differs; expect more missing values in the shallower set"),
    ("search_engine", "minor", "different scoring and FDR; re-search both with one tool before comparing"),
    ("tissue", "minor", "different tissues: biological, not technical, difference"),
]


@app.get("/compare")
def compare(accessions: str):
    accs = [a.strip() for a in accessions.split(",") if a.strip()][:6]
    if len(accs) < 2:
        raise HTTPException(400, "give at least two accessions")
    con = db()
    rows = {}
    for a in accs:
        r = con.execute("SELECT t.*, p.title, p.publication_date, p.pubmed_id FROM tags t JOIN projects p USING(accession) WHERE t.accession=?", (a,)).fetchone()
        if not r:
            raise HTTPException(404, f"{a} is not tagged (or unknown)")
        rows[a] = dict(r)
    ev = {}
    for e in con.execute(f"SELECT accession, tag, confidence, evidence FROM tag_evidence WHERE accession IN ({','.join('?' * len(accs))})", accs):
        ev.setdefault(e["accession"], {})[e["tag"]] = {"confidence": e["confidence"], "evidence": e["evidence"]}
    issues = []
    for tag, sev, why in COMPARE_RULES:
        vals = {a: rows[a].get(tag) for a in accs}
        distinct = {v for v in vals.values() if v is not None}
        if len(distinct) > 1:
            issues.append({"tag": tag, "severity": sev, "why": why, "values": {a: pretty(v) for a, v in vals.items()}})
        elif any(v is None for v in vals.values()):
            issues.append({"tag": tag, "severity": "unknown", "why": f"{tag.replace('_', ' ')} is unknown for some datasets; check the protocol", "values": {a: pretty(v) for a, v in vals.items()}})
    blockers = [i for i in issues if i["severity"] == "blocker"]
    majors = [i for i in issues if i["severity"] == "major"]
    if blockers:
        verdict, summary = "not_comparable", "Don't compare quantities directly: " + "; ".join(i["why"] for i in blockers) + "."
    elif majors:
        verdict, summary = "with_caution", "Comparable with caution: " + "; ".join(i["why"] for i in majors) + ". Normalise and batch-correct before any test."
    else:
        verdict, summary = "comparable", "Methods match on everything that matters. Treat remaining differences as batch effects."
    table = {}
    for tag in TAG_COLS + ["depth_proteins"]:
        table[tag] = {a: {"value": pretty(rows[a].get(tag)) if tag != "depth_proteins" else rows[a].get(tag),
                         "confidence": ev.get(a, {}).get(tag, {}).get("confidence"),
                         "evidence": ev.get(a, {}).get(tag, {}).get("evidence")} for a in accs}
    return {"accessions": accs, "datasets": {a: {"title": rows[a]["title"], "summary": rows[a]["summary_one_line"], "publication_date": rows[a]["publication_date"], "pride_url": PRIDE_URL + a} for a in accs},
            "verdict": verdict, "summary": summary, "issues": issues, "table": table}


# ----------------------------------------------------------------------------- reanalyse
import urllib.parse
import urllib.request

PROTEOMES = {9606: ("UP000005640", "Homo sapiens"), 10090: ("UP000000589", "Mus musculus"), 10116: ("UP000002494", "Rattus norvegicus"),
             4932: ("UP000002311", "Saccharomyces cerevisiae"), 3702: ("UP000006548", "Arabidopsis thaliana"), 562: ("UP000000625", "Escherichia coli K-12"),
             7227: ("UP000000803", "Drosophila melanogaster"), 6239: ("UP000001940", "Caenorhabditis elegans"), 7955: ("UP000000437", "Danio rerio")}
MQ_ENZYME = {"trypsin": ["Trypsin/P"], "trypsin_lysc": ["Trypsin/P", "LysC"], "lysc": ["LysC"], "chymotrypsin": ["Chymotrypsin+"],
             "gluc": ["GluC"], "aspn": ["AspN"], "none": ["unspecific"], "other": ["(check protocol)"]}
TOOL = {"DDA": ("MaxQuant or FragPipe", "DDA: spectrum-centric search against a FASTA"),
        "DIA": ("DIA-NN or Spectronaut", "DIA: library-free search (predicted library from FASTA) or a spectral library"),
        "PRM_SRM": ("Skyline", "targeted: you need the original transition/target list"),
        "top_down": ("ProSight or TopPIC", "intact proteins: bottom-up tools don't apply"),
        "MALDI": ("the vendor software, or FlexAnalysis + Mascot", "MALDI data needs vendor conversion first")}


def ensure_files_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS files (accession TEXT, file_name TEXT, category TEXT, size_bytes INTEGER, ftp TEXT, fetched_at TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_files_acc ON files(accession)")


def pride_files(con, accession):
    ensure_files_table(con)
    rows = con.execute("SELECT file_name, category, size_bytes, ftp FROM files WHERE accession=?", (accession,)).fetchall()
    if rows:
        return [dict(r) for r in rows]
    try:
        with urllib.request.urlopen(f"https://www.ebi.ac.uk/pride/ws/archive/v3/projects/{accession}/files", timeout=60) as r:
            data = json.load(r)
    except Exception as e:
        print("pride files failed:", e, file=sys.stderr)
        return []
    out = []
    for f in data:
        ftp = next((l["value"] for l in f.get("publicFileLocations", []) if l.get("name") == "FTP Protocol"), None)
        out.append({"file_name": f.get("fileName"), "category": (f.get("fileCategory") or {}).get("value"), "size_bytes": f.get("fileSizeBytes"), "ftp": ftp})
    con.executemany("INSERT INTO files VALUES (?,?,?,?,?,?)", [(accession, o["file_name"], o["category"], o["size_bytes"], o["ftp"], dt.datetime.now(dt.UTC).isoformat()) for o in out])
    con.commit()
    return out


def recipe(t: dict, ptms: list[str], ev: dict):
    """Search parameters derived from the tags. Each param carries which tag it came from so the UI can show evidence."""
    def src(tag):
        e = ev.get(tag) or {}
        return {"tag": tag, "confidence": e.get("confidence"), "evidence": e.get("evidence")}
    acq = t.get("acquisition") or "DDA"
    tool, tool_note = TOOL.get(acq, TOOL["DDA"])
    enz = t.get("enzyme") or "trypsin"
    fixed = [] if enz == "none" or acq == "top_down" else ["Carbamidomethyl (C)"]
    variable = ["Oxidation (M)", "Acetyl (Protein N-term)"]
    notes = []
    enr = t.get("enrichment")
    pt = " ".join(ptms).lower()
    if enr == "phospho" or "phospho" in pt:
        variable.append("Phospho (STY)")
    if enr == "ubiquitin" or "glycyl" in pt or "gg" in pt:
        variable.append("GlyGly (K)")
    if enr == "acetyl":
        variable.append("Acetyl (K)")
    if enr == "glyco":
        variable.append("Deamidation (N)")
        notes.append("Glyco: for intact glycopeptides use a glyco-aware tool (pGlyco, MSFragger-Glyco) instead of plain deamidation.")
    if "dimethyl" in pt or t.get("labelling") == "dimethyl":
        notes.append("Dimethyl labelling: set light/medium/heavy labels rather than variable mods.")
    lab = t.get("labelling") or "label_free"
    quant = {"label_free": "LFQ on, match between runs on",
             "TMT": "Reporter ion MS2 (or MS3 if the instrument did SPS-MS3); pick the plex (10/11/16/18) from the protocol",
             "iTRAQ": "Reporter ion MS2, iTRAQ 4-plex or 8-plex from the protocol",
             "SILAC": "Multiplicity 2 or 3; heavy labels Lys8 / Arg10 unless the protocol says otherwise",
             "dimethyl": "Dimethyl light/medium/heavy", "other": "check protocol"}[lab if lab in ("label_free", "TMT", "iTRAQ", "SILAC", "dimethyl") else "other"]
    if lab in ("TMT", "iTRAQ"):
        fixed.append(f"{lab} on K and peptide N-term (set via the reporter-ion option, not as a mod)")
    taxon = t.get("taxon_id")
    prot = PROTEOMES.get(taxon) if taxon else None
    fasta = {"uniprot_proteome": prot[0] if prot else None, "organism": prot[1] if prot else t.get("organism"),
             "url": f"https://rest.uniprot.org/uniprotkb/stream?query=proteome:{prot[0]}+AND+reviewed:true&format=fasta&compressed=true" if prot else None,
             "note": "reviewed (Swiss-Prot) entries; add contaminants (MaxQuant ships them)"}
    if enr == "ap_ms":
        notes.append("AP-MS: include the bait/tag sequence in the FASTA and plan a mock/control IP comparison (CRAPome).")
    if acq == "DIA":
        notes.append("DIA-NN: library-free with 'deep learning predictor' on, MBR on, precursor FDR 1%.")
    return {
        "tool": {"value": tool, "why": tool_note, **src("acquisition")},
        "enzyme": {"value": MQ_ENZYME.get(enz, [enz]), "missed_cleavages": 2, **src("enzyme")},
        "fixed_modifications": {"value": fixed, **src("labelling")},
        "variable_modifications": {"value": variable, "from_pride_ptms": ptms, **src("enrichment")},
        "quantification": {"value": quant, **src("labelling")},
        "fdr": {"value": "PSM 1%, protein 1% (target-decoy, reversed sequences)"},
        "fasta": fasta,
        "instrument_preset": {"value": {"orbitrap": "Orbitrap (MS1 4.5 ppm, MS2 20 ppm)", "timstof": "Bruker TIMS-DDA / diaPASEF", "qtof": "Q-TOF (MS2 40 ppm)", "triple_quad": "n/a (targeted)"}.get(t.get("instrument_family") or "", "check instrument"), **src("instrument_family")},
        "notes": notes,
    }


@app.get("/dataset/{accession}/reanalyse")
def reanalyse(accession: str):
    con = db()
    p = con.execute("SELECT ptms FROM projects WHERE accession=?", (accession,)).fetchone()
    if not p:
        raise HTTPException(404, "unknown accession")
    t = con.execute("SELECT * FROM tags WHERE accession=?", (accession,)).fetchone()
    files = pride_files(con, accession)
    groups = {}
    for f in files:
        g = groups.setdefault(f["category"] or "OTHER", {"count": 0, "bytes": 0, "examples": []})
        g["count"] += 1
        g["bytes"] += f["size_bytes"] or 0
        if len(g["examples"]) < 3:
            g["examples"].append({"file_name": f["file_name"], "size_bytes": f["size_bytes"], "ftp": f["ftp"]})
    ev = {e["tag"]: dict(e) for e in con.execute("SELECT tag, confidence, evidence FROM tag_evidence WHERE accession=?", (accession,))}
    return {
        "accession": accession, "tagged": bool(t),
        "files": {"groups": groups, "total": len(files), "all_ftp_url": f"/dataset/{accession}/files.txt"},
        "recipe": recipe(dict(t), jl(p["ptms"]), ev) if t else None,
        "has_submitter_results": any(f["category"] in ("RESULT", "SEARCH") for f in files),
    }


@app.get("/dataset/{accession}/files.txt", response_class=PlainTextResponse)
def files_txt(accession: str, category: Optional[str] = None):
    files = pride_files(db(), accession)
    return "\n".join(f["ftp"] for f in files if f["ftp"] and (not category or f["category"] == category)) + "\n"


# ----------------------------------------------------------------------------- why / eval
WHY_SYSTEM = """You help a proteomics researcher decide whether a public PRIDE dataset is a good candidate for their question.
Write 3 short sentences, plain English, no bullet points: (1) what the dataset is, (2) why it fits the question, (3) the main caveat.
Ground every claim in the tags and evidence given. Never invent methods. If the question is empty, judge general reusability."""


@app.get("/dataset/{accession}/why")
def why(accession: str, q: str = ""):
    con = db()
    t = con.execute("SELECT t.*, p.title FROM tags t JOIN projects p USING(accession) WHERE t.accession=?", (accession,)).fetchone()
    if not t:
        raise HTTPException(404, "not tagged")
    t = dict(t)
    ev = {e["tag"]: dict(e) for e in con.execute("SELECT tag, value, confidence, evidence, source FROM tag_evidence WHERE accession=?", (accession,))}
    facts = "\n".join(f"{k}: {t.get(k)} (confidence {ev.get(k, {}).get('confidence')}, evidence: {ev.get(k, {}).get('evidence')!r})" for k in TAG_COLS + ["depth_proteins"])
    text, how = None, "rules"
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic(timeout=45, max_retries=1)
            msg = client.messages.create(model=os.environ.get("ASK_MODEL", "claude-sonnet-5"), max_tokens=400, output_config={"effort": "low"},
                                         system=WHY_SYSTEM, messages=[{"role": "user", "content": f"Question: {q or '(none)'}\nDataset {accession}: {t['title']}\nSummary: {t.get('summary_one_line')}\n{facts}"}])
            text = next((b.text for b in msg.content if b.type == "text"), None)
            how = "llm"
        except Exception as e:
            print("why failed:", e, file=sys.stderr)
    if not text:
        caveats = [k for k in TAG_COLS if ev.get(k) and (ev[k]["confidence"] or 1) < 0.6]
        text = (f"{t.get('summary_one_line') or t['title']}. "
                f"It is a {pretty(t.get('acquisition'))} {pretty(t.get('labelling'))} dataset on {t.get('organism')} {t.get('tissue') or t.get('cell_line') or ''} "
                f"with {pretty(t.get('enrichment'))} enrichment, digested with {pretty(t.get('enzyme'))} on an {pretty(t.get('instrument_family'))}. "
                + (f"Low-confidence tags to check by hand: {', '.join(caveats)}." if caveats else "All key tags are high confidence."))
    return {"accession": accession, "question": q, "why": text.strip(), "parser": how}


@app.get("/eval")
def eval_route():
    out = {}
    for name in ["bench", "report"]:
        f = ROOT / "eval" / f"{name}.json"
        out[name] = json.load(open(f)) if f.exists() else None
    out["tagged_by_model"] = {r[0]: r[1] for r in db().execute("SELECT model, count(*) FROM tags GROUP BY model")}
    return out


# ----------------------------------------------------------------------------- analyse
# Upload a protein table (search-engine export), pick group A vs B, get log2FC / Welch t / BH q + a volcano.
# pandas/numpy/scipy are imported lazily so the rest of the API works even if they aren't installed yet.
import shutil
import subprocess
import uuid

from fastapi import File, UploadFile

UPLOADS = ROOT / "data" / "uploads"
ANALYSIS_DIR = ROOT / "analysis"
EXAMPLE_TABLE = ANALYSIS_DIR / "data" / "lysoip_brain_batch2.csv"
UPLOAD_EXTS = (".csv", ".tsv", ".txt", ".xlsx")
MAX_UPLOAD = 200 * 1024 * 1024
REPLICATE_RE = re.compile(r"^(.+?)[._ ](\d{1,3})$")  # Set1_Brain_IP_LTKI.3 -> stem Set1_Brain_IP_LTKI
ID_PRIORITY = ["genes", "gene names", "gene", "protein.group", "protein ids", "majority protein ids", "protein.names", "protein", "accession", "id"]
TRUTHY = {"+", "true", "1", "yes", "y"}
_DF_CACHE: dict = {}


def _sci():
    """numpy + pandas, or a clear 503 until the API is restarted with the new dependencies."""
    try:
        import numpy as np
        import pandas as pd
        return np, pd
    except ImportError:
        raise HTTPException(503, "analysis needs pandas/numpy/scipy: restart the API (uv run api/main.py) to pick up the new dependencies")



class RunIn(BaseModel):
    upload_id: str
    id_column: str
    group_a: list[str]
    group_b: list[str]
    log_transform: str = "auto"  # auto | log2 | none
    min_valid: int = 2
    contaminant_column: Optional[str] = None


class OdettaIn(BaseModel):
    upload_id: str
    prefix: str = "Set1_Brain"
    name: str = "batch"


def _safe_id(upload_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", upload_id or ""):
        raise HTTPException(400, "bad upload_id")
    return upload_id


def upload_path(upload_id: str) -> pathlib.Path:
    _safe_id(upload_id)
    for ext in UPLOAD_EXTS:
        p = UPLOADS / f"{upload_id}{ext}"
        if p.exists():
            return p
    raise HTTPException(404, "unknown upload_id (uploads are kept in data/uploads)")


def as_csv(path: pathlib.Path) -> pathlib.Path:
    """Odetta's script wants CSV; convert xlsx once and cache next to it."""
    if path.suffix != ".xlsx":
        return path
    out = path.with_name(path.stem + "_conv.csv")
    if not out.exists():
        load_df(path.stem).to_csv(out, index=False)
    return out


def load_df(upload_id: str):
    _, pd = _sci()
    if upload_id in _DF_CACHE:
        return _DF_CACHE[upload_id]
    path = upload_path(upload_id)
    if path.suffix == ".xlsx":
        df = pd.read_excel(path)
    else:
        with open(path, "r", errors="replace") as fh:
            head = fh.readline()
        sep = "\t" if head.count("\t") > head.count(",") else ","
        df = pd.read_csv(path, sep=sep, low_memory=False)
    df.columns = [str(c) for c in df.columns]
    while len(_DF_CACHE) >= 3:
        _DF_CACHE.pop(next(iter(_DF_CACHE)))
    _DF_CACHE[upload_id] = df
    return df


def _clean(v):
    """JSON-safe scalar: NaN/inf -> None, numpy -> python."""
    if v is None:
        return None
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if hasattr(v, "item"):
        return _clean(v.item())
    return v


def inspect_df(upload_id: str):
    np, pd = _sci()
    df = load_df(upload_id)
    n = len(df)
    id_columns, numeric, fill = [], [], {}
    for c in df.columns:
        s = df[c]
        if s.dtype == bool or s.dtype == object or str(s.dtype) in ("string", "str"):
            nn = s.notna().sum()
            if nn >= 0.5 * n and s.nunique() >= 0.8 * max(nn, 1) and not pd.api.types.is_bool_dtype(s):
                # text column with mostly unique values: an identifier, unless it's secretly numeric
                num = pd.to_numeric(s, errors="coerce")
                if num.notna().sum() < 0.5 * nn:
                    id_columns.append(c)
                    continue
        num = pd.to_numeric(s, errors="coerce") if not pd.api.types.is_numeric_dtype(s) else s
        if pd.api.types.is_bool_dtype(num):
            continue
        f = float(num.notna().mean()) if n else 0.0
        if f >= 0.2:
            numeric.append(c)
            fill[c] = f
    pri = {k: i for i, k in enumerate(ID_PRIORITY)}
    id_columns.sort(key=lambda c: (pri.get(c.lower(), 99), df.columns.get_loc(c)))
    groups: dict = {}
    for c in numeric:
        m = REPLICATE_RE.match(c)
        if m:
            groups.setdefault(m.group(1), []).append(c)
    suggested = [{"stem": k, "columns": v, "fill": round(float(np.mean([fill[c] for c in v])), 3)} for k, v in groups.items() if len(v) >= 2]
    suggested.sort(key=lambda g: (-len(g["columns"]), -g["fill"], g["stem"]))
    ki = {m.group(1) for c in df.columns if (m := re.match(r"^(.+)_IP_LTKI\.\d+$", c)) and not c.startswith("Imputation_")}
    wt = {m.group(1) for c in df.columns if (m := re.match(r"^(.+)_IP_LTWT\.\d+$", c)) and not c.startswith("Imputation_")}
    prefixes = sorted(ki & wt)
    contaminant_columns = [c for c in df.columns if re.search(r"contamin|reverse|decoy|only identified by site", c, re.I)]
    return {
        "upload_id": upload_id, "file": upload_path(upload_id).name, "n_rows": int(n), "n_cols": int(df.shape[1]),
        "id_columns": id_columns, "numeric_columns": numeric, "suggested_groups": suggested,
        "contaminant_columns": contaminant_columns,
        "odetta_compatible": bool(prefixes), "odetta_prefixes": prefixes,
    }


try:  # FastAPI refuses to register a File(...) route without python-multipart; keep the API bootable until the server is restarted with the new deps
    import multipart  # noqa: F401
    HAVE_MULTIPART = True
except ImportError:
    HAVE_MULTIPART = False


async def _analyse_inspect(file: UploadFile):
    ext = pathlib.Path(file.filename or "table.csv").suffix.lower()
    if ext not in UPLOAD_EXTS:
        raise HTTPException(400, f"unsupported file type {ext}; use .csv, .tsv, .txt or .xlsx")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:12]
    dest = UPLOADS / f"{uid}{ext}"
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "file larger than 200 MB")
            out.write(chunk)
    try:
        return inspect_df(uid)
    except HTTPException:
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"could not read the table: {e}")


if HAVE_MULTIPART:
    @app.post("/analyse/inspect")
    async def analyse_inspect(file: UploadFile = File(...)):
        return await _analyse_inspect(file)
else:
    @app.post("/analyse/inspect")
    async def analyse_inspect_unavailable():
        raise HTTPException(503, "upload needs python-multipart: restart the API (uv run api/main.py) to pick up the new dependencies")


@app.get("/analyse/example")
def analyse_example():
    """Register Odetta's lyso-IP batch 2 export as an upload (symlink) so the UI has a one-click example."""
    src = EXAMPLE_TABLE if EXAMPLE_TABLE.exists() else EXAMPLE_TABLE.with_suffix(".xlsx")
    if not src.exists():
        raise HTTPException(404, "example table not found under analysis/data/")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    uid = "example_" + src.stem
    dest = UPLOADS / f"{uid}{src.suffix}"
    if not dest.exists():
        try:
            os.symlink(src, dest)
        except OSError:
            shutil.copyfile(src, dest)
    return {"upload_id": uid, "file": src.name}


@app.get("/analyse/{upload_id}")
def analyse_get(upload_id: str):
    """Re-open an existing upload (same payload as POST /analyse/inspect)."""
    return inspect_df(upload_id)


def bh_fdr(p):
    """Benjamini-Hochberg q-values; NaN p stays NaN."""
    np, _ = _sci()
    p = np.asarray(p, float)
    q = np.full_like(p, np.nan)
    ok = ~np.isnan(p)
    m = int(ok.sum())
    if m == 0:
        return q
    order = np.argsort(p[ok])
    ranked = p[ok][order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    qq = np.empty(m)
    qq[order] = np.minimum(ranked, 1.0)
    q[ok] = qq
    return q


@app.post("/analyse/run")
def analyse_run(body: RunIn):
    np, pd = _sci()
    from scipy import stats as sps
    df = load_df(body.upload_id)
    missing = [c for c in [body.id_column, *body.group_a, *body.group_b] if c not in df.columns]
    if missing:
        raise HTTPException(400, f"columns not in table: {missing[:5]}")
    if len(body.group_a) < 2 or len(body.group_b) < 2:
        raise HTTPException(400, "each group needs at least 2 columns (replicates) for a t-test")
    if set(body.group_a) & set(body.group_b):
        raise HTTPException(400, "a column can't be in both groups")
    n_input = len(df)
    dropped_contaminants = 0
    if body.contaminant_column:
        if body.contaminant_column not in df.columns:
            raise HTTPException(400, "contaminant column not in table")
        flag = df[body.contaminant_column].astype(str).str.strip().str.lower().isin(TRUTHY)
        dropped_contaminants = int(flag.sum())
        df = df[~flag]
    ids = df[body.id_column].astype(str).where(df[body.id_column].notna(), "").to_numpy()
    A = np.array(df[body.group_a].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float), copy=True)
    B = np.array(df[body.group_b].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float), copy=True)
    A[~(A > 0)] = np.nan  # 0 / negative / NaN = missing
    B[~(B > 0)] = np.nan
    allv = np.concatenate([A.ravel(), B.ravel()])
    allv = allv[~np.isnan(allv)]
    p99 = float(np.percentile(allv, 99)) if allv.size else 0.0
    mode = body.log_transform
    if mode == "auto":
        mode = "log2" if p99 > 100 else "none"
    if mode == "log2":
        A, B = np.log2(A), np.log2(B)
    elif mode != "none":
        raise HTTPException(400, "log_transform must be auto, log2 or none")
    min_valid = max(2, int(body.min_valid))
    n_a, n_b = np.sum(~np.isnan(A), axis=1), np.sum(~np.isnan(B), axis=1)
    keep = (n_a >= min_valid) & (n_b >= min_valid)
    A, B, ids, n_a, n_b = A[keep], B[keep], ids[keep], n_a[keep], n_b[keep]
    with np.errstate(all="ignore"):
        ma, mb = np.nanmean(A, axis=1), np.nanmean(B, axis=1)
        va, vb = np.nanvar(A, axis=1, ddof=1), np.nanvar(B, axis=1, ddof=1)
        se = np.sqrt(va / n_a + vb / n_b)
        t = (mb - ma) / se
        dof = se ** 4 / ((va / n_a) ** 2 / (n_a - 1) + (vb / n_b) ** 2 / (n_b - 1))
        p = 2 * sps.t.sf(np.abs(t), dof)
    p = np.where(np.isfinite(p), p, np.nan)
    q = bh_fdr(p)
    log2fc = mb - ma
    sig = ~np.isnan(q) & (q < 0.05)
    up, down = sig & (log2fc > 0), sig & (log2fc < 0)
    order = np.argsort(np.where(np.isnan(p), np.inf, p), kind="stable")
    n_keep = max(2000, int(sig.sum()))
    top = order[:n_keep]
    r = lambda x, d=4: None if (x is None or not np.isfinite(x)) else round(float(x), d)  # noqa: E731
    rows = [{"id": ids[i], "log2fc": r(log2fc[i]), "t": r(t[i], 3), "p": r(p[i], 6) if p[i] >= 1e-4 else _clean(float(p[i])),
             "q": r(q[i], 6) if q[i] >= 1e-4 else _clean(float(q[i])), "mean_a": r(ma[i], 3), "mean_b": r(mb[i], 3),
             "n_a": int(n_a[i]), "n_b": int(n_b[i])} for i in top]
    vol_idx = order[:20000]
    volcano = [{"id": ids[i], "log2fc": r(log2fc[i], 3), "neglog10p": r(-np.log10(p[i]), 3),
                "sig": "up" if up[i] else "down" if down[i] else "ns"} for i in vol_idx if not np.isnan(p[i])]
    return {
        "summary": {"n_proteins": int(n_input), "n_dropped_contaminants": dropped_contaminants, "n_tested": int(len(ids)),
                    "n_sig_q05": int(sig.sum()), "n_up": int(up.sum()), "n_down": int(down.sum()),
                    "log_transform": mode, "p99_raw": r(p99, 2), "min_valid": min_valid,
                    "sign": "positive = higher in B", "test": "Welch t-test, two-sided; q = Benjamini-Hochberg FDR"},
        "columns": ["id", "log2fc", "p", "q", "t", "mean_a", "mean_b", "n_a", "n_b"],
        "rows": rows, "volcano": volcano,
    }


@app.post("/analyse/odetta")
def analyse_odetta(body: OdettaIn):
    """Run Odetta's TESE lyso-IP script on an upload. Never 500s: failures come back as ok=false with the log."""
    _, pd = _sci()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", body.prefix) or not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", body.name):
        return {"ok": False, "error": "prefix and name must be letters, digits, _ or -", "stdout": "", "stderr": "", "outputs": []}
    try:
        csv = as_csv(upload_path(body.upload_id))
    except HTTPException as e:
        return {"ok": False, "error": str(e.detail), "stdout": "", "stderr": "", "outputs": []}
    except Exception as e:
        return {"ok": False, "error": f"could not convert to CSV: {e}", "stdout": "", "stderr": "", "outputs": []}
    outdir = UPLOADS / f"{body.upload_id}_out"
    outdir.mkdir(parents=True, exist_ok=True)
    script = ANALYSIS_DIR / "tese_lysoip_analysis.py"
    uv = shutil.which("uv")
    cmd = ([uv, "run", "--with", "pandas", "--with", "numpy", "--with", "scipy", "python"] if uv else [sys.executable]) + [
        str(script), "--input", str(csv), "--name", body.name, "--prefix", body.prefix, "--outdir", str(outdir)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        out, err, rc = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "script timed out after 600 s", "stdout": "", "stderr": "", "outputs": [], "command": " ".join(cmd)}
    except Exception as e:
        return {"ok": False, "error": str(e), "stdout": "", "stderr": "", "outputs": [], "command": " ".join(cmd)}
    outputs = []
    for f in sorted(outdir.glob("*.csv")):
        try:
            d = pd.read_csv(f, low_memory=False)
            head = [{k: _clean(v) for k, v in rec.items()} for rec in d.head(20).to_dict(orient="records")]
            outputs.append({"name": f.name, "rows": int(len(d)), "columns": [str(c) for c in d.columns], "head": head})
        except Exception as e:
            outputs.append({"name": f.name, "rows": 0, "columns": [], "head": [], "error": str(e)})
    tail = lambda s: "\n".join(s.strip().splitlines()[-30:])  # noqa: E731
    return {"ok": rc == 0, "returncode": rc, "error": None if rc == 0 else f"script exited with {rc}",
            "stdout": tail(out), "stderr": tail(err), "outputs": outputs, "outdir": str(outdir.relative_to(ROOT)), "command": " ".join(cmd)}


# ----------------------------------------------------------------------------- protein (UniProt)
ORG_TAXON = {"homo sapiens": 9606, "mus musculus": 10090, "rattus norvegicus": 10116, "saccharomyces cerevisiae": 4932,
             "arabidopsis thaliana": 3702, "escherichia coli": 562, "drosophila melanogaster": 7227, "caenorhabditis elegans": 6239, "danio rerio": 7955}


def tryptic_peptides(seq: str, lo=7, hi=25):
    """Count peptides trypsin would produce (cut after K/R unless followed by P) with a usable length; a quick 'can it be seen' proxy."""
    peps, cur = [], ""
    for i, aa in enumerate(seq):
        cur += aa
        nxt = seq[i + 1] if i + 1 < len(seq) else ""
        if aa in "KR" and nxt != "P":
            peps.append(cur)
            cur = ""
    if cur:
        peps.append(cur)
    return sum(1 for x in peps if lo <= len(x) <= hi), len(peps)


@app.get("/protein")
def protein(name: str, organism: Optional[str] = None):
    """UniProt lookup for a gene/protein name: what it is, where it's expressed, how detectable it is by tryptic LC-MS."""
    con = db()
    con.execute("CREATE TABLE IF NOT EXISTS protein_cache (key TEXT PRIMARY KEY, json TEXT, fetched_at TEXT)")
    taxon = ORG_TAXON.get((norm_org(organism) or "").lower()) if organism else None
    key = f"{name.strip().upper()}|{taxon or ''}"
    row = con.execute("SELECT json FROM protein_cache WHERE key=?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    q = f"(gene_exact:{name} OR protein_name:{name})" + (f" AND organism_id:{taxon}" if taxon else "") + " AND reviewed:true"
    url = ("https://rest.uniprot.org/uniprotkb/search?query=" + urllib.parse.quote(q)
           + "&fields=accession,protein_name,gene_primary,organism_name,cc_tissue_specificity,cc_subcellular_location,cc_function,sequence,length,mass&format=json&size=1")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            res = json.load(r).get("results", [])
    except Exception as e:
        return {"found": False, "name": name, "error": str(e)[:200]}
    if not res:
        out = {"found": False, "name": name, "organism": organism}
    else:
        r = res[0]
        seq = r.get("sequence", {}).get("value", "")
        usable, total = tryptic_peptides(seq)
        comments = {c["commentType"]: c for c in r.get("comments", [])}
        tissue = (comments.get("TISSUE SPECIFICITY", {}).get("texts") or [{}])[0].get("value")
        func = (comments.get("FUNCTION", {}).get("texts") or [{}])[0].get("value")
        locs = [l["location"]["value"] for l in comments.get("SUBCELLULAR LOCATION", {}).get("subcellularLocations", [])][:4]
        out = {
            "found": True, "name": name, "accession": r["primaryAccession"],
            "protein_name": r.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value"),
            "gene": (r.get("genes") or [{}])[0].get("geneName", {}).get("value"),
            "organism": r.get("organism", {}).get("scientificName"),
            "length": r.get("sequence", {}).get("length"), "mass_kda": round((r.get("sequence", {}).get("molWeight") or 0) / 1000, 1),
            "tissue_specificity": tissue, "function": (func or "")[:400], "locations": locs,
            "tryptic_peptides_usable": usable, "tryptic_peptides_total": total,
            "detectability": ("good" if usable >= 8 else "fair" if usable >= 4 else "poor") + f": trypsin yields {usable} peptides of usable length (7–25 aa)",
            "url": f"https://www.uniprot.org/uniprotkb/{r['primaryAccession']}/entry",
        }
    con.execute("INSERT OR REPLACE INTO protein_cache VALUES (?,?,?)", (key, json.dumps(out), dt.datetime.now(dt.UTC).isoformat()))
    con.commit()
    return out


@app.get("/")
def root():
    return {"name": "PRIDE Map API", "docs": "/docs", "stats": "/stats", "search": "/search?organism=mouse&enrichment=phospho"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=True, app_dir=str(pathlib.Path(__file__).parent))
