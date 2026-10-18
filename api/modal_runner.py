"""
Modal serverless runner for MaxQuant.

Deploy:  modal deploy api/modal_runner.py
Verify:  curl $MQ_SERVER_URL/health

Contract (consumed by api/omics.py lines 2276-2487):
  GET  /health                → {"ok": true}
  POST /run                   → {"job_id": "..."}   multipart field: kit (zip)
  GET  /status/{job_id}       → {"status": "running"|"done"|"error", "log": "...", "progress": [...]}
                                 progress carries run_all.sh's own stage events, which is what lets
                                 the caller show stages, elapsed time and ETA for a remote job.
  GET  /results/{job_id}      → proteinGroups.txt (TSV)
  DELETE /job/{job_id}        → {"deleted": bool, "cancelled": bool, "problems": [...]}
  GET  /jobs/{accession}      → {"job_id": "..."|null}   (Phase 2: durable lookup)
  GET  /jobs                  → {"jobs": [{"accession", "job_id", "status"}, ...]}
"""

# /// script
# requires-python = ">=3.11"
# dependencies = ["modal>=0.73"]
# ///

import io
import json
import os
import pathlib
import subprocess
import tempfile
import threading
import time
import zipfile

import modal
import modal.exception
from modal import Secret, Volume

# ----------------------------------------------------------------------------- module-level objects (all four required)

app     = modal.App("pridemap-maxquant")
vol     = Volume.from_name("pridemap-runs", create_if_missing=True)
raw_vol = Volume.from_name("pridemap-raw",  create_if_missing=True)
jobs    = modal.Dict.from_name("pridemap-jobs", create_if_missing=True)
secret  = Secret.from_name("pridemap-mq-token")   # provides MQ_TOKEN

# Where job directories live. In a Modal container this is the Volume mount point; off Modal
# (a local run, a test, a plain host serving the same gateway) nothing is mounted at /data and it
# may not even be writable, so the root is overridable. One owner, so the worker that writes a job
# directory and the gateway that reads it can never diverge.
VOLUME_MOUNT   = "/data"       # where both @app.function decorators mount `vol` (job results)
RAW_CACHE_MOUNT = "/raw-cache" # where run_maxquant mounts `raw_vol` (cached raw files, cross-job)
DATA_ROOT = pathlib.Path(os.environ.get("PRIDEMAP_DATA_ROOT", VOLUME_MOUNT))

# Single source of truth for container sizing. Change one number here to resize both the Modal
# decorator and the log lines that describe the phase; the runtime also patches mqpar.xml's
# numThreads to os.cpu_count() so the kit's static value is never wrong by accident.
MAXQUANT_SEARCH_CPU    = 16
MAXQUANT_SEARCH_MEMORY = 65536   # MB (64 GB)
MAXQUANT_DOWNLOAD_CPU    = 2
MAXQUANT_DOWNLOAD_MEMORY = 4096  # MB (4 GB)


def _vol_sync(action: str, v: "Volume | None" = None) -> None:
    """Best-effort Volume commit/reload for vol (default) or any other Volume.

    Reads only. A reload that fails costs nothing — the next one will pick the data up — so it stays
    silent. For WRITES use _vol_commit: a commit that fails silently is how durable state stops
    being durable without anyone finding out.
    """
    try:
        getattr(v or vol, action)()
    except Exception:
        pass


def _vol_commit(v: "Volume | None" = None, *, what: str = "volume") -> bool:
    """Commit a Volume, reporting failure. Returns True when the data is durable.

    Separated from _vol_sync because resume depends on this specific call succeeding. The stage
    markers run_all.sh writes to .pipeline/ become durable only when a commit lands; if one fails,
    the next container re-runs every stage from scratch and nothing anywhere says why. "Best effort"
    is the right policy for a reload and the wrong one for the write that a guarantee rests on.
    """
    try:
        (v or vol).commit()
        return True
    except Exception as exc:
        # Reported unconditionally. An earlier version gated this on a MODAL_TASK_ID env var to stay
        # quiet off Modal — an env var that was never verified, which would have re-hidden every
        # commit failure behind a guess. _vol_commit only runs inside @app.function bodies, so
        # there is no off-Modal caller to stay quiet for.
        print(f"WARNING: {what} commit failed — resume may re-run stages: {exc}", flush=True)
        return False

# ----------------------------------------------------------------------------- container images

# Lightweight image for the ASGI web gateway.
#
# Versions are pinned: an unpinned pip_install resolves at BUILD time, so the image's behaviour can
# change with no commit. That is how the missing-multipart bug became a hard failure — a Starlette
# release turned absent form support into an AssertionError inside request.form().
#
# python-multipart is required by Starlette's request.form() and is an implicit dependency of a
# feature, not of any import in this repo, so no import scan can find it missing.
#
# The build assertion checks the *capability* rather than a package name: Starlette sets
# parse_options_header to None when multipart support is unavailable, and that is the exact symbol
# whose None-ness produced the 500. Asserting `import multipart` instead would track a deprecated
# alias and can pass while form parsing is broken (or fail while it works).
web_image = (
    modal.Image.debian_slim()
    .pip_install("fastapi==0.143.5", "uvicorn==0.54.0", "python-multipart==0.0.32")
    .run_commands(
        'python -c "from starlette.requests import parse_options_header as p; '
        'assert p is not None, \'form parsing unavailable: python-multipart missing\'"'
    )
    .add_local_python_source("modal_web")   # the gateway module, imported inside web() only
)

