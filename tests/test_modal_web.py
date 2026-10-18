# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "python-multipart>=0.0.12", "httpx<0.28", "pytest>=8"]
# ///
"""Contract tests for the Modal MaxQuant gateway.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/modal_web.py build_app() — the request-handling invariants the deployed /run depends on.

Why this file exists: two bugs reached the deployed Modal gateway on 2026-10-18 and together cost a
full debugging session, and both were reproducible locally in under a second with TestClient.

  1. `from __future__ import annotations` made every annotation lazy. FastAPI resolves lazy
     annotations against the *module* globals only, and `Request` was imported inside `def web():`,
     so it was unresolvable and FastAPI silently reclassified `request: Request` as a required query
     parameter. Every upload returned 422 `{"loc": ["query", "request"]}` — an error naming neither
     the cause nor the file.
  2. The gateway image never installed `python-multipart`, so `await request.form()` raised
     `AssertionError` and every upload returned a bare 500 with no clue in the response body.

Neither is visible by reading imports: multipart support is an implicit dependency of a *feature*,
not of an import statement. Only an actual request through the stack catches them, which is what
every test below does. Run these before `modal deploy`.

run:  uv run tests/test_modal_web.py
      (or: uv run pytest tests/test_modal_web.py)
"""
import io
import pathlib
import sys
import zipfile

from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api import modal_web  # noqa: E402
from api.modal_web import build_app, source_fingerprint  # noqa: E402

TOKEN = "test-token"


