# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pydantic>=2"]
# ///
"""Tag PRIDE datasets through any OpenAI-compatible provider (Hugging Face router, OpenRouter) into the tags table.

usage:
  uv run tag_oai.py --provider hf --limit 20000 --workers 16        # HF_TOKENS=hf_a,hf_b,... in .env (rotates when one runs dry)
  uv run tag_oai.py --provider openrouter --limit 5                 # OPENROUTER_API_KEY in .env
  uv run tag_oai.py --provider hf --accessions data/slice.txt --force
Resume-safe: skips accessions already in tags unless --force. Same prompt/schema as tag.py (Sonnet 5).
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import pathlib
import re
import sqlite3
import sys
import threading
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from tag import DB, SYSTEM, VOCAB, Tags, load, render  # noqa: E402  (loads .env)
from tag_bench import STRICT_SCHEMA, extract_json  # noqa: E402

PROVIDERS = {
    # HF bills at the provider's own price, no markup; DeepInfra is the cheapest route for this model ($0.20 / $0.60 per M).
    "hf": {"url": "https://router.huggingface.co/v1/chat/completions", "keys_env": "HF_TOKENS", "key_env": "HF_TOKEN",
           "model": "deepseek-ai/DeepSeek-V4.1-Flash:deepinfra", "price": (0.20, 0.60),
           "extra": {"chat_template_kwargs": {"thinking": False}}},
    "openrouter": {"url": "https://openrouter.ai/api/v1/chat/completions", "keys_env": "OPENROUTER_API_KEYS", "key_env": "OPENROUTER_API_KEY",
                   "model": "deepseek/deepseek-v4.1-flash", "price": (0.15, 0.60),
                   "extra": {"reasoning": {"enabled": False}}},
}
CREDIT_RE = re.compile(r"402|insufficient|credit|balance|quota|exceeded|payment", re.I)


class Keys:
    """Round-robin over several API keys; drop a key when the provider says it's out of credit."""

    def __init__(self, keys):
        self.keys = list(keys)
        self.i = 0
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if not self.keys:
                return None
            k = self.keys[self.i % len(self.keys)]
            self.i += 1
            return k

    def drop(self, k):
        with self.lock:
            if k in self.keys:
                self.keys.remove(k)
                print(f"!! key ...{k[-6:]} exhausted, {len(self.keys)} left", file=sys.stderr, flush=True)


def one_call(client, url, key, model, messages, use_schema, extra=None):
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 8000, **(extra or {})}
    if use_schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "tags", "strict": True, "schema": STRICT_SCHEMA}}
    r = client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    d = r.json()
    if "error" in d:
        raise RuntimeError(str(d["error"])[:300])
    msg = d["choices"][0]["message"]
    return msg.get("content") or "", d.get("usage") or {}


def tag_one(client, url, keys: Keys, model, p, state, extra=None):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": render(p, p.get("methods_text"))}]
    if not state["schema_ok"]:
        msgs[0]["content"] += "\n\nRespond with ONLY a JSON object matching this schema, no prose:\n" + json.dumps(Tags.model_json_schema())
    tin = tout = 0
    for attempt in range(3):
        key = keys.get()
        if key is None:
            raise RuntimeError("no API keys left with credit")
        try:
            content, usage = one_call(client, url, key, model, msgs, state["schema_ok"], extra if state["extra_ok"] else None)
        except RuntimeError as e:
            m = str(e)
            if CREDIT_RE.search(m) and "HTTP 4" in m:
                keys.drop(key)
                continue
            if state["extra_ok"] and extra and any(k in m for k in extra):  # provider rejects the no-thinking hint: drop it
                state["extra_ok"] = False
                continue
            if ("response_format" in m or "json_schema" in m) and state["schema_ok"]:
                state["schema_ok"] = False
                msgs[0]["content"] += "\n\nRespond with ONLY a JSON object matching this schema, no prose:\n" + json.dumps(Tags.model_json_schema())
                continue
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        tin += usage.get("prompt_tokens") or 0
        tout += usage.get("completion_tokens") or 0
        try:
            tags = Tags.model_validate(extract_json(content))
        except Exception as e:
            if attempt < 2:
                msgs += [{"role": "assistant", "content": content}, {"role": "user", "content": f"That was not valid for the schema ({str(e)[:200]}). Return only the corrected JSON object."}]
                continue
            raise
        bad = [(k, getattr(tags, k).value) for k, allowed in VOCAB.items() if getattr(tags, k).value is not None and getattr(tags, k).value not in allowed]
        if not bad:
            return tags, tin, tout
        if attempt < 2:
            fix = "; ".join(f"{k}={v!r} is not allowed, choose from {VOCAB[k]}" for k, v in bad)
            msgs += [{"role": "assistant", "content": content}, {"role": "user", "content": f"Fix these values and return the full corrected JSON only: {fix}"}]
            continue
        for k, v in bad:  # last resort: keep the record, mark the field as 'other' at low confidence
            t = getattr(tags, k)
            t.value = "other"
            t.confidence = min(t.confidence, 0.3)
            t.evidence = (f"[model said {v!r}] " + (t.evidence or ""))[:200]
        return tags, tin, tout
    raise RuntimeError("exhausted attempts")