# Heavy MaxQuant image for the compute worker.
#
# Built from docker/maxquant/Dockerfile (a multi-stage build) so Modal constructs the image
# at deploy time without a separate registry push.  The Dockerfile replicates what
# preflight.sh does locally: copies MaxQuant assemblies from the biocontainers image onto
# the .NET SDK base (mcr.microsoft.com/dotnet/sdk:3.1-bullseye), giving exact parity with
# the local macOS Docker setup.
#
# Why the SDK base is required: MaxQuant's DotNetCore31Installed() checks `dotnet --info`
# for a line starting with "3.1" (an installed SDK).  The biocontainers image has .NET
# runtime only; no such line exists and the run fails immediately with
# "Please install .NET Core 3.1 SDK" — before mqpar.xml is even read.
# The SDK image outputs "3.1.426 [/usr/share/dotnet/sdk]" which satisfies the check.
#
# The SDK image (Debian Bullseye) ships curl and ca-certificates, so the static-curl
# fetch and CA-bundle workarounds the old image required are not needed here.
_DOCKERFILE = pathlib.Path(__file__).parent.parent / "docker" / "maxquant" / "Dockerfile"

# Modal's post-build Python probe reports only "unable to determine the version of Python
# installed in the Image" — it names neither of the two binaries it looks for and runs after the
# build, so its output carries no diagnostics. Seven deploys were spent guessing which was
# missing. This asserts the contract *inside* the build, where stdout reaches the deploy log,
# and dumps the environment once so a later image fault has a baseline to diff against rather
# than needing another deploy to inspect.
#
# It must stay ONE logical line.  run_commands() emits each string as a `RUN` instruction into a
# Dockerfile that Modal parses itself, and that parser rejects an embedded newline: a `for ... done`
# written across lines failed the whole build with `could not parse Dockerfile: expected
# any_breakable` pointing at `done`.  Hence the `;`-joined fragments below, and `echo` rather than
# `printf` — a literal `\n` in a RUN line is at the mercy of the Dockerfile escape handling.
_IMAGE_PROBE = " ".join(
    [
        "set -eu;",
        'echo "=== PATH ==="; echo "$PATH";',
        'echo "=== python/pip resolution (Modal needs python and pip, not the 3-suffixed names) ===";',
        'for b in python python3 pip pip3; do echo "$b -> $(command -v "$b" || echo MISSING)"; done;',
        'echo "=== versions ==="; python --version 2>&1 || true; pip --version 2>&1 || true;',
        'echo "=== dotnet SDKs (DotNetCore31Installed() needs a line starting 3.1) ===";',
        "dotnet --list-sdks 2>&1 || true;",
        'echo "=== /opt/maxquant ==="; ls /opt/maxquant 2>&1 | head -20 || true;',
        'echo "=== assert Modal image contract ===";',
        "missing='';",
        'command -v python >/dev/null 2>&1 || missing="$missing python";',
        'command -v pip >/dev/null 2>&1 || missing="$missing pip";',
        'if [ -n "$missing" ]; then',
        'echo "FATAL: not on PATH:$missing" >&2;',
        'echo "Modal requires both python and pip on PATH. This base ships python3 only;" >&2;',
        'echo "add_python= on from_dockerfile is what supplies them." >&2;',
        "exit 1; fi;",
        'echo "OK: python and pip both resolve"',
    ]
)

image = (
    # add_python installs a reproducible standalone Python build. The alternative — apt plus the
    # frozen get-pip.py for 3.9 — depends on Debian 11's debian-security, which has already
    # dropped python3-pip (404) once during this work.
    modal.Image.from_dockerfile(_DOCKERFILE, add_python="3.12")
    .run_commands(_IMAGE_PROBE)
    .run_commands(
        # Prove TLS actually works against the two hosts this pipeline fetches from, at
        # BUILD time. A range request keeps it to a few bytes; -f makes an HTTP error a failure.
        "curl -fsS -r 0-99 -o /dev/null "
        "https://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD046787/2019_12_13_Daria_01.raw",
        "curl -fsS -o /dev/null https://rest.uniprot.org/uniprotkb/P01308.fasta",
    )
    .run_commands("which maxquant")   # fail at BUILD time if binary is missing
)

# ----------------------------------------------------------------------------- worker

# ----------------------------------------------------------------------------- download worker (cheap)

