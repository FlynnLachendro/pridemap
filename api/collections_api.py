"""Collections (named, shareable shortlists) and saved searches with alerts. Included from main.py.

Owns two tables in data/pride.db, created on first use:
  collections(id, name, note, accessions JSON, created_at, updated_at)
  saved_searches(id, name, filters JSON, url, total_at_save, baseline_max_date, last_checked, new_count, created_at)

main.py imports this router at module end, so anything from main is imported lazily inside handlers.
"""
import datetime as dt
import inspect
import json
import pathlib
import secrets
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "pride.db"
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/l/i: ids get read aloud and typed
DOWNLOAD_CATEGORIES = ("RAW", "RESULT", "SEARCH", "PEAK")
SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, note TEXT, accessions TEXT NOT NULL,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS saved_searches (
  id TEXT PRIMARY KEY, name TEXT, filters TEXT NOT NULL, url TEXT, total_at_save INTEGER,
  baseline_max_date TEXT, last_checked TEXT, new_count INTEGER DEFAULT 0, created_at TEXT
);
"""
# browse params that describe the view, not the question
VIEW_KEYS = {"page", "size", "sort", "view", "ask", "tour", "facet_top"}

router = APIRouter()


def db():
    con = sqlite3.connect(DB, check_same_thread=False, timeout=60)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def now():
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def new_id():
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def clean_accessions(accs: list[str]) -> list[str]:
    out = []
    for a in accs:
        a = (a or "").strip().upper()
        if a and a not in out:
            out.append(a)
    return out


def human_gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


# ----------------------------------------------------------------------------- collections
class CollectionIn(BaseModel):
    name: str
    accessions: list[str]
    note: Optional[str] = None


class CollectionPatch(BaseModel):
    name: Optional[str] = None
    note: Optional[str] = None
    accessions: Optional[list[str]] = None


def get_collection(con, cid: str) -> dict:
    r = con.execute("SELECT * FROM collections WHERE id=?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "unknown collection")
    d = dict(r)
    d["accessions"] = json.loads(d["accessions"] or "[]")
    return d


def collection_datasets(con, accessions: list[str]) -> list[dict]:
    """projects + tags for the collection's accessions, in collection order; unknown accessions keep a stub row."""
    if not accessions:
        return []
    rows = {}
    for i in range(0, len(accessions), 900):
        sub = accessions[i:i + 900]
        for r in con.execute(
            "SELECT p.accession, p.title, p.publication_date, p.organic_downloads, t.summary_one_line, t.organism, t.tissue, "
            "t.enrichment, t.acquisition, t.labelling, t.accession IS NOT NULL AS tagged "
            f"FROM projects p LEFT JOIN tags t ON t.accession = p.accession WHERE p.accession IN ({','.join('?' * len(sub))})", sub
        ):
            rows[r["accession"]] = dict(r)
    out = []
    for a in accessions:
        r = rows.get(a)
        if not r:
            out.append({"accession": a, "title": None, "summary": None, "publication_date": None, "downloads": None, "tags": {}, "tagged": False, "known": False})
            continue
        out.append({
            "accession": a, "title": r["title"], "summary": r["summary_one_line"], "publication_date": r["publication_date"],
            "downloads": r["organic_downloads"], "tagged": bool(r["tagged"]), "known": True,
            "tags": {k: r[k] for k in ("organism", "tissue", "enrichment", "acquisition", "labelling")},
        })
    return out


@router.post("/collections")
def create_collection(body: CollectionIn):
    name = body.name.strip()
    accs = clean_accessions(body.accessions)
    if not name:
        raise HTTPException(400, "name required")
    if not accs:
        raise HTTPException(400, "at least one accession required")
    con = db()
    cid = new_id()
    while con.execute("SELECT 1 FROM collections WHERE id=?", (cid,)).fetchone():
        cid = new_id()
    t = now()
    con.execute("INSERT INTO collections VALUES (?,?,?,?,?,?)", (cid, name, (body.note or "").strip() or None, json.dumps(accs), t, t))
    con.commit()
    return {"id": cid, "name": name, "note": body.note, "accessions": accs, "n": len(accs), "created_at": t, "updated_at": t}


@router.get("/collections")
def list_collections():
    con = db()
    return [
        {"id": r["id"], "name": r["name"], "note": r["note"], "n": len(json.loads(r["accessions"] or "[]")), "created_at": r["created_at"], "updated_at": r["updated_at"]}
        for r in con.execute("SELECT * FROM collections ORDER BY updated_at DESC")
    ]


