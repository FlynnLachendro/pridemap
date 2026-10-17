"""omics router: OmicsDI cross-omics links, the quantms reanalysis kit, and the corrections feed. Included from main.py.

Helpers from main (pride_files, recipe, jl, TAG_COLS, PRIDE_URL) are imported lazily inside handlers: main imports this
module at load time, so a top-level import would be circular.
"""
import concurrent.futures as cf
import datetime as dt
import io
import json
import os
import pathlib
import re
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "pride.db"
router = APIRouter()


def db():
    con = sqlite3.connect(DB, check_same_thread=False, timeout=60)
    con.row_factory = sqlite3.Row
    return con


def now_iso():
    return dt.datetime.now(dt.UTC).isoformat()


# ----------------------------------------------------------------------------- OmicsDI cross-omics links
OMICSDI = "https://www.omicsdi.org/ws/dataset/search"
OMICS_TYPES = ["Transcriptomics", "Genomics", "Metabolomics", "Multiomics"]
CACHE_HOURS = 24
STOP = set("""a an and the of in on for to with by from at as is are was were be been into via using use used under over
between across within without during after before new novel study studies analysis analyses data dataset datasets based
approach approaches method methods reveals reveal revealed identifies identify identification characterization characterisation
quantitative quantification global comprehensive integrated integrative large scale high throughput deep profiling profile
proteome proteomes proteomic proteomics phosphoproteome phosphoproteomic phosphoproteomics phosphoproteomes proteogenomic
mass spectrometry spectrometric ms lc tandem label free tmt silac dia dda targeted peptide peptides protein proteins
sample samples tissue tissues cell cells cellular human mouse mice rat murine model models response responses effect effects
role roles level levels change changes dependent mediated associated related specific different wide multiple various
several across""".split())


def _terms(words: str, n: int):
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", words or ""):
        lw = w.lower()
        if lw in STOP or lw in out or len(lw) < 4:
            continue
        out.append(lw)
        if len(out) >= n:
            break
    return out


def _q(s: str) -> str:
    return '"' + s.replace('"', " ").strip() + '"'


def omics_query_parts(tags: dict, project: dict) -> dict:
    """Required part (organism + tissue/disease) and optional title keywords for the OmicsDI lucene query."""
    from main import jl
    organism = (tags.get("organism") or (jl(project.get("organisms")) or [None])[0] or "").strip()
    required = []
    if organism:
        required.append(_q(organism))
    tissue_terms = _terms(tags.get("tissue") or "", 3)
    if tissue_terms:
        required.append("(" + " OR ".join(tissue_terms) + ")")
    disease = (tags.get("disease") or "").strip()
    if disease and disease.lower() not in ("disease free", "none", "healthy"):
        required.append("(" + " OR ".join(_terms(disease, 3) or [_q(disease)]) + ")")
    keywords = [k for k in _terms(project.get("title") or "", 6) if k not in tissue_terms][:4]
    return {"required": required, "keywords": keywords}


def _omicsdi(query: str, size: int) -> list[dict]:
    url = f"{OMICSDI}?{urllib.parse.urlencode({'query': query, 'size': size})}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "pridemap/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    return data.get("datasets") or []


def _fetch_types(base: str) -> dict[str, list[dict]]:
    with cf.ThreadPoolExecutor(len(OMICS_TYPES)) as ex:
        futs = {t: ex.submit(_omicsdi, f'{base} AND omics_type:{_q(t)}', 4) for t in OMICS_TYPES}
        out = {}
        for t, f in futs.items():
            try:
                out[t] = f.result()
            except Exception as e:  # one failing type should not kill the panel
                print("omicsdi failed:", t, e, file=sys.stderr)
                out[t] = []
        return out


def _shape(d: dict, accession: str) -> dict | None:
    types = d.get("omicsType") or []
    if d.get("id") == accession or not types:
        return None
    if "Proteomics" in types and "Multiomics" not in types:
        return None
    omics = next((t for t in OMICS_TYPES if t in types), None) or next((t for t in types if t != "Proteomics"), None)
    if not omics:
        return None
    desc = re.sub(r"\s+", " ", d.get("description") or "").strip()
    if len(desc) > 160:
        desc = desc[:157].rstrip() + "…"
    organisms = [o.get("name") for o in (d.get("organisms") or []) if isinstance(o, dict) and o.get("name")]
    return {"id": d.get("id"), "source": d.get("source"), "omics_type": omics, "all_types": types,
            "name": (d.get("title") or d.get("name") or d.get("id") or "").strip(), "description": desc,
            "organisms": organisms[:2], "publication_date": d.get("publicationDate"),
            "link": f"https://www.omicsdi.org/dataset/{d.get('source')}/{d.get('id')}"}


def omics_links(accession: str, tags: dict, project: dict) -> dict:
    parts = omics_query_parts(tags, project)
    if not parts["required"]:
        return {"accession": accession, "query": None, "groups": [], "total": 0}
    base = " AND ".join(parts["required"])
    query = base + (" AND (" + " OR ".join(parts["keywords"]) + ")" if parts["keywords"] else "")
    by_type = _fetch_types(query)
    if sum(len(v) for v in by_type.values()) < 3 and parts["keywords"]:
        query = base
        wider = _fetch_types(base)
        for t in OMICS_TYPES:
            if not by_type.get(t):
                by_type[t] = wider.get(t, [])
    seen, shaped = set(), {t: [] for t in OMICS_TYPES}
    for t in OMICS_TYPES:
        for d in by_type.get(t, []):
            s = _shape(d, accession)
            if not s or (s["source"], s["id"]) in seen:
                continue
            seen.add((s["source"], s["id"]))
            shaped[s["omics_type"] if s["omics_type"] in shaped else t].append(s)
    # round-robin across types so one noisy source doesn't fill all 8 slots
    picked = {t: [] for t in OMICS_TYPES}
    total = 0
    for i in range(4):
        for t in OMICS_TYPES:
            if total < 8 and len(shaped[t]) > i:
                picked[t].append(shaped[t][i])
                total += 1
    groups = [{"omics_type": t, "datasets": picked[t]} for t in OMICS_TYPES if picked[t]]
    return {"accession": accession, "query": query, "groups": groups, "total": total}


@router.get("/dataset/{accession}/omics")
def dataset_omics(accession: str, refresh: bool = False):
    con = db()
    con.execute("CREATE TABLE IF NOT EXISTS omics_cache (accession TEXT PRIMARY KEY, json TEXT, fetched_at TEXT)")
    p = con.execute("SELECT accession, title, organisms FROM projects WHERE accession=?", (accession,)).fetchone()
    if not p:
        raise HTTPException(404, "unknown accession")
    if not refresh:
        c = con.execute("SELECT json, fetched_at FROM omics_cache WHERE accession=?", (accession,)).fetchone()
        if c and c["fetched_at"] and dt.datetime.fromisoformat(c["fetched_at"]) > dt.datetime.now(dt.UTC) - dt.timedelta(hours=CACHE_HOURS):
            out = json.loads(c["json"])
            out["cached"] = True
            return out
    t = con.execute("SELECT organism, tissue, disease, cell_line FROM tags WHERE accession=?", (accession,)).fetchone()
    try:
        out = omics_links(accession, dict(t) if t else {}, dict(p))
    except Exception as e:
        print("omicsdi failed:", e, file=sys.stderr)
        raise HTTPException(502, f"OmicsDI unavailable: {e}")
    out["fetched_at"] = now_iso()
    out["cached"] = False
    con.execute("INSERT OR REPLACE INTO omics_cache VALUES (?,?,?)", (accession, json.dumps(out), out["fetched_at"]))
    con.commit()
    return out


# ----------------------------------------------------------------------------- per-run sample annotations
# PRIDE has no field for "which sample is this run?". The answer, when it exists at all, is in files the
# submitter attached: an SDRF (the community standard) and/or a spreadsheet of their own. Neither can be
# trusted blindly — PXD046787's community-annotated SDRF says organism part = brain for a study of pulmonary
# endothelial cells, and carries none of the sex / condition / duration the design actually turns on, while its
# Samplelist.xlsx carries all three. So this reports every source side by side with its provenance, flags where
# a source contradicts the dataset's own tags, and leaves the choice to the human. Same contract as the tags:
# show the evidence, never launder a guess into a fact.
SDRF_RE = re.compile(r"\.sdrf\.tsv$", re.I)
SHEET_EXT = (".xlsx", ".xls", ".csv", ".tsv")
RUN_NAME_RE = re.compile(r"\.(raw|wiff|wiff2|d|mzml|mzxml|mgf|baf|yep|tdf)(\.gz|\.zip)?$", re.I)
# Plumbing: true of every row, so it says nothing about which sample a run is.
SDRF_SKIP = {"comment[file uri]", "comment[data file]", "comment[sdrf version]", "comment[sdrf template]",
             "comment[sdrf annotation tool]", "assay name", "technology type", "source name"}
MAX_ANNOT_BYTES = 8 * 1024 * 1024
SAMPLE_CACHE_HOURS = 24
# Which tag a given SDRF/spreadsheet field should agree with, so a contradiction can be surfaced rather than
# silently preferred one way or the other.
ANNOT_VS_TAG = {"characteristics[organism part]": "tissue", "characteristics[organism]": "organism",
                "characteristics[disease]": "disease", "characteristics[cell line]": "cell_line"}


def raw_cache_dir(accession: str) -> pathlib.Path:
    """Where downloaded raw files live between runs. A temp directory by default, one folder per accession, so
    selecting a different pair of runs from the same dataset reuses what is already on disk instead of pulling
    gigabytes again. Override with PRIDEMAP_RAW_DIR to keep them somewhere that survives a reboot."""
    base = os.environ.get("PRIDEMAP_RAW_DIR") or str(pathlib.Path(tempfile.gettempdir()) / "pridemap-raw")
    return pathlib.Path(base).expanduser() / accession


def _cached_raw(accession: str, file_name: str, size_bytes: int | None) -> bool:
    """True when the cache already holds this run, complete. Size is the check: a part file from an interrupted
    download is present but useless, and claiming it is there would skip the resume that would fix it."""
    p = raw_cache_dir(accession) / pathlib.PurePath(file_name).name
    try:
        return p.is_file() and (not size_bytes or p.stat().st_size == size_bytes)
    except OSError:
        return False


def _with_local_state(out: dict, accession: str) -> dict:
    """Stamp each run with whether it is already downloaded. Never cached: the answer changes as files land."""
    cache = raw_cache_dir(accession)
    for r in out.get("rows") or []:
        r["local"] = _cached_raw(accession, r.get("file_name") or "", r.get("size_bytes"))
    out["raw_cache"] = str(cache)
    out["local_runs"] = sum(1 for r in (out.get("rows") or []) if r.get("local"))
    return out


def _run_key(name: str) -> str:
    """A run's identity across sources: basename, run extension dropped, lowercased. The SDRF says
    '2019_12_13_Daria_16', the spreadsheet says '2019_12_13_Daria_16.raw', PRIDE lists the file — all one run."""
    base = pathlib.PurePath((name or "").strip().replace("\\", "/")).name
    return RUN_NAME_RE.sub("", base).strip().lower()


def _loose_key(key: str) -> str:
    """A run key with zero-padding removed from its numbers. Submitters routinely write Daria_1 in the sample
    sheet while the deposited file is Daria_01, which would silently drop the annotations for runs 1-9 — the
    failure looks like 'this dataset has no sample metadata' rather than an error, so match on both forms."""
    return re.sub(r"\d+", lambda m: m.group(0).lstrip("0") or "0", key)


def _slug(value: str, n: int = 14) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "", re.sub(r"\s*y\.?o\.?\s*", "", str(value or ""), flags=re.I))
    return s[:n]


def _fetch_annot_bytes(ftp: str | None) -> bytes | None:
    if not ftp:
        return None
    url = ftp.replace("ftp://", "https://", 1)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pridemap/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read(MAX_ANNOT_BYTES + 1)[:MAX_ANNOT_BYTES]
    except Exception as e:
        print(f"sample annotation fetch failed ({ftp}):", e, file=sys.stderr)
        return None


def _sdrf_annotations(data: bytes) -> dict[str, dict[str, str]]:
    """One row per run, keyed by run. SDRF names the run in comment[data file] / file uri / source name."""
    import csv

    text = data.decode("utf-8", errors="replace")
    out: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
        key = ""
        for col in ("comment[data file]", "comment[file uri]", "source name", "assay name"):
            if _run_key(row.get(col) or ""):
                key = _run_key(row.get(col) or "")
                break
        if not key:
            continue
        fields = {k.strip(): str(v).strip() for k, v in row.items()
                  if k and k.strip() not in SDRF_SKIP and str(v or "").strip()
                  and str(v).strip().lower() not in (NA, "not applicable")}
        # Ontology-style cells ("NT=q exactive;AC=MS:1001911") read as noise in a picker; keep the name.
        for k, v in list(fields.items()):
            m = re.match(r"^NT=([^;]+)", v)
            if m:
                fields[k] = m.group(1)
        out[key] = fields
    return out


def _sheet_annotations(data: bytes, file_name: str) -> dict[str, dict[str, str]]:
    """One row per run from a submitter spreadsheet. The run column is whichever column actually holds run
    names — submitters title it anything ('Raw datafile name', 'File', 'Sample'), so it is found, not assumed."""
    _, pd = _pandas()
    buf = io.BytesIO(data)
    if file_name.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(buf)
    else:
        text = data.decode("utf-8", errors="replace")
        sep = "\t" if text.count("\t") > text.count(",") else ","
        df = pd.read_csv(io.StringIO(text), sep=sep)
    if df.empty:
        return {}
    scores = {c: sum(1 for v in df[c] if _run_key(str(v))) for c in df.columns}
    key_col = max(scores, key=lambda c: scores[c])
    if scores[key_col] == 0:
        return {}
    out: dict[str, dict[str, str]] = {}
    for _, row in df.iterrows():
        key = _run_key(str(row[key_col]))
        if not key:
            continue
        out[key] = {str(c).strip(): str(row[c]).strip() for c in df.columns
                    if c != key_col and str(row[c]).strip() and str(row[c]).strip().lower() != "nan"}
    return out