def write(con, acc, d, model, tin, tout):
    con.execute(
        "INSERT OR REPLACE INTO tags VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (acc, d["organism"]["value"], d["taxon_id"], d["sample_type"]["value"], d["tissue"]["value"], d["cell_line"]["value"],
         d["disease"]["value"], d["enrichment"]["value"], d["acquisition"]["value"], d["labelling"]["value"], d["enzyme"]["value"],
         d["fractionation"]["value"], d["instrument_family"]["value"],
         int(d["depth_proteins"]["value"]) if (d["depth_proteins"]["value"] or "").isdigit() else None,
         d["search_engine"]["value"], d["summary_one_line"], model, dt.datetime.now(dt.UTC).isoformat(), tin, tout, json.dumps(d)))
    con.execute("DELETE FROM tag_evidence WHERE accession=?", (acc,))
    for k, v in d.items():
        if isinstance(v, dict) and "value" in v:
            con.execute("INSERT INTO tag_evidence VALUES (?,?,?,?,?,?)", (acc, k, v["value"], v["confidence"], v["source"], v["evidence"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=PROVIDERS, default="hf")
    ap.add_argument("--model")
    ap.add_argument("--accessions")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    prov = PROVIDERS[a.provider]
    model = a.model or prov["model"]
    # keys: HF_TOKENS (comma list) plus any HF_TOKENS2, HF_TOKENS3... so teammates' tokens can be pasted as separate lines
    raw = [v for k, v in os.environ.items() if k == prov["keys_env"] or (k.startswith(prov["keys_env"]) and k[len(prov["keys_env"]):].isdigit())]
    if not raw and os.environ.get(prov["key_env"]):
        raw = [os.environ[prov["key_env"]]]
    keys = list(dict.fromkeys(k.strip() for v in raw for k in v.split(",") if k.strip()))
    if not keys:
        sys.exit(f"set {prov['keys_env']} (comma-separated) or {prov['key_env']} in .env")
    keyring = Keys(keys)
    con = sqlite3.connect(DB, check_same_thread=False, timeout=120)
    rows = load(con, a.accessions, a.limit, a.force)
    print(f"{a.provider}/{model}: {len(rows)} to tag, {len(keys)} key(s), {a.workers} workers", flush=True)
    if not rows:
        return
    client = httpx.Client(timeout=180)
    state = {"schema_ok": True, "extra_ok": True}
    lock = threading.Lock()
    pin, pout = prov["price"]
    t0 = time.time()
    done = fails = tin_all = tout_all = 0

    def work(p):
        try:
            tags, tin, tout = tag_one(client, prov["url"], keyring, model, p, state, prov.get("extra"))
            return p["accession"], tags, tin, tout, None
        except Exception as e:
            return p["accession"], None, 0, 0, f"{type(e).__name__}: {e}"[:200]

    with cf.ThreadPoolExecutor(a.workers) as ex:
        for acc, tags, tin, tout, err in ex.map(work, rows):
            with lock:
                if err:
                    fails += 1
                    print(f"{acc} FAILED {err}", file=sys.stderr, flush=True)
                    if "no API keys left" in err:
                        break
                    continue
                write(con, acc, tags.model_dump(), f"{model} ({a.provider})", tin, tout)
                con.commit()
                done += 1
                tin_all += tin
                tout_all += tout
                if done % 50 == 0:
                    el = time.time() - t0
                    est = (tin_all * pin + tout_all * pout) / 1e6
                    print(f"{done}/{len(rows)} fails={fails} ~${est:.2f} {el/60:.1f} min ({done/el*60:.0f}/min, ~{len(rows)*el/done/60:.0f} min total)", flush=True)
    el = time.time() - t0
    print(f"done={done} fails={fails} tokens in={tin_all} out={tout_all} ~${(tin_all * pin + tout_all * pout) / 1e6:.2f} (list-price estimate) time={el/60:.1f} min")


if __name__ == "__main__":
    main()
