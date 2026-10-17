# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic>=0.80", "pydantic>=2"]
# ///
"""Feed user corrections back into the tags.

1. Reads the corrections table (filled by the "wrong?" button → POST /dataset/{acc}/correct).
2. Writes gold/from_corrections.csv in the gold_50.csv column layout, only the corrected tags filled
   (so `uv run eval_tags.py gold/from_corrections.csv` scores the model against what users said).
3. Re-tags every corrected dataset with tag.py's prompt plus a system addendum pinning the corrections as ground
   truth, and writes the result into tags / tag_evidence with the model name suffixed "(corrected)".

usage:
  uv run apply_corrections.py --dry                 # print what it would do, no API calls, no writes
  uv run apply_corrections.py                       # csv + re-tag with claude-sonnet-5
  uv run apply_corrections.py --model claude-haiku-4-5 --accession PXD029247
Reads ANTHROPIC_API_KEY from .env / env (tag.py loads .env on import).
"""
import argparse
import csv
import datetime as dt
import json
import pathlib
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from tag import SYSTEM, Tags, load, render  # noqa: E402

DB = ROOT / "data" / "pride.db"
GOLD_DIR = ROOT / "gold"
OUT_CSV = GOLD_DIR / "from_corrections.csv"
TAGS = ["organism", "sample_type", "tissue", "cell_line", "disease", "enrichment", "acquisition", "labelling",
        "enzyme", "fractionation", "instrument_family", "search_engine", "depth_proteins"]
PRIDE_URL = "https://www.ebi.ac.uk/pride/archive/projects/"
ADDENDUM = ("A human reviewer corrected these tags for this dataset: {pairs}. "
            "Treat them as ground truth and re-derive the rest consistently.")


def corrections_by_accession(con) -> dict[str, dict[str, dict]]:
    """{accession: {tag: {value, note, created_at}}}; the newest correction per (accession, tag) wins."""
    out: dict[str, dict[str, dict]] = {}
    for r in con.execute("SELECT accession, tag, suggested_value, note, created_at FROM corrections ORDER BY id"):
        out.setdefault(r["accession"], {})[r["tag"]] = {"value": (r["suggested_value"] or "").strip(), "note": (r["note"] or "").strip(),
                                                        "created_at": r["created_at"]}
    return out


def addendum(corr: dict[str, dict]) -> str:
    pairs = []
    for tag, c in corr.items():
        val = c["value"] or "null"
        pairs.append(f"{tag}={val}" + (f" ({c['note']})" if c["note"] else ""))
    return ADDENDUM.format(pairs="; ".join(pairs))


