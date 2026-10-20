# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "pydantic>=2", "modal>=1.5", "pytest>=8"]
# ///
"""Contract tests for the MaxQuant licence-acceptance gate.

Author:  PRIDE Map
Created: 2026-10-20
Scope:   api/omics.py preflight_sh() — the licence check in the generated preflight.sh, on the two
         hosts it runs on (local Docker, Modal container); api/modal_runner.py run_maxquant() — the
         same check before the Modal job downloads anything.

Why this file exists: MaxQuant and the Thermo/Bruker readers it bundles are third-party software
under their own licences (README › Licence), and the kit fetches them automatically. Nothing asked
anyone to accept those terms, so the kit now refuses to run MaxQuant until MAXQUANT_ACCEPT_LICENSE=1
is set. The check is an environment variable, not a prompt, because the Modal container has no
console; there the deployer accepts once by putting it in the pridemap-mq-token secret.

A gate that always refuses passes "it refuses" as easily as a correct one, so every refusal here
comes with its counterexample: the same host, with the variable set, gets through.

run:  uv run tests/test_maxquant_licence_gate.py
      (or: uv run pytest tests/test_maxquant_licence_gate.py)
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from unittest import mock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api import modal_runner  # noqa: E402
from api.omics import preflight_sh, search_plan  # noqa: E402

LICENCE_ENV = "MAXQUANT_ACCEPT_LICENSE"

TAGS = {
    "instrument_family": "orbitrap",
    "labelling": "label_free",
    "acquisition": "dda",
    "organism": "Homo sapiens",
}
RECIPE = {
    "enzyme": {"value": ["Trypsin/P"], "missed_cleavages": 2},
    "fixed_modifications": {"value": ["Carbamidomethyl (C)"]},
    "variable_modifications": {"value": ["Oxidation (M)"]},
    "fasta": {"uniprot_proteome": "UP000005640", "organism": "Homo sapiens", "url": None},
}
RUNS = ["a.raw", "b.raw"]

# A docker that is running, has the MaxQuant image, and whose MaxQuant starts. Every call succeeds and
# prints the banner mq_probe_ok looks for, so on the local route the licence is the only open question.
DOCKER_STUB = '#!/usr/bin/env bash\necho "$*" >> "$(dirname "$0")/docker.calls"\necho "MaxQuantCmd 2.0.3.0"\n'
MAXQUANT_STUB = "#!/usr/bin/env bash\necho MaxQuantCmd 2.0.3.0\n"

# The two hosts preflight.sh runs on. "modal-container" is the env api/modal_runner.py builds for
# run_all.sh: tools baked into the image, MAXQUANT_CMD on PATH, and no Docker daemon at all.
HOSTS = {
    "local-docker": ({"docker": DOCKER_STUB}, {}),
    "modal-container": (
        {"maxquant": MAXQUANT_STUB},
        {"PRIDEMAP_TOOLS_PREINSTALLED": "1", "MAXQUANT_CMD": "maxquant"},
    ),
}


def _run_preflight(tmp_path: pathlib.Path, host: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run the real generated preflight.sh on a stubbed `host`. No fixture script: it would drift."""
    stubs, host_env = HOSTS[host]
    d = tmp_path / "kit"
    d.mkdir()
    ctx = {"accession": "PXD046787", "t": TAGS, "r": RECIPE, "raw": [], "run_labels": {}}
    (d / "preflight.sh").write_text(preflight_sh(ctx, search_plan(TAGS, RUNS)))
    bin_dir = d / "stub-bin"
    bin_dir.mkdir()
    for name, body in stubs.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "PRIDEMAP_MIN_DISK_GB": "1",
        "HOME": str(d),
        **host_env,
        **extra_env,
    }
    return subprocess.run(["bash", "preflight.sh"], cwd=d, env=env, capture_output=True, text=True, timeout=120)


