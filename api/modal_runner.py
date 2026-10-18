"""
Modal serverless runner for MaxQuant.

Deploy:  modal deploy api/modal_runner.py
Verify:  curl $MQ_SERVER_URL/health

Contract (consumed by api/omics.py lines 2276-2487):
  GET  /health                → {"ok": true}
  POST /run                   → {"job_id": "..."}   multipart field: kit (zip)
  GET  /status/{job_id}       → {"status": "running"|"done"|"error", "log": "..."}
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

app    = modal.App("pridemap-maxquant")
vol    = Volume.from_name("pridemap-runs", create_if_missing=True)
jobs   = modal.Dict.from_name("pridemap-jobs", create_if_missing=True)
secret = Secret.from_name("pridemap-mq-token")   # provides MQ_TOKEN

# Where job directories live. In a Modal container this is the Volume mount point; off Modal
# (a local run, a test, a plain host serving the same gateway) nothing is mounted at /data and it
# may not even be writable, so the root is overridable. One owner, so the worker that writes a job
# directory and the gateway that reads it can never diverge.
VOLUME_MOUNT = "/data"   # where both @app.function decorators below mount `vol`
DATA_ROOT = pathlib.Path(os.environ.get("PRIDEMAP_DATA_ROOT", VOLUME_MOUNT))


def _vol_sync(action: str) -> None:
    """Best-effort Volume commit/reload.

    Off Modal there is no Volume to talk to and these calls raise; the local filesystem is already
    coherent there, so a failure is not an error. Never let it mask a real one by propagating.
    """
    try:
        getattr(vol, action)()
    except Exception:
        pass

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
image = (
    modal.Image.from_registry(
        "quay.io/biocontainers/maxquant:2.0.3.0--py310hdfd78af_1"
    )
    .run_commands(
        # The base image (Debian buster, stripped) has wget but no apt-get, no conda,
        # and no curl. Fetch a static curl binary so download_raw.sh can use it.
        "wget -qO /usr/local/bin/curl "
        "https://github.com/moparisthebest/static-curl/releases/download/v8.6.0/curl-amd64 "
        "&& chmod +x /usr/local/bin/curl "
        "&& curl --version"
    )
    .run_commands("which maxquant")   # fail at BUILD time if binary is missing
)

# ----------------------------------------------------------------------------- worker

@app.function(
    image=image,
    volumes={VOLUME_MOUNT: vol},
    secrets=[secret],
    cpu=16,
    memory=65536,   # 64 GB
    timeout=86400,  # 24 h — NOT Modal's 300 s default
)
def run_maxquant(kit_bytes: bytes, accession: str) -> str:
    """Unpack the kit, download raw files, run MaxQuant. Returns the job_id."""
    job_id = _job_id_for_this_run(accession)
    job_dir = DATA_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    _vol_sync("commit")

    log_path = job_dir / "run.log"
    progress_path = job_dir / "progress.jsonl"

    def _log(msg: str) -> None:
        print(msg, flush=True)
        with log_path.open("a") as f:
            f.write(msg + "\n")

    # run_all.sh writes its own stage events next to itself, in the container-local work dir that
    # never reaches the Volume. Copying that file in on every sync is what lets /status report
    # stages, elapsed time and ETA for a remote job exactly as a local run does — without it the
    # client can only ever see "running". It is kept under a different name from the coarse
    # status file this module writes, because the two have different schemas and merging them
    # would corrupt both readers.
    kit_progress: list[pathlib.Path] = []

    def _ship_stages() -> None:
        for src in kit_progress:
            try:
                (job_dir / "stages.jsonl").write_bytes(src.read_bytes())
            except OSError:
                pass  # the file appears only once run_all.sh reaches its first stage

    # Background thread: ship log + progress to the Volume every 15 s so /status has live output.
    _stop_shipper = threading.Event()

    def _shipper() -> None:
        while not _stop_shipper.wait(15):
            _ship_stages()
            _vol_sync("commit")

    shipper = threading.Thread(target=_shipper, daemon=True)
    shipper.start()

    try:
        _log(f"job_id={job_id}  accession={accession}")

        # Unpack kit into container-local temp (raw files must NOT go on the Volume)
        work_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"mq-{job_id}-"))
        _log(f"work_dir={work_dir}")
        kit_progress.append(work_dir / "progress.jsonl")

        with zipfile.ZipFile(io.BytesIO(kit_bytes)) as zf:
            zf.extractall(work_dir)
        _log("kit unpacked")

        # run_all.sh drives the full pipeline (download + MaxQuant)
        run_script = work_dir / "run_all.sh"
        if not run_script.exists():
            raise FileNotFoundError("run_all.sh not found in kit")

        run_script.chmod(0o755)
        env = {
            **os.environ,
            "PRIDEMAP_RAW_DIR": str(work_dir / "cache"),
            "MAXQUANT_CMD": "maxquant",
        }

        _log("starting run_all.sh")
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

        if proc.returncode != 0:
            _write_progress(progress_path, "error", f"run_all.sh exited {proc.returncode}")
            _log(f"FAILED exit={proc.returncode}")
        else:
            # Copy proteinGroups.txt to the Volume so /results can serve it.
            protein_src = _find_protein_groups(work_dir)
            if protein_src:
                out_dir = job_dir / "combined" / "txt"
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "proteinGroups.txt").write_bytes(protein_src.read_bytes())
                _log("proteinGroups.txt copied to volume")
                _write_progress(progress_path, "done", "complete")
            else:
                _write_progress(progress_path, "error", "proteinGroups.txt not found")
                _log("FAILED: proteinGroups.txt not found")

    except Exception as exc:
        _log(f"EXCEPTION: {exc}")
        _write_progress(progress_path, "error", str(exc))
    finally:
        _stop_shipper.set()
        _ship_stages()  # final sweep: the stages that finished since the last 15 s tick
        _vol_sync("commit")

    return job_id


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
    with path.open("a") as f:
        f.write(json.dumps({"status": status, "message": message, "ts": time.time()}) + "\n")


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

    def record_job(self, accession: str, job_id: str) -> None:
        try:
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


def _call_status(job_id: str) -> str:
    """"running" | "done" | "error" for a spawned call. Shared by /status and /jobs."""
    try:
        modal.FunctionCall.from_id(job_id).get(timeout=0)
        return "done"
    except _EXPIRED_ERRORS:
        return "error"
    except _PENDING_ERRORS:
        return "running"
    except Exception:
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
    try:
        text = (job_dir / "stages.jsonl").read_text(errors="replace")
    except FileNotFoundError:
        return []
    events = []
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