@router.get("/collections/{cid}")
def read_collection(cid: str):
    con = db()
    c = get_collection(con, cid)
    c["datasets"] = collection_datasets(con, c["accessions"])
    c["n"] = len(c["accessions"])
    return c


@router.patch("/collections/{cid}")
def patch_collection(cid: str, body: CollectionPatch):
    con = db()
    c = get_collection(con, cid)
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(400, "name required")
        c["name"] = body.name.strip()
    if body.note is not None:
        c["note"] = body.note.strip() or None
    if body.accessions is not None:
        c["accessions"] = clean_accessions(body.accessions)
    c["updated_at"] = now()
    con.execute("UPDATE collections SET name=?, note=?, accessions=?, updated_at=? WHERE id=?", (c["name"], c["note"], json.dumps(c["accessions"]), c["updated_at"], cid))
    con.commit()
    c["datasets"] = collection_datasets(con, c["accessions"])
    c["n"] = len(c["accessions"])
    return c


@router.delete("/collections/{cid}")
def delete_collection(cid: str):
    con = db()
    get_collection(con, cid)
    con.execute("DELETE FROM collections WHERE id=?", (cid,))
    con.commit()
    return {"ok": True}


@router.get("/collections/{cid}/accessions.txt", response_class=PlainTextResponse)
def collection_accessions(cid: str):
    c = get_collection(db(), cid)
    return "\n".join(c["accessions"]) + "\n"


@router.get("/collections/{cid}/export.sdrf.tsv", response_class=PlainTextResponse)
def collection_sdrf(cid: str):
    from main import PRIDE_URL, SDRF_MAP  # lazy: main imports this module at its end

    con = db()
    c = get_collection(con, cid)
    tags = {}
    for i in range(0, len(c["accessions"]), 900):
        sub = c["accessions"][i:i + 900]
        for r in con.execute(f"SELECT * FROM tags WHERE accession IN ({','.join('?' * len(sub))})", sub):
            tags[r["accession"]] = dict(r)
    cols = ["source name"] + list(SDRF_MAP.values()) + ["comment[pride accession]", "comment[summary]"]
    lines = ["\t".join(cols)]
    for a in c["accessions"]:
        t = tags.get(a, {})
        lines.append("\t".join([a] + [str(t.get(k) or "not available") for k in SDRF_MAP] + [PRIDE_URL + a, (t.get("summary_one_line") or "").replace("\t", " ")]))
    return "\n".join(lines) + "\n"


@router.get("/collections/{cid}/download.sh", response_class=PlainTextResponse)
def collection_download_script(cid: str):
    from main import PRIDE_URL, pride_files  # lazy

    con = db()
    c = get_collection(con, cid)
    out = [
        "#!/usr/bin/env bash",
        f"# PRIDE Map collection: {c['name']} ({cid})",
        f"# {len(c['accessions'])} dataset(s): {', '.join(c['accessions'])}",
        "#",
        "# Downloads every RAW, RESULT, SEARCH and PEAK file of each dataset from the PRIDE FTP into one folder per",
        "# accession, next to this script. Re-run to resume: wget -c skips finished files and continues partial ones.",
        "# Needs wget (or edit the loop to use: curl -C - -O). Check the per-dataset sizes below before you start.",
        f"# Generated {now()} by PRIDE Map.",
        "set -e",
        'cd "$(dirname "$0")"',
        "",
    ]
    grand_n, grand_bytes = 0, 0
    for a in c["accessions"]:
        files = [f for f in pride_files(con, a) if f.get("ftp") and (f.get("category") or "") in DOWNLOAD_CATEGORIES]
        per_cat = {}
        for f in files:
            per_cat[f["category"]] = per_cat.get(f["category"], 0) + 1
        size = sum(f.get("size_bytes") or 0 for f in files)
        grand_n += len(files)
        grand_bytes += size
        cats = ", ".join(f"{n} {k}" for k, n in sorted(per_cat.items())) or "no downloadable files found"
        out.append(f"# {a}: {len(files)} file(s), {human_gb(size)} ({cats})  {PRIDE_URL}{a}")
        if not files:
            out.append("")
            continue
        out.append(f'mkdir -p "{a}"')
        for f in files:
            out.append(f'wget -c -P "{a}" "{f["ftp"]}"')
        out.append("")
    out.insert(10, f"# Total: {grand_n} file(s), {human_gb(grand_bytes)}")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- saved searches + alerts
class SavedIn(BaseModel):
    name: Optional[str] = None
    filters: dict[str, Any]
    url: str
    total_at_save: int = 0


