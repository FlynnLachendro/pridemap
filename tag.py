# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic>=0.80", "pydantic>=2"]
# ///
"""Tag PRIDE datasets with a fixed schema using Claude structured outputs.

usage:
  uv run tag.py --accessions data/slice.txt            # direct mode, resume-safe
  uv run tag.py --accessions data/slice.txt --force    # re-tag
  uv run tag.py --limit 50 --model claude-haiku-4-5
Reads ANTHROPIC_API_KEY from env (source ~/.zshrc first if needed).
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import pathlib
import sqlite3
import sys
import threading
from typing import Literal, Optional

from pydantic import BaseModel, Field

ROOT = pathlib.Path(__file__).parent
DB = ROOT / "data" / "pride.db"
# load .env (ANTHROPIC_API_KEY=...) without a dependency
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))
PRICE = {  # $/MTok input, output
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-5": (5.0, 25.0),
}

Source = Literal["metadata", "paper", "inferred", "none"]


class Tag(BaseModel):
    value: Optional[str] = Field(description="One of the allowed values for this tag, or null if not determinable")
    confidence: float = Field(ge=0, le=1)
    source: Source
    evidence: str = Field(description="Short verbatim quote (<=200 chars) that supports the value, or empty")


class EnumTag(Tag):
    pass


class Tags(BaseModel):
    organism: Tag
    taxon_id: Optional[int]
    sample_type: Tag
    tissue: Tag
    cell_line: Tag
    disease: Tag
    enrichment: Tag
    acquisition: Tag
    labelling: Tag
    enzyme: Tag
    fractionation: Tag
    instrument_family: Tag
    depth_proteins: Tag
    search_engine: Tag
    summary_one_line: str = Field(description="<=140 chars: sample, prep, acquisition, instrument, purpose")


VOCAB = {
    "sample_type": ["tissue", "cell_line", "primary_cells", "biofluid", "organism_whole", "environmental", "other"],
    "enrichment": ["none", "phospho", "ubiquitin", "glyco", "acetyl", "ap_ms", "proximity_labelling", "secretome", "membrane", "other"],
    "acquisition": ["DDA", "DIA", "PRM_SRM", "top_down", "MALDI", "other"],
    "labelling": ["label_free", "TMT", "iTRAQ", "SILAC", "dimethyl", "other"],
    "enzyme": ["trypsin", "trypsin_lysc", "lysc", "chymotrypsin", "gluc", "aspn", "none", "other"],
    "fractionation": ["none", "high_ph_rp", "scx", "gel", "other"],
    "instrument_family": ["orbitrap", "timstof", "qtof", "triple_quad", "fticr", "ion_trap", "other"],
    "search_engine": ["maxquant", "proteome_discoverer", "fragpipe", "diann", "spectronaut", "mascot", "other"],
}

SYSTEM = f"""You are a proteomics data curator annotating PRIDE datasets so researchers can find datasets that could detect their protein of interest.

You will receive one dataset: PRIDE metadata (title, description, sample processing protocol, data processing protocol, structured fields) and, when available, the Methods section of its paper.