def _pandas():
    try:
        import numpy as np
        import pandas as pd
        return np, pd
    except ImportError:
        raise HTTPException(503, "reading sample annotations needs pandas: restart the API to pick up dependencies")


def sample_annotations(accession: str, refresh: bool = False) -> dict:
    """Per-run sample annotations for one dataset, assembled from whatever the deposition actually carries."""
    from main import pride_files

    con = db()
    con.execute("CREATE TABLE IF NOT EXISTS sample_cache (accession TEXT PRIMARY KEY, json TEXT, fetched_at TEXT)")
    p = con.execute("SELECT accession FROM projects WHERE accession=?", (accession,)).fetchone()
    if not p:
        raise HTTPException(404, "unknown accession")
    if not refresh:
        c = con.execute("SELECT json, fetched_at FROM sample_cache WHERE accession=?", (accession,)).fetchone()
        if c and c["fetched_at"] and dt.datetime.fromisoformat(c["fetched_at"]) > dt.datetime.now(dt.UTC) - dt.timedelta(hours=SAMPLE_CACHE_HOURS):
            out = json.loads(c["json"])
            out["cached"] = True
            return _with_local_state(out, accession)

    files = pride_files(con, accession)
    runs = [f for f in files if (f.get("category") or "").upper() == "RAW" and is_run(f.get("file_name") or "")]
    if not runs:
        runs = [f for f in files if (f.get("file_name") or "").lower().endswith(RAW_EXT) and is_run(f.get("file_name") or "")]

    sources, per_run = [], {}
    for f in files:
        name = f.get("file_name") or ""
        if SDRF_RE.search(name):
            data = _fetch_annot_bytes(f.get("ftp"))
            parsed = _sdrf_annotations(data) if data else {}
            if parsed:
                sources.append({"kind": "sdrf", "file_name": name, "runs_annotated": len(parsed),
                                "note": "SDRF-Proteomics, the community standard. Often curated after the fact — check it against the paper."})
                per_run["sdrf:" + name] = parsed
        elif name.lower().endswith(SHEET_EXT) and (f.get("category") or "").upper() not in ("RAW", "PEAK", "SEARCH", "RESULT"):
            data = _fetch_annot_bytes(f.get("ftp"))
            try:
                parsed = _sheet_annotations(data, name) if data else {}
            except HTTPException:
                raise
            except Exception as e:
                print(f"sheet parse failed ({name}):", e, file=sys.stderr)
                parsed = {}
            if parsed:
                sources.append({"kind": "spreadsheet", "file_name": name, "runs_annotated": len(parsed),
                                "note": "The submitter's own sample list. Free-form columns, so the field names are theirs, not a standard's."})
                per_run["sheet:" + name] = parsed

    # Merge per run, remembering which file each value came from.
    rows = []
    for f in sorted(runs, key=lambda f: f.get("file_name") or ""):
        name = f.get("file_name") or ""
        key = _run_key(name)
        annotations: dict[str, dict] = {}
        for src, table in per_run.items():
            row_fields = table.get(key)
            if row_fields is None:
                loose = {_loose_key(k): v for k, v in table.items()}
                row_fields = loose.get(_loose_key(key))
            for field, value in (row_fields or {}).items():
                annotations.setdefault(field, {"value": value, "source": src.split(":", 1)[1],
                                               "source_kind": src.split(":", 1)[0]})
        rows.append({"file_name": name, "run": key, "size_bytes": f.get("size_bytes"), "ftp": f.get("ftp"),
                     "annotations": annotations})

    # The fields that differ between runs are the ones worth showing and the only possible experimental factors.
    counts: dict[str, set] = {}
    for r in rows:
        for field, a in r["annotations"].items():
            counts.setdefault(field, set()).add(a["value"])
    # A field with one value per run identifies the run; it does not group runs, so it is not a factor and must
    # not take a slot in the label (replicate number 16 says nothing; Hypoxia / 48 h do). Fewest distinct values
    # first, so the coarsest grouping — the thing most likely to be the experimental factor — leads.
    fields = [{"field": f, "distinct": len(v), "varies": len(v) > 1,
               "identifier": len(v) >= max(2, len(rows)), "values": sorted(v)[:12]}
              for f, v in counts.items()]
    fields.sort(key=lambda f: (not f["varies"], f["identifier"], f["distinct"], f["field"]))
    varying = [f["field"] for f in fields if f["varies"] and not f["identifier"]]
    for r in rows:
        r["label"] = "_".join(_slug(r["annotations"][f]["value"]) for f in varying[:3]
                              if f in r["annotations"]) or _run_key(r["file_name"])

    t = con.execute("SELECT organism, tissue, disease, cell_line FROM tags WHERE accession=?", (accession,)).fetchone()
    tags = dict(t) if t else {}
    conflicts = []
    for field, tag in ANNOT_VS_TAG.items():
        vals = counts.get(field) or set()
        tag_val = (tags.get(tag) or "").strip()
        if len(vals) == 1 and tag_val:
            got = next(iter(vals))
            if got.strip().lower() not in tag_val.lower() and tag_val.lower() not in got.strip().lower():
                conflicts.append({"field": field, "annotation_says": got, "tag": tag, "tag_says": tag_val,
                                  "note": "These disagree. Neither is automatically right — the tag comes from the "
                                          "protocol text, the annotation from a file someone attached."})

    out = {"accession": accession, "runs": len(rows), "sources": sources, "fields": fields,
           "varying_fields": varying, "rows": rows, "conflicts": conflicts,
           "note": None if sources else "This deposition carries no SDRF or sample spreadsheet PRIDE Map could read, "
                                        "so the runs can only be told apart by file name.",
           "fetched_at": now_iso(), "cached": False}
    con.execute("INSERT OR REPLACE INTO sample_cache VALUES (?,?,?)", (accession, json.dumps(out), out["fetched_at"]))
    con.commit()
    return _with_local_state(out, accession)


@router.get("/dataset/{accession}/samples")
def dataset_samples(accession: str, refresh: bool = False):
    """Which sample each raw run is, from the deposition's own SDRF and/or sample spreadsheet, with provenance.

    Drives the 'pick two runs' step: varying_fields are the candidate factors, conflicts are where a source
    contradicts the dataset's tags, and local tells you whether the file is already in the raw cache.
    """
    return sample_annotations(accession, refresh=refresh)


# ----------------------------------------------------------------------------- quantms reanalysis kit
NA = "not available"
CLEAVAGE = {"trypsin": ["NT=Trypsin;AC=MS:1001251"], "trypsin_lysc": ["NT=Trypsin;AC=MS:1001251", "NT=Lys-C;AC=MS:1001309"],
            "lysc": ["NT=Lys-C;AC=MS:1001309"], "chymotrypsin": ["NT=Chymotrypsin;AC=MS:1001306"], "gluc": ["NT=Glu-C;AC=MS:1001917"],
            "aspn": ["NT=Asp-N;AC=MS:1001304"], "none": ["NT=unspecific cleavage;AC=MS:1001956"]}
MODS = {"Carbamidomethyl (C)": "NT=Carbamidomethyl;AC=UNIMOD:4;TA=C;MT=Fixed",
        "Oxidation (M)": "NT=Oxidation;AC=UNIMOD:35;TA=M;MT=Variable",
        "Acetyl (Protein N-term)": "NT=Acetyl;AC=UNIMOD:1;PP=Protein N-term;MT=Variable",
        "Phospho (STY)": "NT=Phospho;AC=UNIMOD:21;TA=S,T,Y;MT=Variable",
        "GlyGly (K)": "NT=GG;AC=UNIMOD:121;TA=K;MT=Variable",
        "Acetyl (K)": "NT=Acetyl;AC=UNIMOD:1;TA=K;MT=Variable",
        "Deamidation (N)": "NT=Deamidated;AC=UNIMOD:7;TA=N;MT=Variable"}
LABEL_MODS = {"TMT": ["NT=TMT6plex;AC=UNIMOD:737;TA=K;MT=Fixed", "NT=TMT6plex;AC=UNIMOD:737;PP=Any N-term;MT=Fixed"],
              "iTRAQ": ["NT=iTRAQ4plex;AC=UNIMOD:214;TA=K;MT=Fixed", "NT=iTRAQ4plex;AC=UNIMOD:214;PP=Any N-term;MT=Fixed"]}
LABEL = {"label_free": ("label free sample", "label free sample"), "TMT": ("TMT126", "TMT10plex"), "iTRAQ": ("iTRAQ4plex-114", "iTRAQ4plex"),
         "SILAC": ("SILAC light", "SILAC"), "dimethyl": ("dimethyl light", "dimethyl")}
TOLERANCE = {"orbitrap": ("10 ppm", "0.02 Da"), "timstof": ("20 ppm", "0.05 Da"), "qtof": ("20 ppm", "0.05 Da"),
             "ion_trap": ("10 ppm", "0.5 Da"), "fticr": ("5 ppm", "0.02 Da")}
RAW_EXT = (".raw", ".d", ".d.zip", ".d.tar", ".d.tar.gz", ".wiff", ".wiff2", ".mzml", ".mzml.gz", ".baf", ".yep")


def _tsv_cell(v) -> str:
    return re.sub(r"[\t\r\n]+", " ", str(v if v not in (None, "") else NA))


def _run_labels(accession: str) -> dict[str, str]:
    """file name -> a label built from the annotations that vary, e.g. M69_Hypoxia_48h. Used for MaxQuant's
    experiment names, so proteinGroups.txt arrives with columns a researcher can read the design out of.
    Annotations are best-effort: no sample metadata means plain file-name stems, which is what it did before."""
    try:
        ann = sample_annotations(accession)
    except Exception as e:
        print(f"run labels unavailable ({accession}):", e, file=sys.stderr)
        return {}
    return {r["file_name"]: r["label"] for r in ann.get("rows") or [] if r.get("label")}


def parse_runs(runs: str) -> list[str]:
    """A comma-separated run selection from a query string. Accepts file names or bare run keys, either way."""
    return [s.strip() for s in (runs or "").split(",") if s.strip()]


def _kit_context(accession: str, runs: list[str] | None = None) -> dict:
    """Everything a reanalysis kit needs about one dataset: project row, tags, derived recipe, raw files and the
    SDRF-shaped values derived from them. Single owner — quantms_kit and search_kit both compose this.

    `runs` narrows it to a chosen subset, so comparing two samples out of twenty-four downloads and searches
    two files rather than all of them. Everything downstream — the scripts, the config, the plan, the stage
    list — is derived from this list, so none of them can disagree about which runs the kit is for.
    """
    from main import jl, pride_files, recipe
    con = db()
    p = con.execute("SELECT * FROM projects WHERE accession=?", (accession,)).fetchone()
    if not p:
        raise HTTPException(404, "unknown accession")
    p = dict(p)
    t = con.execute("SELECT * FROM tags WHERE accession=?", (accession,)).fetchone()
    t = dict(t) if t else {}
    ev = {e["tag"]: dict(e) for e in con.execute("SELECT tag, confidence, evidence FROM tag_evidence WHERE accession=?", (accession,))}
    r = recipe(t, jl(p.get("ptms")), ev) if t else None
    files = pride_files(con, accession)
    raw = [f for f in files if f.get("category") == "RAW"]
    if not raw:
        raw = [f for f in files if (f.get("file_name") or "").lower().endswith(RAW_EXT)]
    raw.sort(key=lambda f: f.get("file_name") or "")
    selected = list(runs or [])
    if selected:
        want = {_run_key(s) for s in selected}
        # Sidecars travel with the run they belong to: selecting X.wiff must still fetch X.wiff.scan.
        kept = [f for f in raw if _run_key(f.get("file_name") or "") in want]
        missing = want - {_run_key(f.get("file_name") or "") for f in kept}
        if missing:
            raise HTTPException(400, f"not raw files of {accession}: {', '.join(sorted(missing))}")
        raw = kept

    organism = t.get("organism") or (jl(p.get("organisms")) or [NA])[0]
    instruments = jl(p.get("instruments"))
    enzyme = t.get("enzyme")
    mods = []
    if r:
        for m in r["fixed_modifications"]["value"] + r["variable_modifications"]["value"]:
            if m in MODS:
                mods.append(MODS[m])
        mods += LABEL_MODS.get(t.get("labelling") or "", [])
    label, labelling_type = LABEL.get(t.get("labelling") or "", (NA, NA))
    prec_tol, frag_tol = TOLERANCE.get(t.get("instrument_family") or "", (NA, NA))
    # raw = everything to download (sidecars included); runs = what the search engine is actually pointed at.
    return {"accession": accession, "p": p, "t": t, "r": r, "raw": raw, "selected": selected,
            "run_labels": _run_labels(accession) if selected else {},
            "runs": [f for f in raw if is_run(f.get("file_name") or "")], "organism": organism,
            "instruments": instruments,
            "instrument": f"NT={instruments[0]}" if instruments else (f"NT={t['instrument_family']}" if t.get("instrument_family") else NA),
            "enzyme": enzyme, "cleavage": CLEAVAGE.get(enzyme or "", [NA]), "mods": mods or [NA],
            "label": label, "labelling_type": labelling_type, "prec_tol": prec_tol, "frag_tol": frag_tol,
            "acq": t.get("acquisition") or "DDA"}


