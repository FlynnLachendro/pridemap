# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Write gold/gold_50.csv: 50 tagged accessions spanning the vocab, with the model's values in *_model
columns for reference and blank gold columns for Charlotte/Juri to fill. Also gold/README.md with the vocab.
usage: uv run make_gold_template.py --n 50
"""
import argparse
import csv
import pathlib
import random
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from tag import VOCAB  # noqa: E402

ROOT = pathlib.Path(__file__).parent
DB = ROOT / "data" / "pride.db"
TAGS = ["organism", "sample_type", "tissue", "cell_line", "disease", "enrichment", "acquisition", "labelling",
        "enzyme", "fractionation", "instrument_family", "search_engine", "depth_proteins"]


def main(n):
    random.seed(17)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT t.*, p.title, p.pubmed_id FROM tags t JOIN projects p USING(accession)")]
    # stratify: at least 3 per enrichment value and per acquisition value where available, then fill randomly
    chosen, seen = [], set()
    for key in ("enrichment", "acquisition", "labelling"):
        for val in VOCAB[key]:
            pool = [r for r in rows if r[key] == val and r["accession"] not in seen]
            random.shuffle(pool)
            for r in pool[:3]:
                chosen.append(r)
                seen.add(r["accession"])
    rest = [r for r in rows if r["accession"] not in seen]
    random.shuffle(rest)
    chosen += rest[: max(0, n - len(chosen))]
    chosen = chosen[:n]
    (ROOT / "gold").mkdir(exist_ok=True)
    with open(ROOT / "gold" / "gold_50.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["accession", "title", "pride_url", "paper_url"] + TAGS + [f"{t}_model" for t in TAGS] + ["notes"])
        for r in chosen:
            w.writerow([r["accession"], r["title"], f"https://www.ebi.ac.uk/pride/archive/projects/{r['accession']}",
                        f"https://europepmc.org/article/MED/{r['pubmed_id']}" if r["pubmed_id"] else ""]
                       + [""] * len(TAGS) + [r[t] for t in TAGS] + [""])
    (ROOT / "gold" / "README.md").write_text(
        "# Gold set\n\nFill the blank tag columns in gold_50.csv by reading the PRIDE page and the paper. "
        "Leave a cell empty if you can't tell. The *_model columns are what the model said; don't just copy them.\n\n"
        "Allowed values:\n\n" + "\n".join(f"- **{k}**: {', '.join(v)}" for k, v in VOCAB.items())
        + "\n- **organism**: NCBI scientific name\n- **tissue / cell_line / disease**: free text\n- **depth_proteins**: integer\n\n"
        "Then: `uv run eval_tags.py`\n"
    )
    print(f"wrote gold/gold_50.csv with {len(chosen)} rows")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    main(ap.parse_args().n)
