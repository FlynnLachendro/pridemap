"""omics router: OmicsDI cross-omics links, the quantms reanalysis kit, and the corrections feed. Included from main.py.

Helpers from main (pride_files, recipe, jl, TAG_COLS, PRIDE_URL) are imported lazily inside handlers: main imports this
module at load time, so a top-level import would be circular.
"""
import concurrent.futures as cf
import datetime as dt
import io
import json
import pathlib
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
import zipfile

from fastapi import APIRouter, HTTPException
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


def quantms_kit(accession: str) -> bytes:
    from main import TAG_COLS, jl, pride_files, recipe
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

    organism = t.get("organism") or (jl(p.get("organisms")) or [NA])[0]
    instruments = jl(p.get("instruments"))
    instrument = f"NT={instruments[0]}" if instruments else (f"NT={t['instrument_family']}" if t.get("instrument_family") else NA)
    enzyme = t.get("enzyme")
    cleavage = CLEAVAGE.get(enzyme or "", [NA])
    mods = []
    if r:
        for m in r["fixed_modifications"]["value"] + r["variable_modifications"]["value"]:
            if m in MODS:
                mods.append(MODS[m])
        mods += LABEL_MODS.get(t.get("labelling") or "", [])
    if not mods:
        mods = [NA]
    label, labelling_type = LABEL.get(t.get("labelling") or "", (NA, NA))
    prec_tol, frag_tol = TOLERANCE.get(t.get("instrument_family") or "", (NA, NA))

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
def dataset_quantms(accession: str):
    data = quantms_kit(accession)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{accession}_quantms.zip"'})


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