Fill every tag. Rules:
- Use ONLY the allowed values for closed tags: {json.dumps(VOCAB)}
- organism: NCBI scientific name (e.g. "Homo sapiens", "Mus musculus"); taxon_id the NCBI taxon id if known (9606, 10090, 10116, 4932, 3702, 562, 7227, 6239, 7955). For multi-organism datasets give the primary one.
- tissue: a concrete tissue or organ term (e.g. "liver", "blood plasma", "cerebral cortex"); null for cell lines unless the tissue of origin is the point. cell_line: the line name (e.g. "HeLa", "HEK293T") or null.
- disease: the disease studied, "disease free" if explicitly healthy controls only, else null.
- enrichment: "phospho" if phosphopeptide enrichment (TiO2, IMAC, Fe-NTA, pY antibody) was done; "ap_ms" for IP/affinity purification/pull-down; "proximity_labelling" for BioID/TurboID/APEX; "none" for global/whole proteome; "other" for anything else enriched.
- acquisition: DDA unless the text says DIA/SWATH/diaPASEF (then DIA), PRM/SRM/MRM targeted (PRM_SRM), intact protein (top_down), MALDI.
- labelling: TMT (TMTpro, TMT10/11/16/18), iTRAQ, SILAC, dimethyl, else label_free.
- enzyme: trypsin_lysc if both used; "none" for top-down.
- fractionation: high_ph_rp (high pH reversed phase / basic RP), scx, gel (SDS-PAGE bands / GeLC), none if single-shot, other.
- instrument_family: orbitrap (Q Exactive*, Orbitrap *, Exploris, Astral, LTQ Orbitrap, Fusion, Lumos, Eclipse), timstof (timsTOF*), qtof (TripleTOF, Synapt, Impact, maXis, ZenoTOF), triple_quad (QTRAP, TSQ, Xevo TQ), fticr, ion_trap (LTQ without Orbitrap, amaZon).
- depth_proteins: number of proteins identified/quantified if stated, as a string of digits; else null.
- search_engine: the primary one.
- confidence: 0.95+ when stated explicitly; 0.6-0.8 when inferred from instrument/software conventions; <=0.5 when guessing; use null value with low confidence rather than inventing.
- source: "metadata" if from PRIDE fields/protocols, "paper" if from Methods, "inferred" if deduced, "none" if null.
- evidence: a short verbatim quote supporting the value. Empty string if none.
- summary_one_line: plain, specific, <=140 chars. E.g. "Mouse liver, phospho-enriched (TiO2), TMT11, high-pH fractionation, Orbitrap Fusion Lumos; insulin signalling."
"""


def render(p: dict, methods: str | None) -> str:
    parts = [
        f"ACCESSION: {p['accession']}",
        f"TITLE: {p['title']}",
        f"DESCRIPTION: {p['description']}",
        f"SAMPLE PROCESSING PROTOCOL: {p['sample_protocol']}",
        f"DATA PROCESSING PROTOCOL: {p['data_protocol']}",
        f"ORGANISMS: {p['organisms']}",
        f"ORGANISM PARTS: {p['organism_parts']}",
        f"DISEASES: {p['diseases']}",
        f"INSTRUMENTS: {p['instruments']}",
        f"EXPERIMENT TYPES: {p['experiment_types']}",
        f"QUANTIFICATION METHODS: {p['quant_methods']}",
        f"SOFTWARE: {p['softwares']}",
        f"PTMS: {p['ptms']}",
        f"KEYWORDS: {p['keywords']}",
        f"PUBLICATION DATE: {p['publication_date']}",
    ]
    if methods:
        parts.append("\nPAPER METHODS SECTION (Europe PMC):\n" + methods)
    else:
        parts.append("\nPAPER METHODS SECTION: not available (paywalled or not linked).")
    return "\n".join(parts)


def load(con, accessions, limit, force):
    q = ("SELECT p.accession,p.title,p.description,p.sample_protocol,p.data_protocol,p.organisms,p.organism_parts,"
         "p.diseases,p.instruments,p.experiment_types,p.quant_methods,p.softwares,p.ptms,p.keywords,p.publication_date,"
         "pp.methods_text FROM projects p LEFT JOIN papers pp ON pp.pubmed_id=p.pubmed_id")
    where, params = [], []
    if accessions:
        accs = [a.strip() for a in pathlib.Path(accessions).read_text().split() if a.strip()]
        where.append(f"p.accession IN ({','.join('?' * len(accs))})")
        params += accs
    if not force:
        where.append("p.accession NOT IN (SELECT accession FROM tags)")
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY p.organic_downloads DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    cols = ["accession", "title", "description", "sample_protocol", "data_protocol", "organisms", "organism_parts",
            "diseases", "instruments", "experiment_types", "quant_methods", "softwares", "ptms", "keywords",
            "publication_date", "methods_text"]
    return [dict(zip(cols, r)) for r in con.execute(q, params)]


def tag_one(client, model: str, p: dict):
    msg = client.messages.parse(
        model=model,
        max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": render(p, p.get("methods_text"))}],
        output_format=Tags,
    )
    return msg.parsed_output, msg.usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accessions")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry", action="store_true", help="print one rendered prompt and exit")
    ap.add_argument("--render-dir", help="write system prompt + one rendered input per dataset to this dir and exit")
    a = ap.parse_args()

    con = sqlite3.connect(DB, check_same_thread=False)
    rows = load(con, a.accessions, a.limit, a.force)
    print(f"to tag: {len(rows)} model={a.model}", flush=True)
    if a.dry:
        print(SYSTEM[:800], "...\n---\n", render(rows[0], rows[0].get("methods_text"))[:3000])
        return
    if a.render_dir:
        d = pathlib.Path(a.render_dir); d.mkdir(parents=True, exist_ok=True)
        (d / "_SYSTEM.txt").write_text(SYSTEM)
        (d / "_SCHEMA.json").write_text(json.dumps(Tags.model_json_schema(), indent=1))
        for p in rows:
            (d / f"{p['accession']}.txt").write_text(render(p, p.get("methods_text")))
        print(f"rendered {len(rows)} → {d}")
        return
    if not rows:
        return
    import anthropic
    client = anthropic.Anthropic()
    lock = threading.Lock()
    tin = tout = done = 0
    pin, pout = PRICE.get(a.model, (2.0, 10.0))

    def work(p):
        try:
            tags, usage = tag_one(client, a.model, p)
            return p["accession"], tags, usage, None
        except Exception as e:
            return p["accession"], None, None, f"{type(e).__name__}: {e}"[:300]

    with cf.ThreadPoolExecutor(a.workers) as ex:
        for acc, tags, usage, err in ex.map(work, rows):
            if err:
                print(f"{acc} FAILED {err}", file=sys.stderr, flush=True)
                continue
            d = tags.model_dump()
            with lock:
                con.execute(
                    "INSERT OR REPLACE INTO tags VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (acc, d["organism"]["value"], d["taxon_id"], d["sample_type"]["value"], d["tissue"]["value"],
                     d["cell_line"]["value"], d["disease"]["value"], d["enrichment"]["value"], d["acquisition"]["value"],
                     d["labelling"]["value"], d["enzyme"]["value"], d["fractionation"]["value"],
                     d["instrument_family"]["value"],
                     int(d["depth_proteins"]["value"]) if (d["depth_proteins"]["value"] or "").isdigit() else None,
                     d["search_engine"]["value"], d["summary_one_line"], a.model, dt.datetime.now(dt.UTC).isoformat(),
                     usage.input_tokens, usage.output_tokens, json.dumps(d)),
                )
                con.execute("DELETE FROM tag_evidence WHERE accession=?", (acc,))
                for k, v in d.items():
                    if isinstance(v, dict) and "value" in v:
                        con.execute("INSERT INTO tag_evidence VALUES (?,?,?,?,?,?)",
                                    (acc, k, v["value"], v["confidence"], v["source"], v["evidence"]))
                con.commit()
                done += 1
                tin += usage.input_tokens + getattr(usage, "cache_read_input_tokens", 0) or 0
                tout += usage.output_tokens
                cost = (usage.input_tokens * pin + usage.output_tokens * pout) / 1e6
                if done % 10 == 0 or done == len(rows):
                    tot = (tin * pin + tout * pout) / 1e6
                    print(f"{done}/{len(rows)} {acc} {d['summary_one_line'][:90]!r} ~${tot:.2f} so far", flush=True)
    print(f"done={done} tokens in={tin} out={tout}")


if __name__ == "__main__":
    main()
