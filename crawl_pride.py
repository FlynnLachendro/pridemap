# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""Pull ALL PRIDE project metadata (not raw MS files) into data/raw/page_NNNN.json.
~42k projects, 100/page, ~0.5MB/page -> ~250MB total, ~25 min. Idempotent: skips existing pages."""
import json, sys, time, pathlib, httpx

OUT = pathlib.Path(__file__).parent / "data" / "raw"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://www.ebi.ac.uk/pride/ws/archive/v3"
SIZE = 100

with httpx.Client(timeout=60) as c:
    total = int(c.get(f"{BASE}/projects/count").text)
    pages = (total + SIZE - 1) // SIZE
    print(f"total={total} pages={pages}", flush=True)
    for p in range(pages):
        f = OUT / f"page_{p:04d}.json"
        if f.exists() and f.stat().st_size > 100:
            continue
        for attempt in range(5):
            try:
                r = c.get(f"{BASE}/projects", params={"page": p, "pageSize": SIZE})
                r.raise_for_status()
                data = r.json()
                f.write_text(json.dumps(data))
                print(f"page {p}/{pages} n={len(data)}", flush=True)
                break
            except Exception as e:
                print(f"page {p} attempt {attempt} failed: {e}", flush=True)
                time.sleep(2 * (attempt + 1))
        time.sleep(0.2)
print("done", flush=True)
