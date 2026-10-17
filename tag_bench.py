# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pydantic>=2"]
# ///
"""Benchmark tagging models via OpenRouter on the same N datasets, recording cost, speed and output.

usage:
  uv run tag_bench.py select --n 200                       # → data/bench.txt (tagged by Sonnet 5 via tag.py, with methods)
  uv run tag_bench.py run --model google/gemini-3.8-flash --workers 8
  uv run tag_bench.py report                               # agreement vs the Sonnet 5 reference, cost, speed
Results go to table tags_bench in data/pride.db.  Needs OPENROUTER_API_KEY in .env.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import pathlib
import re
import sqlite3
import statistics
import sys
import threading
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from tag import DB, SYSTEM, VOCAB, Tags, render  # noqa: E402  (loads .env)

ROOT = pathlib.Path(__file__).parent
BENCH = ROOT / "data" / "bench.txt"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
CLOSED = list(VOCAB.keys())
SCHEMA = Tags.model_json_schema()


def strictify(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}).keys())
        for v in node.values():
            strictify(v)
    elif isinstance(node, list):
        for v in node:
            strictify(v)
    return node


STRICT_SCHEMA = strictify(json.loads(json.dumps(SCHEMA)))


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS tags_bench (
        model TEXT, accession TEXT, ok INTEGER, error TEXT, raw_json TEXT,
        input_tokens INTEGER, output_tokens INTEGER, cost REAL, seconds REAL, created_at TEXT,
        PRIMARY KEY (model, accession))""")


def load_rows(con, accs):
    q = ("SELECT p.accession,p.title,p.description,p.sample_protocol,p.data_protocol,p.organisms,p.organism_parts,"
         "p.diseases,p.instruments,p.experiment_types,p.quant_methods,p.softwares,p.ptms,p.keywords,p.publication_date,"
         f"pp.methods_text FROM projects p LEFT JOIN papers pp ON pp.pubmed_id=p.pubmed_id WHERE p.accession IN ({','.join('?' * len(accs))})")
    cols = ["accession", "title", "description", "sample_protocol", "data_protocol", "organisms", "organism_parts",
            "diseases", "instruments", "experiment_types", "quant_methods", "softwares", "ptms", "keywords", "publication_date", "methods_text"]
    return [dict(zip(cols, r)) for r in con.execute(q, accs)]


def extract_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def call(client: httpx.Client, model: str, p: dict, use_schema=True):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": render(p, p.get("methods_text"))}],
        "temperature": 0,
        "max_tokens": 8000,
        "usage": {"include": True},
    }
    # extraction, not reasoning: keep the output budget for the JSON. Some endpoints (Gemini) refuse to disable it.
    body["reasoning"] = {"effort": "low"} if model.startswith("google/") else {"enabled": False}
    if use_schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "tags", "strict": True, "schema": STRICT_SCHEMA}}
    else:
        body["messages"][0]["content"] += "\n\nRespond with ONLY a JSON object matching this schema, no prose:\n" + json.dumps(SCHEMA)
    t = time.time()
    r = client.post(OR_URL, json=body)
    secs = time.time() - t
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    if "error" in d:
        raise RuntimeError(str(d["error"])[:200])
    msg = d["choices"][0]["message"]
    content = msg.get("content") or ""
    data = extract_json(content)
    tags = Tags.model_validate(data)
    for k, allowed in VOCAB.items():
        v = getattr(tags, k).value
        if v is not None and v not in allowed:
            raise ValueError(f"{k}={v!r} not in vocab")
    u = d.get("usage", {})
    return tags, u.get("prompt_tokens"), u.get("completion_tokens"), u.get("cost"), secs


def cmd_select(n):
    con = sqlite3.connect(DB, timeout=120)
    accs = [r[0] for r in con.execute(
        "SELECT t.accession FROM tags t JOIN projects p USING(accession) JOIN papers pp ON pp.pubmed_id=p.pubmed_id "
        "WHERE t.model='claude-sonnet-5' AND length(pp.methods_text) > 500 ORDER BY t.accession LIMIT ?", (n,))]
    BENCH.write_text("\n".join(accs) + "\n")
    print(f"wrote {len(accs)} → {BENCH}")