def quantms_kit(accession: str, runs: list[str] | None = None) -> bytes:
    from main import TAG_COLS
    ctx = _kit_context(accession, runs)
    p, t, r, raw = ctx["p"], ctx["t"], ctx["r"], ctx["raw"]
    organism, instrument = ctx["organism"], ctx["instrument"]
    cleavage, mods = ctx["cleavage"], ctx["mods"]
    label, labelling_type = ctx["label"], ctx["labelling_type"]
    prec_tol, frag_tol = ctx["prec_tol"], ctx["frag_tol"]

    cols = (["source name", "characteristics[organism]", "characteristics[organism part]", "characteristics[disease]", "characteristics[cell type]",
             "assay name", "comment[fraction identifier]", "comment[label]", "comment[data file]", "comment[file uri]", "comment[instrument]"]
            + ["comment[cleavage agent details]"] * len(cleavage) + ["comment[modification parameters]"] * len(mods)
            + ["comment[technical replicate]", "comment[fragment mass tolerance]", "comment[precursor mass tolerance]"])
    lines = ["\t".join(cols)]
    for i, f in enumerate(raw, 1):
        row = [f"{accession}-Sample-{i}", organism, t.get("tissue"), t.get("disease"), t.get("cell_line"),
               f"run {i}", 1, label, f.get("file_name"), f.get("ftp"), instrument] + cleavage + mods + [1, frag_tol, prec_tol]
        lines.append("\t".join(_tsv_cell(v) for v in row))
    sdrf = "\n".join(lines) + "\n"

    acq = t.get("acquisition") or "DDA"
    acquisition_method = "dia" if acq == "DIA" else "dda"
    fasta_url = (r or {}).get("fasta", {}).get("url") if r else None
    search_engines = "comet,msgf" if acquisition_method == "dda" else "diann"
    params = [
        f"# quantms parameters for {accession}, derived from PRIDE Map tags (check anything marked '{NA}')",
        "input: sdrf.tsv",
        f"database: {fasta_url or NA}",
        f"acquisition_method: {acquisition_method}",
        f"labelling_type: {labelling_type}",
        f"search_engines: {search_engines}",
        "protein_level_fdr_cutoff: 0.01",
        "psm_level_fdr_cutoff: 0.01",
        "add_decoys: true",
        "outdir: results",
    ]
    if t.get("enrichment") == "phospho":
        params.append("enable_mod_localization: true")
    if acq in ("PRM_SRM", "top_down", "MALDI"):
        params.append(f"# acquisition tag is {acq}: quantms only runs DDA/DIA bottom-up data; this kit is a starting point only")
    tagged_from = ", ".join(f"{k}={t.get(k)}" for k in TAG_COLS if t.get(k)) or "untagged"
    params.append(f"# tags: {tagged_from}")
    params_yml = "\n".join(params) + "\n"

    run_sh = "\n".join([
        "#!/usr/bin/env bash",
        f"# quantms reanalysis of {accession}. Runs on YOUR machine or cloud, not on PRIDE Map.",
        "# Install first: Nextflow (https://www.nextflow.io, needs Java 17+) and Docker (or use -profile singularity / conda).",
        "# Raw files are pulled from the FTP links in sdrf.tsv; expect tens of GB. Check params.yml before running.",
        "set -euo pipefail",
        "nextflow run bigbio/quantms -r 1.3.0 -profile docker -params-file params.yml -resume",
        "",
    ])
    readme = "\n".join([
        f"# quantms kit for {accession}",
        "",
        f"1. This is a ready-to-run reanalysis kit for PRIDE dataset {accession}: {p.get('title')}",
        "2. sdrf.tsv lists one row per raw file with the sample, enzyme, modification and instrument annotations derived from PRIDE Map's tags; params.yml holds the quantms settings; run.sh starts the Nextflow pipeline.",
        f"3. Nothing runs on PRIDE Map: `bash run.sh` executes on your machine or cloud and downloads the raw files ({len(raw)} files) from PRIDE's FTP.",
        f"4. Cells marked '{NA}' could not be derived from the metadata; fill them in from the paper before running (quantms validates the SDRF).",
        "5. Multiplexed designs (TMT/iTRAQ) need one row per channel with the channel in comment[label]; this kit writes one row per file with the first channel as a placeholder.",
        "6. Output: results/ with protein and peptide quantification tables (MSstats-ready), QC reports and an mzTab; docs at https://nf-co.re/quantms.",
        "",
    ])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("sdrf.tsv", sdrf)
        z.writestr("params.yml", params_yml)
        z.writestr("run.sh", run_sh)
        z.writestr("README.md", readme)
    return buf.getvalue()


@router.get("/dataset/{accession}/quantms.zip")
def dataset_quantms(accession: str, runs: str = ""):
    data = quantms_kit(accession, parse_runs(runs))
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{accession}_quantms.zip"'})


# ----------------------------------------------------------------------------- which search engine can actually run
# Longest extension first: ".d.tar.gz" must win over ".gz", ".wiff.scan" over ".wiff".
VENDOR_EXT = [(".wiff.scan", "sciex"), (".wiff2", "sciex"), (".wiff", "sciex"), (".raw", "thermo"),
              (".d.tar.gz", "bruker"), (".d.tar", "bruker"), (".d.zip", "bruker"), (".d", "bruker"),
              (".baf", "bruker"), (".yep", "bruker"), (".tdf", "bruker"),
              (".mzml.gz", "open"), (".mzml", "open"), (".mzxml", "open")]
VENDOR_LABEL = {"thermo": "Thermo .raw", "bruker": "Bruker .d", "sciex": "SCIEX .wiff", "open": "open mzML/mzXML"}
# What each engine reads without a conversion step first.
MQ_NATIVE = {"thermo", "bruker", "open"}
DIANN_NATIVE = {"thermo", "bruker", "open"}
MSCONVERT = ("msconvert", "https://proteowizard.sourceforge.io/download.html")
# DIA markers submitters put in run names. Each token must end the segment, so "Diaphragm" and "median" don't match.
DIA_NAME_RE = re.compile(r"(?:^|[_\-. ])(swath|dia|diapasef|hrm|scanningswath)(?:[_\-. 0-9]|$)", re.I)
DDA_NAME_RE = re.compile(r"(?:^|[_\-. ])(dda|ida|shotgun)(?:[_\-. 0-9]|$)", re.I)
# Index/sidecar files that sit next to a run. They must be downloaded (the readers need them) but are not runs
# themselves: listing a .wiff.scan as an input would make the search engine try to open an index as spectra.
SIDECAR_EXT = (".wiff.scan", ".scan", ".idx", ".index", ".mzml.index")


def is_run(name: str) -> bool:
    return not (name or "").lower().endswith(SIDECAR_EXT)


def raw_vendor(names: list[str]) -> str | None:
    """Vendor of the raw files, by extension. Majority wins; None when nothing recognisable is there."""
    counts: dict[str, int] = {}
    for n in names:
        low = (n or "").lower()
        for ext, vendor in VENDOR_EXT:
            if low.endswith(ext):
                counts[vendor] = counts.get(vendor, 0) + 1
                break
    return max(counts, key=lambda v: counts[v]) if counts else None


def _suffixed(tr: dict) -> dict:
    """When one kit carries two tracks, each needs its own config name and output directory so they can't collide."""
    k = tr["kind"].lower()
    tr["outdir"] = f"out_{k}"
    if tr["engine"] == "maxquant":
        tr["config"] = f"mqpar_{k}.xml"
    else:
        tr["config"] = f"diann_{k}.cfg"
        tr["output_table"] = f"out_{k}/report.pg_matrix.tsv"
    return tr


def search_plan(t: dict, raw_names: list[str]) -> dict:
    """The engine decision plus the full stage list. Every caller gets both, so none has to assemble them."""
    plan = _build_plan(t, raw_names)
    plan["stages"] = pipeline_stages(plan)
    return plan


