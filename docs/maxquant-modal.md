# Running MaxQuant on Modal

PRIDE Map can offload MaxQuant searches to [Modal](https://modal.com), a
serverless container platform. The appeal over the other two paths: the search
runs on a dedicated 8-core container rather than whatever your laptop can spare, the
laptop can be **shut down mid-run** with results collected later from any
machine, and because Modal is x86-64 the arm64 workarounds that
[maxquant-local.md](maxquant-local.md) carries simply do not apply — Bruker data
included.

A finished search is **cached by the content of its inputs**, so submitting the same
raw files with the same search parameters returns the earlier tables in seconds
instead of repeating the work. See [Result cache](#result-cache).

> ### STATUS: Phases 1a, 1b and 2 are built; not yet deployed
>
> `api/modal_runner.py` exists and implements Phase 1a and the Phase 2
> `GET /jobs/{accession}` endpoint; `api/omics.py` carries the Phase 1b parallel
> downloader and the Phase 2 Dict fallback. Nobody has run
> [Step-by-step setup](#step-by-step-setup) against a real Modal account yet, so
> the [Implementation verification](#implementation-verification) checklist is
> unticked — treat the first deploy as the proving run.
>
> Phase 3 (right-sizing the box) is deliberately still open; it needs usage
> numbers from a real run.
>
> Alternatives that are already proven: [maxquant-local.md](maxquant-local.md)



## How it works

```
Your machine                          Modal (serverless, x86-64)
──────────────────────────────────    ──────────────────────────────────────────────
PRIDE Map UI                          pridemap-maxquant app
  │                                         │
  │  POST /dataset/{acc}/pipeline/remote    │  POST /run?force=    ← kit.zip upload (KB)
  │  GET  …/remote/{id}/status             │  GET  /status/{id}   → status, log, stages, cpu
  │  GET  …/remote/{id}/results            │  GET  /results/{id}
  └──► PRIDE Map API (localhost:8000) ──►  └──► https://<workspace>--pridemap-mq.modal.run
                                                  │
                                                  ├─ run_maxquant       1 CPU   orchestrator
                                                  │    ├─ _download_raw  1 CPU   ──► PRIDE FTP (GB)
                                                  │    │                         ──► Volume: pridemap-raw
                                                  │    └─ _run_maxquant_search  8 CPU / 20 GB
                                                  │         stages raw → local SSD, then searches
                                                  │                         ──► Volume: pridemap-runs
                                                  │                              /<job_id>/  log, stages
                                                  │                              /cache/search/<key>/
                                                  └──► pridemap-janitor app (6-hourly, separate)
```

1. PRIDE Map API bundles the search kit (scripts + MaxQuant XML configs) as a
  `kit.zip` — a few KB. **Raw files are not in it.**
2. It POSTs the zip to `MQ_SERVER_URL/run` with `Authorization: Bearer $MQ_TOKEN`.
3. Modal spawns a lightweight orchestrator, which sequences a cheap 1-CPU download
  worker and then the 8-CPU search worker. The download worker fetches the raw files
  **directly from PRIDE FTP at datacentre speed** into a shared Volume, so the files
  never cross your uplink and a second run of the same dataset skips the download
  entirely. The search worker copies that run's raw files onto container-local SSD
  before starting MaxQuant — see the storage split below for why that is worth a
  factor of six — and runs `run_all.sh`. If the same inputs and parameters have been
  searched before, it returns the cached tables instead.
4. The container writes its log and `run_all.sh`'s stage events to a Volume; PRIDE Map polls
  `/status/{job_id}` every 3 seconds and shows the same stage list, elapsed time and ETA a local
  run shows — the two cards render through one component, from one reduction of the same events.
5. On completion the protein table is fetched and loaded into the Analyse tab.

Because step 3 happens entirely inside Modal, **your machine can be closed or
powered off between steps 2 and 5.**

## Prerequisites

- A [Modal account](https://modal.com) — the free tier is enough to try this.
- The [Modal dashboard](https://modal.com/apps) is where apps, logs, secrets and
running jobs are managed. Worth opening now if you have not seen it before.
- `uv`, and this repo cloned.
- No Docker, no MaxQuant install, no particular CPU architecture.
- Having read the third-party terms for MaxQuant and the vendor readers it bundles, including the limits on publishing the worker image and on charging — see [Licence](../README.md#licence). You accept them in step 3.



## Result cache

A completed search is stored under a key derived from **the content of its inputs plus
the parameters that change the result**. Submit the same raw files with the same search
settings and the tables come back in seconds; MaxQuant is not run.

What the key covers:

- the **sha256 of every raw file** in the run, not name and size — a re-downloaded or
  repaired file can keep both;
- the search parameters from `mqpar.xml`: FASTA identity, taxonomy, enzymes, fixed and
  variable modifications, missed cleavages, both FDR thresholds, `minPepLen`,
  `lfqMode`, `matchBetweenRuns`, and the MS/MS tolerances.

What it deliberately ignores, because these differ on every run while describing
identical science: `filePaths`, `fixedCombinedFolder`, `fixedSearchFolder` (all carry
the per-container kit directory) and `numThreads` (rewritten at container start).
Hashing `mqpar.xml` verbatim would mean the cache never hit.

Two properties worth understanding:

- **Keyed on the whole input set, never per file.** MaxQuant's LFQ and iBAQ
  normalisation is global across a search, so a two-file result is not valid for any
  part of a three-file run. Caching per file and recombining would be a faster wrong
  answer.
- **An entry is valid only if its table is readable.** There is no "complete" flag that
  could disagree with disk. Persistent stage markers were tried earlier in this project
  and reverted for exactly that reason: the marker outlived the products it described,
  so the next run skipped work whose outputs no longer existed. Entries are published
  through a temporary directory and moved into place, and only after the run has
  produced a protein table.

A cache hit is written to the run log rather than applied silently — reusing scientific
output without saying so is how a stale number reaches a paper. To recompute anyway:

```bash
curl -X POST -H "Authorization: Bearer $MQ_TOKEN" \
     -F kit=@kit.zip "$MQ_SERVER_URL/run?force=1"
```

Entries live under `/data/cache/search/<key>/` on the `pridemap-runs` Volume with a
`manifest.json` recording the inputs, so an entry can always be traced back to the run
that produced it.

## Cost and limits

Read this before running the setup — the ceiling matters more than the steps.


|                             |                                                          |
| --------------------------- | -------------------------------------------------------- |
| Search worker               | `cpu=8`, `memory=20480` (20 GB), `ephemeral_disk=524288` (512 GiB), `timeout=86400` (24 h) |
| Download worker             | `cpu=1`, `memory=1024` (1 GB), `timeout=7200` (2 h)      |
| Worst case, one runaway job | **~$6–8** if it burns the full 24 h timeout              |
| Modal free tier             | roughly **$30/month** of compute credit                  |
| First deploy                | pulls a multi-GB image — **expect 2–5 minutes**          |
| Cold start after idle       | the same 2–5 min on the first search                     |


Verify both figures against [modal.com/pricing](https://modal.com/pricing); they
change. The point to internalise: **one runaway job eats about half the monthly
free credit, and two exhaust it.**

**Spotting and killing a runaway job.** All of this works from any machine
holding `MQ_SERVER_URL` and `MQ_TOKEN` — no Modal credentials, no dashboard:

```bash
# What is running?
curl -H "Authorization: Bearer $MQ_TOKEN" "$MQ_SERVER_URL/jobs"
# → {"jobs": [{"accession": "PXD055942", "job_id": "fc-…", "status": "running"}]}

# Kill one. A finished job's output tables are KEPT — add ?purge=1 to delete them too.
curl -X DELETE -H "Authorization: Bearer $MQ_TOKEN" "$MQ_SERVER_URL/job/fc-…"
# → {"deleted": true, "cancelled": true, "kept_results": false, "problems": []}
```

`kept_results: true` means the job had already produced `proteinGroups.txt`, so the
call cancelled it and released the registry slot but left the tables in place. This
is deliberate: the Stop button used to remove the whole job directory, and the first
search that ever ran to completion lost its protein table, log and resource
measurements that way — after which re-running looked necessary.

**What the container is actually using.** Each worker reports its own CPU, memory and
disk from its cgroup, so sizing comes from measurement rather than from what was
requested:

```bash
curl -H "Authorization: Bearer $MQ_TOKEN" "$MQ_SERVER_URL/status/fc-…" | jq .cpu
# → {"cores": 8, "host_cores": 24, "busy_cores": 7.5, "busy_pct": 94,
#    "peak_busy_cores": 9.65, "rss_gb": 10.7, "peak_rss_gb": 13.4,
#    "peak_disk_used_gb": 15.1, "phase": "search", "phases": {...}}

# Aggregate peaks across past jobs, with recommended constants.
curl -H "Authorization: Bearer $MQ_TOKEN" "$MQ_SERVER_URL/resource-advice"
```

Two things to know before acting on those numbers:

- `cores` is the **reservation**; `host_cores` is what `os.cpu_count()` sees, and the
  two differ — a `cpu=8` container reported 24. Percentages are against the
  reservation, and `mqpar.xml`'s `numThreads` is sized from it. `peak_busy_cores` can
  exceed the reservation because Modal allows bursting to request + 16 cores.
- `/resource-advice` only aggregates jobs whose reports survived, so a short or failed
  run can dominate it. Check `jobs_analysed` before applying anything it suggests.

**Read the response.** `cancelled: false`, or a non-empty `problems`, means the
compute may still be billing — fall back to the dashboard and confirm. A
`deleted: true` with an empty `problems` is the only answer that means stopped.

The credential-bearing routes remain available on a machine that has them:
Dashboard → Apps → `pridemap-maxquant` → Functions → cancel the invocation, or
`python3 -c "import modal; modal.FunctionCall.from_id('<job_id>').cancel()"`.
Prefer the HTTP route — needing `~/.modal.toml` to stop a job undoes the
property that lets any machine drive a run.

## Step-by-step setup



### 1. Modal SDK — no install step needed

`api/modal_runner.py` carries a [PEP 723](https://peps.python.org/pep-0723/) inline
dependency declaration:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["modal>=0.73"]
# ///
```

`uv run` reads this and pulls `modal` into an isolated ephemeral environment
automatically. The project has no `pyproject.toml`, so `uv add modal` **will
fail** — that command only works against a project lockfile. Use `uvx modal …`
everywhere below instead. Note that `uvx modal` is the correct invocation —
`uv run modal` has no project venv to find `modal` in and falls through to
pyenv, which says "modal: command not found". `uvx` (= `uv tool run`) downloads
and runs it in an isolated environment automatically.

`api/main.py`**'s dependency block must not gain** `modal`**.** The API talks to
Modal over plain HTTP via `urllib` (`api/omics.py:2276–2487`). Pulling the SDK
into the API would couple your laptop to Modal for no reason and undo the main
benefit of this path.

### 2. Authenticate (once per machine)

```bash
uvx modal setup      # opens a browser, writes ~/.modal.toml
```

On a headless machine or in CI there is no browser. Create a token in the
dashboard and export it instead:

```bash
export MODAL_TOKEN_ID=ak-...
export MODAL_TOKEN_SECRET=as-...
```

For unattended deploys, a [Service User](https://modal.com/docs/guide/service-users)
is the cleaner option. If you belong to more than one Modal workspace, confirm
the right one is active before deploying:

```bash
uvx modal profile list
```



### 3. Create the shared secret

This token is what stops strangers from spending your compute — see
[Security](#security--the-endpoint-is-public-by-default). The same secret carries your acceptance of
MaxQuant's licence: without `MAXQUANT_ACCEPT_LICENSE=1` every job fails before it downloads anything —
see [Licence](../README.md#licence).

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"   # generate one
uvx modal secret create pridemap-mq-token MQ_TOKEN=<that-token> MAXQUANT_ACCEPT_LICENSE=1
```

Already created the secret without it? Re-create it with both values and `--force`, then redeploy
(step 4) so no warm container keeps the old environment.



### 4. Deploy

```bash
uvx modal deploy api/modal_runner.py
```

Use `modal deploy`, not `modal serve`: serve is for local development and dies
when you Ctrl-C, while deploy creates the persistent endpoint this design needs.

**Copy the URL the command prints.** Do not construct it by hand — it embeds
your workspace slug. It will look like
`https://<workspace>--pridemap-mq.modal.run`.

Note that the gateway container stays warm for 30 minutes after the last request
(`scaledown_window=1800`), and a warm container keeps serving the code it started
with. After a deploy, `GET /health` reports a `source_sha` — compare it against the
local files before concluding a change is live:

```bash
curl "$MQ_SERVER_URL/health"    # → {"ok": true, "source_sha": "…", …}
```

### 5. Deploy the janitor (optional)

A second, separate app reclaims volume storage on a six-hourly schedule. It is
deliberately not part of `pridemap-maxquant`, so a broken janitor cannot block
deploying a fix to the search.

| Command | Category | What it does |
| ------- | -------- | ------------ |
| `modal run api/modal_janitor.py` | maintenance | Dry run — prints what would be deleted, deletes nothing |
| `modal run api/modal_janitor.py::usage_now` | maintenance | Reports what the two volumes currently hold |
| `modal run api/modal_janitor.py::sweep_now` | maintenance | Runs the sweep immediately |
| `modal deploy api/modal_janitor.py` | one-off | Installs the six-hourly schedule |
| `modal run api/modal_runner.py::prune_raw_cache` | maintenance | Evicts cached raw files older than 30 days (`--max-age-days N` to override) |

It only removes storage that regenerates on the next request: MaxQuant scratch left on
the raw volume, stale `.downloading` locks, and job directories whose calls have
finished. It never cancels a `FunctionCall`, never touches a running job's directory or
a busy accession, and fails closed — an unreadable job registry aborts the sweep.
Raw file eviction is delegated to `prune_raw_cache` rather than reimplemented.

Checklist for the deploy to actually work:

- [ ] The ASGI function carries `secrets=[secret]` **as well as** the worker
  ```
  function. Omitting it deploys cleanly and then throws `KeyError` on
  `os.environ["MQ_TOKEN"]` at the first request — a confusing 500 where you
  expected a 401.
  ```
- [ ] `uvx modal app list` shows `pridemap-maxquant` as `deployed`.
- [ ] `uvx modal app logs pridemap-maxquant` shows the first image build completing.

Redeploying later is idempotent: the URL does not change, `.env` needs no edit,
and in-flight runs are not interrupted — new invocations pick up the new image
while existing ones finish on the old one.

### 5. Point PRIDE Map at it

Add both values to `.env` in the repo root:

```dotenv
MQ_SERVER_URL=https://<workspace>--pridemap-mq.modal.run
MQ_TOKEN=<the same token from step 3>
```

Restart the API (`uv run api/main.py`) so it picks up the new env vars. The
**Reanalyse** page will show a **"Remote MaxQuant connected"** badge and a
**Run on cloud** button on datasets where MaxQuant is the recommended engine.

## Running a search

Pick a dataset, choose your samples, and press **Run on cloud**. From there:

- Progress polls every 3 seconds, the same as the local path.
- **You can close the tab, close the laptop, or shut the machine down.** The job
continues on Modal.
- To collect results later, reopen the dataset in PRIDE Map. The run is
rediscovered and the protein table loads into the Analyse tab.

> Recovery after a full shutdown is Phase 2, and it is built: on a cache miss
> `pipeline_remote_active()` asks Modal for `GET /jobs/{accession}` and picks the
> run back up. This path has not been exercised against a live deploy yet
> (verification steps 13–14), so if a restarted API does not rediscover a run,
> check the dashboard for an orphan still billing.



### Per run, there is no Modal login

Worth being explicit, because it is the property the whole design rests on:

> Once deployed, the Modal app is an ordinary HTTPS endpoint. Running a search
> needs **only** `MQ_SERVER_URL` and `MQ_TOKEN` in `.env`. Your machine never
> runs `modal`, never reads `~/.modal.toml`, and holds no Modal credentials. Any
> machine that can reach the URL can drive a run and collect its results — which
> is exactly what makes "shut the laptop, pick the results up elsewhere" work.

Re-deploying is the only operation needing Modal credentials, and only on the
machine doing the deploy.

## Security — the endpoint is public by default

[Modal web endpoints are reachable by anyone who knows the URL.](https://modal.com/docs/guide/webhooks)
The bearer-token check is therefore **not optional**: without it, `/run` is an
open door to 16-core, 24-hour jobs billed to your account.

Reject any request whose `Authorization: Bearer …` does not match `MQ_TOKEN`
**before doing any work** — before unpacking the kit zip, not after.

Two stronger alternatives were considered and rejected, recorded here so they do
not get re-raised:


| Option                                                                                           | Why not                                                                                                                                    |
| ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Modal [proxy auth](https://modal.com/docs/guide/webhook-proxy-auth) (`requires_proxy_auth=True`) | Requires `Modal-Key` / `Modal-Secret` headers that the existing client does not send — adopting it breaks the zero-client-changes property |
| A gateway (Cloudflare Worker etc.) injecting those headers                                       | Preserves the client contract, but adds infrastructure well beyond this scope                                                              |


The hand-rolled check is chosen to keep the existing contract intact — which is
precisely why it has to actually be implemented.

## Verifying

```bash
# From your machine — should return {"available": true}
curl http://localhost:8000/remote-mq

# Direct health check against Modal
curl $MQ_SERVER_URL/health
# → {"ok": true}

# Negative check — this one guards your budget
curl -X POST $MQ_SERVER_URL/run
# → 401, and no work started
```

The third check matters as much as the first two. If an unauthenticated POST
does anything other than fail fast, stop and fix it before running a real search.

## Troubleshooting


| Symptom                                            | Fix                                                                                                                                               |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `uvx modal deploy` succeeds but nothing is listed  | `uvx modal app list` — confirm `pridemap-maxquant` is `deployed`, and `uvx modal profile list` to check you deployed into the workspace you think |
| Deploy fails during image build                    | `uvx modal app logs pridemap-maxquant` — the build log names the failing layer                                                                    |
| `/run` returns 500 instead of 401                  | The ASGI function is missing `secrets=[secret]`, so `os.environ["MQ_TOKEN"]` raises `KeyError`                                                    |
| `/remote-mq` says unavailable                      | `MQ_SERVER_URL` wrong or API not restarted after editing `.env`                                                                                   |
| Run appears stuck                                  | `uvx modal volume ls pridemap-runs`, then `uvx modal volume get pridemap-runs <job_id>/run.log /tmp/run.log` to read the live log                 |
| First search takes minutes before anything happens | Cold start pulling a multi-GB image — expected, 2–5 min                                                                                           |
| Job finished but no results                        | `uvx modal volume ls pridemap-runs` — check `<job_id>/combined/txt/` exists; if not, the search failed, see `run.log`                             |




## Teardown

```bash
uvx modal app stop pridemap-maxquant
uvx modal volume delete pridemap-runs      # NOT done by app stop — see below
uvx modal dict delete pridemap-jobs
uvx modal secret delete pridemap-mq-token
```

Then unset `MQ_SERVER_URL` and `MQ_TOKEN` in `.env` and restart the API. The
**Run on cloud** card disappears from the UI.

> `uvx modal app stop` **does not delete the Volume.** It stops the app and kills
> live containers, but `pridemap-runs` keeps billing for stored data until you
> run `uvx modal volume delete`. Someone who stops the app and closes the laptop
> believes they have torn down and is still paying.

Two other things survive a partial teardown:

- An orphaned in-flight worker bills 8 cores until its 86400 s timeout fires.
Cancel it from the dashboard or with `FunctionCall.from_id(...).cancel()`.
- A `keep_warm=1` container (Phase 3, if enabled) bills continuously even with
no jobs running.



## Architecture decisions


| Decision                                            | Reason                                                                                                                                                                                                                                                                                                                                              |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Implement the existing `MQ_SERVER_URL` contract     | `api/omics.py:2276–2487` already speaks it, and the UI already has the local-vs-remote toggle. No client, UI, or endpoint-shape changes needed                                                                                                                                                                                                      |
| `.spawn()`, not `.remote()`                         | `.remote()` blocks the caller until the function returns — the laptop could never disconnect                                                                                                                                                                                                                                                        |
| Job id in `modal.Dict`                              | An in-process dict dies with the API process, losing the handle to a job that is still running and billing                                                                                                                                                                                                                                          |
| `timeout=86400`                                     | Modal's default is 300 s, which would silently kill every real MaxQuant run a few minutes in                                                                                                                                                                                                                                                        |
| Raw files on container-local `/tmp`, not the Volume | 24 × 1–5 GB is pure scratch; `vol.commit()` on 120 GB costs minutes and real money. Only logs and `combined/txt/` are committed                                                                                                                                                                                                                     |
| `log` read from a shipped `run.log`                 | Modal exposes no SDK call for a spawned function's stdout — `FunctionCall` has no `.logs()`, and `uvx modal app logs` is CLI-only. A background loop ships the log to the Volume instead                                                                                                                                                            |
| Bearer token, not proxy auth                        | Modal's proxy auth needs headers the existing client does not send (see [Security](#security--the-endpoint-is-public-by-default))                                                                                                                                                                                                                   |
| `label="pridemap-mq"` on the ASGI app               | Makes the URL deterministic and independent of the Python function name. Labels are lowercase alphanumeric plus hyphens (`pridemap_mq` is invalid) and workspace-scoped, so a second app reusing the label conflicts at deploy time. The app name (`modal.App("pridemap-maxquant")`) is separate — the **label** is what appears in `MQ_SERVER_URL` |
| Stock biocontainers image                           | Modal is x86-64, so the arm64 assembly-rebuild path at `api/omics.py:1242–1333` is never invoked, and Bruker data needs no emulation                                                                                                                                                                                                                |
| `MAXQUANT_CMD=maxquant`                             | Makes `run_all.sh` take its native branch (`api/omics.py:1706–1708`) and skip Docker entirely — there is no Docker daemon inside the container                                                                                                                                                                                                      |


---



# Implementation plan

Everything above describes the finished system. This section is the build spec.
Phases are independently shippable. **Phases 1a, 1b and 2 are implemented** —
what follows is kept as the record of why each piece is shaped the way it is,
and the obligations any change to them must still honour. Phase 3 is open.

## What to reuse

`api/omics.py` lines 2276–2487 already hold a complete remote-execution
**client**: it health-gates on `GET /health`, POSTs the kit zip to
`{MQ_SERVER_URL}/run`, polls `GET /status/{job_id}`, fetches
`GET /results/{job_id}`, and DELETEs `/job/{job_id}`. **So we write a server
satisfying that contract** — no new client, no new UI, no new endpoint shapes.

## Phase 1a — Skateboard: MaxQuant runs on Modal, laptop closes the tab

**New file:** `api/modal_runner.py`.

### Module-level objects (all four required, or deploy fails)

```python
app    = modal.App("pridemap-maxquant")
vol    = modal.Volume.from_name("pridemap-runs", create_if_missing=True)
jobs   = modal.Dict.from_name("pridemap-jobs",  create_if_missing=True)
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
    image=image, volumes={"/data": vol, "/raw-cache": raw_vol}, secrets=[secret],
    cpu=8, memory=20480,            # 20 GB — measured peak RSS 13.4 GB
    ephemeral_disk=524288,          # 512 GiB — Modal's MINIMUM, not a target
    timeout=86400,                  # 24 h — NOT the 300 s default
)
def _run_maxquant_search(kit_bytes, accession, job_id, force=False) -> None: ...
```

**Storage split — MaxQuant's scratch must not go on the Volume.** This is the single
biggest lever on wall clock, and getting it wrong cost a factor of six.

MaxQuant writes per-file intermediates **next to each raw file** — `<name>.index` and
a `<name>/` directory — and follows symlinks to do it. The kit symlinks `raw/` at
`$PRIDEMAP_RAW_DIR`, so pointing that at the `raw-cache` Volume put every intermediate
on a network filesystem Modal documents as "write-once, read-many", where a write
inside an existing file can cost a rewrite of the whole file. Feature detection and
deisotoping are exactly the write-heavy phases:

| Feature detection | raw dir on Volume | raw dir on local SSD |
| ----------------- | ----------------- | -------------------- |
| 1.6 GB file       | 619 s             | **96 s**             |
| 2.4 GB file       | 893 s             | **148 s**            |

So `_stage_raw_local` copies the run's raw files from `/raw-cache` onto container-local
disk before MaxQuant starts and points `PRIDEMAP_RAW_DIR` there — 3.8 GB in about 2 s
at ~2.1 GB/s. It stages only the files this run's `mqpar.xml` names, because the cache
is per accession while a run is per selection: staging the directory wholesale would
copy all 57.6 GB of PXD046787 for a two-file search.

The search container therefore only ever **reads** `raw-cache`, and does not commit it.
Committing it on a timer during the search was pure overhead on data that cannot change.

- `ephemeral_disk` has a **floor of 512 GiB** (`[524288, 3145728]` MiB). Measured peak
use is ~15 GB on a two-run job, about 3% of the minimum you are obliged to request, so
this is not a cost lever downwards and no measurement can inform it. Deriving it from
observed usage produced 40960 and then 20480 MiB, and the platform rejected both with
`Function disk request out of bounds`. Raise it only for a dataset whose raw set plus
intermediates exceeds 512 GiB.
- The Volume holds **only** `/data/<job_id>/`: `progress.jsonl`, `run.log`, and
the final `combined/txt/proteinGroups.txt`.
- A background shipper loop copies `progress.jsonl` + `run.log` into
`/data/<job_id>/` and calls `vol.commit()` every ~15 s, so `/status` has
something live to return. Final commit after `combined/txt/` is copied across.



### Endpoints

ASGI app decorated `@modal.asgi_app(label="pridemap-mq")`, with
`secrets=[secret]` so `MQ_TOKEN` is injected.


| Endpoint                | Behaviour                                                                                                                                                                                     |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /health`           | `{"ok": true}` — satisfies the client's health gate                                                                                                                                           |
| `POST /run`             | multipart field `kit`; optional `?force=1` to recompute a cached search; **reject bad bearer token before any work**; `run_maxquant.spawn(...)`; write `jobs[accession] = call.object_id` **before** responding; return `{"job_id": ...}`       |
| `GET /status/{job_id}`  | see below. Returns `{status, log, progress, cpu}` — `progress` is `run_all.sh`'s own stage events, copied off the container's work dir onto the Volume, and is what the UI's stages, elapsed time and ETA are computed from; `cpu` is the container's own cgroup measurement, or `null` when nothing has been measured yet |
| `GET /resource-advice`  | per-phase peak CPU, memory and disk across past jobs, with recommended constants. Check `jobs_analysed` — a short or failed run can dominate it |
| `GET /results/{job_id}` | `vol.reload()`, stream `combined/txt/proteinGroups.txt`                                                                                                                                       |
| `DELETE /job/{job_id}`  | cancel if live, drop the `jobs` entry, and remove the Volume dir **only when the job produced no results** — pass `?purge=1` to delete a finished run's tables deliberately. Reports `{deleted, cancelled, kept_results, problems}` — a swallowed cancel error here tells the caller an 8-core job stopped when it did not |
| `GET /jobs`             | all known accession→job_id with live status, so a runaway job can be found and killed over HTTP without Modal credentials                                                                     |
| `GET /jobs/{accession}` | *(Phase 2)* read `jobs`, return the job_id                                                                                                                                                    |


**Status polling must order its excepts correctly.**
`modal.FunctionCall.from_id(job_id).get(timeout=0)` is the right non-blocking
idiom, but while the function is still running it raises
`modal.exception.TimeoutError` — **not** Python's built-in `TimeoutError`.
Catch `modal.exception.TimeoutError` → `running` **first**, then a generic
`except Exception` → `error`. Inverting these reports every healthy running job
as failed.

**Why Phase 2 is not optional.** If the API process dies, `_active_remote_jobs`
(`api/omics.py:2281`) loses the accession→job_id mapping. Without a reader for
the `modal.Dict`, the job runs to completion unreachable, billing 8 cores for
up to 24 h. Phase 2 is that reader, and it ships alongside this.

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
with no indication of *which* file failed. The portable fix: record each PID
alongside its filename, run the blocking `wait || true` so `set -e` does not
fire, then iterate `wait "$pid"` per recorded PID and report the filename of
any non-zero one. This boilerplate is mandatory, not optional.
- **Cache semantics must not regress:** complete files still skip, partial files
still resume via `curl -C -`.
- `ln -sf` concurrency is safe — each call targets a distinct filename in a
pre-existing directory, and `ln -sf` is atomic per filename.

**Value standalone:** download wall-clock drops roughly 4× wherever bandwidth
allows — on the laptop today, and more so on Modal's uplink after Phase 1a.

## Phase 2 — Bicycle: laptop fully powers off and recovers

The `modal.Dict` written in Phase 1a is already the durable record. Phase 2
teaches the laptop to read it instead of trusting its own memory, closing the
orphan-cost window above.

- Add `GET /jobs/{accession}` to `modal_runner.py`, reading `jobs`.
- `pipeline_remote_active()` (`api/omics.py:2368`) currently returns
`{"job_id": None}` the moment the in-process dict misses. Make it fall through
to that endpoint before giving up. The in-process dict stays a fast cache; the
Modal Dict becomes the source of truth.



## Phase 3 — Motorbike: right-size the box

Only after 1a/1b/2 run on real data.

**Done, from measurement rather than guesswork** — each container reports its own
cgroup figures (`GET /status/{id}` → `cpu`, aggregated by `GET /resource-advice`), and
the constants were set from observed peaks: search `cpu=8, memory=20480`, download
`cpu=1, memory=1024`.

What the measurements showed, and what is still open:

- MaxQuant's early phases — feature detection, deisotoping, MS/MS preparation — run
**one thread per raw file**, not one per core. On a two-file search that is ~2 busy
cores whatever the allocation, so `numThreads` is sized from the reservation but more
cores do not shorten those phases. The Andromeda phases do fan out and saturated all 8
(`peak_busy_cores` 9.65, bursting past the reservation), so **8 is a floor rather than
a settled optimum** — whether raising it helps depends on whether Modal bills the burst,
which has not been checked.
- `<numThreads>` is patched at container start from `MAXQUANT_SEARCH_CPU`, **not** from
`os.cpu_count()`: a `cpu=8` container reports 24 visible cores, so sizing the thread
pool from the host count oversubscribed the Andromeda phases 3:1.
- The download worker's figures (0.22 busy cores, 0.1 GB RSS) were taken on a **warm
cache**, where that container does almost nothing. They are not a valid basis for its
sizing; a cold 57.6 GB download has never been measured.
- `keep_warm=1` removes the 2–5 min cold start if demo reliability outweighs
cost. Note it bills continuously.



## Explicitly cut

**Splitting the two samples into two parallel MaxQuant jobs.** Looks like a free
2× and is not. MaxQuant's LFQ and iBAQ normalisation runs across all samples in
one process; splitting A and B normalises each independently, so their
abundances are no longer comparable — which is the entire point of the volcano
plot. It would be a faster wrong answer. Revisit only if a MaxQuant version
exposes partial runs preserving global normalisation.

Per-file parallelism *within* feature detection and MS/MS search is genuinely
independent, but MaxQuant 2.0.3.0 exposes no entry point for it.

## Files to change


| File                                            | Phase | Change                                                               |
| ----------------------------------------------- | ----- | -------------------------------------------------------------------- |
| `api/modal_runner.py`                           | 1a    | New. Modal app, worker, `/health` `/run` `/status` `/results` `/job` |
| `api/omics.py` `_download_sh()` ~1221           | 1b    | Sequential curl loop → N-way parallel wave                           |
| `api/modal_runner.py`                           | 2     | Add `GET /jobs/{accession}`                                          |
| `api/omics.py` `pipeline_remote_active()` ~2368 | 2     | Fall back to Modal Dict on cache miss                                |


No frontend changes in any phase.

## Implementation verification

**Before deploying anything**

1. Ground the `MAXQUANT_CMD=maxquant` assumption:
  `docker run --rm quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1 which maxquant`

**Phase 1a**

1. Complete [Step-by-step setup](#step-by-step-setup) from a clean machine.
2. `GET /remote-mq` reports available (proves the health gate passes).
3. Unauthenticated `POST /run` is rejected before any work starts.
4. Run the 2-sample comparison via the remote toggle.
5. `uvx modal app logs pridemap-maxquant` shows the container pulling from PRIDE FTP directly; your
  machine shows no multi-GB egress.
6. Close the browser tab mid-run, reopen — progress resumes, and `log` shows
  live `run.log` text (proves the shipper loop works).
7. `proteinGroups.txt` downloads and loads into the Analyse view.
8. `uvx modal volume ls pridemap-runs` shows only logs plus `combined/txt/` — no raw files.

**Phase 1b**

1. Run `download_raw.sh` on a multi-file dataset; confirm 4 concurrent `curl`
  processes and a ~4× drop in the `download` stage `seconds` in `progress.jsonl`.
2. Re-run: cached files skip, no re-download.
3. Interrupt mid-download and re-run: partial files resume, not restart.
4. Point one manifest URL at a 404; confirm the error **names that file**.

**Phase 2**

1. Start a run, kill the local API process entirely (simulated shutdown).
2. Restart it, open the dataset — the run is rediscovered via the Modal Dict
  and results are fetchable.

**Regression (all phases)**

1. Unset `MQ_SERVER_URL`; the local Docker path via `POST /pipeline/start`
  still works unchanged.

**Correctness guard**

1. Run the same comparison locally and on Modal; `proteinGroups.txt` LFQ
  intensities must match. Divergence means the Modal path altered the search,
    not just where it ran.

