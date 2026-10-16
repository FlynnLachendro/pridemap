# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Export the tagged slice (projects + papers + tags + evidence) to data/demo/*.jsonl so the repo can run without a crawl."""
import json
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).parent
DB = ROOT / "data" / "pride.db"
OUT = ROOT / "data" / "demo"


def dump(con, name, sql, params=()):
    rows = [dict(r) for r in con.execute(sql, params)]
    with open(OUT / f"{name}.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"{name}: {len(rows)}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cols = [c[1] for c in con.execute("PRAGMA table_info(projects)") if c[1] != "raw_json"]
    dump(con, "projects", f"SELECT {','.join(cols)} FROM projects WHERE accession IN (SELECT accession FROM tags)")
    dump(con, "papers", "SELECT * FROM papers WHERE pubmed_id IN (SELECT pubmed_id FROM projects WHERE accession IN (SELECT accession FROM tags))")
    dump(con, "tags", "SELECT * FROM tags")
    dump(con, "tag_evidence", "SELECT * FROM tag_evidence")


if __name__ == "__main__":
    main()
