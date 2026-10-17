# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Load data/demo/*.jsonl into data/pride.db so the API/UI run without crawling PRIDE. Safe to re-run."""
import gzip
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from build_db import DB, SCHEMA  # noqa: E402

ROOT = pathlib.Path(__file__).parent
DEMO = ROOT / "data" / "demo"


def load(con, name):
    """Read data/demo/<name>*.jsonl and *.jsonl.gz (sharded by export_demo.py)."""
    n = 0
    for f in sorted(DEMO.glob(f"{name}*.jsonl*")):
        opener = gzip.open if f.suffix == ".gz" else open
        with opener(f, "rt") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        if not rows:
            continue
        cols = list(rows[0].keys())
        con.executemany(f"INSERT OR REPLACE INTO {name} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [[r.get(c) for c in cols] for r in rows])
        n += len(rows)
    return n


def main():
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB, timeout=120)
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
