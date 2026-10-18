# MaxQuant on Modal — plan

Speeding up the "Run the search" card: move the MaxQuant search onto Modal so
the laptop can be shut down mid-run, and parallelise the raw-file download.

Companion to [maxquant-local.md](maxquant-local.md) (same search on your own
machine) and [maxquant-cloud-devin.md](maxquant-cloud-devin.md) (the existing
`MQ_SERVER_URL` remote path this plan implements a server for).

Status: **planned, not yet implemented.** Reviewed twice by the solution-architect
and platform-architect reviewers; all findings below are folded in.

---

## Context

Comparing two samples (e.g. `Normoxia_24_Female53` vs `Normoxia_24_Male65`) runs
the "Run the search" card: preflight → download raw files → fetch FASTA →
MaxQuant → hand the table back. Today every stage runs on the researcher's
laptop in Docker, driven by the generated `run_all.sh`. The laptop must stay
awake for the whole search — hours on a real dataset.

**Goal:** make that card faster, and let the laptop be closed, shut down, or go
offline mid-run with results fetched later.

**Scope:** the "Run the search" card only — the MaxQuant search stage and the
raw-file download stage. The analyse/volcano view, the frontend polling
interval, and the stats pipeline are out of scope and unchanged.

---

## ELI15

Right now the laptop is both the person asking the question *and* the person
doing the maths. If they go to sleep, the maths stops.

We want the laptop to *post the question* to a lab that never sleeps, get back a
ticket number, and go home. The lab does the maths on a much bigger machine.
Next morning — from any machine — the ticket number gets the answer.

Separately, the lab currently fetches its 24 reference books **one at a time**.
It should send four runners at once.

Three things make the "go home" part work, and they are exactly what the first
review round found missing:

1. **Post and walk away**, not stand and wait. (`.spawn()`, not `.remote()`)
2. **The ticket number must live somewhere that isn't the laptop.**
   (`modal.Dict`, not a Python variable in memory)
3. **The lab must not be told to stop after 5 minutes.** (Modal's default
   timeout is 300 s; a real MaxQuant run is hours.)

---

## The fact that makes the offload feasible

The kit zip the laptop sends to Modal holds **only** `mqpar.xml` and shell
scripts — a few KB. It does **not** hold raw files.

The raw files (1–5 GB each) are fetched **by the kit's own script, from inside
Modal, directly from PRIDE FTP**. They never cross the laptop's uplink. Modal's
datacentre pulls from EBI at datacentre speed.

This is why submitting is cheap, and why parallelising the download (Phase 1b)
pays off more once it runs on Modal's network rather than home wifi.

---

## What already exists (reuse, don't rebuild)

`api/omics.py` lines 2276–2487 already hold a complete remote-execution
**client**: it health-gates on `GET /health`, POSTs the kit zip to
`{MQ_SERVER_URL}/run`, polls `GET /status/{job_id}`, fetches
`GET /results/{job_id}`, and DELETEs `/job/{job_id}`. Auth is
`Authorization: Bearer $MQ_TOKEN` when set. The frontend already has the
local-vs-remote toggle (`RunHere` vs `RunRemote` in
`web/components/analyse-view.tsx`).

**So we write a server satisfying that existing contract.** No new client, no
new UI, no new endpoint shapes.

Modal also *removes* complexity we currently carry:

- Modal is x86-64, so the arm64 assembly-rebuild path at `omics.py:1242–1333`
  is never invoked. The stock
  `quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1` image is used directly.
- Bruker data works natively — no `--platform linux/amd64` emulation.
- No Docker-in-Docker. Setting `MAXQUANT_CMD=maxquant` makes `run_all.sh` take
  its **native** branch (`omics.py:1706–1708`) and skip Docker. In `preflight.sh`,
  `docker info` fails inside the container so `HAVE_DOCKER=0`, and the
  `MAXQUANT_CMD` branch at line 1467 sets `MQ_RUNNER=native`.

> **Unverified assumption:** that the bioconda package puts an executable
> literally named `maxquant` on `PATH`. The conda wrapper lives at
> `/opt/conda/bin/maxquant`. If it is actually named `MaxQuantCmd.exe`, then
> `command -v maxquant` fails, `preflight.sh` exits 1 with "MaxQuant not found",
> and the failure is confusing. Verification step 0 checks this before deploying.

---

## Phase 1a — Skateboard: MaxQuant runs on Modal, laptop closes the tab

**New file:** `api/modal_runner.py` — one Modal app, one worker, six endpoints.

### Module-level objects (all three required, or deploy fails)

```python
app  = modal.App("pridemap-maxquant")
vol  = modal.Volume.from_name("pridemap-runs", create_if_missing=True)
jobs = modal.Dict.from_name("pridemap-jobs",  create_if_missing=True)
secret = modal.Secret.from_name("pridemap-mq-token")   # provides MQ_TOKEN
```

### Image

```python
image = (
    modal.Image.from_registry(
        "quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1"
    )
    .apt_install("curl", "unzip", "bash")
    .run_commands("which maxquant")   # fail at BUILD time, not after a 2-min cold start
)
```

