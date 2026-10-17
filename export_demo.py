# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Export every tagged dataset (projects + papers + tags + evidence) to data/demo/*.jsonl.gz so a fresh clone runs
without crawling PRIDE or tagging. Files are gzipped and split into <40 MB shards (GitHub's per-file limit is 100 MB).
Safe to re-run; it deletes the old shards first. Load with `uv run load_demo.py`."""
import gzip
import json
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).parent
DB = ROOT / "data" / "pride.db"
OUT = ROOT / "data" / "demo"
SHARD_BYTES = 40 * 1024 * 1024   # uncompressed bytes per shard (gzips to ~8-10 MB)


def dump(con, name, sql, params=()):
    for old in OUT.glob(f"{name}*.jsonl*"):
        old.unlink()
    n = shard = size = 0
    f = None
    for r in con.execute(sql, params):
        line = (json.dumps(dict(r)) + "\n").encode()
        if f is None or size + len(line) > SHARD_BYTES:
            if f: f.close()
            shard += 1; size = 0
            f = gzip.open(OUT / f"{name}.{shard:02d}.jsonl.gz", "wb", compresslevel=6)
        f.write(line); size += len(line); n += 1
    if f: f.close()
    print(f"{name}: {n} rows in {shard} shard(s)")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=120)
    con.row_factory = sqlite3.Row
    cols = [c[1] for c in con.execute("PRAGMA table_info(projects)") if c[1] != "raw_json"]
    dump(con, "projects", f"SELECT {','.join(cols)} FROM projects WHERE accession IN (SELECT accession FROM tags) ORDER BY accession")
    dump(con, "papers", "SELECT * FROM papers WHERE pubmed_id IN (SELECT pubmed_id FROM projects WHERE accession IN (SELECT accession FROM tags)) ORDER BY pubmed_id")
    dump(con, "tags", "SELECT * FROM tags ORDER BY accession")
    dump(con, "tag_evidence", "SELECT * FROM tag_evidence ORDER BY accession, tag")


if __name__ == "__main__":
    main()
