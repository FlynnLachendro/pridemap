# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Load data/demo/*.jsonl into data/pride.db so the API/UI run without crawling PRIDE. Safe to re-run."""
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from build_db import DB, SCHEMA  # noqa: E402

ROOT = pathlib.Path(__file__).parent
DEMO = ROOT / "data" / "demo"


def load(con, name):
    rows = [json.loads(l) for l in open(DEMO / f"{name}.jsonl") if l.strip()]
    if not rows:
        return 0
    cols = list(rows[0].keys())
    con.executemany(f"INSERT OR REPLACE INTO {name} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [[r.get(c) for c in cols] for r in rows])
    return len(rows)


def main():
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    for name in ["projects", "papers", "tags", "tag_evidence"]:
        print(name, load(con, name))
    con.execute("DELETE FROM projects_fts")
    con.execute("INSERT INTO projects_fts(accession,title,description,sample_protocol,data_protocol,keywords) "
                "SELECT accession,title,description,sample_protocol,data_protocol,keywords FROM projects")
    con.commit()
    print("ok →", DB)


if __name__ == "__main__":
    main()