### Worker

```python
@app.function(
    image=image, volumes={"/data": vol}, secrets=[secret],
    cpu=16, memory=65536,   # 64 GB
    timeout=86400,          # 24 h — NOT the 300 s default
)
def run_maxquant(kit_bytes: bytes, accession: str) -> str: ...
```

**Storage split — raw files must NOT go on the Volume.** 24 × 1–5 GB = up to
120 GB; `vol.commit()` serialises the whole dirty set back to Modal's object
store, costing minutes and real money for data that is pure scratch.

- Unpack the kit and run the whole pipeline under container-local
  `/tmp/<job_id>/`, with `PRIDEMAP_RAW_DIR=/tmp/<job_id>/cache`. Discarded on
  container exit, no commit cost. This also removes the `ln -sf` symlink
  question entirely.
- Check Modal's ephemeral-disk allowance covers the dataset; raise the
  `ephemeral_disk` parameter if a large deposition exceeds it.
- The Volume holds **only** `/data/<job_id>/`: `progress.jsonl`, `run.log`, and
  the final `combined/txt/proteinGroups.txt`.
- A background shipper loop copies `progress.jsonl` + `run.log` into
  `/data/<job_id>/` and calls `vol.commit()` every ~15 s, so `/status` has
  something live to return. Final commit after `combined/txt/` is copied across.

### Endpoints

ASGI app on a function named **`web_app`**, so the deployed URL is predictable:
`https://<workspace>--pridemap-maxquant-web-app.modal.run`. That value is what
goes into `MQ_SERVER_URL`. Bearer token read from `os.environ["MQ_TOKEN"]`
(injected by `secret`); the ASGI function needs `secrets=[secret]` too.

| Endpoint | Behaviour |
|---|---|
| `GET /health` | `{"ok": true}` — satisfies the client's health gate |
| `POST /run` | multipart field `kit`; `run_maxquant.spawn(...)`; writes `jobs[accession] = call.object_id` **before** responding; returns `{"job_id": ...}` |
| `GET /status/{job_id}` | see below. Returns `{status, log}` |
| `GET /results/{job_id}` | `vol.reload()`, stream `combined/txt/proteinGroups.txt` |
| `DELETE /job/{job_id}` | cancel if live, drop the `jobs` entry, remove the Volume dir |
| `GET /jobs/{accession}` | *(Phase 2)* read `jobs` and return the job_id |

**Status polling must order its excepts correctly.**
`modal.FunctionCall.from_id(job_id).get(timeout=0)` is the right non-blocking
idiom, but while the function is still running it raises
`modal.exception.TimeoutError` — **not** Python's built-in `TimeoutError`.
Catch `modal.exception.TimeoutError` → `running` **first**, then a generic
`except Exception` → `error`. Inverting these reports every healthy running job
as failed.

`log` is read from the shipped `/data/<job_id>/run.log`. There is **no** Modal
SDK surface to fetch stdout from a spawned call — `modal.FunctionCall` has no
`.logs()` method, and `modal app logs` is CLI/dashboard only. The shipper loop
is what makes this field real; returning `""` would silently blank a field the
existing client renders.

**Deploy:** create the `pridemap-mq-token` Secret in the Modal dashboard, then
`modal deploy api/modal_runner.py` (not `modal serve`, which is dev-only). Set
`MQ_SERVER_URL` and `MQ_TOKEN` in `.env`. The existing client needs no changes.

**Value standalone:** the search runs on 16 cores / 64 GB instead of the laptop;
the fans stop; the browser tab can be closed and reopened mid-run.

**Known risk window until Phase 2 ships.** If the laptop's API *process* dies,
`_active_remote_jobs` (`omics.py:2281`) loses the accession→job_id mapping. The
`modal.Dict` entry still exists, but nothing reads it yet — so the job runs to
completion unreachable, billing 16 cores for up to 24 h. **Ship Phase 2 before
running real datasets,** or cancel orphans manually via the Modal dashboard.

---

## Phase 1b — Push scooter: parallel raw-file downloads

Independent of Phase 1a; ships and helps on its own, locally or on Modal.

**File:** `api/omics.py` → `_download_sh()`, lines 1221–1234.

Today the generator emits an inline `if have … else curl -C - … fi; ln -sf …`
block **per file**, run top to bottom. 24 files = 24 sequential transfers.

**Change:** emit instead

1. a `fetch_one()` shell function carrying the existing `have`-check,
   `curl -C -` resume and `ln -sf` steps unchanged,
2. a tab-separated manifest heredoc of `name<TAB>size<TAB>url`,
3. a wave loop running `${PRIDEMAP_DOWNLOAD_JOBS:-4}` at a time.

### Design obligations

- **macOS ships bash 3.2**, so `wait -n` is unavailable. Use the portable wave
  pattern: launch N background jobs, `wait`, launch the next N.
