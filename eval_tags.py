# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compare model tags against the hand-labelled gold set.

usage: uv run eval_tags.py [gold/gold_50.csv]
Gold CSV columns: accession, then any subset of the tag names. Empty cells are skipped (not counted).
Prints per-tag agreement and writes eval/report.json (also served by the About page if present).
"""
import csv
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).parent
DB = ROOT / "data" / "pride.db"
TAGS = ["organism", "sample_type", "tissue", "cell_line", "disease", "enrichment", "acquisition", "labelling",
        "enzyme", "fractionation", "instrument_family", "search_engine", "depth_proteins"]
FUZZY = {"organism", "tissue", "cell_line", "disease"}


def norm(v):
    if v is None:
        return ""
    return str(v).strip().lower().replace("-", "_").replace(" ", "_")


def same(tag, gold, model):
    g, m = norm(gold), norm(model)
    if tag in FUZZY:
        return g == m or (g and m and (g in m or m in g))
    if tag == "depth_proteins":
        try:
            g, m = int(float(g)), int(float(m))
            return abs(g - m) / max(g, 1) < 0.1
        except ValueError:
            return g == m
    return g == m


def main(path):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = list(csv.DictReader(open(path)))
    per = {t: {"n": 0, "agree": 0, "misses": []} for t in TAGS}
    missing = []
    for g in rows:
        acc = g["accession"].strip()
        t = con.execute("SELECT * FROM tags WHERE accession=?", (acc,)).fetchone()
        if not t:
            missing.append(acc)
            continue
        for tag in TAGS:
            gv = (g.get(tag) or "").strip()
            if not gv:
                continue
            per[tag]["n"] += 1
            if same(tag, gv, t[tag]):
                per[tag]["agree"] += 1
            else:
                per[tag]["misses"].append({"accession": acc, "gold": gv, "model": t[tag]})
    print(f"gold rows={len(rows)} untagged={len(missing)} {missing[:5]}")
    print(f"{'tag':18} {'n':>4} {'agree':>6} {'acc':>6}")
    tot_n = tot_a = 0
    out = {}
    for tag in TAGS:
        n, a = per[tag]["n"], per[tag]["agree"]
        if not n:
            continue
        tot_n += n
        tot_a += a
        out[tag] = {"n": n, "agree": a, "accuracy": round(a / n, 3), "misses": per[tag]["misses"]}
        print(f"{tag:18} {n:4d} {a:6d} {a / n:6.1%}")
    if tot_n:
        print(f"{'ALL':18} {tot_n:4d} {tot_a:6d} {tot_a / tot_n:6.1%}")
    (ROOT / "eval").mkdir(exist_ok=True)
    json.dump({"gold_file": str(path), "rows": len(rows), "overall": {"n": tot_n, "agree": tot_a, "accuracy": round(tot_a / tot_n, 3) if tot_n else None}, "per_tag": out},
              open(ROOT / "eval" / "report.json", "w"), indent=1)
    for tag in TAGS:
        for m in per[tag]["misses"][:3]:
            print(f"  miss {tag}: {m['accession']} gold={m['gold']!r} model={m['model']!r}")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "gold" / "gold_50.csv"))
