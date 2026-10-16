# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Pick a diverse slice of accessions to tag first → data/slice.txt.

Quotas are over the vocab we care about so the demo and the gold set see every value.
usage: uv run select_slice.py --n 300
"""
import argparse
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).parent
DB = ROOT / "data" / "pride.db"

BASE = "SELECT p.accession FROM projects p JOIN projects_fts f ON f.accession=p.accession WHERE p.pubmed_id IS NOT NULL AND p.publication_date >= '2014-01-01'"
QUOTAS = [
    # (share, fts match, extra sql)
    (0.22, "phospho*", ""),
    (0.12, '"data independent" OR DIA OR SWATH OR diaPASEF', ""),
    (0.10, "plasma OR serum", ""),
    (0.08, "TMT OR tandem NEAR mass NEAR tag", ""),
    (0.06, "SILAC", ""),
    (0.08, "immunoprecipitation OR \"affinity purification\" OR interactome OR BioID OR TurboID", ""),
    (0.06, "ubiquitin* OR glyco* OR acetyl*", ""),
    (0.08, "liver OR hepat*", ""),
    (0.20, None, ""),  # top downloads, anything
]


def main(n: int, out: pathlib.Path):
    con = sqlite3.connect(DB)
    chosen: list[str] = []
    seen = set()
    for share, match, extra in QUOTAS:
        k = max(1, round(n * share))
        q = BASE + (" AND projects_fts MATCH ?" if match else "") + extra + " ORDER BY p.organic_downloads DESC LIMIT ?"
        params = (match, k * 3) if match else (k * 3,)
        got = 0
        for (acc,) in con.execute(q, params):
            if acc in seen:
                continue
            seen.add(acc)
            chosen.append(acc)
            got += 1
            if got >= k:
                break
        print(f"{match or 'top'}: {got}")
    chosen = chosen[:n]
    out.write_text("\n".join(chosen) + "\n")
    print(f"wrote {len(chosen)} → {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--out", default=str(ROOT / "data" / "slice.txt"))
    a = ap.parse_args()
    main(a.n, pathlib.Path(a.out))