@app.function(
    image=image,
    volumes={VOLUME_MOUNT: vol, RAW_CACHE_MOUNT: raw_vol},
    secrets=[secret],
    cpu=MAXQUANT_DOWNLOAD_CPU,
    memory=MAXQUANT_DOWNLOAD_MEMORY,
    timeout=7200,   # 2 h — enough for any current public PRIDE dataset
)
def _download_raw(kit_bytes: bytes, accession: str, job_id: str) -> None:
    """Download raw files for one accession into raw_vol. Cheap container (~10× less than MaxQuant).

    Runs only download_raw.sh. raw_vol.reload() at start means an already-cached accession
    finishes in seconds — have() returns true for each file and curl is never invoked.
    raw_vol is committed every 15 s so an interrupted container leaves a partial file that
    the next run resumes via curl -C - rather than restarting.
    """
    job_dir = DATA_ROOT / job_id
    log_path = job_dir / "run.log"

    def _log(msg: str) -> None:
        print(msg, flush=True)
        try:
            with log_path.open("a") as f:
                f.write(msg + "\n")
        except OSError:
            pass   # job_dir may not be visible before the first reload

    _vol_sync("reload")        # see the job_dir the orchestrator created and committed
    _vol_sync("reload", raw_vol)
    _log(f"raw cache: {RAW_CACHE_MOUNT} (reload attempted — failures are swallowed off Modal)")
    _log(f"container CPUs available: {os.cpu_count()} (allocated: {MAXQUANT_DOWNLOAD_CPU})")

    # Concurrent-download guard: two containers downloading the same accession simultaneously
    # both run `curl -C -` against the same partial file on raw_vol, producing a corrupt result.
    # A lock file on raw_vol prevents the second container from starting its own curl run.
    # The lock's mtime is the evidence of liveness — anything older than the download timeout
    # (7200 s) was left by a crashed container and is ignored.
    _LOCK_TIMEOUT = 7200  # matches this function's Modal timeout
    cache_dir = pathlib.Path(RAW_CACHE_MOUNT) / accession
    cache_dir.mkdir(exist_ok=True)
    lock_file = cache_dir / ".downloading"

    def _take_lock() -> bool:
        """Create the lock, or False if someone else holds it. O_EXCL makes this a single step.

        exists()-then-touch() was two steps with a gap: both containers could see no lock and both
        proceed, which is the exact race the lock exists to prevent — two curls resuming the same
        partial file on the same volume, producing a corrupt raw file that fails deep inside
        MaxQuant hours later.
        """
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.close(fd)
        return True

    lock_acquired = _take_lock()
    if not lock_acquired:
        age = time.time() - lock_file.stat().st_mtime
        if age >= _LOCK_TIMEOUT:
            # Left by a container that died. Its mtime is the evidence.
            _log(f"stale lock for {accession} (age {age:.0f} s) — taking it over")
            lock_file.unlink(missing_ok=True)
            lock_acquired = _take_lock()
        else:
            _log(f"another container is downloading {accession} (lock age {age:.0f} s) — waiting")
            for _ in range(72):   # up to 6 min (72 × 5 s)
                time.sleep(5)
                _vol_sync("reload", raw_vol)
                if not lock_file.exists():
                    break
            else:
                # The wait expired with the lock still held. Returning here handed the 64 GB search
                # worker a raw set that may be half-downloaded, and it failed deep inside MaxQuant
                # with nothing pointing back at this moment. Failing here is cheaper and legible.
                raise RuntimeError(
                    f"timed out after 6 min waiting for another container to finish downloading "
                    f"{accession}; refusing to start the search on a possibly-incomplete raw set"
                )
            _log("concurrent download finished — raw_vol reloaded, skipping to search")
            return   # search worker will reload and have() will find the files

    if not lock_acquired:  # pragma: no cover - takeover raced with another container
        raise RuntimeError(f"could not acquire the download lock for {accession}")
    _vol_commit(raw_vol, what="raw cache")   # make the lock visible before any curl runs

    work_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"dl-{job_id}-"))
    with zipfile.ZipFile(io.BytesIO(kit_bytes)) as zf:
        zf.extractall(work_dir)

    dl_script = work_dir / "download_raw.sh"
    if not dl_script.exists():
        raise FileNotFoundError("download_raw.sh not found in kit")
    dl_script.chmod(0o755)

    env = {**os.environ, "PRIDEMAP_RAW_DIR": RAW_CACHE_MOUNT}

    _stop = threading.Event()

    def _ship() -> None:
        while not _stop.wait(15):
            _vol_commit()
            _vol_commit(raw_vol, what="raw cache")

    t = threading.Thread(target=_ship, daemon=True)
    t.start()
    try:
        proc = subprocess.Popen(
            ["bash", str(dl_script)],
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            _log(line.rstrip())
        proc.wait()
        _vol_commit(raw_vol, what="raw cache")
        _vol_commit()
        if proc.returncode != 0:
            raise RuntimeError(f"download_raw.sh exited {proc.returncode}")
    finally:
        _stop.set()
        if lock_acquired:
            lock_file.unlink(missing_ok=True)   # release lock so concurrent jobs can proceed
        _vol_commit(raw_vol, what="raw cache")
        _vol_commit()


# ----------------------------------------------------------------------------- MaxQuant worker (expensive)

@app.function(
    image=image,
    volumes={VOLUME_MOUNT: vol, RAW_CACHE_MOUNT: raw_vol},
    secrets=[secret],
    cpu=MAXQUANT_SEARCH_CPU,
    memory=MAXQUANT_SEARCH_MEMORY,
    timeout=86400,  # 24 h
)
def _run_maxquant_search(kit_bytes: bytes, accession: str, job_id: str) -> None:
    """Run MaxQuant. Raw files are already in raw_vol — download_raw.sh's have() skips them.

    The expensive 64 GB container only starts after _download_raw has committed all raw files,
    so it spends zero time waiting for downloads.
    """
    job_dir = DATA_ROOT / job_id
    log_path = job_dir / "run.log"
    progress_path = job_dir / "progress.jsonl"

    # Reload before first write so the log lines from _download_raw are visible in the file
    # we append to, and so the raw files committed by _download_raw are on-disk.
    _vol_sync("reload")
    _vol_sync("reload", raw_vol)

    # Then ensure the directory, because the reload does not always bring it. The orchestrator
    # creates and commits job_dir before spawning this function, but it was observed missing here
    # anyway — _log swallows OSError, so the only symptom was the run dying on progress.jsonl with
    # a FileNotFoundError that read as a MaxQuant fault. Creating it is idempotent and removes the
    # dependency on cross-container directory propagation entirely.
    job_dir.mkdir(parents=True, exist_ok=True)

    def _log(msg: str) -> None:
        print(msg, flush=True)
        try:
            with log_path.open("a") as f:
                f.write(msg + "\n")
        except OSError:
            pass

    _log(f"raw cache: {RAW_CACHE_MOUNT} (reloaded — raw files should already be present)")

    # run_all.sh writes its own stage events next to itself in the container-local work dir.
    # Copying that file to the Volume on every sync is what lets /status report stages,
    # elapsed time and ETA while the search runs.
    kit_progress: list[pathlib.Path] = []

    def _ship_stages() -> None:
        for src in kit_progress:
            try:
                (job_dir / "stages.jsonl").write_bytes(src.read_bytes())
            except OSError:
                pass

    _stop_shipper = threading.Event()

    def _shipper() -> None:
        while not _stop_shipper.wait(15):
            _ship_stages()
            _vol_commit()
            _vol_commit(raw_vol, what="raw cache")

    shipper = threading.Thread(target=_shipper, daemon=True)
    shipper.start()

    try:
        work_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"mq-{job_id}-"))
        kit_progress.append(work_dir / "progress.jsonl")

        with zipfile.ZipFile(io.BytesIO(kit_bytes)) as zf:
            zf.extractall(work_dir)
        _log("kit unpacked")

        # Patch mqpar.xml numThreads to match actual container CPUs so the kit's static value
        # can never cause a mismatch. MAXQUANT_SEARCH_CPU controls the Modal allocation; os.cpu_count()
        # is what the container actually sees — they are equal under normal operation.
        import re as _re
        actual_cpus = os.cpu_count() or MAXQUANT_SEARCH_CPU
        _log(f"container CPUs available: {actual_cpus} (allocated: {MAXQUANT_SEARCH_CPU})")
        mqpar_files = list(work_dir.rglob("mqpar.xml"))
        if mqpar_files:
            mqpar_path = mqpar_files[0]
            mqpar_text = mqpar_path.read_text(errors="replace")
            m = _re.search(r"<numThreads>(\d+)</numThreads>", mqpar_text)
            old_threads = m.group(1) if m else "not found"
            patched = _re.sub(r"<numThreads>\d+</numThreads>", f"<numThreads>{actual_cpus}</numThreads>", mqpar_text)
            mqpar_path.write_text(patched)
            _log(f"mqpar.xml numThreads: {old_threads} → {actual_cpus} (patched, file: {mqpar_path.relative_to(work_dir)})")
        else:
            _log("mqpar.xml: not found in kit — numThreads not patched")

        run_script = work_dir / "run_all.sh"
        if not run_script.exists():
            raise FileNotFoundError("run_all.sh not found in kit")
        run_script.chmod(0o755)

        # NO stage markers across runs, deliberately. run_all.sh's .pipeline/<id>.done markers stay
        # in this container's work dir and die with it.
        #
        # Backing them with the Volume was tried and reverted. A marker asserts "this stage's work
        # is done", but the work PRODUCTS — raw/ symlinks, fasta/, combined/ — live in this
        # ephemeral work dir, which is new and empty on every run. So a second run skipped download
        # and fasta, then handed MaxQuant paths to files nothing had created:
        #
        #   [2/5] ✓ already done — skipping (delete .pipeline/download.done to redo)
        #   Error creating a RawFile instance from /tmp/mq-.../raw/..._01.raw:
        #   Path does not exist, neither as a file nor as a folder.
        #
        # Persistent markers over ephemeral artifacts is a lie the pipeline cannot detect.
        #
        # The resume that actually matters is content-addressed and already here: download_raw.sh's
        # have() finds each file in raw_vol at full size and skips the curl, so re-running costs
        # seconds rather than tens of GB. Markers would only have saved that have() check and a
        # ~10 s FASTA fetch.
        #
        # raw files are already in raw_vol — download_raw.sh's have() check skips them instantly
        env = {
            **os.environ,
            "PRIDEMAP_RAW_DIR": RAW_CACHE_MOUNT,
            "MAXQUANT_CMD": "maxquant",
            # The tools are in the image; preflight must not warn about a Docker daemon that is not
            # meant to exist here, in a log the UI now shows the user.
            "PRIDEMAP_TOOLS_PREINSTALLED": "1",
        }

        _log("starting run_all.sh (download stage will be skipped — files already in raw_vol)")
        _write_progress(progress_path, "running", "starting run_all.sh")
        proc = subprocess.Popen(
            ["bash", str(run_script)],
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            _log(line.rstrip())
        proc.wait()

        _vol_commit(raw_vol, what="raw cache")

        # Every failure below RAISES. Writing "error" into progress.jsonl and returning normally is
        # what made a dead run report as finished: the FunctionCall completed without an exception,
        # so _call_status saw .get() succeed and answered "done". The progress file it wrote instead
        # is not read by any endpoint. Modal's own call state is the only channel the UI's status
        # comes through, so a failure has to travel on that channel to be seen at all.
        protein_src = _find_protein_groups(work_dir) if proc.returncode == 0 else None
        problem = search_failure(proc.returncode, protein_src)
        if problem:
            _write_progress(progress_path, "error", problem)
            _log(f"FAILED: {problem}")
            raise RuntimeError(problem)

        assert protein_src is not None  # search_failure returns a reason when it is None
        out_dir = job_dir / "combined" / "txt"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "proteinGroups.txt").write_bytes(protein_src.read_bytes())
        _log("proteinGroups.txt copied to volume")
        _write_progress(progress_path, "done", "complete")

    except Exception as exc:
        _log(f"EXCEPTION: {exc}")
        _write_progress(progress_path, "error", str(exc))
        raise  # the caller's status is derived from this propagating, not from the progress file
    finally:
        _stop_shipper.set()
        _ship_stages()
        _vol_commit()
        _vol_commit(raw_vol, what="raw cache")