def cmd_run(model, workers, limit):
    key = os.environ.get("OPENROUTER_API_KEY")
    assert key, "OPENROUTER_API_KEY missing"
    con = sqlite3.connect(DB, check_same_thread=False, timeout=120)
    ensure_table(con)
    accs = [a for a in BENCH.read_text().split() if a]
    done = {r[0] for r in con.execute("SELECT accession FROM tags_bench WHERE model=? AND ok=1", (model,))}
    rows = [r for r in load_rows(con, accs) if r["accession"] not in done]
    if limit:
        rows = rows[:limit]
    print(f"{model}: {len(rows)} to do ({len(done)} already)", flush=True)
    client = httpx.Client(timeout=180, headers={"Authorization": f"Bearer {key}", "HTTP-Referer": "https://github.com/FlynnLachendro/pridemap", "X-Title": "pridemap bench"})
    lock = threading.Lock()
    state = {"schema_ok": True}
    t0 = time.time()
    n = fails = 0
    cost_total = 0.0

    def work(p):
        for attempt in range(2):
            try:
                use = state["schema_ok"]
                return p["accession"], call(client, model, p, use_schema=use), None
            except Exception as e:
                msg = str(e)
                if "response_format" in msg or "json_schema" in msg or "structured" in msg.lower():
                    state["schema_ok"] = False  # provider doesn't support it: fall back to prompt-only JSON
                    continue
                if attempt == 0 and ("429" in msg or "502" in msg or "503" in msg or "timed out" in msg.lower()):
                    time.sleep(5)
                    continue
                return p["accession"], None, msg[:300]
        return p["accession"], None, "gave up"

    with cf.ThreadPoolExecutor(workers) as ex:
        for acc, res, err in ex.map(work, rows):
            with lock:
                if err:
                    fails += 1
                    con.execute("INSERT OR REPLACE INTO tags_bench VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (model, acc, 0, err, None, None, None, None, None, dt.datetime.now(dt.UTC).isoformat()))
                    print(f"  {acc} FAILED {err[:120]}", file=sys.stderr, flush=True)
                else:
                    tags, tin, tout, cost, secs = res
                    cost_total += cost or 0
                    con.execute("INSERT OR REPLACE INTO tags_bench VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (model, acc, 1, None, tags.model_dump_json(), tin, tout, cost, secs, dt.datetime.now(dt.UTC).isoformat()))
                n += 1
                con.commit()
                if n % 20 == 0:
                    el = time.time() - t0
                    print(f"{model}: {n}/{len(rows)} fails={fails} ${cost_total:.3f} {el/60:.1f} min ({n/el*60:.0f}/min)", flush=True)
    el = time.time() - t0
    print(f"{model}: done={n} fails={fails} cost=${cost_total:.3f} time={el/60:.1f} min schema_mode={'json_schema' if state['schema_ok'] else 'prompt'}")


def norm(v):
    return (v or "").strip().lower()


def cmd_report():
    con = sqlite3.connect(DB, timeout=120)
    con.row_factory = sqlite3.Row
    ensure_table(con)
    accs = [a for a in BENCH.read_text().split() if a]
    ref = {r["accession"]: dict(r) for r in con.execute(f"SELECT * FROM tags WHERE accession IN ({','.join('?' * len(accs))})", accs)}
    models = [r[0] for r in con.execute("SELECT DISTINCT model FROM tags_bench")]
    print(f"reference: claude-sonnet-5 via tag.py on {len(accs)} datasets. Agreement = exact match on closed-vocab tags (organism: substring).\n")
    hdr = f"{'model':34} {'ok':>5} {'fail':>4} {'cost$':>7} {'$/1k':>6} {'s/item':>6} {'min':>5} | " + " ".join(f"{k[:7]:>7}" for k in ["organism"] + CLOSED) + " | avg  null%"
    print(hdr)
    out = {}
    for m in models:
        rows = [dict(r) for r in con.execute("SELECT * FROM tags_bench WHERE model=?", (m,))]
        ok = [r for r in rows if r["ok"]]
        cost = sum(r["cost"] or 0 for r in ok)
        secs = [r["seconds"] for r in ok if r["seconds"]]
        agree = {k: [0, 0] for k in ["organism"] + CLOSED}
        nulls = total = 0
        for r in ok:
            d = json.loads(r["raw_json"])
            rf = ref.get(r["accession"])
            if not rf:
                continue
            for k in ["organism"] + CLOSED:
                mv, rv = d[k]["value"], rf[k]
                total += 1
                nulls += mv is None
                if rv is None:
                    continue
                agree[k][1] += 1
                if k == "organism":
                    agree[k][0] += bool(mv) and (norm(mv) in norm(rv) or norm(rv) in norm(mv))
                else:
                    agree[k][0] += norm(mv) == norm(rv)
        accs_ = {k: (a / n if n else 0) for k, (a, n) in agree.items()}
        avg = statistics.mean(accs_.values()) if accs_ else 0
        per_item = statistics.median(secs) if secs else 0
        wall = (sum(secs) / 8 / 60) if secs else 0  # approx at 8 workers
        print(f"{m:34} {len(ok):5d} {len(rows)-len(ok):4d} {cost:7.3f} {cost/len(ok)*1000 if ok else 0:6.2f} {per_item:6.1f} {wall:5.1f} | "
              + " ".join(f"{accs_[k]*100:6.0f}%" for k in ["organism"] + CLOSED) + f" | {avg*100:4.0f}% {nulls/total*100 if total else 0:4.0f}%")
        out[m] = {"ok": len(ok), "fail": len(rows) - len(ok), "cost": cost, "median_seconds": per_item, "agreement": accs_, "avg_agreement": avg, "null_rate": nulls / total if total else None}
    (ROOT / "eval").mkdir(exist_ok=True)
    json.dump(out, open(ROOT / "eval" / "bench.json", "w"), indent=1)
    print("\n→ eval/bench.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select"); s.add_argument("--n", type=int, default=200)
    r = sub.add_parser("run"); r.add_argument("--model", required=True); r.add_argument("--workers", type=int, default=8); r.add_argument("--limit", type=int, default=0)
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "select":
        cmd_select(a.n)
    elif a.cmd == "run":
        cmd_run(a.model, a.workers, a.limit)
    else:
        cmd_report()