def write_csv(con, by_acc: dict[str, dict[str, dict]], dry: bool) -> list[list[str]]:
    header = ["accession", "title", "pride_url", "paper_url"] + TAGS + [f"{t}_model" for t in TAGS] + ["notes"]
    rows = []
    for acc in sorted(by_acc):
        p = con.execute("SELECT title, pubmed_id FROM projects WHERE accession=?", (acc,)).fetchone()
        t = con.execute("SELECT * FROM tags WHERE accession=?", (acc,)).fetchone()
        corr = by_acc[acc]
        gold = [corr[tag]["value"] if tag in corr else "" for tag in TAGS]
        model = [("" if t is None or t[tag] is None else str(t[tag])) for tag in TAGS]
        notes = "; ".join(f"{tag}: {c['note']}" for tag, c in corr.items() if c["note"])
        extra = [f"{tag}={c['value']}" for tag, c in corr.items() if tag not in TAGS]  # e.g. summary_one_line
        if extra:
            notes = "; ".join([notes] + extra) if notes else "; ".join(extra)
        rows.append([acc, p["title"] if p else "", PRIDE_URL + acc,
                     f"https://europepmc.org/article/MED/{p['pubmed_id']}" if p and p["pubmed_id"] else ""] + gold + model + [notes])
    if not dry:
        GOLD_DIR.mkdir(exist_ok=True)
        with open(OUT_CSV, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
    return rows


def retag(con, client, model: str, p: dict, corr: dict[str, dict]):
    system = SYSTEM + "\n\n" + addendum(corr)
    msg = client.messages.parse(
        model=model, max_tokens=4000,
        system=[{"type": "text", "text": system}],
        messages=[{"role": "user", "content": render(p, p.get("methods_text"))}],
        output_format=Tags,
    )
    tags, usage = msg.parsed_output, msg.usage
    if tags is None:
        raise RuntimeError("no parsed output (refusal or empty response)")
    d = tags.model_dump()
    acc = p["accession"]
    con.execute(
        "INSERT OR REPLACE INTO tags VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (acc, d["organism"]["value"], d["taxon_id"], d["sample_type"]["value"], d["tissue"]["value"],
         d["cell_line"]["value"], d["disease"]["value"], d["enrichment"]["value"], d["acquisition"]["value"],
         d["labelling"]["value"], d["enzyme"]["value"], d["fractionation"]["value"], d["instrument_family"]["value"],
         int(d["depth_proteins"]["value"]) if (d["depth_proteins"]["value"] or "").isdigit() else None,
         d["search_engine"]["value"], d["summary_one_line"], f"{model} (corrected)", dt.datetime.now(dt.UTC).isoformat(),
         usage.input_tokens, usage.output_tokens, json.dumps(d)),
    )
    con.execute("DELETE FROM tag_evidence WHERE accession=?", (acc,))
    for k, v in d.items():
        if isinstance(v, dict) and "value" in v:
            con.execute("INSERT INTO tag_evidence VALUES (?,?,?,?,?,?)", (acc, k, v["value"], v["confidence"], v["source"], v["evidence"]))
    con.commit()
    return d, usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--accession", help="only this accession")
    ap.add_argument("--dry", action="store_true", help="print the csv rows and the prompt addenda; no API calls, no writes")
    a = ap.parse_args()

    con = sqlite3.connect(DB, check_same_thread=False, timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS corrections (id INTEGER PRIMARY KEY AUTOINCREMENT, accession TEXT, tag TEXT, suggested_value TEXT, "
                "note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    by_acc = corrections_by_accession(con)
    if a.accession:
        by_acc = {k: v for k, v in by_acc.items() if k == a.accession}
    n_corr = sum(len(v) for v in by_acc.values())
    print(f"corrections: {n_corr} across {len(by_acc)} datasets{' (dry run)' if a.dry else ''}")
    if not by_acc:
        return

    rows = write_csv(con, by_acc, a.dry)
    print(f"{'would write' if a.dry else 'wrote'} {OUT_CSV.relative_to(ROOT)} ({len(rows)} rows)")
    for r in rows:
        filled = {tag: val for tag, val in zip(TAGS, r[4:4 + len(TAGS)]) if val}
        print(f"  {r[0]}: {filled}" + (f"  notes: {r[-1]}" if r[-1] else ""))

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(sorted(by_acc)))
        acc_file = f.name
    projects = load(con, acc_file, 0, force=True)
    pathlib.Path(acc_file).unlink(missing_ok=True)
    missing = sorted(set(by_acc) - {p["accession"] for p in projects})
    if missing:
        print(f"not in projects table, skipped: {missing}")

    if a.dry:
        for p in projects:
            print(f"\n{p['accession']} would be re-tagged with {a.model} (corrected); system addendum:\n  {addendum(by_acc[p['accession']])}")
        return

    import anthropic
    client = anthropic.Anthropic()
    done = 0
    for p in projects:
        acc = p["accession"]
        try:
            d, usage = retag(con, client, a.model, p, by_acc[acc])
            done += 1
            changed = [f"{tag}={d[tag]['value']}" for tag in by_acc[acc] if tag in d and isinstance(d[tag], dict)]
            print(f"{acc} re-tagged ({usage.input_tokens} in / {usage.output_tokens} out): {', '.join(changed)} | {d['summary_one_line'][:80]!r}")
        except Exception as e:
            print(f"{acc} FAILED {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
    print(f"done={done}/{len(projects)}; now: uv run eval_tags.py {OUT_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
