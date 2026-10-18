"""FastAPI gateway for the Modal MaxQuant runner.

This lives in its own module for one structural reason: FastAPI types are imported at MODULE level
here. FastAPI resolves string type annotations with `get_type_hints()`, which only ever looks in the
function's *module* globals — never in an enclosing function's locals. So a handler written inside
`def web():` whose `Request` was imported inside that same function is unresolvable the moment
annotations become lazy (`from __future__ import annotations`), and FastAPI silently demotes
`request: Request` to a required *query parameter*. The symptom is a 422 reading
`{"loc": ["query", "request"], "msg": "Field required"}` on every upload, which names neither the
real cause nor this file. Keeping these imports at module level makes that failure unreachable.

Do not add `from __future__ import annotations` to this module.

Every Modal-specific operation is injected, so the app can be built and exercised with
`fastapi.testclient.TestClient` without Modal, a Volume, or a deployment. See
tests/test_modal_web.py.
"""
import hashlib
import pathlib
from typing import Iterable, Protocol

import fastapi
import starlette
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

# Starlette sets this to None when python-multipart is absent, then asserts on it inside
# request.form(). Surfacing it on /health turns a 500-on-upload into a visible capability flag.
from starlette.requests import parse_options_header


def source_fingerprint(paths: Iterable[pathlib.Path]) -> str:
    """Short digest of the gateway's own source, reported by /health.

    Modal keeps containers warm for `scaledown_window` seconds, so a deploy does not stop older
    containers from serving requests. Without this, "is my fix live?" is unanswerable except by
    reading a traceback's line numbers.
    """
    h = hashlib.sha256()
    for p in sorted(paths):
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()[:12]


class Backend(Protocol):
    """The Modal-side operations the gateway needs. Implemented for real in modal_runner.py."""

    def spawn_job(self, kit_bytes: bytes, accession: str) -> str: ...
    def accession_from_kit(self, kit_bytes: bytes) -> str: ...
    def record_job(self, accession: str, job_id: str) -> None: ...
    def job_status(self, job_id: str) -> str: ...
    def job_log(self, job_id: str) -> str: ...
    def results_path(self, job_id: str) -> pathlib.Path | None: ...
    def delete_job(self, job_id: str) -> dict: ...
    def list_jobs(self) -> list[tuple[str, str]]: ...
    def job_for_accession(self, accession: str) -> str | None: ...


def build_app(backend: Backend, *, expected_token: str = "", fingerprint: str = "unknown") -> FastAPI:
    api = FastAPI()

    def _check_token(authorization: str) -> None:
        if not expected_token:
            return  # no token configured — open (dev mode)
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or token != expected_token:
            raise HTTPException(401, "Unauthorized")

    @api.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "source_sha": fingerprint,
            "form_parsing": parse_options_header is not None,
            "fastapi": fastapi.__version__,
            "starlette": starlette.__version__,
        }

    @api.post("/run")
    async def run(request: Request, authorization: str = Header(default="")) -> dict:
        _check_token(authorization)
        form = await request.form()
        kit_file = form.get("kit")
        if kit_file is None:
            raise HTTPException(400, "missing multipart field 'kit'")
        kit_bytes = await kit_file.read()  # type: ignore[union-attr]

        accession = backend.accession_from_kit(kit_bytes)
        job_id = backend.spawn_job(kit_bytes, accession)
        backend.record_job(accession, job_id)
        return {"job_id": job_id}

    @api.get("/status/{job_id}")
    def status(job_id: str, authorization: str = Header(default="")) -> dict:
        _check_token(authorization)
        return {"status": backend.job_status(job_id), "log": backend.job_log(job_id)}

    @api.get("/results/{job_id}")
    def results(job_id: str, authorization: str = Header(default="")) -> StreamingResponse:
        _check_token(authorization)
        protein_path = backend.results_path(job_id)
        if protein_path is None:
            raise HTTPException(404, "proteinGroups.txt not found — job may still be running or failed")
        return StreamingResponse(
            open(protein_path, "rb"),
            media_type="text/tab-separated-values",
            headers={"Content-Disposition": 'attachment; filename="proteinGroups.txt"'},
        )

    @api.delete("/job/{job_id}")
    def delete_job(job_id: str, authorization: str = Header(default="")) -> dict:
        _check_token(authorization)
        return backend.delete_job(job_id)

    @api.get("/jobs")
    def list_jobs(authorization: str = Header(default="")) -> dict:
        """Every job the Dict knows about, with live status — find orphans without the dashboard."""
        _check_token(authorization)
        try:
            entries = backend.list_jobs()
        except Exception as exc:
            raise HTTPException(503, f"jobs dict unreadable: {exc}")
        return {"jobs": [{"accession": acc, "job_id": jid, "status": backend.job_status(jid)}
                         for acc, jid in entries]}

    @api.get("/jobs/{accession}")
    def jobs_for_accession(accession: str, authorization: str = Header(default="")) -> dict:
        _check_token(authorization)
        return {"job_id": backend.job_for_accession(accession)}

    return api