- **Error reporting is the trap.** `set -euo pipefail` is already on
  (line 1208). A bare `wait` after a failed background job aborts the script
  with no indication of *which* file failed — the exact failure mode this plan
  forbids. The portable fix: record each PID alongside its filename, run the
  blocking `wait || true` so `set -e` does not fire, then iterate
  `wait "$pid"` per recorded PID and report the filename of any non-zero one.
  An implementer who writes the wave loop and adds a bare `wait` will get the
  forbidden behaviour, so this boilerplate is mandatory, not optional.
- **Cache semantics must not regress:** complete files still skip, partial files
  still resume via `curl -C -`.
- `ln -sf` concurrency is safe — each call targets a distinct filename in a
  pre-existing directory, and `ln -sf` is atomic per filename.

**Value standalone:** download wall-clock drops roughly 4× wherever bandwidth
allows — on the laptop today, and more so on Modal's uplink after Phase 1a.

---

## Phase 2 — Bicycle: laptop fully powers off and recovers

The `modal.Dict` written in Phase 1a is already the durable record. Phase 2
teaches the laptop to read it instead of trusting its own memory, and closes the
orphan-cost window above.

- Add `GET /jobs/{accession}` to `modal_runner.py`, reading `jobs`.
- `pipeline_remote_active()` (`omics.py:2368`) currently returns
  `{"job_id": None}` the moment the in-process dict misses. Make it fall through
  to that endpoint before giving up. The in-process dict stays a fast cache; the
  Modal Dict becomes the source of truth.

**Value:** shut the laptop down entirely, come back next day or sit at a
different machine, open the dataset — the run is still going or finished and
fetchable. This is the stated hard requirement, met.

---

## Phase 3 — Motorbike: right-size the box

Only after 1a/1b/2 run on real data.

- `cpu=16, memory=65536` are starting guesses. Read peak usage from
  `modal app logs` and tune.
- Set `<numThreads>` in `mqpar.xml` to match the Modal `cpu` value — a mismatch
  means paying for cores MaxQuant never uses.
- Measure cold start: the biocontainers image is multi-GB, so the first run
  after idle spends 2–5 min pulling before MaxQuant starts. `keep_warm=1`
  removes it if demo reliability outweighs cost.

---

## Explicitly cut

**Splitting the two samples into two parallel MaxQuant jobs.** Looks like a free
2× and is not. MaxQuant's LFQ and iBAQ normalisation runs across all samples in
one process; splitting A and B normalises each independently, so their
abundances are no longer comparable — which is the entire point of the volcano
plot. It would be a faster wrong answer. Revisit only if a MaxQuant version
exposes partial runs preserving global normalisation.

Per-file parallelism *within* feature detection and MS/MS search is genuinely
independent, but MaxQuant 2.0.3.0 exposes no entry point for it.

---

## Files touched

| File | Phase | Change |
|---|---|---|
| `api/modal_runner.py` | 1a | New. Modal app, worker, `/health` `/run` `/status` `/results` `/job` |
| `api/omics.py` `_download_sh()` ~1221 | 1b | Sequential curl loop → N-way parallel wave |
| `api/modal_runner.py` | 2 | Add `GET /jobs/{accession}` |
| `api/omics.py` `pipeline_remote_active()` ~2368 | 2 | Fall back to Modal Dict on cache miss |

No frontend changes in any phase.

---

## Verification

**Phase 1a**

0. Pre-deploy sanity: `docker run --rm quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1 which maxquant`
   — grounds the `MAXQUANT_CMD=maxquant` assumption before anything is built.
1. Create the `pridemap-mq-token` Secret; `modal deploy api/modal_runner.py`;
   set `MQ_SERVER_URL` + `MQ_TOKEN`.
2. `GET /remote-mq` reports available (proves the health gate passes).
3. Run the 2-sample comparison via the remote toggle.
4. `modal app logs` shows the container pulling from PRIDE FTP directly; the
   laptop's network shows no multi-GB egress.
5. Close the browser tab mid-run, reopen — progress resumes, and `log` shows
   live `run.log` text (proves the shipper loop works).
6. `proteinGroups.txt` downloads and loads into the analyse view.
7. Confirm the Volume holds only logs plus `combined/txt/` — no raw files.

**Phase 1b**

8. Run `download_raw.sh` on a multi-file dataset; confirm 4 concurrent `curl`
   processes and a ~4× drop in the `download` stage `seconds` in `progress.jsonl`.
9. Re-run: cached files skip, no re-download.
10. Interrupt mid-download and re-run: partial files resume, not restart.
11. Point one manifest URL at a 404; confirm the error **names that file** and
    does not abort anonymously.

**Phase 2**

12. Start a run, kill the local API process entirely (simulated shutdown).
13. Restart it, open the dataset — the run is rediscovered via the Modal Dict
    and results are fetchable.

**Regression (all phases)**

14. Unset `MQ_SERVER_URL`; the local Docker path via `POST /pipeline/start`
    still works unchanged.

**Correctness guard**

15. Run the same comparison locally and on Modal; `proteinGroups.txt` LFQ
    intensities must match. Divergence means the Modal path altered the search,
    not just where it ran.
