# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Import tag JSON files (one per accession, same schema as tag.py's Tags) into the db.
usage: uv run tag_import.py data/tags_json/ --model "claude-sonnet-5 (claude-code session)"
"""
import argparse, datetime as dt, json, pathlib, sqlite3, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from tag import Tags, DB, VOCAB  # noqa: E402

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("dir"); ap.add_argument("--model", default="claude-sonnet-5")
    a = ap.parse_args(); con = sqlite3.connect(DB); n = bad = 0
    for f in sorted(pathlib.Path(a.dir).glob("PXD*.json")):
        acc = f.stem
        try:
            t = Tags.model_validate_json(f.read_text()); d = t.model_dump()
            for k, allowed in VOCAB.items():
                if d[k]["value"] is not None and d[k]["value"] not in allowed:
                    raise ValueError(f"{k}={d[k]['value']!r} not in vocab")
        except Exception as e:
            print(f"{acc}: {e}", file=sys.stderr); bad += 1; continue
        con.execute("INSERT OR REPLACE INTO tags VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (acc, d["organism"]["value"], d["taxon_id"], d["sample_type"]["value"], d["tissue"]["value"], d["cell_line"]["value"],
             d["disease"]["value"], d["enrichment"]["value"], d["acquisition"]["value"], d["labelling"]["value"], d["enzyme"]["value"],
             d["fractionation"]["value"], d["instrument_family"]["value"],
             int(d["depth_proteins"]["value"]) if (d["depth_proteins"]["value"] or "").isdigit() else None,
             d["search_engine"]["value"], d["summary_one_line"], a.model, dt.datetime.now(dt.UTC).isoformat(), None, None, json.dumps(d)))
        con.execute("DELETE FROM tag_evidence WHERE accession=?", (acc,))
        for k, v in d.items():
            if isinstance(v, dict) and "value" in v:
                con.execute("INSERT INTO tag_evidence VALUES (?,?,?,?,?,?)", (acc, k, v["value"], v["confidence"], v["source"], v["evidence"]))
        n += 1
    con.commit(); print(f"imported={n} rejected={bad}")

if __name__ == "__main__":
    main()