# ----------------------------------------------------------------------------- orchestrator (lightweight)

@app.function(
    image=image,
    volumes={VOLUME_MOUNT: vol},
    secrets=[secret],
    cpu=1,
    memory=1024,
    timeout=86400,   # Modal's maximum (24 h). _download_raw is capped at 2 h, leaving up to
                     # 22 h for MaxQuant — sufficient for all but the largest public datasets.
)
def run_maxquant(kit_bytes: bytes, accession: str) -> str:
    """Lightweight orchestrator: sequences download (cheap) then search (expensive).

    Returning this function's own call_id keeps the polling contract unchanged:
    /status maps call_id → "running" while this orchestrator is alive (i.e. while either
    phase is in progress), and → "done" when it returns. The job directory is named after
    this call_id so /results and /logs resolve correctly without any gateway changes.

    Cost model vs the old single-function approach:
      download phase: 2 CPU / 4 GB  (~10× cheaper than 16 CPU / 64 GB)
      search phase:   16 CPU / 64 GB (unchanged)
      orchestrator:   1 CPU / 1 GB  (negligible; it just blocks waiting for children)
    """
    job_id = _job_id_for_this_run(accession)
    job_dir = DATA_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    _vol_commit()   # make job_dir visible to children before they start

    log_path = job_dir / "run.log"
    progress_path = job_dir / "progress.jsonl"

    def _log(msg: str) -> None:
        print(msg, flush=True)
        with log_path.open("a") as f:
            f.write(msg + "\n")

    # Phase events go in their own file. The shipper REPLACES stages.jsonl wholesale with the
    # container's progress.jsonl, so anything written there by the orchestrator would be erased the
    # moment phase 2 starts; _read_stage_events concatenates the two instead.
    phases_path = job_dir / "phases.jsonl"

    _log(f"job_id={job_id}  accession={accession}")
    _log(f"phase 1/2: download (cheap container — {MAXQUANT_DOWNLOAD_CPU} CPU / {MAXQUANT_DOWNLOAD_MEMORY // 1024} GB)")
    _write_progress(progress_path, "running", "download phase starting")
    _write_stage_event(phases_path, "run_started", "", "pipeline start")
    _write_stage_event(phases_path, "stage_started", "download", "Download raw files from PRIDE")
    _vol_commit()

    try:
        _download_raw.remote(kit_bytes, accession, job_id)
    except Exception as exc:
        _log(f"DOWNLOAD FAILED: {exc}")
        _write_progress(progress_path, "error", f"download failed: {exc}")
        _write_stage_event(phases_path, "stage_failed", "download", f"download failed: {exc}")
        _vol_commit()
        # Raising, not returning. A return here completed the FunctionCall normally, so _call_status
        # read the job as "done" and the card reported a finished run that had downloaded nothing.
        raise

    _write_stage_event(phases_path, "stage_finished", "download", "completed")
    _vol_sync("reload")   # pull log lines written by the download worker
    _log(f"phase 2/2: MaxQuant search (expensive container — {MAXQUANT_SEARCH_CPU} CPU / {MAXQUANT_SEARCH_MEMORY // 1024} GB)")
    _write_progress(progress_path, "running", "search phase starting")
    _write_stage_event(phases_path, "stage_started", "search_dda", "MaxQuant search starting")
    _vol_commit()

    try:
        _run_maxquant_search.remote(kit_bytes, accession, job_id)
    except Exception as exc:
        _log(f"MAXQUANT FAILED: {exc}")
        _write_progress(progress_path, "error", f"maxquant failed: {exc}")
        _write_stage_event(phases_path, "stage_failed", "search_dda", f"maxquant failed: {exc}")
        _vol_commit()
        raise

    return job_id