def _licence_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if LICENCE_ENV in line]


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_preflight_refuses_maxquant_until_the_licence_is_accepted(tmp_path, host):
    """Scenario: nobody has accepted the MaxQuant licence on this host.

    Slice: MaxQuant licence gate (preflight).
    Given a host where every other check passes and MAXQUANT_ACCEPT_LICENSE is not set,
    When preflight runs,
    Then it fails, and the failed line names the variable and points at README › Licence, because a
      refusal that does not say how to proceed is a dead end in a log nobody can type into.
    """
    ### Given
    unset: dict[str, str] = {}

    ### When
    proc = _run_preflight(tmp_path, host, unset)
    out = proc.stdout + proc.stderr

    ### Then
    assert proc.returncode != 0, f"an unaccepted licence must stop the run:\n{out}"
    lines = _licence_lines(out)
    assert lines and all(line.lstrip().startswith("✗") for line in lines), f"expected a ✗ line naming {LICENCE_ENV}:\n{out}"
    assert "README" in lines[0], f"the refusal must say where the terms are:\n{out}"


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_preflight_lets_maxquant_run_once_the_licence_is_accepted(tmp_path, host):
    """Scenario: the counterexample — the same host, with the licence accepted.

    Slice: MaxQuant licence gate (preflight).
    Given the same host with MAXQUANT_ACCEPT_LICENSE=1,
    When preflight runs,
    Then it passes and reports the licence as accepted, so the refusal above is the variable's doing
      and not a gate that refuses everything.
    """
    ### Given
    accepted = {LICENCE_ENV: "1"}

    ### When
    proc = _run_preflight(tmp_path, host, accepted)
    out = proc.stdout + proc.stderr

    ### Then
    assert proc.returncode == 0, f"an accepted licence must not block the run:\n{out}"
    lines = _licence_lines(out)
    assert lines and all(line.lstrip().startswith("✓") for line in lines), f"expected a ✓ line naming {LICENCE_ENV}:\n{out}"


def _run_modal_job(tmp_path: pathlib.Path, env: dict[str, str]) -> tuple[mock.MagicMock, BaseException | None]:
    """Run the Modal orchestrator in-process with its workers and volumes stubbed out."""
    download = mock.MagicMock()
    search = mock.MagicMock()
    raised: BaseException | None = None
    with mock.patch.object(modal_runner, "DATA_ROOT", tmp_path), mock.patch.object(
        modal_runner, "_job_id_for_this_run", return_value="job-1"
    ), mock.patch.object(modal_runner, "_vol_commit"), mock.patch.object(modal_runner, "_vol_sync"), mock.patch.object(
        modal_runner, "_download_raw", download
    ), mock.patch.object(modal_runner, "_run_maxquant_search", search), mock.patch.dict(
        modal_runner.os.environ, env, clear=False
    ):
        if LICENCE_ENV not in env:
            modal_runner.os.environ.pop(LICENCE_ENV, None)
        try:
            modal_runner.run_maxquant.local(b"kit", "PXD046787")
        except Exception as exc:  # noqa: BLE001 - the refusal is expected to raise
            raised = exc
    return download, raised


def test_the_modal_job_refuses_before_downloading_anything(tmp_path):
    """Scenario: a Modal workspace whose secret does not accept the licence.

    Slice: MaxQuant licence gate (Modal orchestrator).
    Given MAXQUANT_ACCEPT_LICENSE is not in the orchestrator's environment,
    When a job is submitted,
    Then it fails before the download worker starts, and the job's stage list shows a failed
      preflight naming the variable — because preflight.sh only runs after the full raw download and
      a 64 GB container cold start, which is a lot to pay for an answer that was known up front.
    """
    ### Given
    no_acceptance: dict[str, str] = {}

    ### When
    download, raised = _run_modal_job(tmp_path, no_acceptance)

    ### Then
    assert raised is not None, "the job must fail, not finish as done"
    download.remote.assert_not_called()
    events = [json.loads(line) for line in (tmp_path / "job-1" / "phases.jsonl").read_text().splitlines()]
    failed = [e for e in events if e["event"] == "stage_failed"]
    assert [e["stage"] for e in failed] == ["preflight"]
    assert LICENCE_ENV in failed[0]["message"]


def test_the_modal_job_downloads_once_the_licence_is_accepted(tmp_path):
    """Scenario: the counterexample — the secret accepts the licence.

    Slice: MaxQuant licence gate (Modal orchestrator).
    Given MAXQUANT_ACCEPT_LICENSE=1 in the orchestrator's environment,
    When a job is submitted,
    Then the download worker starts as before.
    """
    ### Given
    accepted = {LICENCE_ENV: "1"}

    ### When
    download, raised = _run_modal_job(tmp_path, accepted)

    ### Then
    assert raised is None, f"an accepted licence must not stop the job: {raised!r}"
    download.remote.assert_called_once()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
