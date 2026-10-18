# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "pytest>=8"]
# ///
"""Contract tests for the shared reduction of run_all.sh progress events.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/omics.py current_run_events / progress_by_stage / stage_view — the one reduction both
         the local run and the Modal run are read through.

Why this file exists: the cloud card showed a bare "running…" for hours while the local card showed
stages, elapsed time and ETA for the identical run_all.sh, because only the local path ever reduced
progress.jsonl. Both paths now share these functions, so what they guarantee has to be pinned:
a running stage must carry the instant it started (elapsed time is derived from it, and without it a
page reload silently restarted the clock at zero), a finished one its duration, and a previous run's
failure must not bleed into the current run's stages.

run:  uv run tests/test_pipeline_progress.py
      (or: uv run pytest tests/test_pipeline_progress.py)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api.omics import current_run_events, progress_by_stage, stage_view  # noqa: E402

PLAN = {"stages": [
    {"id": "download", "label": "Download raw files from PRIDE", "detail": "2 runs", "where": "your machine"},
    {"id": "fasta", "label": "Fetch the protein database", "detail": "UniProt", "where": "your machine"},
    {"id": "upload", "label": "Hand the tables back to PRIDE Map", "detail": "", "where": "automatic"},
]}


def test_a_running_stage_reports_when_it_started():
    """Scenario: the stage in progress carries its start instant, not just its state.

    Slice: shared progress reduction.
    Given a download stage that has started and not finished,
    When the events are reduced,
    Then the stage is running and carries the event's timestamp, because elapsed time is computed
      from that instant — timing it from when a browser started watching restarted the clock at zero
      on every page reload.
    """
    ### Given
    events = [{"event": "stage_started", "stage": "download", "message": "Download raw files",
               "ts": "2026-10-18T09:14:02Z"}]

    ### When
    by_stage = progress_by_stage(events)

    ### Then
    assert by_stage["download"]["state"] == "running"
    assert by_stage["download"]["started"] == "2026-10-18T09:14:02Z"
    assert by_stage["download"]["seconds"] is None, "a stage still running has no final duration"


def test_a_finished_stage_reports_the_duration_the_run_measured():
    """Scenario: a completed stage carries its own elapsed seconds.

    Slice: shared progress reduction.
    Given a download stage that started and then finished in 412 seconds,
    When the events are reduced,
    Then the stage is done and reports those 412 seconds, which is what the run's total elapsed time
      is summed from rather than re-measured in the browser.
    """
    ### Given
    events = [
        {"event": "stage_started", "stage": "download", "message": "Download", "ts": "2026-10-18T09:14:02Z"},
        {"event": "stage_finished", "stage": "download", "message": "completed", "seconds": 412},
    ]

    ### When
    by_stage = progress_by_stage(events)

    ### Then
    assert by_stage["download"]["state"] == "done"
    assert by_stage["download"]["seconds"] == 412


def test_internal_steps_accumulate_on_their_stage():
    """Scenario: MaxQuant's own step names land on the search stage that emitted them.

    Slice: shared progress reduction.
    Given a search stage that has reported three internal MaxQuant steps,
    When the events are reduced,
    Then those steps are listed against that stage, since the per-sample progress rows and their
      ETAs are read from the step names.
    """
    ### Given
    events = [
        {"event": "stage_started", "stage": "search_dda", "message": "MaxQuant", "ts": "2026-10-18T09:20:00Z"},
        {"event": "maxquant_step", "stage": "search_dda", "message": "Feature_detection 11"},
        {"event": "maxquant_step", "stage": "search_dda", "message": "Deisotoping 11"},
        {"event": "maxquant_step", "stage": "search_dda", "message": "Deisotoping 21"},
    ]

    ### When
    by_stage = progress_by_stage(events)

    ### Then
    assert by_stage["search_dda"]["steps"] == [
        "Feature_detection 11", "Deisotoping 11", "Deisotoping 21",
    ]


def test_a_previous_runs_failure_does_not_bleed_into_this_run():
    """Scenario: a resumed run is not marked failed by the attempt before it.

    Slice: shared progress reduction.
    Given a progress file in which the first attempt failed at download and a second run then
      started and completed that same stage,
    When only the current run's events are reduced,
    Then download reads as done, because showing the earlier failure would tell the user a healthy
      resumed run had broken.
    """
    ### Given
    all_events = [
        {"event": "run_started", "stage": "", "message": "attempt 1"},
        {"event": "stage_started", "stage": "download", "message": "Download", "ts": "2026-10-18T08:00:00Z"},
        {"event": "stage_failed", "stage": "download", "message": "connection reset"},
        {"event": "run_started", "stage": "", "message": "attempt 2"},
        {"event": "stage_started", "stage": "download", "message": "Download", "ts": "2026-10-18T09:00:00Z"},
        {"event": "stage_finished", "stage": "download", "message": "completed", "seconds": 300},
    ]

    ### When
    by_stage = progress_by_stage(current_run_events(all_events))

    ### Then
    assert by_stage["download"]["state"] == "done"
    assert by_stage["download"]["message"] is None


def test_stage_view_annotates_every_planned_stage_including_the_untouched_ones():
    """Scenario: the stage list the UI draws covers the whole plan, not only what has run.

    Slice: shared progress reduction.
    Given a plan of three stages of which only the first has started,
    When the plan is annotated with the reduced progress,
    Then all three stages come back in plan order, the started one running and the rest pending, so
      the user sees what is still to come rather than a list that grows as the run proceeds.
    """
    ### Given
    by_stage = progress_by_stage([
        {"event": "stage_started", "stage": "download", "message": "Download", "ts": "2026-10-18T09:14:02Z"},
    ])

    ### When
    stages = stage_view(PLAN, by_stage)

    ### Then
    assert [s["id"] for s in stages] == ["download", "fasta", "upload"]
    assert [s["state"] for s in stages] == ["running", "pending", "pending"]
    assert stages[0]["label"] == "Download raw files from PRIDE", "plan detail must survive annotation"
    assert stages[1]["started"] is None


def test_only_the_last_six_steps_of_a_stage_are_carried():
    """Scenario: a long-running search does not grow its status payload without bound.

    Slice: shared progress reduction.
    Given a search stage that has reported twenty internal steps,
    When the plan is annotated with the reduced progress,
    Then only the last six are carried, because a 24-hour search polls every two seconds and the
      display only ever reads the latest step per sample.
    """
    ### Given
    plan = {"stages": [{"id": "search_dda", "label": "MaxQuant", "detail": "", "where": "your machine"}]}
    by_stage = progress_by_stage(
        [{"event": "stage_started", "stage": "search_dda", "message": "MaxQuant", "ts": "2026-10-18T09:00:00Z"}]
        + [{"event": "maxquant_step", "stage": "search_dda", "message": f"Step {i}"} for i in range(20)]
    )

    ### When
    stages = stage_view(plan, by_stage)

    ### Then
    assert stages[0]["steps"] == [f"Step {i}" for i in range(14, 20)]


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
