# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""Pull the last 14 days of PRIDE projects into data/pride.db, then re-check every saved search and print one report
line per search.  run:  uv run check_alerts.py [--days 14] [--no-fetch] [--max-pages 10]

Meant for a cron / LaunchAgent (e.g. once a day at 07:00). Email delivery of the report is a follow-up; for now the
report goes to stdout, so pipe it wherever you like.

Steps
  1. Walk the PRIDE v3 /projects list from its last page backwards (the newest projects sit at the end; the endpoint
     ignores sort params) until a whole page is older than --days. Upsert each project with build_db.row
     (INSERT OR REPLACE, same JSON shape crawl_pride.py stores) and refresh just those rows in projects_fts.
     New projects are untagged until tag.py runs, so a saved search with method filters (enrichment, acquisition, ...)
     only sees them after tagging; keyword / organism / year searches see them straight away.
  2. For every saved search run the same check as GET /saved/{id}/check (matches with publication_date newer than the
     search's baseline). In-process when the API's deps are importable
     (uv run --with fastapi --with pydantic --with anthropic check_alerts.py), otherwise through the running API at
     $PRIDEMAP_API (default http://localhost:8000).
  3. Print:  <id>  <new>/<total>  <name>  [newest accessions]
"""
import argparse
import datetime as dt
import os
import pathlib
import sqlite3
import sys
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parent
DB = ROOT / "data" / "pride.db"
BASE = "https://www.ebi.ac.uk/pride/ws/archive/v3"
SIZE = 100
sys.path.insert(0, str(ROOT))
from build_db import row  # noqa: E402


def fetch_recent(con, days: int, max_pages: int) -> tuple[int, int]:
    """Upsert every project published in the last `days` days. Returns (projects seen, projects recent)."""
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    seen, recent, touched = 0, 0, []
    with httpx.Client(timeout=60) as c:
        total = int(c.get(f"{BASE}/projects/count").text)
        page, walked = (total - 1) // SIZE, 0  # /count overshoots the list by a few hundred, so the last pages can be empty
        while page >= 0 and walked < max_pages:
            data = None
            for attempt in range(4):
                try:
                    r = c.get(f"{BASE}/projects", params={"page": page, "pageSize": SIZE})
                    r.raise_for_status()
                    data = r.json()
                    break
                except Exception as e:
                    print(f"page {page} attempt {attempt} failed: {e}", file=sys.stderr)
                    time.sleep(2 * (attempt + 1))
            if data is None:
                break
            page -= 1
            if not data:
                continue  # trailing empty page: not counted
            walked += 1
            fresh = [p for p in data if p.get("accession") and (p.get("publicationDate") or "") >= cutoff]
            seen += len(data)
            recent += len(fresh)
            if fresh:
                con.executemany("INSERT OR REPLACE INTO projects VALUES (" + ",".join("?" * 24) + ")", [row(p) for p in fresh])
                touched += [p["accession"] for p in fresh]
            print(f"page {page + 1}: {len(data)} projects, {len(fresh)} within {days} days", flush=True)
            if not fresh:  # a full page with nothing recent: everything before it is older still
                break
            time.sleep(0.2)
    for i in range(0, len(touched), 500):
        sub = touched[i:i + 500]
        qs = ",".join("?" * len(sub))
        con.execute(f"DELETE FROM projects_fts WHERE accession IN ({qs})", sub)
        con.execute(
            "INSERT INTO projects_fts(accession,title,description,sample_protocol,data_protocol,keywords) "
            f"SELECT accession,title,description,sample_protocol,data_protocol,keywords FROM projects WHERE accession IN ({qs})", sub
        )
    con.commit()
    return seen, recent


def check_all(con) -> list[dict]:
    rows = [dict(r) for r in con.execute("SELECT * FROM saved_searches ORDER BY created_at")]
    if not rows:
        return []
    try:
        sys.path.insert(0, str(ROOT / "api"))
        from collections_api import check_saved  # needs fastapi/pydantic (+ anthropic via main.py)
        import main  # noqa: F401  (fails fast here, not inside check_saved)
        return [check_saved(con, r) for r in rows]
    except ImportError as e:
        api = os.environ.get("PRIDEMAP_API", "http://localhost:8000")
        print(f"api deps not importable ({e.name}); checking through {api}", file=sys.stderr)
        out = []
        with httpx.Client(base_url=api, timeout=300) as c:
            for r in rows:
                res = c.get(f"/saved/{r['id']}/check")
                res.raise_for_status()
                out.append(res.json())
        return out


def main_cli():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--max-pages", type=int, default=10, help="safety cap on pages walked back from the end")
    ap.add_argument("--no-fetch", action="store_true", help="skip the PRIDE fetch, only re-check saved searches")
    a = ap.parse_args()
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS saved_searches (id TEXT PRIMARY KEY, name TEXT, filters TEXT NOT NULL, url TEXT, total_at_save INTEGER, "
                "baseline_max_date TEXT, last_checked TEXT, new_count INTEGER DEFAULT 0, created_at TEXT)")
    if not a.no_fetch:
        seen, recent = fetch_recent(con, a.days, a.max_pages)
        print(f"fetched {seen} projects, upserted {recent} published since {a.days} days ago", flush=True)
    results = check_all(con)
    if not results:
        print("no saved searches")
    for r in results:
        newest = ", ".join(d["accession"] for d in r["new"][:5])
        print(f"{r['id']}  {r['new_count']}/{r['total']}  {r['name']}" + (f"  [{newest}]" if newest else ""))
    print(f"checked {len(results)} saved search(es) at {dt.datetime.now().isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main_cli()