def _build_plan(t: dict, raw_names: list[str]) -> dict:
    """Which search engine a researcher can actually run on THIS dataset's raw files, and why.

    Single owner of the engine decision: the kit, the Analyse banner and the set-up sheet all compose this
    rather than each guessing. Honest by design — it refuses to offer MaxQuant for data MaxQuant cannot read.
    """
    all_names = raw_names
    raw_names = [n for n in raw_names if is_run(n)]  # sidecars are not runs; callers need not know the difference
    sidecars = len(all_names) - len(raw_names)
    vendor = raw_vendor(raw_names)
    acq = (t or {}).get("acquisition") or "DDA"
    vlabel = VENDOR_LABEL.get(vendor or "", "unrecognised raw format")
    # Run names are evidence the tag does not carry. A deposition is often BOTH: fractionated DDA runs that build a
    # spectral library plus SWATH/DIA runs that do the quantification. One engine cannot be right for both.
    dia_runs = [n for n in raw_names if DIA_NAME_RE.search(pathlib.PurePath(n or "").name)]
    dda_runs = [n for n in raw_names if DDA_NAME_RE.search(pathlib.PurePath(n or "").name) and n not in set(dia_runs)]
    unknown = [n for n in raw_names if n not in set(dia_runs) | set(dda_runs)]
    mixed = bool(dia_runs and dda_runs)
    conflict = None
    if not mixed and acq == "DDA" and raw_names and len(dia_runs) >= max(1, len(raw_names) // 2):
        conflict = (f"The tag says DDA, but {len(dia_runs)} of {len(raw_names)} run names are marked DIA/SWATH. "
                    "Going with the file names: a DDA search on DIA spectra gives meaningless results.")
        acq = "DIA"
    plan = {"vendor": vendor, "vendor_label": vlabel, "acquisition": "mixed" if mixed else acq,
            "raw_count": len(raw_names), "convert": None, "maxquant_offered": False, "maxquant_reason": None,
            "engine": None, "engine_label": None, "why": None, "output_table": None, "caveat": None,
            "tag_acquisition": (t or {}).get("acquisition"), "conflict": conflict, "mixed": mixed, "sidecars": sidecars,
            "runs_dda": len(dda_runs), "runs_dia": len(dia_runs), "runs_unclassified": len(unknown), "tracks": []}

    if not raw_names:
        plan["why"] = "No raw files listed for this dataset, so there is nothing to search."
        return plan

    if acq in ("PRM_SRM", "top_down", "MALDI"):
        plan["engine"] = "quantms"
        plan["engine_label"] = {"PRM_SRM": "Skyline", "top_down": "ProSight or TopPIC", "MALDI": "vendor software"}[acq]
        plan["why"] = (f"The acquisition tag is {acq}: neither MaxQuant nor DIA-NN is the right tool. "
                       "Bottom-up DDA/DIA kits do not apply here.")
        plan["maxquant_reason"] = f"MaxQuant is a bottom-up DDA search engine; this dataset is tagged {acq}."
        return plan

    needs_convert = vendor not in MQ_NATIVE  # MaxQuant and DIA-NN read the same vendor formats natively
    if needs_convert:
        plan["convert"] = {"from": vlabel, "to": "mzML", "tool": MSCONVERT[0], "url": MSCONVERT[1],
                           "why": f"{vlabel} is not read directly by the engines below; convert to mzML first."}

    def track(kind: str, runs: list[str]) -> dict:
        if kind == "DDA":
            return {"kind": "DDA", "engine": "maxquant", "engine_label": "MaxQuant", "runs": sorted(runs),
                    "run_count": len(runs), "output_table": "combined/txt/proteinGroups.txt", "config": "mqpar.xml",
                    "why": f"{len(runs)} DDA run(s) — MaxQuant searches them against a FASTA and writes proteinGroups.txt."}
        return {"kind": "DIA", "engine": "diann", "engine_label": "DIA-NN", "runs": sorted(runs),
                "run_count": len(runs), "output_table": "report.pg_matrix.tsv", "config": "diann.cfg",
                "why": f"{len(runs)} DIA/SWATH run(s) — DIA-NN runs library-free from a FASTA and writes the protein matrix."}

    if mixed:
        # Two genuinely different experiments in one deposition. Each gets its own config and its own output table,
        # and both outputs can be analysed here. They are not chained: neither needs the other to produce a result.
        # Names are settled here, not in the kit builder, so the UI advertises exactly what the zip contains.
        plan["tracks"] = [_suffixed(track("DDA", dda_runs + unknown)), _suffixed(track("DIA", dia_runs))]
        plan["engine"] = "maxquant"
        plan["engine_label"] = "MaxQuant"
        plan["maxquant_offered"] = True
        plan["output_table"] = "combined/txt/proteinGroups.txt"
        plan["why"] = (f"This deposition holds both: {len(dda_runs) + len(unknown)} DDA run(s) and {len(dia_runs)} "
                       f"DIA/SWATH run(s) on {vlabel}. The kit configures both — MaxQuant for the DDA set, "
                       "DIA-NN for the SWATH set — and either output table can be analysed here.")
        if needs_convert:
            plan["caveat"] = f"{vlabel} must go through {MSCONVERT[0]} to mzML before either engine will open it."
        return plan

    if acq == "DIA":
        plan["tracks"] = [track("DIA", raw_names)]
        plan["engine"] = "diann"
        plan["engine_label"] = "DIA-NN"
        plan["output_table"] = "report.pg_matrix.tsv"
        plan["why"] = (f"{len(raw_names)} × {vlabel}, acquisition DIA — DIA-NN runs library-free from a FASTA and "
                       "writes the protein matrix this Analyse step reads.")
        plan["maxquant_reason"] = (
            "MaxQuant cannot read SCIEX .wiff at all, and its DIA mode (MaxDIA) is not the standard route for "
            "SWATH data — DIA-NN is." if vendor == "sciex" else
            "MaxQuant's DIA mode (MaxDIA) needs a different workflow and is rarely used for this data; DIA-NN is the standard route.")
        if needs_convert:
            plan["caveat"] = f"{vlabel} must go through {MSCONVERT[0]} to mzML before DIA-NN will open it."
        return plan

    plan["tracks"] = [track("DDA", raw_names)]
    plan["engine"] = "maxquant"
    plan["engine_label"] = "MaxQuant"
    plan["maxquant_offered"] = True
    plan["output_table"] = "combined/txt/proteinGroups.txt"
    plan["why"] = f"{len(raw_names)} × {vlabel}, acquisition DDA — MaxQuant searches them against a FASTA and writes proteinGroups.txt."
    if needs_convert:
        plan["caveat"] = f"{vlabel} must go through {MSCONVERT[0]} to mzML before MaxQuant will open it."
    return plan


def dataset_search_plan(accession: str, runs: list[str] | None = None) -> dict:
    ctx = _kit_context(accession, runs)
    plan = search_plan(ctx["t"], [f.get("file_name") or "" for f in ctx["raw"]])
    plan["accession"] = accession
    plan["kit_url"] = f"/dataset/{accession}/searchkit.zip"
    plan["tagged"] = bool(ctx["t"])
    return plan


@router.get("/dataset/{accession}/searchplan")
def dataset_searchplan(accession: str, runs: str = ""):
    return dataset_search_plan(accession, parse_runs(runs))


# ----------------------------------------------------------------------------- the pipeline, start to finish
# MaxQuant's own job names, in the order it runs them. It writes one combined/proc/<job>.finished.txt per step and
# appends to combined/proc/#runningTimes.txt, which is what run_all.sh tails for real progress. The list here is the
# documented sequence so the UI can show what is coming before anyone commits hours of compute to it.
MAXQUANT_STAGES = [
    ("Configuring", "reads mqpar.xml, checks the raw files are readable"),
    ("Testing fasta files", "parses the FASTA and the identifier rules"),
    ("Feature detection", "finds 3D peaks (m/z × retention time × intensity) in every run"),
    ("Deisotoping", "groups peaks into isotope patterns and assigns charge"),
    ("Calculating peak properties", "centroids and scores each feature"),
    ("Preparing searches", "builds the peptide index from the FASTA"),
    ("MS/MS first search", "quick Andromeda search used only to calibrate"),
    ("Mass recalibration", "corrects systematic m/z and retention-time drift"),
    ("MS/MS search", "the real Andromeda search of every spectrum"),
    ("Applying FDR", "target-decoy filtering to 1% PSM and peptide FDR"),
    ("Matching between runs", "transfers identifications across runs by accurate mass and RT"),
    ("Assembling proteins", "groups peptides into protein groups, razor/unique assignment"),
    ("Protein FDR", "1% protein-level target-decoy filtering"),
    ("Label-free quantification", "MaxLFQ normalisation across the runs"),
    ("Writing tables", "writes combined/txt/, including proteinGroups.txt"),
]
DIANN_STAGES = [
    ("Loading FASTA", "in-silico digest of the protein database"),
    ("Spectral library prediction", "deep-learning prediction of spectra and retention times"),
    ("First pass", "identifies precursors across the runs"),
    ("RT alignment", "aligns retention time between runs"),
    ("Quantification", "MaxLFQ protein quantities"),
    ("Writing report", "writes report.tsv and report.pg_matrix.tsv"),
]


def pipeline_stages(plan: dict) -> list[dict]:
    """Every stage from 'PRIDE holds only raw files' to 'a protein table this app can analyse'.

    Single owner of the stage list: run_all.sh echoes it, progress.jsonl records it, and the UI draws it.
    """
    stages = [
        {"id": "preflight", "label": "Check tools and disk space",
         "detail": "verifies every engine is reachable and there is room, before anything is downloaded; "
                   "msconvert is pulled automatically if Docker is running", "where": "automatic"},
        {"id": "download", "label": "Download raw files from PRIDE",
         "detail": f"{plan['raw_count']} runs"
                   + (f" plus {plan['sidecars']} index sidecars" if plan.get("sidecars") else "")
                   + " over FTP; expect tens of GB", "where": "your machine"},
        {"id": "fasta", "label": "Fetch the protein database",
         "detail": "reviewed UniProt reference proteome for the dataset's organism", "where": "your machine"},
    ]
    if plan.get("convert"):
        stages.append({"id": "convert", "label": f"Convert {plan['vendor_label']} to mzML",
                       "detail": f"{plan['convert']['tool']}; the search engines cannot read the vendor format directly",
                       "where": "your machine"})
    for tr in plan.get("tracks") or []:
        subs = MAXQUANT_STAGES if tr["engine"] == "maxquant" else DIANN_STAGES
        stages.append({
            "id": f"search_{tr['kind'].lower()}", "label": f"{tr['engine_label']} — {tr['run_count']} {tr['kind']} runs",
            "detail": f"config {tr['config']} → {tr['output_table']}", "where": "your machine",
            "engine": tr["engine"], "engine_label": tr["engine_label"], "output_table": tr["output_table"],
            "substages": [{"name": n, "detail": d} for n, d in subs]})
    stages.append({"id": "upload", "label": "Hand the tables back to PRIDE Map",
                   "detail": "uploaded automatically when the search finishes; prints a link that opens Analyse "
                             "with the table already loaded (PRIDEMAP_NO_UPLOAD=1 to keep it local)",
                   "where": "automatic"})
    stages.append({
        "id": "analyse", "label": "Analyse the protein table",
        "detail": "pick group A and group B — log2 fold change, Welch t-test, Benjamini-Hochberg FDR and a volcano plot",
        "where": "you decide"})
    return stages


# ----------------------------------------------------------------------------- MaxQuant / DIA-NN kits
# Bioconda's MaxQuant container. Published amd64 only, and MaxQuant is .NET: under QEMU emulation Mono's JIT
# aborts in its own trampolines before MaxQuant parses an argument. So the image is detected AND smoke-tested,
# rather than assumed to work because it is present — and on a non-amd64 host we build a native image instead
# of relying on emulation at all (see _mq_docker_lib).
MAXQUANT_IMAGE = "quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1"
MAXQUANT_IMAGE_VERSION = "2.0.3.0"
# Our own native-architecture rebuild of the same MaxQuant release, built on the host that needs it.
MAXQUANT_NATIVE_IMAGE = f"pridemap/maxquant:{MAXQUANT_IMAGE_VERSION}-native"
MAXQUANT_MONO_BASE = "mono:6.12"  # multi-arch: amd64, arm64/v8, armv7, ppc64le
DOCKER_MOUNT = "/data"

MQ_INSTRUMENT = {"orbitrap": 0, "timstof": 1, "qtof": 2, "fticr": 0, "ion_trap": 0}
MQ_VERSION = MAXQUANT_IMAGE_VERSION  # match the container we provision; a newer native install will offer to upgrade the file


def _xml_list(tag: str, items: list[str], item_tag: str = "string") -> str:
    inner = "".join(f"\n         <{item_tag}>{_xml(v)}</{item_tag}>" for v in items)
    return f"      <{tag}>{inner}\n      </{tag}>"


def _xml(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fasta(ctx: dict) -> tuple[str, str | None]:
    """Local path the configs point at, and the URL to fetch it from. Engines open files, not URLs."""
    f = ((ctx["r"] or {}).get("fasta") or {}) if ctx["r"] else {}
    prot = f.get("uniprot_proteome")
    return (f"fasta/{prot}.fasta" if prot else "fasta/REPLACE_WITH_LOCAL.fasta"), f.get("url")


def _fasta_sh(ctx: dict) -> str:
    path, url = _fasta(ctx)
    if not url:
        return ("#!/usr/bin/env bash\n# No UniProt reference proteome is known for this organism.\n"
                f"# Download a FASTA by hand and save it as {path}\n")
    return "\n".join(["#!/usr/bin/env bash", "# Reviewed (Swiss-Prot) reference proteome for this dataset's organism.",
                      "set -euo pipefail", "mkdir -p fasta",
                      f'curl -L "{url}" -o {path}.gz', f"gunzip -f {path}.gz",
                      "# Add contaminants before searching (MaxQuant ships its own; DIA-NN does not).", ""])


def _run_path(name: str, plan: dict) -> str:
    """Where a run sits once the kit's scripts have run. msconvert REPLACES the extension, so X.wiff -> mzml/X.mzML."""
    if plan.get("convert"):
        return "mzml/" + re.sub(r"\.[^.]+$", "", pathlib.PurePath(name).name) + ".mzML"
    return "raw/" + name


# MaxQuant spawns MaxQuantTask workers that resolve paths from their own working directory, not the one
# MaxQuantCmd was started in. A relative filePath therefore fails deep into the run with "Given path for proc
# folder does not exist. Path=raw/combined/proc" — after the download, which is the expensive part. The config
# ships with this placeholder and run_all.sh substitutes the kit's real directory before the search, so the
# paths are absolute wherever the kit was unpacked, without the generator having to know that directory.
KIT_DIR_TOKEN = "__KIT_DIR__"


def _abs_in_kit(path: str) -> str:
    return f"{KIT_DIR_TOKEN}/{path.lstrip('/')}"


def _docker_config(name: str) -> str:
    """The container-path copy of a config, written beside the shipped one."""
    return re.sub(r"(\.[^.]+)$", r".docker\1", name)


def _local_config(name: str) -> str:
    """The run-time copy of a config, with the kit directory substituted in. Kept beside the original so the
    shipped config stays diffable and the resolved one is obviously generated."""
    return re.sub(r"(\.[^.]+)$", r".local\1", name)


def mqpar_xml(ctx: dict, plan: dict, run_names: list[str]) -> str:
    """A MaxQuant mqpar.xml seeded from the tags. A starting point: open it in MaxQuant's GUI and check it."""
    r, t = ctx["r"], ctx["t"]
    fasta_path, _ = _fasta(ctx)
    enzymes = (r or {}).get("enzyme", {}).get("value") or ["Trypsin/P"]
    fixed = [m for m in ((r or {}).get("fixed_modifications", {}).get("value") or []) if "(" in m]
    variable = (r or {}).get("variable_modifications", {}).get("value") or ["Oxidation (M)"]
    lfq = 1 if (t.get("labelling") or "label_free") == "label_free" else 0
    paths = [_abs_in_kit(_run_path(n, plan)) for n in run_names]
    # Experiment names become the column names in proteinGroups.txt. When the deposition says what each run is,
    # use that ("M69_Hypoxia_48h") instead of a file name that hides the design.
    labels = ctx.get("run_labels") or {}
    exps = [labels.get(pathlib.PurePath(n).name) or re.sub(r"\.[^.]+$", "", pathlib.PurePath(n).name)
            for n in run_names]
    head = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<MaxQuantParams xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
        f"   <!-- Generated by PRIDE Map for {ctx['accession']} from its tags. Check every value before running. -->",
        "   <fastaFiles>",
        "      <FastaFileInfo>",
        f"         <fastaFilePath>{_xml(_abs_in_kit(fasta_path))}</fastaFilePath>",
        "         <identifierParseRule>&gt;.*\\|(.*)\\|</identifierParseRule>",
        "         <descriptionParseRule>&gt;(.*)</descriptionParseRule>",
        "         <taxonomyParseRule></taxonomyParseRule>",
        "         <variationParseRule></variationParseRule>",
        "         <modificationParseRule></modificationParseRule>",
        f"         <taxonomyId>{_xml(t.get('taxon_id') or '')}</taxonomyId>",
        "      </FastaFileInfo>",
        "   </fastaFiles>",
        "   <fastaFilesProteogenomics></fastaFilesProteogenomics>",
        "   <fastaFilesFirstSearch></fastaFilesFirstSearch>",
        "   <fixedSearchFolder></fixedSearchFolder>",
        # Left empty, MaxQuant derives this from the raw directory and writes raw/combined, while run_all.sh and
        # its progress watcher both look for combined/ at the kit root. Pin it so the two agree. This is the
        # folder MaxQuant creates "combined" INSIDE, so it is the kit root — naming combined here nests it.
        f"   <fixedCombinedFolder>{_xml(KIT_DIR_TOKEN)}</fixedCombinedFolder>",
        "   <includeContaminants>True</includeContaminants>",
        f"   <maxQuantVersion>{MQ_VERSION}</maxQuantVersion>",
        "   <numThreads>8</numThreads>",
        _xml_list("filePaths", paths),
        _xml_list("experiments", exps),
        _xml_list("fractions", ["32767"] * len(paths), "short"),
        _xml_list("ptms", ["False"] * len(paths), "boolean"),
        _xml_list("paramGroupIndices", ["0"] * len(paths), "int"),
        "   <parameterGroups>",
        "      <parameterGroup>",
        f"         <msInstrument>{MQ_INSTRUMENT.get(t.get('instrument_family') or '', 0)}</msInstrument>",
        "         <maxCharge>7</maxCharge>",
        "         <lcmsRunType>Standard</lcmsRunType>",
        f"         <lfqMode>{lfq}</lfqMode>",
        f"         <matchBetweenRuns>{'True' if lfq else 'False'}</matchBetweenRuns>",
        _xml_list("enzymes", enzymes).replace("      <", "         <").replace("\n         <string>", "\n            <string>"),
        "         <maxMissedCleavages>2</maxMissedCleavages>",
        _xml_list("variableModifications", variable).replace("      <", "         <").replace("\n         <string>", "\n            <string>"),
        _xml_list("fixedModifications", fixed or ["Carbamidomethyl (C)"]).replace("      <", "         <").replace("\n         <string>", "\n            <string>"),
        "      </parameterGroup>",
        "   </parameterGroups>",
        "   <peptideFdr>0.01</peptideFdr>",
        "   <proteinFdr>0.01</proteinFdr>",
        "   <minPepLen>7</minPepLen>",
        "   <writeMzTab>True</writeMzTab>",
        "</MaxQuantParams>",
        "",
    ]
    return "\n".join(head)


def diann_cfg(ctx: dict, plan: dict, track: dict, outdir: str) -> str:
    """A DIA-NN command-line config seeded from the tags (library-free, FASTA-predicted spectral library)."""
    r, t = ctx["r"], ctx["t"]
    fasta_path, _ = _fasta(ctx)
    # --out names the main report; --matrices additionally writes report.pg_matrix.tsv next to it.
    out = (f"{outdir}/report.tsv" if outdir not in ("", ".") else "report.tsv")
    lines = [f"# DIA-NN config for {ctx['accession']}, generated by PRIDE Map from its tags.",
             f"# {track['run_count']} {track['kind']} run(s) on {plan['vendor_label']}. Check every line before running.",
             "--threads 8", "--verbose 1",
             f"--out {out}", "--matrices",
             f"--fasta {fasta_path}",
             "--fasta-search", "--predictor", "--gen-spec-lib", "--reanalyse", "--smart-profiling",
             "--qvalue 0.01", "--missed-cleavages 2", "--min-pep-len 7", "--max-pep-len 30", "--unimod4"]
    cut = {"trypsin": "K*,R*", "trypsin_lysc": "K*,R*", "lysc": "K*", "chymotrypsin": "F*,W*,Y*,L*",
           "gluc": "E*", "aspn": "*D"}.get(t.get("enzyme") or "trypsin")
    lines.append(f"--cut {cut}" if cut else "# --cut  (enzyme tag unavailable: set this from the paper)")
    for m in ((r or {}).get("variable_modifications", {}).get("value") or []):
        if m.startswith("Oxidation"):
            lines.append("--var-mod UniMod:35,15.994915,M")
        elif m.startswith("Acetyl (Protein N-term)"):
            lines.append("--var-mod UniMod:1,42.010565,*n")
        elif m.startswith("Phospho"):
            lines.append("--var-mod UniMod:21,79.966331,STY")
    lines.append("--var-mods 2")
    for n in track["runs"]:
        lines.append(f"--f {_run_path(n, plan)}")
    return "\n".join(lines) + "\n"


def _convert_sh(ctx: dict, plan: dict) -> str:
    return "\n".join([
        "#!/usr/bin/env bash",
        f"# Convert {plan['vendor_label']} to mzML so the search engine can read it.",
        f"# Needs ProteoWizard msconvert: {MSCONVERT[1]}  (Windows for vendor formats, or the Docker image below).",
        "set -euo pipefail",
        'cd "$(dirname "$0")"',
        "mkdir -p mzml",
        "# Native msconvert if installed; otherwise the ProteoWizard image, which preflight.sh already pulled.",
        "# Vendor readers run under Wine inside that image.",
        'if command -v msconvert >/dev/null 2>&1; then',
        '  CONV() { msconvert "$@"; }',
        'elif command -v docker >/dev/null 2>&1; then',
        f'  CONV() {{ docker run --rm -v "$PWD":/data {PWIZ_IMAGE} wine msconvert "$@"; }}',
        "  PREFIX=/data/",
        "else",
        '  echo "msconvert not available and docker not installed — see preflight.sh" >&2; exit 1',
        "fi",
        'PREFIX="${PREFIX:-}"',
        "",
        "# Only the runs are converted; index sidecars (.wiff.scan) must stay beside them in raw/.",
        "RUNS=(",
        *[f'  "{(f.get("file_name") or "")}"' for f in ctx["runs"]],
        ")",
        'for f in "${RUNS[@]}"; do',
        '  [ -f "mzml/${f%.*}.mzML" ] && continue   # already converted; resume cheaply',
        '  CONV "${PREFIX}raw/$f" --mzML --zlib --filter "peakPicking vendor msLevel=1-" -o "${PREFIX}mzml"',
        "done",
        "",
    ])


def _download_sh(ctx: dict) -> str:
    """Fetch this kit's runs, via a cache shared by every kit for the same dataset.

    A run already in the cache, at its full size, is not downloaded again — so picking a different pair of
    samples from the same deposition costs nothing for the files you already have, and an interrupted download
    resumes rather than restarting. Files are linked into raw/ so the configs still see a local raw/ directory.
    """
    total = sum(f.get("size_bytes") or 0 for f in ctx["raw"])
    gb = total / (1 << 30)
    lines = ["#!/usr/bin/env bash",
             f"# Download the {len(ctx['raw'])} raw file(s) of {ctx['accession']} from PRIDE"
             + (f" — about {gb:.1f} GB." if total else "."),
             "#",
             "# Cached in PRIDEMAP_RAW_DIR (a temp directory by default) and linked into raw/, so re-running this",
             "# or building another kit for the same dataset re-uses what is already downloaded.",
             "set -euo pipefail",
             f'CACHE="${{PRIDEMAP_RAW_DIR:-${{TMPDIR:-/tmp}}/pridemap-raw}}/{ctx["accession"]}"',
             'mkdir -p "$CACHE" raw',
             'echo "raw cache: $CACHE"',
             "",
             "# have <file> <bytes> — true when the cache holds the whole file, so it can be skipped.",
             "have() {",
             '  [ -f "$CACHE/$1" ] || return 1',
             '  [ -z "$2" ] && return 0',
             '  local n; n=$(wc -c < "$CACHE/$1" | tr -d " ")',
             '  [ "$n" = "$2" ]',
             "}",
             ""]
    for f in ctx["raw"]:
        if not f.get("ftp"):
            continue
        name = pathlib.PurePath(f.get("file_name") or "").name
        size = f.get("size_bytes") or ""
        lines += [
            f'if have "{name}" "{size}"; then',
            f'  echo "  ✓ {name} already downloaded"',
            "else",
            f'  echo "  ↓ {name}"',
            f'  curl -C - -o "$CACHE/{name}" "{f["ftp"]}"',
            "fi",
            f'ln -sf "$CACHE/{name}" "raw/{name}"',
        ]
    lines.append("")
    return "\n".join(lines)


PWIZ_IMAGE = "chambm/pwiz-skyline-i-agree-to-the-vendor-licenses"


def _mq_docker_lib(plan: dict) -> list[str]:
    """Which MaxQuant image this host should use, how to prove it runs, and how to build it if it must.

    Single owner of that decision: preflight.sh and run_all.sh both source these definitions, so the image the
    preflight verifies is necessarily the image the search runs in.

    MaxQuant ships 113 managed .NET assemblies plus five native x86-64 libraries. The assemblies are
    architecture-neutral CIL and Thermo's RawFileReader is pure managed, so on a non-amd64 host we copy the
    assemblies out of the published image — without ever executing them — and rebuild them onto a native Mono
    base. Mono's own architecture is then the host's, which is the configuration Mono is actually tested in.
    The exception is Bruker: libtimsdata.so and libbaf2sql_c.so are vendor-supplied x86-64 only, so Bruker data
    has no native path and must go through emulation.
    """
    bruker = (plan.get("vendor") == "bruker")
    lines = [
        "# ---- which MaxQuant image fits this host",
        f"MQ_IMAGE_AMD64={MAXQUANT_IMAGE}",
        f"MQ_IMAGE_NATIVE={MAXQUANT_NATIVE_IMAGE}",
        'MQ_ARCH="$(uname -m)"',
        "case \"$MQ_ARCH\" in x86_64|amd64) MQ_NEED_NATIVE=0 ;; *) MQ_NEED_NATIVE=1 ;; esac",
    ]
    if bruker:
        lines += [
            "# This deposition is Bruker: its readers are vendor x86-64 binaries with no arm64 build, so the",
            "# native rebuild would load MaxQuant but fail to open a single run. Emulation is the only option.",
            "MQ_NEED_NATIVE=0",
        ]
    lines += [
        'if [ "$MQ_NEED_NATIVE" = "1" ]; then',
        '  MQ_IMAGE="$MQ_IMAGE_NATIVE"; MQ_RUN_OPTS=""',
        "else",
        '  MQ_IMAGE="$MQ_IMAGE_AMD64"; MQ_RUN_OPTS="--platform linux/amd64"',
        "fi",
        "",
        "# Prints MaxQuant's version, or whatever it crashed with. Never fails the caller: the text is the",
        "# evidence, and the caller decides. Captured by the caller rather than piped, because under pipefail a",
        "# pipeline reports the crashing container's status instead of the matcher's — which silently turned an",
        "# earlier version of this check into an always-pass.",
        "mq_probe() {",
        '  docker run --rm $MQ_RUN_OPTS "$MQ_IMAGE" maxquant --version 2>&1 || true',
        "}",
        "",
        "# Mono aborts in its JIT under emulation; a working MaxQuant prints its own banner.",
        "mq_probe_ok() {",
        '  case "$1" in *"should not be reached"*|*"Assertion at"*|*SIGABRT*|*SIGSEGV*) return 1 ;; esac',
        '  case "$1" in *MaxQuantCmd*) return 0 ;; esac',
        "  return 1",
        "}",
        "",
        "# Build the native image from the published one. docker create + docker cp reads the assemblies out of",
        "# the amd64 image without running any amd64 code, so this works on a host that cannot execute it.",
        "mq_build_native() {",
        '  local d cid rc',
        '  d="$(mktemp -d)" || return 1',
        '  printf "  … building a native %s MaxQuant image, once (~1.4GB pull, a few minutes)\\n" "$MQ_ARCH"',
        '  docker pull -q --platform linux/amd64 "$MQ_IMAGE_AMD64" >/dev/null 2>&1 \\',
        '    || { rm -rf "$d"; return 1; }',
        '  cid="$(docker create --platform linux/amd64 "$MQ_IMAGE_AMD64" true 2>/dev/null)" \\',
        '    || { rm -rf "$d"; return 1; }',
        '  docker cp "$cid":/usr/local/bin/. "$d/bin" >/dev/null 2>&1; rc=$?',
        '  docker rm -f "$cid" >/dev/null 2>&1 || true',
        '  [ "$rc" = "0" ] || { rm -rf "$d"; return 1; }',
        "  cat > \"$d/Dockerfile\" <<'MQDOCKERFILE'",
        f"FROM {MAXQUANT_MONO_BASE}",
        "COPY bin/ /opt/maxquant/",
        "RUN printf '#!/bin/bash\\nexec mono /opt/maxquant/MaxQuantCmd.exe \"$@\"\\n' \\",
        "      > /usr/local/bin/maxquant \\",
        " && chmod +x /usr/local/bin/maxquant",
        f"WORKDIR {DOCKER_MOUNT}",
        "ENTRYPOINT []",
        "MQDOCKERFILE",
        '  docker build -q -t "$MQ_IMAGE_NATIVE" "$d" >/dev/null 2>&1; rc=$?',
        '  rm -rf "$d"',
        '  return $rc',
        "}",
        "",
        "# Make $MQ_IMAGE exist, by whichever route this host needs.",
        "mq_provision() {",
        '  if [ "$MQ_NEED_NATIVE" = "1" ]; then',
        "    mq_build_native",
        "  else",
        '    printf "  … pulling %s (one-time, ~1.4GB)\\n" "$MQ_IMAGE"',
        '    docker pull -q --platform linux/amd64 "$MQ_IMAGE" >/dev/null 2>&1',
        "  fi",
        "}",
        "",
    ]
    return lines


def preflight_sh(ctx: dict, plan: dict) -> str:
    """Check every tool and enough disk BEFORE the download, not four hours into it.

    msconvert is provisioned automatically from the ProteoWizard image. MaxQuant and DIA-NN cannot be:
    both require you to accept a licence and fetch the build yourself, so the most honest automation is to
    detect them in seconds and say exactly what is missing and where to get it.
    """
    need_convert = bool(plan.get("convert"))
    engines = {tr["engine"] for tr in (plan.get("tracks") or [])}
    # Raw bytes are known from the file listing; leave headroom for mzML (~1x) and the search output.
    raw_bytes = sum(f.get("size_bytes") or 0 for f in ctx["raw"])
    need_gb = max(5, int((raw_bytes * (2.2 if need_convert else 1.4)) / 1e9) + 5)
    out = [
        "#!/usr/bin/env bash",
        "# Preflight: everything this pipeline needs, checked up front.",
        "# No -e: every check should run so you see the whole list of problems, not just the first.",
        "set -uo pipefail",
        'cd "$(dirname "$0")" || exit 1',
        "",
        "MISSING=0",
        'ok()   { printf "  ✓ %s\\n" "$1"; }',
        'warn() { printf "  ! %s\\n" "$1"; }',
        'bad()  { printf "  ✗ %s\\n" "$1"; MISSING=1; }',
        "",
        'printf "Preflight\\n"',
        "",
        "# ---- disk. The estimate is raw size plus room for conversion and results; override it if you are",
        "# writing to another volume, or only running one of the tracks.",
        f"NEED_GB=${{PRIDEMAP_MIN_DISK_GB:-{need_gb}}}",
        'FREE_GB=$(df -Pk . | awk "NR==2 {print int(\\$4/1048576)}")',
        '[ "$FREE_GB" -ge "$NEED_GB" ] \\',
        '  && ok "disk: ${FREE_GB}GB free, need ~${NEED_GB}GB" \\',
        '  || bad "disk: only ${FREE_GB}GB free, need ~${NEED_GB}GB for raw + converted + results"',
        "",
        "# ---- curl",
        'command -v curl >/dev/null 2>&1 && ok "curl" || bad "curl not found"',
        "",
    ]
    # Docker provides msconvert and MaxQuant when they are not installed natively, so check it whenever either
    # could need it — not only when there is a conversion stage.
    if need_convert or "maxquant" in engines:
        out += [
            "# ---- docker: where msconvert and MaxQuant come from when they are not installed natively",
            "HAVE_DOCKER=0",
            'if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then',
            '  HAVE_DOCKER=1; ok "docker is running"',
            "else",
            '  warn "docker not available — only matters if a tool below is missing natively"',
            "fi",
            "",
        ]
    if need_convert:
        out += [
            "# ---- msconvert. Checked now; the 2 GB image pull is deferred to the end, because pulling it only to",
            "# abort on a different failed check wastes bandwidth and several minutes.",
            "NEED_PWIZ_PULL=0",
            'if command -v msconvert >/dev/null 2>&1; then',
            '  ok "msconvert (native)"',
            'elif [ "$HAVE_DOCKER" = "1" ]; then',
            f'  if docker image inspect {PWIZ_IMAGE} >/dev/null 2>&1; then',
            '    ok "msconvert (docker image already present)"',
            "  else",
            "    NEED_PWIZ_PULL=1",
            f'    warn "msconvert will come from {PWIZ_IMAGE} — a ~2GB pull, once, after the other checks pass"',
            "  fi",
            "else",
            '  bad "msconvert missing and docker unavailable. Install ProteoWizard "'
            '"(https://proteowizard.sourceforge.io/download.html) or start Docker and re-run."',
            "fi",
            "",
        ]
    if "maxquant" in engines:
        out += _mq_docker_lib(plan)
        out += [
            "# ---- MaxQuant. Native install, or the container, which we can provision and verify.",
            "MQ_RUNNER=none",
            "NEED_MQ_PULL=0",
            'if [ -n "${MAXQUANT_CMD:-}" ] && command -v "$MAXQUANT_CMD" >/dev/null 2>&1; then',
            '  MQ_RUNNER=native; ok "MaxQuant via \\$MAXQUANT_CMD"',
            'elif command -v MaxQuantCmd.exe >/dev/null 2>&1; then',
            '  MQ_RUNNER=native; ok "MaxQuantCmd.exe"',
            'elif command -v mono >/dev/null 2>&1 && [ -f "${MAXQUANT_DIR:-}/bin/MaxQuantCmd.exe" ]; then',
            '  MQ_RUNNER=mono; ok "MaxQuant via mono (\\$MAXQUANT_DIR)"',
            'elif [ "${HAVE_DOCKER:-0}" = "1" ]; then',
            "  MQ_RUNNER=docker",
            '  if ! docker image inspect "$MQ_IMAGE" >/dev/null 2>&1; then',
            "    NEED_MQ_PULL=1",
            '    if [ "$MQ_NEED_NATIVE" = "1" ]; then',
            '      warn "MaxQuant will be built for $MQ_ARCH from $MQ_IMAGE_AMD64 — once, after the other checks pass"',
            "    else",
            '      warn "MaxQuant will come from $MQ_IMAGE — a ~1.4GB pull, once"',
            "    fi",
            "  else",
            "    # Present is not the same as working: prove it can start before committing to the download.",
            '    MQ_PROBE="$(mq_probe)"',
            '    if mq_probe_ok "$MQ_PROBE"; then',
            '      ok "MaxQuant ($MQ_IMAGE, verified it starts)"',
            '    elif [ "$MQ_NEED_NATIVE" = "1" ]; then',
            '      NEED_MQ_PULL=1',
            '      warn "the native MaxQuant image is stale or broken — it will be rebuilt"',
            '    elif [ "$MQ_ARCH" = "x86_64" ] || [ "$MQ_ARCH" = "amd64" ]; then',
            "      # Same architecture as the image, so emulation is not the explanation; show what it said.",
            '      bad "the MaxQuant image is present but will not start on this $MQ_ARCH host: $MQ_PROBE"',
            "    else",
            '      bad "the MaxQuant image is present but crashes here: it is linux/amd64, this machine is '
            '$MQ_ARCH, and Mono aborts in its own JIT under QEMU emulation. This deposition is Bruker, whose '
            'readers the vendor ships for x86-64 only, so there is no native build to fall back on. Switch on '
            'Rosetta in Docker Desktop (Settings > General) and re-run, or run this kit on an x86_64 machine."',
            "    fi",
            "  fi",
            "else",
            '  bad "MaxQuant not found, and docker is not running. Either start Docker (the container is fetched '
            'for you), or install MaxQuant from https://maxquant.org/download_asset/maxquant/latest and set '
            'MAXQUANT_CMD=/path/to/MaxQuantCmd.exe."',
            "fi",
            'echo "$MQ_RUNNER" > .pipeline_mq_runner 2>/dev/null || true',
            "",
        ]
    if "diann" in engines:
        out += [
            "# ---- DIA-NN",
            'if [ -n "${DIANN_CMD:-}" ] && command -v "$DIANN_CMD" >/dev/null 2>&1; then',
            '  ok "DIA-NN via \\$DIANN_CMD"',
            'elif command -v diann-linux >/dev/null 2>&1 || command -v diann >/dev/null 2>&1; then',
            '  ok "DIA-NN"',
            "else",
            '  bad "DIA-NN not found. Get a build from https://github.com/vdemichev/DiaNN/releases "'
            '"then put it on PATH or set DIANN_CMD=/path/to/diann."',
            "fi",
            "",
        ]
    out += [
        'if [ "$MISSING" != "0" ]; then',
        '  printf "\\nPreflight failed. Nothing has been downloaded — fix the items marked ✗ and re-run.\\n" >&2',
        "  exit 1",
        "fi",
    ]
    # Everything else passed, so the expensive provisioning is now worth doing.
    if need_convert:
        out += [
            'if [ "${NEED_PWIZ_PULL:-0}" = "1" ]; then',
            f'  printf "  … pulling {PWIZ_IMAGE} (one-time, ~2GB)\\n"',
            f'  docker pull -q {PWIZ_IMAGE} >/dev/null 2>&1 \\',
            f'    || {{ printf "  ✗ could not pull {PWIZ_IMAGE}\\n" >&2; exit 1; }}',
            '  printf "  ✓ msconvert (docker, pulled automatically)\\n"',
            "fi",
        ]
    if "maxquant" in engines:
        out += [
            'if [ "${NEED_MQ_PULL:-0}" = "1" ]; then',
            '  mq_provision \\',
            '    || { printf "  ✗ could not provision MaxQuant (%s)\\n" "$MQ_IMAGE" >&2; exit 1; }',
            "  # Provisioned is not the same as runnable; the same smoke test as above, now that it exists.",
            '  MQ_PROBE="$(mq_probe)"',
            '  if ! mq_probe_ok "$MQ_PROBE"; then',
            '    printf "  ✗ MaxQuant was provisioned but will not start on this %s host:\\n%s\\n" \\',
            '      "$MQ_ARCH" "$MQ_PROBE" >&2',
            "    exit 1",
            "  fi",
            '  printf "  ✓ MaxQuant (%s, provisioned and verified)\\n" "$MQ_IMAGE"',
            "fi",
        ]
    out += [
        'printf "\\nPreflight passed.\\n"',
        "",
    ]
    return "\n".join(out)


def bootstrap_sh(accession: str, api_base: str, web_base: str = "") -> str:
    """The whole thing as one command: fetch the kit, unpack it, run it. No zip to click, nothing to unzip."""
    return "\n".join([
        "#!/usr/bin/env bash",
        f"# PRIDE Map — full reanalysis of {accession}, from nothing to a protein table.",
        "#",
        f"#   curl -fsSL {api_base}/dataset/{accession}/pipeline.sh | bash",
        "#",
        "# Creates ./reanalysis/<accession>/, fetches the kit into it and runs the pipeline.",
        "# Everything runs here, on this machine. Re-run the same command to resume.",
        "set -euo pipefail",
        "",
        f'ACC="{accession}"',
        f'API="${{PRIDEMAP_API:-{api_base}}}"',
        'DIR="${PRIDEMAP_DIR:-reanalysis/$ACC}"',
        "",
        'command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }',
        'command -v unzip >/dev/null 2>&1 || { echo "unzip is required" >&2; exit 1; }',
        "",
        'mkdir -p "$DIR"',
        'cd "$DIR"',
        "",
        'if [ ! -f run_all.sh ]; then',
        '  printf "Fetching the search kit for %s…\\n" "$ACC"',
        f'  curl -fsSL "$API/dataset/$ACC/searchkit.zip{("?web=" + urllib.parse.quote(web_base, safe="")) if web_base else ""}" -o kit.zip',
        '  unzip -oq kit.zip && rm -f kit.zip',
        '  chmod +x ./*.sh',
        "else",
        '  printf "Kit already here — resuming.\\n"',
        "fi",
        "",
        'exec bash run_all.sh',
        "",
    ])


KIT_GITIGNORE = """\
# Everything the pipeline downloads or produces. None of it belongs in git:
# the raw files alone run to tens of GB.
raw/
mzml/
fasta/
combined/
out_*/
.pipeline/
progress.jsonl
*.log
# Configs with this kit's directory substituted in, written by run_all.sh. The shipped config is the one to keep.
*.local.xml
*.local.cfg
*.docker.xml
*.docker.cfg
"""


def _stage_lib() -> str:
    """Shared stage framework: echoes to the terminal AND appends a JSON line per event.

    Three audiences, one source of truth — the terminal for whoever is watching, progress.jsonl for the UI or
    any later tooling, and .pipeline/<id>.done markers so re-running resumes instead of repeating hours of work.
    """
    return r'''
PIPE_DIR=".pipeline"
PROGRESS="progress.jsonl"
mkdir -p "$PIPE_DIR"

_now()   { date -u +%Y-%m-%dT%H:%M:%SZ; }
_epoch() { date +%s; }

# JSON-escape a string (quotes, backslashes, control chars) so progress.jsonl stays parseable.
_esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/ /g' | tr -d '\000-\037'; }

emit() {  # emit <event> <stage-id> <message> [extra-json]
  local event="$1" id="$2" msg="$3" extra="${4:-}"
  printf '{"ts":"%s","event":"%s","stage":"%s","message":"%s"%s}\n' \
    "$(_now)" "$(_esc "$event")" "$(_esc "$id")" "$(_esc "$msg")" \
    "${extra:+,$extra}" >> "$PROGRESS"
}

STAGE_START=0
STAGE_ID=""
STAGE_N=0

stage() {  # stage <id> <label>; returns 1 when already done so the caller can skip
  STAGE_ID="$1"; local label="$2"
  STAGE_N=$((STAGE_N + 1))
  if [ -f "$PIPE_DIR/$STAGE_ID.done" ]; then
    printf '\n[%d/%d] %s\n      ✓ already done — skipping (delete %s/%s.done to redo)\n' \
      "$STAGE_N" "$TOTAL_STAGES" "$label" "$PIPE_DIR" "$STAGE_ID"
    emit stage_skipped "$STAGE_ID" "$label"
    return 1
  fi
  STAGE_START=$(_epoch)
  printf '\n[%d/%d] %s\n      started %s\n' "$STAGE_N" "$TOTAL_STAGES" "$label" "$(_now)"
  emit stage_started "$STAGE_ID" "$label"
  return 0
}

stage_done() {
  local secs=$(( $(_epoch) - STAGE_START ))
  printf '      ✓ done in %dm %02ds\n' "$((secs / 60))" "$((secs % 60))"
  emit stage_finished "$STAGE_ID" "completed" "\"seconds\":$secs"
  : > "$PIPE_DIR/$STAGE_ID.done"
}

step() {  # a sub-step inside a stage
  printf '        · %s\n' "$1"
  emit substage "$STAGE_ID" "$1"
}

fail() {
  printf '\n      ✗ FAILED: %s\n' "$1" >&2
  emit stage_failed "$STAGE_ID" "$1"
  exit 1
}

warn_up() {  # non-fatal: the search already succeeded, only the hand-off didn't
  printf '        ! %s\n' "$1"
  emit warning "$STAGE_ID" "$1"
}

# Tail MaxQuant's own progress. It writes one combined/proc/<job>.finished.txt per internal step; we echo each
# as it appears, so the long opaque middle of the run reports what it is actually doing.
MQ_SEEN="$PIPE_DIR/mq_seen"

mq_scan() {  # one pass: echo every finished step not already reported, oldest first
  local proc="combined/proc" f name
  [ -d "$proc" ] || return 0
  # -tr sorts by mtime oldest-first, so a burst between polls is reported in the order MaxQuant finished them.
  # Job names contain spaces, so read line by line rather than word-splitting.
  ls -tr "$proc"/*.finished.txt 2>/dev/null | while IFS= read -r f; do
    [ -e "$f" ] || continue
    name="$(basename "$f" .finished.txt)"
    if ! grep -Fqx -- "$name" "$MQ_SEEN" 2>/dev/null; then
      printf '%s\n' "$name" >> "$MQ_SEEN"
      printf '        · MaxQuant: %s\n' "$name"
      emit maxquant_step "$STAGE_ID" "$name"
    fi
  done
}

watch_maxquant() {
  : > "$MQ_SEEN"
  while :; do mq_scan; sleep 5; done
}
'''


def run_all_sh(ctx: dict, plan: dict, mq_cmd: str = "MaxQuantCmd.exe", diann_cmd: str = "diann-linux") -> str:
    """One command that runs the whole pipeline, echoing every stage and recording it to progress.jsonl."""
    acc = ctx["accession"]
    fasta_path, _ = _fasta(ctx)
    total = len(plan.get("stages") or [])
    out = [
        "#!/usr/bin/env bash",
        f"# Full reanalysis pipeline for {acc}, generated by PRIDE Map.",
        "#",
        "# Runs on YOUR machine, start to finish. Re-running resumes: finished stages are skipped.",
        "# Every stage is echoed here and appended to progress.jsonl as one JSON object per line.",
        "#",
        "# Needs: curl, and whichever of msconvert / MaxQuant / DIA-NN the stages below use.",
        "set -euo pipefail",
        'cd "$(dirname "$0")"',
        "",
        # Every stage except the final 'analyse', which is yours to make in the browser.
        f"TOTAL_STAGES={max(1, total - 1)}",
        f'ACCESSION="{acc}"',
        f'API="${{PRIDEMAP_API:-{ctx.get("api_base", "")}}}"',
        "# The web app is a different origin from the API, so it is passed in rather than guessed.",
        f'WEB="${{PRIDEMAP_WEB:-{ctx.get("web_base", "")}}}"',
        "# Tool commands resolve automatically; override any of them with an env var.",
        'MQ="${MAXQUANT_CMD:-}"',
        'DIANN="${DIANN_CMD:-}"',
        '[ -n "$MQ" ]    || { command -v MaxQuantCmd.exe >/dev/null 2>&1 && MQ=MaxQuantCmd.exe; }',
        '[ -n "$DIANN" ] || { command -v diann-linux >/dev/null 2>&1 && DIANN=diann-linux; }',
        '[ -n "$DIANN" ] || { command -v diann >/dev/null 2>&1 && DIANN=diann; }',
        # Same definitions preflight.sh checked, so the search runs in the image that was verified.
        *(_mq_docker_lib(plan) if "maxquant" in {tr["engine"] for tr in (plan.get("tracks") or [])} else []),
        _stage_lib(),
        'printf "PRIDE Map pipeline — %s\\n" "$ACCESSION"',
        f'printf "%s\\n" "{max(1, total - 1)} automated stages, then group A vs B is yours to pick. '
        f'Progress is also written to progress.jsonl."',
        'emit run_started "" "pipeline start"',
        "",
        "# ---------------------------------------------------------------- 0. preflight",
        '# Fails here, in seconds, rather than after the download. Nothing is fetched until this passes.',
        'if stage preflight "Check tools and disk space"; then',
        '  bash preflight.sh || fail "preflight failed — nothing was downloaded"',
        "  stage_done",
        "fi",
        "",
        "# ---------------------------------------------------------------- 1. raw files",
        'if stage download "Download raw files from PRIDE"; then',
        '  step "fetching into raw/ — this is the slow one, expect tens of GB"',
        '  bash download_raw.sh || fail "raw download failed (re-run to resume; curl -C - continues part files)"',
        '  step "$(find raw -type f | wc -l | tr -d " ") files in raw/"',
        "  stage_done",
        "fi",
        "",
        "# ---------------------------------------------------------------- 2. protein database",
        'if stage fasta "Fetch the protein database"; then',
        f'  bash download_fasta.sh || fail "FASTA download failed"',
        f'  [ -s "{fasta_path}" ] || fail "{fasta_path} is missing or empty"',
        f'  step "$(grep -c \'^>\' "{fasta_path}" || echo 0) sequences in {fasta_path}"',
        "  stage_done",
        "fi",
    ]
    if plan.get("convert"):
        out += [
            "",
            "# ---------------------------------------------------------------- 3. vendor format -> mzML",
            f'if stage convert "Convert {plan["vendor_label"]} to mzML"; then',
            '  command -v msconvert >/dev/null 2>&1 || fail "msconvert not on PATH — see convert_to_mzml.sh for the Docker alternative"',
            '  bash convert_to_mzml.sh || fail "msconvert failed"',
            '  step "$(find mzml -name \'*.mzML\' | wc -l | tr -d \' \') mzML files written"',
            "  stage_done",
            "fi",
        ]
    for tr in plan.get("tracks") or []:
        kind = tr["kind"].lower()
        if tr["engine"] == "maxquant":
            out += [
                "",
                f"# ---------------------------------------------------------------- {tr['engine_label']} ({tr['run_count']} {tr['kind']} runs)",
                f'if stage search_{kind} "{tr["engine_label"]} — {tr["run_count"]} {tr["kind"]} runs"; then',
                # MaxQuant's workers resolve paths from their own cwd, so the config needs absolute ones. The kit
                # cannot know where it will be unpacked, so the directory is substituted here, once, per run.
                '  step "MaxQuant writes one combined/proc/<step>.finished.txt per internal step; echoing them as they land"',
                "  watch_maxquant & MQ_WATCH=$!",
                '  trap \'kill "$MQ_WATCH" 2>/dev/null || true\' EXIT',
                # The config carries a placeholder rather than a path, because MaxQuant's workers resolve relative
                # paths from their own cwd. What it resolves to differs per runner: the kit directory natively,
                # the mount point inside the container.
                '  if [ -n "$MQ" ]; then',
                f'    sed "s#{KIT_DIR_TOKEN}#$PWD#g" {tr["config"]} > {_local_config(tr["config"])}',
                f'    step "paths resolved against $PWD -> {_local_config(tr["config"])}"',
                f'    "$MQ" {_local_config(tr["config"])} || fail "MaxQuant failed — see combined/proc/ for the step it stopped on"',
                '  elif command -v mono >/dev/null 2>&1 && [ -f "${MAXQUANT_DIR:-}/bin/MaxQuantCmd.exe" ]; then',
                f'    sed "s#{KIT_DIR_TOKEN}#$PWD#g" {tr["config"]} > {_local_config(tr["config"])}',
                f'    mono "$MAXQUANT_DIR/bin/MaxQuantCmd.exe" {_local_config(tr["config"])} \\',
                '      || fail "MaxQuant (mono) failed — see combined/proc/ for the step it stopped on"',
                '  elif command -v docker >/dev/null 2>&1; then',
                f'    sed "s#{KIT_DIR_TOKEN}#{DOCKER_MOUNT}#g" {tr["config"]} > {_docker_config(tr["config"])}',
                f'    step "running MaxQuant in $MQ_IMAGE; paths resolved against {DOCKER_MOUNT}"',
                f'    docker run --rm $MQ_RUN_OPTS -v "$PWD":{DOCKER_MOUNT} -w {DOCKER_MOUNT} \\',
                f'      "$MQ_IMAGE" maxquant {DOCKER_MOUNT}/{_docker_config(tr["config"])} \\',
                '      || fail "MaxQuant (docker) failed — see combined/proc/ for the step it stopped on"',
                "  else",
                '    fail "MaxQuant not found — preflight.sh explains the options (MAXQUANT_CMD / MAXQUANT_DIR / docker)"',
                "  fi",
                '  kill "$MQ_WATCH" 2>/dev/null || true; trap - EXIT',
                '  mq_scan  # final sweep: catch steps that finished between the last poll and now',
                f'  [ -s "{tr["output_table"]}" ] || fail "{tr["output_table"]} was not produced"',
                f'  step "$(($(wc -l < "{tr["output_table"]}") - 1)) protein groups in {tr["output_table"]}"',
                "  stage_done",
                "fi",
            ]
        else:
            out += [
                "",
                f"# ---------------------------------------------------------------- {tr['engine_label']} ({tr['run_count']} {tr['kind']} runs)",
                f'if stage search_{kind} "{tr["engine_label"]} — {tr["run_count"]} {tr["kind"]} runs"; then',
                '  [ -n "$DIANN" ] || fail "DIA-NN not found — set DIANN_CMD, see preflight.sh"',
                '  step "library-free: DIA-NN predicts a spectral library from the FASTA first"',
                f'  bash run_diann_{kind}.sh || fail "DIA-NN failed"',
                f'  [ -s "{tr["output_table"]}" ] || fail "{tr["output_table"]} was not produced"',
                f'  step "$(($(wc -l < "{tr["output_table"]}") - 1)) protein groups in {tr["output_table"]}"',
                "  stage_done",
                "fi",
            ]
    tables = [tr["output_table"] for tr in (plan.get("tracks") or [])]
    out += [
        "",
        "# ---------------------------------------------------------------- hand the tables back to PRIDE Map",
        "# Uploaded automatically so the last manual step disappears: the script prints a link that opens",
        "# Analyse with the table already loaded. Set PRIDEMAP_NO_UPLOAD=1 to keep everything local.",
        'if stage upload "Hand the protein tables to PRIDE Map"; then',
        '  if [ -n "${PRIDEMAP_NO_UPLOAD:-}" ] || [ -z "$API" ]; then',
        '    step "skipped (PRIDEMAP_NO_UPLOAD set, or no API URL) — upload the tables by hand"',
        "  else",
        "    TABLES=(" + " ".join(f'"{t}"' for t in tables) + ")",
        '    for T in "${TABLES[@]}"; do',
        '      [ -s "$T" ] || continue',
        '      step "uploading $T to $API"',
        '      RESP="$(curl -fsS -F "file=@$T" "$API/analyse/inspect" 2>/dev/null || true)"',
        '      UID_="$(printf \'%s\' "$RESP" | sed -n \'s/.*"upload_id"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p\')"',
        '      if [ -n "$UID_" ]; then',
        '        if [ -n "$WEB" ]; then',
        '          printf "        → open: %s\\n" "$WEB/analyse?upload=$UID_"',
        "        else",
        '          printf "        → loaded as upload %s — open PRIDE Map → Analyse\\n" "$UID_"',
        "        fi",
        '        emit uploaded "upload" "$T" "\\"upload_id\\":\\"$UID_\\""',
        '        printf "%s\\t%s\\n" "$T" "$UID_" >> uploaded.txt',
        "      else",
        '        warn_up "could not upload $T — upload it by hand in the Analyse step"',
        "      fi",
        "    done",
        "  fi",
        "  stage_done",
        "fi",
        "",
        "# ---------------------------------------------------------------- done",
        'emit run_finished "" "pipeline complete"',
        'printf "\\n%s\\n" "──────────────────────────────────────────────"',
        'printf "Pipeline complete.\\n\\n"',
        'if [ -s uploaded.txt ]; then',
        '  printf "Already loaded into PRIDE Map — open Analyse and pick group A and group B:\\n"',
        '  while IFS="$(printf \'\\t\')" read -r t _; do printf "  • %s\\n" "$t"; done < uploaded.txt',
        "else",
        '  printf "Upload these in PRIDE Map → Analyse:\\n"',
        *[f'  printf "  • %s\\n" "{t}"' for t in tables],
        "fi",
        'printf "\\nThat gives log2 fold change, Welch t-test, Benjamini-Hochberg FDR and a volcano plot.\\n"',
        'printf "Stage-by-stage record: progress.jsonl\\n"',
        "",
    ]
    return "\n".join(out)


def search_kit(accession: str, api_base: str = "", web_base: str = "", runs: list[str] | None = None) -> tuple[bytes, dict]:
    """A ready-to-run kit for the engine that can actually process this dataset's raw files.

    `runs` restricts it to a chosen subset of the deposition's runs."""
    ctx = _kit_context(accession, runs)
    ctx["api_base"] = api_base.rstrip("/")
    ctx["web_base"] = _safe_origin(web_base)
    plan = search_plan(ctx["t"], [f.get("file_name") or "" for f in ctx["raw"]])
    p = ctx["p"]
    fasta_path, _ = _fasta(ctx)
    files: dict[str, str] = {"download_raw.sh": _download_sh(ctx), "download_fasta.sh": _fasta_sh(ctx),
                             "preflight.sh": preflight_sh(ctx, plan), ".gitignore": KIT_GITIGNORE}
    steps = [f"1. `bash download_raw.sh` pulls the {len(ctx['raw'])} raw files from PRIDE's FTP into raw/ "
             "(runs plus their index sidecars — the readers need both).",
             f"2. `bash download_fasta.sh` fetches the protein database to `{fasta_path}`, where the configs expect it."]
    n = 3
    if plan.get("convert"):
        files["convert_to_mzml.sh"] = _convert_sh(ctx, plan)
        steps.append(f"{n}. `bash convert_to_mzml.sh` converts {plan['vendor_label']} to mzML "
                     f"({plan['convert']['tool']}); the search engines cannot read the vendor format directly.")
        n += 1

    tracks = plan.get("tracks") or []
    multi = len(tracks) > 1
    for tr in tracks:
        label = f"the {tr['kind']} set" if multi else "the runs"
        outdir = tr.get("outdir", ".")
        if tr["engine"] == "maxquant":
            name = tr["config"]
            files[name] = mqpar_xml(ctx, plan, tr["runs"])
            steps += [f"{n}. Open `{name}` in MaxQuant ({MQ_VERSION}) for {label} ({tr['run_count']} runs), check the "
                      "FASTA path, enzyme and modifications, then Start. Headless: "
                      f"`MaxQuantCmd.exe {name}`.",
                      f"{n + 1}. Upload the resulting `{tr['output_table']}` to PRIDE Map's Analyse step and pick the "
                      "LFQ intensity columns as group A and group B."]
        else:
            cfg = tr["config"]
            sh = f"run_{cfg.rsplit('.', 1)[0]}.sh"
            files[cfg] = diann_cfg(ctx, plan, tr, outdir)
            files[sh] = "\n".join([
                "#!/usr/bin/env bash",
                f"# DIA-NN library-free search of {accession} ({label}, {tr['run_count']} runs).",
                "# Runs on YOUR machine, not on PRIDE Map. Build: https://github.com/vdemichev/DiaNN/releases",
                "set -euo pipefail",
                'cd "$(dirname "$0")"',
                "# Resolve the binary here too, so this script also works on its own.",
                'DIANN="${DIANN_CMD:-}"',
                '[ -n "$DIANN" ] || { command -v diann-linux >/dev/null 2>&1 && DIANN=diann-linux; }',
                '[ -n "$DIANN" ] || { command -v diann >/dev/null 2>&1 && DIANN=diann; }',
                '[ -n "$DIANN" ] || { echo "DIA-NN not found — set DIANN_CMD=/path/to/diann" >&2; exit 1; }',
                *([f"mkdir -p {outdir}"] if multi else []),
                f'"$DIANN" --cfg {cfg}',
                "",
            ])
            steps += [f"{n}. `bash {sh}` runs DIA-NN library-free over {label} ({tr['run_count']} runs); it predicts a "
                      f"spectral library from the FASTA. Check `{cfg}` before starting.",
                      f"{n + 1}. Upload the resulting `{tr['output_table']}` to PRIDE Map's Analyse step and pick sample "
                      "columns as group A and group B."]
        n += 2
    if not tracks:
        steps.append(f"{n}. {plan['why']} No engine config is generated for this acquisition type.")

    readme = [f"# Search kit for {accession}", "", f"{p.get('title')}", "",
              f"**Runs:** {plan['raw_count']} × {plan['vendor_label']} "
              f"({plan['runs_dda']} DDA, {plan['runs_dia']} DIA/SWATH, {plan['runs_unclassified']} unclassified)",
              f"**Acquisition:** {plan['acquisition']} (tag said: {plan['tag_acquisition'] or 'untagged'})", ""]
    for tr in tracks:
        readme.append(f"- **{tr['kind']} → {tr['engine_label']}** · {tr['run_count']} runs · "
                      f"config `{tr['config']}` · output `{tr['output_table']}`")
    readme += ["", f"{plan['why']}", ""]
    if plan.get("conflict"):
        readme += [f"> **Tag conflict:** {plan['conflict']}", ""]
    if plan.get("caveat"):
        readme += [f"> {plan['caveat']}", ""]
    if not plan["maxquant_offered"] and plan.get("maxquant_reason"):
        readme += [f"**Why not MaxQuant:** {plan['maxquant_reason']}", ""]
    if tracks:
        base = ctx.get("api_base") or ""
        readme += ["## Run it", "",
                   "From nothing, in one command — fetches this kit and runs everything:", "",
                   "```bash",
                   f"curl -fsSL {base}/dataset/{accession}/pipeline.sh | bash" if base
                   else "curl -fsSL <pride-map-api>/dataset/%s/pipeline.sh | bash" % accession,
                   "```", "",
                   "Or, if you already have this folder:", "",
                   "```bash",
                   "bash run_all.sh",
                   "```", "",
                   "That is the whole pipeline. It echoes each stage as it goes, appends one JSON object per event "
                   "to `progress.jsonl`, and tails MaxQuant's own internal steps so the long middle of the run is "
                   "not a black box. Re-running resumes — finished stages are skipped, so a failure costs you that "
                   "stage and not the download.",
                   "",
                   "`.gitignore` is included: nothing the pipeline downloads or writes should ever enter git.",
                   "", "### What it does, stage by stage", ""]
        for i, s in enumerate(plan.get("stages") or [], 1):
            readme.append(f"{i}. **{s['label']}** — {s['detail']} _({s['where']})_")
            for sub in s.get("substages") or []:
                readme.append(f"   - {sub['name']} — {sub['detail']}")
        readme += ["", "## The individual steps", "",
                   "`run_all.sh` just chains these; run them yourself if you prefer.", ""] + steps
    else:
        readme += ["## Steps", ""] + steps
    readme += ["",
               "Nothing in this kit runs on PRIDE Map — every step happens on your own machine.",
               f"Values PRIDE Map could not derive are marked `{NA}` or `REPLACE_WITH_...`; fill them in from the paper.",
               ""]
    if tracks:
        files["run_all.sh"] = run_all_sh(ctx, plan)
    files["README.md"] = "\n".join(readme)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue(), plan


def _api_base(request) -> str:
    return str(request.base_url).rstrip("/")


def _safe_origin(value: str) -> str:
    """The caller supplies this, so only a plain http(s) origin is ever written into a generated script."""
    v = (value or "").strip().rstrip("/")
    if not v:
        return ""
    p = urllib.parse.urlparse(v)
    if p.scheme not in ("http", "https") or not p.hostname or p.path or p.query or p.fragment:
        return ""
    return f"{p.scheme}://{p.netloc}"


@router.get("/dataset/{accession}/searchkit.zip")
def dataset_searchkit(accession: str, request: Request, web: str = "", runs: str = ""):
    data, plan = search_kit(accession, _api_base(request), web, runs=parse_runs(runs))
    name = "search" if len(plan.get("tracks") or []) > 1 else (plan.get("engine") or "search")
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{accession}_{name}_kit.zip"'})


@router.get("/dataset/{accession}/pipeline.sh")
def dataset_pipeline_sh(accession: str, request: Request, web: str = "", runs: str = ""):
    """The one-liner: `curl -fsSL <api>/dataset/<acc>/pipeline.sh | bash` runs the whole thing."""
    _kit_context(accession, parse_runs(runs))  # 404 early for an unknown accession rather than handing back a broken script
    return Response(content=bootstrap_sh(accession, _api_base(request), _safe_origin(web)),
                    media_type="text/x-shellscript")


# ----------------------------------------------------------------------------- running the pipeline for you
# The deployed API is a slim container with no .NET, no vendor readers and no room for 500 GB, so it cannot run a
# search. A local API is a different machine entirely — the researcher's own — and there is no reason to make them
# copy a command into a terminal when the server is already sitting on the disk the files belong on. These routes
# therefore only work for a loopback client, unless the operator explicitly opts in.
RUNS_DIR = ROOT / "reanalysis"
_ALLOW_RUN_ENV = "PRIDEMAP_ALLOW_LOCAL_RUN"


def _is_local(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost") or os.environ.get(_ALLOW_RUN_ENV) == "1"


def _require_local(request: Request):
    if not _is_local(request):
        raise HTTPException(403, "the pipeline can only be run by a local PRIDE Map; "
                                 f"set {_ALLOW_RUN_ENV}=1 on a server that really should run searches")


def _selection_key(runs: list[str] | None) -> str:
    """A short stable id for a run selection, so two different pairs from one dataset get their own run
    directory instead of overwriting each other's combined/ and progress.jsonl."""
    keys = sorted({_run_key(r) for r in (runs or []) if _run_key(r)})
    if not keys:
        return ""
    import hashlib
    return hashlib.sha1("\n".join(keys).encode()).hexdigest()[:8]


def run_dir(accession: str, runs: list[str] | None = None) -> pathlib.Path:
    d = RUNS_DIR / re.sub(r"[^A-Za-z0-9_-]", "", accession)[:32]
    sel = _selection_key(runs)
    return d.with_name(f"{d.name}__{sel}") if sel else d


def _write_kit(accession: str, api_base: str, web_base: str, runs: list[str] | None = None) -> pathlib.Path:
    """Unpack the kit into reanalysis/<accession>/ — the same layout the one-liner uses."""
    data, _ = search_kit(accession, api_base, web_base, runs=runs)
    d = run_dir(accession, runs)
    d.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(d)
    for p in d.glob("*.sh"):
        p.chmod(0o755)
    return d


def _read_progress(d: pathlib.Path) -> list[dict]:
    p = d / "progress.jsonl"
    if not p.exists():
        return []
    out = []
    try:
        with open(p, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return []
    return out


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    return True


@router.post("/dataset/{accession}/pipeline/start")
def pipeline_start(accession: str, request: Request, web: str = "", runs: str = ""):
    """Start the whole reanalysis here, in the background. Returns immediately; poll /pipeline/status.

    `runs` is a comma-separated selection of this dataset's raw files — the two samples you want to compare,
    say. Each selection gets its own run directory, so starting a different pair does not disturb this one,
    and the raw cache means files already downloaded for an earlier selection are not fetched again.
    """
    _require_local(request)
    import subprocess
    sel = parse_runs(runs)
    d = run_dir(accession, sel)
    state = d / ".pipeline" / "run.pid"
    if state.exists():
        try:
            if _pid_alive(int(state.read_text().strip())):
                raise HTTPException(409, "a run is already in progress for this dataset")
        except ValueError:
            pass
    d = _write_kit(accession, _api_base(request), _safe_origin(web), runs=sel)
    if not (d / "run_all.sh").exists():
        raise HTTPException(400, "this dataset has no runnable pipeline (nothing for an engine to do)")
    (d / ".pipeline").mkdir(exist_ok=True)
    log = open(d / "run.log", "ab", buffering=0)
    proc = subprocess.Popen(["bash", "run_all.sh"], cwd=d, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    (d / ".pipeline" / "run.pid").write_text(str(proc.pid))
    return {"accession": accession, "started": True, "pid": proc.pid, "dir": str(d), "runs": sel}


@router.get("/dataset/{accession}/pipeline/status")
def pipeline_status(accession: str, request: Request, tail: int = 40, runs: str = ""):
    """Where the run has got to: one entry per stage, plus the last lines of output.

    `runs` must match the selection the run was started with, since each selection has its own directory."""
    local = _is_local(request)
    sel = parse_runs(runs)
    d = run_dir(accession, sel)
    plan = dataset_search_plan(accession, sel)
    events = _read_progress(d)
    by_stage: dict[str, dict] = {}
    for e in events:
        sid = e.get("stage") or ""
        if not sid:
            continue
        s = by_stage.setdefault(sid, {"state": "pending", "seconds": None, "steps": [], "message": None})
        ev = e.get("event")
        if ev == "stage_started":
            s["state"] = "running"
        elif ev == "stage_finished":
            s["state"] = "done"
            s["seconds"] = e.get("seconds")
        elif ev == "stage_skipped":
            s["state"] = "done"
            s["message"] = "already done"
        elif ev == "stage_failed":
            s["state"] = "failed"
            s["message"] = e.get("message")
        elif ev in ("substage", "maxquant_step", "uploaded", "warning"):
            s["steps"].append(e.get("message"))
    pid_file = d / ".pipeline" / "run.pid"
    running = False
    if pid_file.exists():
        try:
            running = _pid_alive(int(pid_file.read_text().strip()))
        except ValueError:
            running = False
    stages = []
    for s in plan.get("stages") or []:
        st = by_stage.get(s["id"], {})
        stages.append({**s, "state": st.get("state", "pending"), "seconds": st.get("seconds"),
                       "steps": (st.get("steps") or [])[-6:], "message": st.get("message")})
    log_lines: list[str] = []
    lg = d / "run.log"
    if lg.exists():
        try:
            log_lines = lg.read_text(errors="replace").splitlines()[-max(1, min(tail, 200)):]
        except OSError:
            pass
    finished = any(e.get("event") == "run_finished" for e in events)
    failed = any(e.get("event") == "stage_failed" for e in events)
    return {"accession": accession, "can_run": local, "exists": d.exists(), "running": running,
            "finished": finished, "failed": failed, "stages": stages, "log": log_lines,
            "uploads": [e.get("upload_id") for e in events if e.get("event") == "uploaded" and e.get("upload_id")],
            "dir": str(d) if d.exists() else None}


# ----------------------------------------------------------------------------- corrections feed
def _ensure_corrections(con):
    con.execute("CREATE TABLE IF NOT EXISTS corrections (id INTEGER PRIMARY KEY AUTOINCREMENT, accession TEXT, tag TEXT, suggested_value TEXT, "
                "note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")


@router.get("/corrections")
def corrections(limit: int = 500):
    from main import TAG_COLS
    con = db()
    _ensure_corrections(con)
    rows = [dict(r) for r in con.execute("SELECT * FROM corrections ORDER BY id DESC LIMIT ?", (min(limit, 5000),))]
    accs = sorted({r["accession"] for r in rows})
    tags = {}
    if accs:
        for t in con.execute(f"SELECT * FROM tags WHERE accession IN ({','.join('?' * len(accs))})", accs):
            tags[t["accession"]] = dict(t)
    titles = {r["accession"]: r["title"] for r in con.execute(f"SELECT accession, title FROM projects WHERE accession IN ({','.join('?' * len(accs))})", accs)} if accs else {}
    allowed = set(TAG_COLS + ["depth_proteins", "summary_one_line"])
    for r in rows:
        t = tags.get(r["accession"]) or {}
        r["current_value"] = t.get(r["tag"]) if r["tag"] in allowed else None
        r["model"] = t.get("model")
        r["title"] = titles.get(r["accession"])
    return {"total": con.execute("SELECT count(*) FROM corrections").fetchone()[0], "corrections": rows,
            "command": "uv run apply_corrections.py"}


@router.get("/corrections/stats")
def corrections_stats():
    con = db()
    _ensure_corrections(con)
    by_tag = {r[0]: r[1] for r in con.execute("SELECT tag, count(*) FROM corrections GROUP BY tag ORDER BY count(*) DESC")}
    return {"total": sum(by_tag.values()), "accessions": con.execute("SELECT count(DISTINCT accession) FROM corrections").fetchone()[0],
            "by_tag": by_tag,
            "applied": con.execute("SELECT count(*) FROM tags WHERE model LIKE '%(corrected)'").fetchone()[0]}


@router.get("/remote-mq")
def remote_mq_info():
    """Tells the UI whether a remote MaxQuant server is configured (MQ_SERVER_URL in .env)."""
    return {"available": bool(os.environ.get("MQ_SERVER_URL", "").strip())}


# ----------------------------------------------------------------------------- remote MaxQuant server (Devin/cloud)
# Set MQ_SERVER_URL and MQ_TOKEN in .env to route searches to a remote Linux machine running the Devin-provisioned
# mq_server. Temporary: tear down by unsetting MQ_SERVER_URL.

def _mq_remote_url() -> str:
    url = os.environ.get("MQ_SERVER_URL", "").rstrip("/")
    if not url:
        raise HTTPException(503, "MQ_SERVER_URL is not set — add it to .env")
    return url


def _mq_auth_headers() -> dict:
    token = os.environ.get("MQ_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


@router.post("/dataset/{accession}/pipeline/remote")
def pipeline_remote_start(accession: str, request: Request, runs: str = ""):
    """Submit the search kit to the remote MaxQuant server. Returns {job_id}."""
    base_url = _mq_remote_url()
    kit_bytes, _ = search_kit(accession, _api_base(request), "", runs=parse_runs(runs))
    boundary = "PrideMapBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="kit"; filename="kit.zip"\r\n'
        f"Content-Type: application/zip\r\n\r\n"
    ).encode() + kit_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{base_url}/run", data=body,
        headers={**_mq_auth_headers(), "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode(errors="replace"))
    except Exception as e:
        raise HTTPException(502, f"remote MQ server unreachable: {e}")


@router.get("/dataset/{accession}/pipeline/remote/{job_id}/status")
def pipeline_remote_status(accession: str, job_id: str):
    """Poll job status from the remote server. Returns {status, log}."""
    base_url = _mq_remote_url()
    req = urllib.request.Request(f"{base_url}/status/{job_id}", headers=_mq_auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode(errors="replace"))
    except Exception as e:
        raise HTTPException(502, f"remote MQ server unreachable: {e}")


@router.get("/dataset/{accession}/pipeline/remote/{job_id}/results")
def pipeline_remote_results(accession: str, job_id: str):
    """Download proteinGroups.txt from a completed remote job."""
    base_url = _mq_remote_url()
    req = urllib.request.Request(f"{base_url}/results/{job_id}", headers=_mq_auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        return Response(content=data, media_type="text/tab-separated-values",
                        headers={"Content-Disposition": f'attachment; filename="{accession}_proteinGroups.txt"'})
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode(errors="replace"))
    except Exception as e:
        raise HTTPException(502, f"remote MQ server unreachable: {e}")


@router.delete("/dataset/{accession}/pipeline/remote/{job_id}")
def pipeline_remote_delete(accession: str, job_id: str):
    """Clean up a finished job's temp directory on the remote server."""
    base_url = _mq_remote_url()
    req = urllib.request.Request(f"{base_url}/job/{job_id}", headers=_mq_auth_headers(), method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode(errors="replace"))
    except Exception as e:
        raise HTTPException(502, f"remote MQ server unreachable: {e}")
