# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "pydantic>=2", "pytest>=8"]
# ///
"""Contract tests for the checks that run before a cloud job is submitted.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/omics.py remote_submission_problems() — the gate between "Run on cloud" and spending.

Why this file exists: the remote route had no preconditions at all. It woke a Modal container (up
to 120 s of cold start), built the kit, uploaded it and spawned a 16 CPU / 64 GB job, and only then
discovered that the dataset was DIA and MaxQuant could not search it, or that MQ_TOKEN was unset and
the upload would be rejected as anonymous, or that the kit was missing run_all.sh. The local route
refuses these inside run_all.sh; the cloud route paid for them first.

The promise under test is not merely "it refuses" but "it reports EVERY problem at once". A
validator that stops at the first one costs the user a Modal cold start per defect, and that promise
regresses silently without a case that pins it.

run:  uv run tests/test_remote_submission_guard.py
      (or: uv run pytest tests/test_remote_submission_guard.py)
"""
from __future__ import annotations

import io
import pathlib
import sys
import zipfile
from unittest import mock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api import omics  # noqa: E402
from api.omics import _REQUIRED_KIT_FILES, remote_submission_problems  # noqa: E402

ACCESSION = "PXD046787"

# What a MaxQuant-runnable plan looks like to this gate. Only the two fields the gate reads are
# modelled; building a real plan needs the dataset database and is the plan builder's own concern.
RUNNABLE = {"maxquant_offered": True, "maxquant_reason": None}
NOT_RUNNABLE = {
    "maxquant_offered": False,
    "maxquant_reason": "MaxQuant is a bottom-up DDA search engine; this dataset is tagged dia.",
}


def _kit(*, omit: tuple[str, ...] = ()) -> bytes:
    """A kit zip containing every required file except those named in `omit`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in _REQUIRED_KIT_FILES:
            if name not in omit:
                zf.writestr(name, "# stub\n")
    return buf.getvalue()


def _problems(plan: dict, kit: bytes, token: str = "a-real-token") -> list[str]:
    with mock.patch.object(omics, "dataset_search_plan", return_value=plan), mock.patch.dict(
        omics.os.environ, {"MQ_TOKEN": token}, clear=False
    ):
        return remote_submission_problems(ACCESSION, "", kit)


def test_a_runnable_dataset_with_a_complete_kit_is_accepted():
    """Scenario: the gate lets a submission that can succeed through.

    Slice: remote submission guard.
    Given a MaxQuant-runnable dataset, a complete kit and a configured token,
    When the submission is checked,
    Then no problems are reported — the counterexample without which every test below is equally
      satisfied by a gate that refuses everything.
    """
    ### Given / When
    problems = _problems(RUNNABLE, _kit())

    ### Then
    assert problems == []


def test_a_dataset_maxquant_cannot_search_is_refused_with_the_reason():
    """Scenario: a DIA dataset is not handed to a MaxQuant runner.

    Slice: remote submission guard.
    Given a dataset whose plan says MaxQuant is not offered, and the reason why,
    When the submission is checked,
    Then that reason is reported, because the cloud route had no engine gate at all and would have
      spawned a 64 GB search that cannot read the data.
    """
    ### Given / When
    problems = _problems(NOT_RUNNABLE, _kit())

    ### Then
    assert len(problems) == 1
    assert "DDA search engine" in problems[0], "the plan's own reason must reach the user"


def test_a_missing_token_is_caught_before_the_upload_is_rejected():
    """Scenario: an unset MQ_TOKEN is named as a setting, not as a 401.

    Slice: remote submission guard.
    Given a configured, runnable dataset but no MQ_TOKEN,
    When the submission is checked,
    Then the missing token is reported, because an empty token is sent as no header at all and the
      far side reads that as anonymous — so the misconfiguration used to surface as an opaque 401
      from /run after the cold start had already been paid for.
    """
    ### Given / When
    problems = _problems(RUNNABLE, _kit(), token="")

    ### Then
    assert len(problems) == 1
    assert "MQ_TOKEN" in problems[0]


@pytest.mark.parametrize("missing", sorted(_REQUIRED_KIT_FILES))
def test_a_kit_missing_a_required_file_is_refused(missing):
    """Scenario: an incomplete kit is caught here rather than inside the container.

    Slice: remote submission guard.
    Given a kit with one required file absent,
    When the submission is checked,
    Then that file is named, because its absence was previously discovered by the container after
      the upload — the run failed minutes in, having already been scheduled and billed.
    """
    ### Given / When
    problems = _problems(RUNNABLE, _kit(omit=(missing,)))

    ### Then
    assert len(problems) == 1
    assert missing in problems[0]


def test_something_that_is_not_a_zip_is_refused():
    """Scenario: a corrupt kit is reported as such rather than crashing the request.

    Slice: remote submission guard.
    Given bytes that are not a zip archive,
    When the submission is checked,
    Then a readable problem is reported rather than a BadZipFile escaping as a 500.
    """
    ### Given / When
    problems = _problems(RUNNABLE, b"not a zip at all")

    ### Then
    assert len(problems) == 1
    assert "zip" in problems[0]


def test_every_problem_is_reported_not_just_the_first():
    """Scenario: three defects at once produce three problems.

    Slice: remote submission guard.
    Given a dataset MaxQuant cannot search, no MQ_TOKEN, and a kit missing run_all.sh,
    When the submission is checked,
    Then all three are reported together — this is the actual promise of the gate. Stopping at the
      first costs the user one Modal cold start per defect, and without this case the gate silently
      regresses to first-error-wins while every other test here still passes.
    """
    ### Given / When
    problems = _problems(NOT_RUNNABLE, _kit(omit=("run_all.sh",)), token="")

    ### Then
    assert len(problems) == 3, f"expected the engine, token and kit problems together, got {problems}"
    joined = " | ".join(problems)
    assert "DDA search engine" in joined
    assert "MQ_TOKEN" in joined
    assert "run_all.sh" in joined


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
