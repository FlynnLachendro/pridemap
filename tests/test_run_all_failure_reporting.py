# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "pydantic>=2", "pytest>=8"]
# ///
"""Contract tests for failure reporting in the generated run_all.sh.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/omics.py _stage_lib() run_step / _on_exit, and the stage bodies in run_all_sh() — the
         path by which a dead stage tells the UI what killed it.

Why this file exists: every stage used to run its sub-script as

    bash x.sh 2>&1 | while IFS= read -r l; do step "$l"; done
    [ "${PIPESTATUS[0]}" = "0" ] || fail "..."

and under `set -euo pipefail` a failing pipeline exits the script THERE, so the guard on the next
line never ran. fail() is the only emitter of stage_failed, so it was unreachable in all four piped
stages. Observed consequence on a real cloud run: the API reported status "done" while the only
stage that had started was still "running", and nothing anywhere said the run had died at preflight.

These tests drive the REAL generator and REAL bash. A test that feeds a hand-written stage_failed
event into progress_by_stage passes both before and after the fix — the reduction was never broken;
the emission was missing — so an event-level test here would be fixture theatre.

run:  uv run tests/test_run_all_failure_reporting.py
      (or: uv run pytest tests/test_run_all_failure_reporting.py)
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api.omics import run_all_sh, search_plan  # noqa: E402

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

# The sub-script each piped stage runs, and the stage id that owns it. Parametrising over this map
# rather than spot-checking one stage is deliberate: the bug was present in four places at once, so
# a single green stage proves nothing about the other three.
PIPED_STAGES = {
    "preflight": "preflight.sh",
    "download": "download_raw.sh",
    "fasta": "download_fasta.sh",
}


def _generated() -> str:
    """The real generator output. No fixture script: a fixture would drift from the generator."""
    ctx = {
        "accession": "PXD046787",
        "t": TAGS,
        "r": RECIPE,
        "raw": [],
        "run_labels": {},
        "api_base": "",
        "web_base": "",
    }
    return run_all_sh(ctx, search_plan(TAGS, RUNS), mq_cmd="true")


def _kit(tmp_path: pathlib.Path, failing: str | None, exit_code: int = 7) -> pathlib.Path:
    """A runnable kit whose sub-scripts all succeed, except `failing` which exits `exit_code`.

    Each stub drops a sentinel file when it runs, so a test can tell "this stage was skipped" from
    "this stage never existed" — a stage_skipped event alone cannot distinguish the two.
    """
    d = tmp_path / "kit"
    d.mkdir()
    (d / "run_all.sh").write_text(_generated())
    for stage, script in PIPED_STAGES.items():
        if script == failing:
            body = f'echo "working on it"\necho "it broke" >&2\ntouch ran_{stage}\nexit {exit_code}\n'
        else:
            body = f'touch ran_{stage}\nexit 0\n'
        (d / script).write_text(body)
    # The fasta stage checks the file it just "downloaded" is non-empty.
    (d / "fasta").mkdir(exist_ok=True)
    for f in (d / "fasta").parent.glob("*"):
        pass
    return d


def _run(d: pathlib.Path) -> tuple[int, list[dict]]:
    """Run the kit and return (exit code, parsed progress events)."""
    proc = subprocess.run(
        ["bash", "run_all.sh"], cwd=d, capture_output=True, text=True, timeout=120
    )
    events = []
    progress = d / "progress.jsonl"
    if progress.exists():
        for line in progress.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a half-written final line is not this test's concern
    return proc.returncode, events


def _of(events: list[dict], event: str, stage: str | None = None) -> list[dict]:
    return [e for e in events if e.get("event") == event and (stage is None or e.get("stage") == stage)]


def test_the_generated_script_still_fails_fast_on_an_unhandled_error():
    """Scenario: the safety flags the whole failure model rests on are still in force.

    Slice: run_all.sh failure reporting.
    Given the generated pipeline script,
    When its options line is read,
    Then it still sets -euo pipefail, because deleting those flags would make the old PIPESTATUS
      guard reachable and turn every test in this file green while removing the protection that
      stops a broken stage from running on into the next one.
    """
    ### Given / When
    script = _generated()

    ### Then
    assert "set -euo pipefail" in script, (
        "the fix must make failure REPORTABLE under these flags, not pass by removing them"
    )


@pytest.mark.parametrize("stage,script", sorted(PIPED_STAGES.items()))
def test_a_failing_piped_stage_reports_why_it_stopped(tmp_path, stage, script):
    """Scenario: a sub-script that exits non-zero names the stage it killed and why.

    Slice: run_all.sh failure reporting.
    Given a kit in which one stage's sub-script exits non-zero,
    When the pipeline runs,
    Then exactly one stage_failed is emitted for that stage carrying a non-empty message, and the
      script exits non-zero — because the UI renders the reason from this event, and with it missing
      a dead run showed as a stage stuck at "running" under an overall status of "done".
    """
    ### Given
    d = _kit(tmp_path, failing=script)

    ### When
    code, events = _run(d)

    ### Then
    assert code != 0, "a failed stage must fail the run"
    # The RED discriminator: without stage_started, a harness that died before reaching this stage
    # would also show "no stage_failed", and would read as the bug rather than as a broken test.
    assert _of(events, "stage_started", stage), f"{stage} never started — the harness, not the code"
    failures = _of(events, "stage_failed", stage)
    assert len(failures) == 1, f"expected exactly one stage_failed for {stage}, got {len(failures)}"
    assert failures[0]["message"].strip(), "a failure with no reason is the bug this file exists for"


@pytest.mark.parametrize("stage,script", sorted(PIPED_STAGES.items()))
def test_a_failing_stage_echoes_the_output_that_explains_it(tmp_path, stage, script):
    """Scenario: the sub-script's own words survive into the progress stream.

    Slice: run_all.sh failure reporting.
    Given a stage whose sub-script writes to stdout and stderr before exiting non-zero,
    When the pipeline runs,
    Then both lines arrive as substages of that stage, because the failure message alone says which
      stage died and the sub-script's output is what says what it was doing when it did.
    """
    ### Given
    d = _kit(tmp_path, failing=script)

    ### When
    _, events = _run(d)

    ### Then
    said = {e["message"].strip() for e in _of(events, "substage", stage)}
    assert "working on it" in said, "stdout must be relayed"
    assert "it broke" in said, "stderr must be relayed — it is usually the only diagnosis there is"


def test_a_stage_that_dies_without_calling_fail_still_reports(tmp_path):
    """Scenario: an unexplained death is still attributed to the stage it happened in.

    Slice: run_all.sh failure reporting.
    Given a stage killed by a path with no explicit fail() — here a sub-script that is missing
      outright, so bash aborts under set -e rather than running run_step's failure branch,
    When the pipeline runs,
    Then a stage_failed is still emitted for that stage, because "no future unreachable path can
      leave a stage pinned at running" is the promise the EXIT trap exists to keep, and a promise
      with no test regresses silently.
    """
    ### Given
    d = _kit(tmp_path, failing=None)
    (d / "download_raw.sh").unlink()  # nothing calls fail(); bash dies on its own

    ### When
    code, events = _run(d)

    ### Then
    assert code != 0
    failures = _of(events, "stage_failed", "download")
    assert len(failures) == 1, "the EXIT trap must attribute an unexplained death to its stage"
    assert failures[0]["message"].strip()


def test_a_failure_is_reported_once_not_twice(tmp_path):
    """Scenario: fail() and the EXIT trap do not both report the same death.

    Slice: run_all.sh failure reporting.
    Given a stage whose sub-script exits non-zero, so fail() runs and the script then exits,
    When the pipeline runs,
    Then exactly one stage_failed exists in the whole run, because the trap also fires on that exit
      and a second, vaguer event would overwrite the specific reason the user needs to read.
    """
    ### Given
    d = _kit(tmp_path, failing="download_raw.sh")

    ### When
    _, events = _run(d)

    ### Then
    assert len(_of(events, "stage_failed")) == 1


def test_stages_after_the_failure_never_start(tmp_path):
    """Scenario: a dead run stops rather than carrying on into work that cannot succeed.

    Slice: run_all.sh failure reporting.
    Given a kit whose download stage fails,
    When the pipeline runs,
    Then the fasta stage's sentinel is absent, because a FASTA fetched for a search that will never
      run is wasted work, and on the cloud path it is wasted money.
    """
    ### Given
    d = _kit(tmp_path, failing="download_raw.sh")

    ### When
    _run(d)

    ### Then
    assert (d / "ran_download").exists(), "the failing stage itself must have run"
    assert not (d / "ran_fasta").exists(), "work after a failure must not start"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
