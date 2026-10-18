# Running MaxQuant on a cloud Linux machine via Devin

PRIDE Map can offload MaxQuant searches to a remote Linux machine — avoiding the need to install
anything locally, bypassing arm64 / emulation constraints, and keeping long jobs alive when you
close your laptop. The remote side is managed by [Devin](https://devin.ai).

## How it works

```
Local machine                         Cloud Linux (Devin)
──────────────────────────────────    ──────────────────────────────────────────────
PRIDE Map UI                          mq_server.py (port 8123)
  │                                         │
  │  POST /dataset/{acc}/pipeline/remote    │  POST /run       ← kit.zip upload
  │  GET  …/remote/{id}/status             │  GET  /status/{id}
  │  GET  …/remote/{id}/results            │  GET  /results/{id}
  └──► PRIDE Map API (localhost:8000) ──►  └──► via Cloudflare tunnel (HTTPS)
```

1. PRIDE Map API bundles the search kit (scripts + MaxQuant XML configs) as a `kit.zip`.
2. It POSTs the zip to `MQ_SERVER_URL/run` with `Authorization: Bearer $MQ_TOKEN`.
3. Devin's `mq_server.py` extracts the kit, runs `run_all.sh` inside Docker, and streams stdout to
   a log buffer.
4. PRIDE Map polls `/status/{job_id}` every 3 seconds and shows live output in the UI.
5. On completion, the proteinGroups table is fetched and loaded into the Analyse tab automatically.

## Prerequisites on the cloud machine

- Linux x86-64 (Devin's default environment)
- Docker Engine installed and the current user in the `docker` group
- Python 3.10+ with `fastapi` and `uvicorn` (`pip install fastapi uvicorn python-multipart`)
- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
  (`cloudflared`) — or any other tool that exposes an HTTP port over HTTPS (ngrok, bore, etc.)

## Step-by-step setup

### 1. Start the mq_server on the Devin machine

Save the server script below as `mq_server.py` and start it:

```bash
uvicorn mq_server:app --host 0.0.0.0 --port 8123
```

The server implements four endpoints that PRIDE Map calls:

| Method | Path | Does |
|--------|------|------|
| `POST` | `/run` | Accepts `kit` (multipart zip), extracts it, runs `bash run_all.sh` in the background, returns `{"job_id": "..."}` |
| `GET` | `/status/{job_id}` | Returns `{"status": "running"\|"done"\|"error", "log": "<stdout so far>"}` |
| `GET` | `/results/{job_id}` | Streams `combined/txt/proteinGroups.txt` from the finished job |
| `DELETE` | `/job/{job_id}` | Removes the job's working directory |

> **Security**: Set `MQ_TOKEN` to a random string (e.g. `openssl rand -hex 16`) and the server
> checks it on every request via `Authorization: Bearer <token>`. Never expose the server without
> a token.

<details>
<summary>mq_server.py — minimal reference implementation</summary>

```python
"""Minimal MaxQuant job runner. Run with: uvicorn mq_server:app --port 8123"""
import os, subprocess, threading, uuid, zipfile, tempfile, shutil
from fastapi import FastAPI, UploadFile, File, Header, HTTPException
from fastapi.responses import FileResponse

app = FastAPI()
TOKEN = os.environ.get("MQ_TOKEN", "")
JOBS: dict[str, dict] = {}  # job_id -> {status, log, dir}

def _check(authorization: str | None = None):
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "Unauthorized")

def _run(job_id: str, kit_dir: str):
    proc = subprocess.Popen(
        ["bash", "run_all.sh"],
        cwd=kit_dir,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    log_lines = []
    for line in proc.stdout:
        log_lines.append(line.rstrip())
        JOBS[job_id]["log"] = "\n".join(log_lines[-2000:])
    proc.wait()
    JOBS[job_id]["status"] = "done" if proc.returncode == 0 else "error"

@app.post("/run")
async def run(kit: UploadFile = File(...), authorization: str | None = Header(default=None)):
    _check(authorization)
    job_id = str(uuid.uuid4())
    kit_dir = tempfile.mkdtemp(prefix=f"mq_{job_id}_")
    data = await kit.read()
    zip_path = os.path.join(kit_dir, "kit.zip")
    with open(zip_path, "wb") as f:
        f.write(data)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(kit_dir)
    # kit may be wrapped in a subdirectory
    subdirs = [d for d in os.listdir(kit_dir) if os.path.isdir(os.path.join(kit_dir, d))]
    work_dir = os.path.join(kit_dir, subdirs[0]) if len(subdirs) == 1 else kit_dir
    JOBS[job_id] = {"status": "running", "log": "", "dir": work_dir}
    threading.Thread(target=_run, args=(job_id, work_dir), daemon=True).start()
    return {"job_id": job_id}

@app.get("/status/{job_id}")
def status(job_id: str, authorization: str | None = Header(default=None)):
    _check(authorization)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return {"status": job["status"], "log": job["log"]}

@app.get("/results/{job_id}")
def results(job_id: str, authorization: str | None = Header(default=None)):
    _check(authorization)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    path = os.path.join(job["dir"], "combined", "txt", "proteinGroups.txt")
    if not os.path.exists(path):
        raise HTTPException(404, "Results not ready")
    return FileResponse(path, media_type="text/tab-separated-values",
                        filename="proteinGroups.txt")

@app.delete("/job/{job_id}")
def delete_job(job_id: str, authorization: str | None = Header(default=None)):
    _check(authorization)
    job = JOBS.pop(job_id, None)
    if job:
        parent = os.path.dirname(job["dir"])
        shutil.rmtree(parent, ignore_errors=True)
    return {"deleted": job_id}
```

</details>

### 2. Expose it over HTTPS with Cloudflare Tunnel

```bash
# One-off: install cloudflared
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
  https://pkg.cloudflare.com/cloudflared any main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared

# Start a temporary tunnel — no account needed
cloudflared tunnel --url http://localhost:8123
```

Cloudflare prints a URL like:

```
https://invoice-tape-decades-dark.trycloudflare.com
```

Copy this URL — it is your `MQ_SERVER_URL`.

> **Note**: `trycloudflare.com` tunnels are ephemeral; the URL changes every restart. For a
> stable URL, create a named tunnel with a Cloudflare account:
> `cloudflared tunnel create pridemap-mq` and follow the [Cloudflare docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).

### 3. Generate a token

```bash
# On the Devin machine
export MQ_TOKEN=$(openssl rand -hex 16)
echo "MQ_TOKEN=$MQ_TOKEN"
```

Start (or restart) `mq_server.py` with the token in the environment:

```bash
MQ_TOKEN=<your-token> uvicorn mq_server:app --host 0.0.0.0 --port 8123
```

### 4. Configure PRIDE Map locally

Add both values to `.env` in the repo root:

```dotenv
MQ_SERVER_URL=https://invoice-tape-decades-dark.trycloudflare.com
MQ_TOKEN=<your-token>
```

Restart the PRIDE Map API (`uv run api/main.py`) so it picks up the new env vars. The **Reanalyse**
page will now show a **"Remote MaxQuant connected"** badge and a **Run on cloud** button on
datasets where MaxQuant is the recommended engine.

## Verifying the connection

```bash
# From your local machine — should return {"available": true}
curl http://localhost:8000/remote-mq

# Direct health-check against the Devin machine
curl -H "Authorization: Bearer $MQ_TOKEN" $MQ_SERVER_URL/status/nonexistent
# → 404 {"detail":"Unknown job"}  (proves the server is reachable and auth works)
```

## Teardown

Unset `MQ_SERVER_URL` in `.env` and restart the API. The **Run on cloud** card disappears from the
UI. On the Devin machine, stop `uvicorn` and `cloudflared`.

## Architecture decisions

| Decision | Reason |
|---|---|
| Server on port 8123 | Arbitrary; chosen to avoid clashing with PRIDE Map's API (8000) and Next.js (3000) |
| Bearer token auth | Simple, stateless, works through Cloudflare without extra config |
| Streaming log via polling | The mq_server buffers the last 2,000 lines; the UI polls every 3 s. No WebSocket needed |
| Job state in memory | Jobs are ephemeral (one run, one result file). Restarting the server loses in-flight jobs — reconnect by re-submitting |
| Cloudflare Tunnel | Free for `trycloudflare.com`, no firewall changes, terminates TLS. Named tunnels add a stable domain |