# ----------------------------------------------------------------------------- cache eviction

@app.function(
    image=image,
    volumes={RAW_CACHE_MOUNT: raw_vol},
    cpu=1,
    memory=1024,
    timeout=3600,
)
def prune_raw_cache(max_age_days: int = 30) -> dict:
    """Remove accession directories from raw_vol that have not been accessed recently.

    Only removes a directory when EVERY raw file inside it is older than max_age_days, so
    an accession that is currently downloading or was recently used is never touched.
    Lock files (.downloading) are ignored when measuring age — only .raw files count.

    Run manually:  modal run api/modal_runner.py::prune_raw_cache
    Override age:  modal run api/modal_runner.py::prune_raw_cache --max-age-days 7
    """
    import shutil

    _vol_sync("reload", raw_vol)
    cutoff = time.time() - max_age_days * 86400
    cache_root = pathlib.Path(RAW_CACHE_MOUNT)
    removed: list[str] = []
    kept: list[str] = []

    for entry in sorted(cache_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        accession = entry.name
        raw_files = [f for f in entry.rglob("*") if f.is_file() and not f.name.startswith(".")]
        if not raw_files:
            continue
        newest_mtime = max(f.stat().st_mtime for f in raw_files)
        age_days = (time.time() - newest_mtime) / 86400
        if newest_mtime < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(f"{accession} ({age_days:.0f} days old)")
        else:
            kept.append(f"{accession} ({age_days:.1f} days old)")

    if removed:
        _vol_commit(raw_vol, what="raw cache")

    return {"removed": removed, "kept": kept, "max_age_days": max_age_days}


def _job_id_for_this_run(accession: str) -> str:
    """The id this run's directory is named after.

    It must be the id the CLIENT polls, which is the FunctionCall id that spawn_job() returns:
    /status and /results look under DATA_ROOT/{that id}. Naming the directory anything else makes
    the whole run invisible — run.log never appears (the UI shows "Waiting for output…" for hours)
    and /results 404s even after a successful search.

    Off Modal there is no call id (and older clients may not expose the accessor at all), so the
    fallback keeps local runs working with a directory name that is still unique per run.
    """
    current_id = getattr(modal, "current_function_call_id", lambda: None)
    try:
        call_id = current_id()
    except Exception:
        call_id = None
    return call_id or f"{accession}-{int(time.time())}"


def _write_progress(path: pathlib.Path, status: str, message: str) -> None:
    # Create the directory rather than assuming it. A job directory made and committed by the
    # ORCHESTRATOR container is not reliably visible in the search container after a reload, and
    # when it was missing this line raised FileNotFoundError before run_all.sh had even started —
    # then raised a second time inside the `except` that was trying to record the first. Both
    # tracebacks named progress.jsonl, so the run looked like a MaxQuant failure. A directory is
    # cheap; depending on another container having published one is not.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"status": status, "message": message, "ts": time.time()}) + "\n")


def search_failure(returncode: int, protein_src: "pathlib.Path | None") -> str | None:
    """Why this search should be treated as failed, or None if it succeeded.

    Pulled out of _run_maxquant_search so the rule can be exercised without a Modal container and a
    mounted Volume. The rule is the whole point of the fix: both of these used to write "error" into
    a progress file nobody reads and then return normally, so the FunctionCall completed cleanly and
    the job reported "done". A run that produced no protein table is not a finished run.
    """
    if returncode != 0:
        return f"run_all.sh exited {returncode} — see the run log for the stage that failed"
    if protein_src is None:
        return (
            "run_all.sh reported success but produced no proteinGroups.txt — the search did not "
            "write a protein table"
        )
    return None


def _write_stage_event(path: pathlib.Path, event: str, stage: str, message: str) -> None:
    """Append one run_all.sh-shaped stage event.

    The orchestrator's own phases were invisible: _write_progress writes {status, message} to
    progress.jsonl, which no endpoint reads, and the stage list the UI draws comes from stages.jsonl
    — which only phase 2 ships. So for the whole download phase, up to two hours, the card showed a
    bare "running" with an empty stage list.

    Emitting in the same shape run_all.sh uses means these events reduce through the same
    current_run_events/progress_by_stage/stage_view path as every other stage, rather than needing a
    second reduction that could disagree with the first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)   # same reason as _write_progress
    with path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "event": event,
                    "stage": stage,
                    "message": message,
                }
            )
            + "\n"
        )


def _find_protein_groups(work_dir: pathlib.Path) -> pathlib.Path | None:
    for p in work_dir.rglob("proteinGroups.txt"):
        return p
    return None


# ----------------------------------------------------------------------------- ASGI web endpoints

class _ModalBackend:
    """Modal-side implementation of modal_web.Backend.

    Deliberately free of FastAPI imports: this module is also loaded in the MaxQuant worker image,
    which has no FastAPI installed.
    """

    def spawn_job(self, kit_bytes: bytes, accession: str) -> str:
        # object_id is read before responding so the caller always receives a pollable id.
        return run_maxquant.spawn(kit_bytes, accession).object_id

    def accession_from_kit(self, kit_bytes: bytes) -> str:
        return _accession_from_kit(kit_bytes)

    def job_key_from_kit(self, kit_bytes: bytes, accession: str) -> str:
        return job_key(accession, selection_from_kit(kit_bytes))

    def record_job(self, accession: str, job_id: str) -> None:
        try:
            # Never displace a job that is still running. The old unconditional write orphaned it:
            # /jobs stopped reporting it while it kept billing 16 CPU / 64 GB, and /jobs is exactly
            # what the kill procedure reads to decide what is safe to terminate. The caller is
            # expected to have refused the second submission already; this is the backstop for the
            # races it cannot see.
            existing = jobs.get(accession)
            if existing and existing != job_id and _call_status(existing) == "running":
                print(
                    f"WARNING: refusing to displace running job {existing} for {accession} "
                    f"with {job_id}",
                    flush=True,
                )
                return
            jobs[accession] = job_id
        except Exception as exc:
            # Non-fatal: /jobs/{accession} has a fallback. Logged rather than swallowed because a
            # silent miss makes /jobs under-report, and an empty /jobs is exactly what gets used to
            # decide whether terminating this app's containers would destroy running work.
            print(f"WARNING: jobs dict write failed for {accession}: {exc}", flush=True)

    def job_status(self, job_id: str) -> str:
        return _call_status(job_id)

    def job_log(self, job_id: str) -> str:
        return _read_log(job_id)

    def job_progress(self, job_id: str) -> list[dict]:
        return _read_stage_events(job_id)

    def results_path(self, job_id: str) -> pathlib.Path | None:
        job_dir = _job_dir(job_id)
        if job_dir is None:
            return None
        path = job_dir / "combined" / "txt" / "proteinGroups.txt"
        return path if path.exists() else None

    def delete_job(self, job_id: str) -> dict:
        # This is the budget stop-button, so every step reports what it actually did. Swallowing a
        # failed cancel here means a 16-core job keeps billing while the caller is told it stopped.
        problems: list[str] = []

        try:
            modal.FunctionCall.from_id(job_id).cancel()
            cancelled = True
        except Exception as exc:
            cancelled = False
            problems.append(f"cancel failed: {exc}")

        job_dir = _job_dir(job_id)
        if job_dir is not None and job_dir.exists():
            import shutil
            shutil.rmtree(job_dir, ignore_errors=True)
            if job_dir.exists():
                problems.append("volume directory could not be removed")
            try:
                vol.commit()
            except Exception as exc:
                problems.append(f"volume commit failed: {exc}")

        try:
            for acc, jid in list(jobs.items()):
                if jid == job_id:
                    del jobs[acc]
        except Exception as exc:
            problems.append(f"jobs dict cleanup failed: {exc}")

        return {"deleted": not problems, "cancelled": cancelled, "problems": problems}

    def list_jobs(self) -> list[tuple[str, str]]:
        return list(jobs.items())

    def job_for_accession(self, accession: str) -> str | None:
        try:
            return jobs.get(accession)
        except Exception:
            return None


@app.function(
    image=web_image,
    volumes={VOLUME_MOUNT: vol},   # required: /status and /results read run.log and proteinGroups.txt
    secrets=[secret],
    scaledown_window=1800,  # stay warm 30 min after last request (Modal default is 300 s)
    # keep_warm=1,          # uncomment to always keep one container hot (eliminates cold starts, costs ~$0.06/h)
)
@modal.asgi_app(label="pridemap-mq")
def web() -> object:
    # Imported here rather than at module level because the MaxQuant worker image has no FastAPI,
    # and importing it there would break run_maxquant at container start. The handlers themselves
    # live in modal_web, where the FastAPI types ARE module-level and therefore always resolvable
    # by get_type_hints() — see that module's docstring for why that distinction matters.
    import modal_web

    return modal_web.build_app(
        _ModalBackend(),
        expected_token=os.environ.get("MQ_TOKEN", ""),
        fingerprint=modal_web.source_fingerprint(
            [pathlib.Path(__file__), pathlib.Path(modal_web.__file__)]
        ),
    )


# Which exception means "this call has not finished yet" is an implementation detail of the modal
# client version, and getting it wrong is silent: the status simply reads "error" for healthy jobs.
# modal/_functions.py imports no TimeoutError from modal.exception, so its `raise TimeoutError()`
# resolves to the BUILTIN — matching only modal.exception.TimeoutError never fired, and every
# running job fell through to `except Exception` and reported failure seconds after submission.
# Both are listed so either client behaviour maps correctly, and each lookup tolerates its absence
# in older or newer clients (an unresolvable name in an `except` clause raises AttributeError at
# handling time, turning a version skew into a 500).
_PENDING_ERRORS: tuple[type[BaseException], ...] = tuple(
    {TimeoutError, getattr(modal.exception, "TimeoutError", TimeoutError)}
)
# OutputExpiredError subclasses modal's TimeoutError, so it must be caught FIRST: a result that has
# been garbage-collected is a failure to surface, not work still in progress. An empty tuple never
# matches, which is the right behaviour when a client has no such class.
_EXPIRED_ERRORS: tuple[type[BaseException], ...] = tuple(
    c for c in (getattr(modal.exception, "OutputExpiredError", None),) if c is not None
)


# Reaching Modal failed, as opposed to Modal reporting that the job failed. These are transient and
# say nothing about the run: the 16-core search carries on regardless. Reporting "error" for them
# marked healthy jobs as dead permanently, because the card never polls again once it reads a
# terminal status. "running" keeps the next poll coming, which is the only way the truth arrives.
_UNREACHABLE_ERRORS: tuple[type[BaseException], ...] = tuple(
    {
        ConnectionError,
        OSError,  # includes socket.error / ssl errors raised during transport
        getattr(modal.exception, "ConnectionError", ConnectionError),
    }
)


def _call_status(job_id: str) -> str:
    """"running" | "done" | "error" for a spawned call. Shared by /status and /jobs."""
    try:
        modal.FunctionCall.from_id(job_id).get(timeout=0)
        return "done"
    except _EXPIRED_ERRORS:
        return "error"
    except _PENDING_ERRORS:
        return "running"
    except _UNREACHABLE_ERRORS:
        # Deliberately NOT terminal. We could not ask; that is not the same as a failed run.
        return "running"
    except Exception:
        # The function itself raised — a genuine failure, and since _run_maxquant_search now
        # propagates instead of swallowing, this is the path a failed search actually takes.
        return "error"


def _job_dir(job_id: str) -> pathlib.Path | None:
    """The directory holding this job's run.log and results, or None.

    Single owner of job-id → path resolution, so the worker that writes a job directory and the
    /status and /results handlers that read it cannot disagree about where output lives.
    """
    _vol_sync("reload")
    direct = DATA_ROOT / job_id
    if direct.exists():
        return direct

    # Transitional: jobs spawned before the id unification named their directory
    # "{accession}-{timestamp}" while the client was handed the FunctionCall id, so their output is
    # only reachable by reversing the jobs Dict. Delete this branch once no such job is in flight.
    try:
        accession = next((acc for acc, jid in jobs.items() if jid == job_id), None)
    except Exception:
        accession = None
    if accession:
        legacy = sorted(DATA_ROOT.glob(f"{accession}-*"))
        if legacy:
            return legacy[-1]
    return None


def _read_log(job_id: str) -> str:
    job_dir = _job_dir(job_id)
    if job_dir is None:
        return ""
    try:
        return (job_dir / "run.log").read_text(errors="replace")[-8000:]  # last 8 KB
    except FileNotFoundError:
        return ""


def _read_stage_events(job_id: str) -> list[dict]:
    """run_all.sh's stage events for this job, oldest first.

    A half-written final line is expected: the file is copied while the script is appending to it,
    so an unparseable line is dropped rather than failing the whole poll.
    """
    job_dir = _job_dir(job_id)
    if job_dir is None:
        return []
    events: list[dict] = []
    # phases.jsonl first: the orchestrator's own download phase, which run_all.sh cannot report
    # because it has not started yet. run_all.sh then emits its own run_started, and
    # current_run_events keeps only events from that point on — so these are superseded exactly when
    # the real stage records arrive, which is the behaviour we want rather than a conflict.
    for name in ("phases.jsonl", "stages.jsonl"):
        try:
            text = (job_dir / name).read_text(errors="replace")
        except FileNotFoundError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


SELECTION_KEY_FILE = "selection_key.txt"


def selection_from_kit(kit_bytes: bytes) -> str:
    """The run-selection key the kit carries, or "" for a whole-dataset run.

    `runs` never reaches this side — /run receives only the kit — so the key is computed by
    _selection_key on the API side and carried in the zip. Reading it here rather than re-deriving
    it is what keeps the two ends agreeing about the registry key.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(kit_bytes)) as zf:
            for name in zf.namelist():
                if pathlib.PurePath(name).name == SELECTION_KEY_FILE:
                    return zf.read(name).decode(errors="replace").strip()
    except Exception:
        pass
    return ""


def job_key(accession: str, selection: str) -> str:
    """The registry key for one dataset + run selection. Mirrors omics.job_key exactly."""
    return f"{accession}:{selection}" if selection else accession


def _accession_from_kit(kit_bytes: bytes) -> str:
    """Extract the dataset accession from the kit zip (best-effort; falls back to timestamp)."""
    try:
        with zipfile.ZipFile(io.BytesIO(kit_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith("mqpar.xml"):
                    text = zf.read(name).decode(errors="replace")
                    # <filePaths> contains paths like …/PXD001234/raw/…
                    import re
                    m = re.search(r"(PXD\d{6})", text)
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return f"unknown-{int(time.time())}"