def saved_filters(raw: dict) -> dict:
    """keep only the browse params that describe the question"""
    return {k: v for k, v in raw.items() if k not in VIEW_KEYS and v not in (None, "", False)}


def describe(filters: dict) -> str:
    parts = [f"{k}: {v}" for k, v in filters.items() if k != "q"]
    if filters.get("q"):
        parts.insert(0, f'"{filters["q"]}"')
    return ", ".join(parts) or "everything"


def browse_matches(filters: dict, newer_than: Optional[str] = None, cap: int = 2000) -> tuple[int, list[dict]]:
    """Run main.browse for the saved filters (same results as the Browse page). Returns (total, newest rows newer than the
    date, oldest excluded); walks pages newest-first until a row is not newer or `cap` rows are gathered."""
    from main import browse  # lazy

    params = inspect.signature(browse).parameters
    allowed = {k: v for k, v in filters.items() if k in params and k not in VIEW_KEYS}
    rows, total, page = [], 0, 1
    while True:
        out = browse(**allowed, sort="date", page=page, size=100)
        total = out["total"]
        batch = out["results"]
        for r in batch:
            if newer_than is not None and not ((r.get("publication_date") or "") > newer_than):
                return total, rows
            rows.append({"accession": r["accession"], "title": r["title"], "publication_date": r.get("publication_date")})
            if len(rows) >= cap:
                return total, rows
        if len(batch) < 100 or newer_than is None:
            return total, rows
        page += 1


def row_out(r) -> dict:
    d = dict(r)
    d["filters"] = json.loads(d["filters"] or "{}")
    d["summary"] = describe(d["filters"])
    return d


def check_saved(con, r) -> dict:
    """The /saved/{id}/check logic as a plain function so check_alerts.py can call it in-process."""
    d = row_out(r)
    total, new = browse_matches(d["filters"], newer_than=d["baseline_max_date"] or "")
    t = now()
    con.execute("UPDATE saved_searches SET new_count=?, last_checked=? WHERE id=?", (len(new), t, d["id"]))
    con.commit()
    return {"id": d["id"], "name": d["name"], "summary": d["summary"], "total": total, "new_count": len(new), "new": new[:10], "last_checked": t, "baseline_max_date": d["baseline_max_date"]}


@router.post("/saved")
def create_saved(body: SavedIn):
    filters = saved_filters(body.filters)
    con = db()
    sid = new_id()
    while con.execute("SELECT 1 FROM saved_searches WHERE id=?", (sid,)).fetchone():
        sid = new_id()
    total, rows = browse_matches(filters, newer_than=None)  # newest page only: max date is on it
    baseline = max((r["publication_date"] or "" for r in rows), default="") or dt.date.today().isoformat()
    t = now()
    name = (body.name or "").strip() or describe(filters)
    con.execute("INSERT INTO saved_searches VALUES (?,?,?,?,?,?,?,?,?)", (sid, name, json.dumps(filters), body.url, body.total_at_save or total, baseline, t, 0, t))
    con.commit()
    return row_out(con.execute("SELECT * FROM saved_searches WHERE id=?", (sid,)).fetchone())


@router.get("/saved")
def list_saved():
    con = db()
    return [row_out(r) for r in con.execute("SELECT * FROM saved_searches ORDER BY created_at DESC")]


@router.get("/saved/{sid}/check")
def check_saved_route(sid: str):
    con = db()
    r = con.execute("SELECT * FROM saved_searches WHERE id=?", (sid,)).fetchone()
    if not r:
        raise HTTPException(404, "unknown saved search")
    return check_saved(con, r)


@router.post("/saved/{sid}/seen")
def seen_saved(sid: str):
    con = db()
    if not con.execute("SELECT 1 FROM saved_searches WHERE id=?", (sid,)).fetchone():
        raise HTTPException(404, "unknown saved search")
    t = now()
    con.execute("UPDATE saved_searches SET baseline_max_date=?, new_count=0, last_checked=? WHERE id=?", (dt.date.today().isoformat(), t, sid))
    con.commit()
    return row_out(con.execute("SELECT * FROM saved_searches WHERE id=?", (sid,)).fetchone())


@router.delete("/saved/{sid}")
def delete_saved(sid: str):
    con = db()
    if not con.execute("SELECT 1 FROM saved_searches WHERE id=?", (sid,)).fetchone():
        raise HTTPException(404, "unknown saved search")
    con.execute("DELETE FROM saved_searches WHERE id=?", (sid,))
    con.commit()
    return {"ok": True}
