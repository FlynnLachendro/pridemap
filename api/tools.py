"""The tools PRIDE Map exposes to AI agents. One definition, used by both the MCP server (api/mcp_server.py)
and the in-app agent console (POST /agent). Every tool is a thin call to the REST API, so the API stays the
single source of truth and the docs at /docs describe the same behaviour."""
import json
import os
import urllib.parse
import urllib.request

API = os.environ.get("PRIDEMAP_API", "http://localhost:8000")


def _get(path: str, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
    with urllib.request.urlopen(f"{API}{path}{'?' + q if q else ''}", timeout=120) as r:
        return json.load(r)


def _post(path: str, body: dict):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def _slim(r: dict) -> dict:
    return {"accession": r["accession"], "summary": r.get("summary") or r.get("title"), "year": (r.get("publication_date") or "")[:4],
            "downloads": r.get("downloads"), "tags": {k: v for k, v in (r.get("tags") or {}).items() if v}, "tagged": r.get("tagged", True),
            "fits": [f["text"] for f in r.get("fits", [])], "watch": [w["text"] for w in r.get("watch", [])], "pride_url": r.get("pride_url")}


# ----------------------------------------------------------------------------- tool functions
def search_datasets(organism: str | None = None, tissue: str | None = None, enrichment: str | None = None, acquisition: str | None = None,
                    labelling: str | None = None, enzyme: str | None = None, instrument_family: str | None = None, sample_type: str | None = None,
                    disease: str | None = None, since: int | None = None, q: str | None = None, limit: int = 10) -> dict:
    d = _get("/browse", organism=organism, tissue=tissue, enrichment=enrichment, acquisition=acquisition, labelling=labelling, enzyme=enzyme,
             instrument_family=instrument_family, sample_type=sample_type, disease=disease, since=since, q=q, size=limit, tagged="only")
    return {"total": d["total"], "results": [_slim(r) for r in d["results"]], "facets": {k: v[:6] for k, v in d["facets"].items()}}


def ask_question(question: str) -> dict:
    d = _post("/ask", {"q": question})
    return {"interpretation": d["interpretation"], "filters": d["filters"], "dropped": d.get("dropped", []), "total": d["total"],
            "results": [_slim(r) for r in d["results"][:10]]}


def get_dataset(accession: str) -> dict:
    d = _get(f"/dataset/{accession}")
    p = d["project"]
    return {"accession": accession, "title": p["title"], "publication_date": p.get("publication_date"), "pride_url": d["pride_url"],
            "paper_url": d.get("paper_url"), "tagged": d["tagged"], "tags": {k: v for k, v in d["tags"].items() if v},
            "evidence": [{"tag": e["tag"], "value": e["value"], "confidence": e["confidence"], "quote": e["evidence"]} for e in d["evidence"]],
            "description": (p.get("description") or "")[:800], "sample_protocol": (p.get("sample_protocol") or "")[:1200]}


def compare_datasets(accessions: list[str]) -> dict:
    d = _get("/compare", accessions=",".join(accessions))
    return {"verdict": d["verdict"], "summary": d["summary"], "issues": d["issues"],
            "table": {tag: {acc: cell["value"] for acc, cell in cells.items()} for tag, cells in d["table"].items()}}


def setup_sheet(accession: str) -> dict:
    d = _get(f"/dataset/{accession}/reanalyse")
    files = {k: {"count": v["count"], "gb": round(v["bytes"] / 1e9, 1)} for k, v in d["files"]["groups"].items()}
    r = d.get("recipe") or {}
    return {"accession": accession, "has_submitter_results": d["has_submitter_results"], "files": files,
            "recipe": {k: (v.get("value") if isinstance(v, dict) else v) for k, v in r.items()} if r else None,
            "all_ftp_links": f"{API}/dataset/{accession}/files.txt"}


def protein_info(name: str, organism: str | None = None) -> dict:
    return _get("/protein", name=name, organism=organism)


def why_candidate(accession: str, question: str = "") -> dict:
    return _get(f"/dataset/{accession}/why", q=question)


# ----------------------------------------------------------------------------- schemas (JSON Schema, for Claude tool use and the docs page)
TAG_ENUMS = {
    "enrichment": ["none", "phospho", "ubiquitin", "glyco", "acetyl", "ap_ms", "proximity_labelling", "secretome", "membrane", "other"],
    "acquisition": ["DDA", "DIA", "PRM_SRM", "top_down", "MALDI", "other"],
    "labelling": ["label_free", "TMT", "iTRAQ", "SILAC", "dimethyl", "other"],
    "enzyme": ["trypsin", "trypsin_lysc", "lysc", "chymotrypsin", "gluc", "aspn", "none", "other"],
    "instrument_family": ["orbitrap", "timstof", "qtof", "triple_quad", "fticr", "ion_trap", "other"],
    "sample_type": ["tissue", "cell_line", "primary_cells", "biofluid", "organism_whole", "environmental", "other"],
}

TOOLS = [
    {"name": "search_datasets", "fn": search_datasets,
     "description": "Find PRIDE proteomics datasets by how they were made. Filters are method tags read from each dataset's protocol and paper "
                    "(organism, tissue, enrichment, DDA/DIA, labelling, enzyme, instrument, sample type, disease, year). Returns ranked datasets with "
                    "'fits' and 'watch' reasons and facet counts. Use q only for free text (keywords); protein names are not in dataset text.",
     "input_schema": {"type": "object", "properties": {
         "organism": {"type": "string", "description": "e.g. 'Homo sapiens', 'Mus musculus', or 'human'/'mouse'"},
         "tissue": {"type": "string"}, "disease": {"type": "string"},
         **{k: {"type": "string", "enum": v} for k, v in TAG_ENUMS.items()},
         "since": {"type": "integer", "description": "publication year, inclusive"},
         "q": {"type": "string", "description": "free-text keywords over titles, descriptions and protocols"},
         "limit": {"type": "integer", "default": 10}}, "additionalProperties": False}},
    {"name": "ask_question", "fn": ask_question,
     "description": "Turn a plain-English research question into filters and run it (what the Browse page does). Returns the interpretation, "
                    "the filters used, anything dropped and why, and the top datasets.",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"], "additionalProperties": False}},
    {"name": "get_dataset", "fn": get_dataset,
     "description": "Full record for one PRIDE dataset: title, tags with confidence and the evidence sentence each came from, protocol text, links.",
     "input_schema": {"type": "object", "properties": {"accession": {"type": "string", "description": "e.g. PXD029247"}}, "required": ["accession"], "additionalProperties": False}},
    {"name": "compare_datasets", "fn": compare_datasets,
     "description": "Are these datasets comparable in one analysis? Verdict (comparable / with caution / not), the method differences that matter "
                    "(labelling, acquisition, enrichment, organism, enzyme, instrument…) and the tag table side by side.",
     "input_schema": {"type": "object", "properties": {"accessions": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6}}, "required": ["accessions"], "additionalProperties": False}},
    {"name": "setup_sheet", "fn": setup_sheet,
     "description": "What you need to reanalyse a dataset: the files PRIDE holds (raw / peak lists / submitter results, counts and GB) and the "
                    "database-search parameters derived from its tags (tool, enzyme, fixed and variable modifications, quantification, FDR, FASTA).",
     "input_schema": {"type": "object", "properties": {"accession": {"type": "string"}}, "required": ["accession"], "additionalProperties": False}},
    {"name": "protein_info", "fn": protein_info,
     "description": "UniProt lookup for a gene or protein name: what it is, tissue expression note, subcellular location, length, and how detectable "
                    "it is by tryptic LC-MS (number of usable tryptic peptides).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "gene symbol or protein name, e.g. MFF"}, "organism": {"type": "string"}}, "required": ["name"], "additionalProperties": False}},
    {"name": "why_candidate", "fn": why_candidate,
     "description": "A three-sentence case for or against one dataset as a candidate for a question: what it is, why it fits, the main caveat.",
     "input_schema": {"type": "object", "properties": {"accession": {"type": "string"}, "question": {"type": "string"}}, "required": ["accession"], "additionalProperties": False}},
]
BY_NAME = {t["name"]: t for t in TOOLS}


def call(name: str, args: dict):
    return BY_NAME[name]["fn"](**args)


def claude_tools():
    """Tool definitions in the shape the Claude Messages API expects."""
    return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in TOOLS]
