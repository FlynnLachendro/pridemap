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
    volumes={"/data": vol},
    secrets=[secret],
    cpu=16,
    memory=65536,   # 64 GB
    timeout=86400,  # 24 h — NOT Modal's 300 s default
)
def run_maxquant(kit_bytes: bytes, accession: str) -> str:
    """Unpack the kit, download raw files, run MaxQuant. Returns the job_id."""
    job_id = f"{accession}-{int(time.time())}"
    job_dir = pathlib.Path(f"/data/{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    vol.commit()

    log_path = job_dir / "run.log"
    progress_path = job_dir / "progress.jsonl"

    def _log(msg: str) -> None:
        print(msg, flush=True)
        with log_path.open("a") as f:
            f.write(msg + "\n")

    # Background thread: ship log + progress to the Volume every 15 s so /status has live output.
    _stop_shipper = threading.Event()

    def _shipper() -> None:
        while not _stop_shipper.wait(15):
            try:
                vol.commit()
            except Exception:
                pass

    shipper = threading.Thread(target=_shipper, daemon=True)
    shipper.start()

    try:
        _log(f"job_id={job_id}  accession={accession}")

        # Unpack kit into container-local temp (raw files must NOT go on the Volume)
        work_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"mq-{job_id}-"))
        _log(f"work_dir={work_dir}")

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
        try:
            vol.commit()
        except Exception:
            pass

    return job_id


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

    def results_path(self, job_id: str) -> pathlib.Path | None:
        vol.reload()
        path = pathlib.Path(f"/data/{job_id}/combined/txt/proteinGroups.txt")
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

        job_dir = pathlib.Path(f"/data/{job_id}")
        if job_dir.exists():
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
    volumes={"/data": vol},   # required: /status and /results read run.log and proteinGroups.txt
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


def _call_status(job_id: str) -> str:
    """"running" | "done" | "error" for a spawned call. Shared by /status and /jobs."""
    try:
        modal.FunctionCall.from_id(job_id).get(timeout=0)
        return "done"
    except modal.exception.TimeoutError:
        # Still running — must be caught BEFORE the generic Exception, or every
        # healthy job reports as failed.
        return "running"
    except Exception:
        return "error"


def _read_log(job_id: str) -> str:
    vol.reload()
    log_path = pathlib.Path(f"/data/{job_id}/run.log")
    try:
        return log_path.read_text(errors="replace")[-8000:]  # last 8 KB
    except FileNotFoundError:
        return ""


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