def _kit_bytes(accession: str = "PXD046787") -> bytes:
    """A minimal kit zip: enough to be a real multipart upload, not a placeholder string."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mqpar.xml", f"<MaxQuantParams><filePaths>/data/{accession}/raw/a.raw</filePaths></MaxQuantParams>")
        zf.writestr("run_all.sh", "#!/bin/bash\necho hi\n")
    return buf.getvalue()


class FakeBackend:
    """Records what the gateway asked for. No Modal, no Volume, no network."""

    def __init__(self) -> None:
        self.spawned: list[tuple[int, str]] = []
        self.recorded: list[tuple[str, str]] = []
        self.jobs: dict[str, str] = {}

    def spawn_job(self, kit_bytes: bytes, accession: str) -> str:
        self.spawned.append((len(kit_bytes), accession))
        return "fc-123"

    def accession_from_kit(self, kit_bytes: bytes) -> str:
        return "PXD046787"

    def job_key_from_kit(self, kit_bytes: bytes, accession: str) -> str:
        # The real backend reads the selection key the kit carries; the gateway only has to pass
        # whatever it returns through to record_job, which is what this fake lets the test observe.
        return accession

    def record_job(self, accession: str, job_id: str) -> None:
        self.recorded.append((accession, job_id))
        self.jobs[accession] = job_id

    def job_status(self, job_id: str) -> str:
        return "running"

    def job_log(self, job_id: str) -> str:
        return "log line"

    def job_progress(self, job_id: str) -> list[dict]:
        return [{"event": "stage_started", "stage": "download", "message": "Download raw files"}]

    def results_path(self, job_id: str) -> pathlib.Path | None:
        return None

    def delete_job(self, job_id: str) -> dict:
        return {"deleted": True, "cancelled": True, "problems": []}

    def list_jobs(self) -> list[tuple[str, str]]:
        return list(self.jobs.items())

    def job_for_accession(self, accession: str) -> str | None:
        return self.jobs.get(accession)


def _client(backend: FakeBackend | None = None, token: str = TOKEN) -> tuple[TestClient, FakeBackend]:
    be = backend or FakeBackend()
    return TestClient(build_app(be, expected_token=token, fingerprint="abc123")), be


def test_run_accepts_a_multipart_kit():
    """Scenario: a kit upload reaches the handler and spawns exactly one job.

    Slice: modal gateway — POST /run.

    Given a gateway built with a fake backend, when a kit zip is POSTed as multipart form data,
    then the response carries the spawned job_id and the backend saw the kit bytes. This is the test
    that fails with 422 `{"loc": ["query", "request"]}` the moment `Request` stops resolving — the
    exact production failure of 2026-10-18, which no import check would have caught.
    """
    ### Given
    client, backend = _client()
    kit = _kit_bytes()

    ### When
    r = client.post(
        "/run",
        files={"kit": ("kit.zip", kit, "application/zip")},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    ### Then
    assert r.status_code == 200, (
        f"expected 200, got {r.status_code}: {r.text}. A 422 naming 'query'/'request' means FastAPI "
        "could not resolve the Request annotation and demoted it to a query parameter."
    )
    assert r.json()["job_id"] == "fc-123"
    assert backend.spawned == [(len(kit), "PXD046787")], "the handler must forward the kit bytes once"
    assert backend.recorded == [("PXD046787", "fc-123")], "the job id must be recorded before responding"


def test_status_relays_the_runs_own_stage_events():
    """Scenario: /status carries run_all.sh's stage events, not just a coarse status word.

    Slice: modal gateway — GET /status.

    Given a gateway whose backend can read the shipped stage file, when a job's status is polled,
    then the reply carries `progress` alongside status and log. Without that key the client can only
    ever draw "running": elapsed time and ETA for a remote job are computed from these events.
    """
    ### Given
    client, _ = _client()

    ### When
    r = client.get("/status/fc-123", headers={"Authorization": f"Bearer {TOKEN}"})

    ### Then
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "running"
    assert body["log"] == "log line"
    assert body["progress"] == [
        {"event": "stage_started", "stage": "download", "message": "Download raw files"}
    ], "the gateway must relay stage events verbatim — the caller reduces them, not the gateway"


def test_missing_kit_field_is_a_client_error_not_a_server_error():
    """Scenario: a POST with no kit part is rejected as a 400, never a 500.

    Slice: modal gateway — POST /run.

    Given a gateway, when /run is POSTed with no multipart body at all, then the reply is 400 and
    names the missing field. A 500 here means form parsing itself blew up — which is precisely how
    the absent python-multipart dependency presented in production: an opaque
    "Internal Server Error" with the real reason only in the container log.
    """
    ### Given
    client, _ = _client()

    ### When
    r = client.post("/run", headers={"Authorization": f"Bearer {TOKEN}"})

    ### Then
    assert r.status_code == 400, (
        f"expected 400, got {r.status_code}: {r.text}. A 500 means request.form() raised — check that "
        "python-multipart is installed in the gateway image."
    )
    assert "kit" in r.text


def test_run_rejects_a_wrong_token_before_touching_the_backend():
    """Scenario: a bad bearer token is refused and spawns nothing.

    Slice: modal gateway — auth.

    Given a gateway with a configured token, when /run is called with the wrong one, then the reply
    is 401 and no job was spawned, because this endpoint starts 16-core billable containers and an
    auth check that runs after the spawn is not an auth check.
    """
    ### Given
    client, backend = _client()

    ### When
    r = client.post(
        "/run",
        files={"kit": ("kit.zip", _kit_bytes(), "application/zip")},
        headers={"Authorization": "Bearer wrong"},
    )

    ### Then
    assert r.status_code == 401
    assert backend.spawned == [], "a rejected request must never reach spawn_job"


def test_health_reports_form_parsing_capability():
    """Scenario: /health states whether the image can parse uploads at all.

    Slice: modal gateway — observability.

    Given a gateway whose multipart support has been removed, when /health is read, then
    form_parsing is False. This is the whole point of the flag: a missing python-multipart becomes
    one readable field instead of a 500 that only appears once a real kit is uploaded.
    """
    ### Given
    client, _ = _client()
    original = modal_web.parse_options_header

    ### When
    healthy = client.get("/health").json()
    try:
        # Patch the name the handler actually reads: modal_web bound it at import time, so
        # patching starlette.requests would leave the handler's own global untouched.
        modal_web.parse_options_header = None
        broken_client, _ = _client()
        broken = broken_client.get("/health").json()
    finally:
        modal_web.parse_options_header = original

    ### Then
    assert healthy["form_parsing"] is True, "python-multipart should be installed for these tests"
    assert broken["form_parsing"] is False, "/health must report lost form-parsing capability"


def test_health_reports_a_source_fingerprint():
    """Scenario: /health identifies which build is answering.

    Slice: modal gateway — observability.

    Given a gateway built with a known fingerprint, when /health is read, then that fingerprint is
    returned. Modal keeps containers warm for scaledown_window seconds, so a fresh deploy does not
    stop older containers from serving; on 2026-10-18 three deploys appeared to have no effect and
    the only evidence of staleness was a traceback's line numbers.
    """
    ### Given
    client, _ = _client()

    ### When
    body = client.get("/health").json()

    ### Then
    assert body["ok"] is True
    assert body["source_sha"] == "abc123"


def test_source_fingerprint_changes_when_source_changes(tmp_path):
    """Scenario: the fingerprint tracks file contents, not file names.

    Slice: modal gateway — observability.

    Given two source files, when one byte of one of them changes, then the digest changes, because a
    fingerprint that ignored content would report a stale container as current.
    """
    ### Given
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("print(1)\n")
    b.write_text("print(2)\n")
    before = source_fingerprint([a, b])

    ### When
    b.write_text("print(3)\n")
    after = source_fingerprint([a, b])

    ### Then
    assert before != after, "the digest must follow file contents"
    assert source_fingerprint([a, b]) == after, "the digest must be stable for unchanged input"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
