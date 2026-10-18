# /// script
# requires-python = ">=3.11"
# dependencies = ["modal>=1.5", "pytest>=8"]
# ///
"""Contract tests for the Modal MaxQuant runner's job-status mapping.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/modal_runner.py _call_status() — the poll-outcome → status mapping /status reports.

Why this file exists: a running job reported "error" within seconds of submission, so the UI marked
download, FASTA and the search as failed while the container was in fact downloading raw files
normally. The cause was the exception type: modal's poll raises the BUILTIN TimeoutError for
"inputs not finished yet", while _call_status caught only modal.exception.TimeoutError — which no
poll ever raises — so every healthy job fell through to `except Exception` and reported failure.
Nothing about this is visible by reading the code; it needs the mapping exercised per exception.

run:  uv run tests/test_modal_runner.py
      (or: uv run pytest tests/test_modal_runner.py)
"""
import json
import pathlib
import sys
from unittest import mock

import modal
import modal.exception

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api import modal_runner  # noqa: E402
from api.modal_runner import (  # noqa: E402
    _call_status,
    _job_dir,
    _job_id_for_this_run,
    _read_stage_events,
)

JOB_ID = "fc-01M57AXN6YWY8BYNX32YPYP4TH"


class _ModalWithout:
    """A stand-in for the modal module with one attribute removed, to model client version skew."""

    def __init__(self, missing: str):
        self._missing = missing

    def __getattr__(self, name: str):
        if name == self._missing:
            raise AttributeError(name)
        return getattr(modal, name)


class _Call:
    """A FunctionCall whose get() reproduces one poll outcome."""

    def __init__(self, outcome):
        self._outcome = outcome

    def get(self, timeout=None):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


def _status_for(outcome: object) -> str:
    with mock.patch("modal.FunctionCall.from_id", return_value=_Call(outcome)):
        return _call_status(JOB_ID)


def test_builtin_timeout_means_running():
    """Scenario: the gateway polls a job whose inputs have not finished.

    Slice: job status reporting.
    Given a spawned call that modal reports as unfinished by raising the builtin TimeoutError,
    When the gateway maps that poll outcome to a status,
    Then it reports "running" — the exact case that regressed to "error".
    """
    ### Given / When
    status = _status_for(TimeoutError())

    ### Then
    assert status == "running"


def test_modal_timeout_also_means_running():
    """Scenario: a modal client version raises its own TimeoutError for an unfinished poll.

    Slice: job status reporting.
    Given a spawned call that raises modal.exception.TimeoutError,
    When the gateway maps that poll outcome to a status,
    Then it still reports "running", so the mapping does not depend on which class modal picks.
    """
    ### Given / When
    status = _status_for(modal.exception.TimeoutError())

    ### Then
    assert status == "running"


def test_returned_value_means_done():
    """Scenario: the worker has finished and its result is available.

    Slice: job status reporting.
    Given a spawned call whose get() returns the worker's job_id,
    When the gateway maps that poll outcome to a status,
    Then it reports "done".
    """
    ### Given / When
    status = _status_for(JOB_ID)

    ### Then
    assert status == "done"


def test_expired_output_means_error():
    """Scenario: the result has been garbage-collected and can never be fetched.

    Slice: job status reporting.
    Given a spawned call that raises OutputExpiredError — a modal TimeoutError SUBCLASS,
    When the gateway maps that poll outcome to a status,
    Then it reports "error" rather than being swept up by the "running" branch, because a result
    that is gone is a failure to surface, not work still in progress.
    """
    ### Given / When
    status = _status_for(modal.exception.OutputExpiredError())

    ### Then
    assert status == "error"


def test_remote_exception_means_error():
    """Scenario: the worker itself raised and modal re-raises on get().

    Slice: job status reporting.
    Given a spawned call that raises an ordinary exception,
    When the gateway maps that poll outcome to a status,
    Then it reports "error".
    """
    ### Given / When
    status = _status_for(RuntimeError("worker blew up"))

    ### Then
    assert status == "error"


def test_job_directory_is_named_after_the_polled_call_id():
    """Scenario: the worker starts inside a Modal container and names its job directory.

    Slice: job output addressing.
    Given a container whose current FunctionCall id is the id the client was handed,
    When the worker decides what to call its job directory,
    Then it uses that same id, because /status and /results look there — the original mismatch left
    run.log unreachable for the whole run and /results returning 404 after a successful search.
    """
    ### Given / When
    with mock.patch.object(modal_runner.modal, "current_function_call_id", lambda: JOB_ID):
        job_id = _job_id_for_this_run("PXD046787")

    ### Then
    assert job_id == JOB_ID


def test_job_id_falls_back_off_modal():
    """Scenario: the worker runs on a local machine, where there is no FunctionCall id.

    Slice: job output addressing.
    Given a context in which modal reports no current call id,
    When the worker decides what to call its job directory,
    Then it falls back to an accession-scoped name so local runs still work.
    """
    ### Given / When
    with mock.patch.object(modal_runner.modal, "current_function_call_id", lambda: None):
        job_id = _job_id_for_this_run("PXD046787")

    ### Then
    assert job_id.startswith("PXD046787-")


def test_job_id_survives_a_client_without_the_accessor():
    """Scenario: the installed modal client does not expose current_function_call_id at all.

    Slice: job output addressing.
    Given a modal module missing that accessor,
    When the worker decides what to call its job directory,
    Then it still returns a usable id rather than raising, because an AttributeError here would
    fail the run before a single raw file was fetched.
    """
    ### Given / When
    without_accessor = _ModalWithout("current_function_call_id")
    with mock.patch.object(modal_runner, "modal", without_accessor):
        job_id = _job_id_for_this_run("PXD046787")

    ### Then
    assert job_id.startswith("PXD046787-")


def test_job_dir_resolves_the_current_layout(tmp_path):
    """Scenario: the gateway looks up the output directory of a job spawned after the fix.

    Slice: job output addressing.
    Given a data root containing a directory named after the FunctionCall id,
    When the gateway resolves that job's directory,
    Then it returns that path, without needing a Volume or any Modal connectivity.
    """
    ### Given
    expected = tmp_path / JOB_ID
    expected.mkdir()

    ### When
    with mock.patch.object(modal_runner, "DATA_ROOT", tmp_path):
        resolved = _job_dir(JOB_ID)

    ### Then
    assert resolved == expected


def test_job_dir_resolves_a_pre_fix_job(tmp_path):
    """Scenario: a job spawned before the id unification is still on the Volume.

    Slice: job output addressing.
    Given a data root holding only the legacy "{accession}-{timestamp}" directory, and a jobs
      mapping that ties the accession to the FunctionCall id the client polls,
    When the gateway resolves that job's directory,
    Then it finds the legacy path, so hours of completed search work remain downloadable.
    """
    ### Given
    legacy = tmp_path / "PXD046787-1792321464"
    legacy.mkdir()

    ### When
    with mock.patch.object(modal_runner, "DATA_ROOT", tmp_path), \
         mock.patch.object(modal_runner, "jobs", {"PXD046787": JOB_ID}):
        resolved = _job_dir(JOB_ID)

    ### Then
    assert resolved == legacy


def test_job_dir_returns_none_for_an_unknown_job(tmp_path):
    """Scenario: nothing on disk belongs to the requested job.

    Slice: job output addressing.
    Given an empty data root and a jobs mapping without this job,
    When the gateway resolves that job's directory,
    Then it returns None so /results answers 404 rather than serving another job's table.
    """
    ### Given / When
    with mock.patch.object(modal_runner, "DATA_ROOT", tmp_path), \
         mock.patch.object(modal_runner, "jobs", {}):
        resolved = _job_dir(JOB_ID)

    ### Then
    assert resolved is None


def test_stage_events_survive_a_half_written_final_line(tmp_path):
    """Scenario: the stage file is read while run_all.sh is still appending to it.

    Slice: remote stage progress.
    Given a shipped stages.jsonl whose last line was copied mid-write,
    When the gateway reads this job's stage events,
    Then every complete event is returned and the torn line is dropped, because one truncated line
      must not cost the caller its elapsed time and ETA for the rest of the run.
    """
    ### Given
    job_dir = tmp_path / JOB_ID
    job_dir.mkdir()
    (job_dir / "stages.jsonl").write_text(
        '{"event":"stage_started","stage":"download","message":"Download raw files"}\n'
        '{"event":"stage_finished","stage":"download","message":"completed","seconds":412}\n'
        '{"event":"stage_started","stage":"fas'
    )

    ### When
    with mock.patch.object(modal_runner, "DATA_ROOT", tmp_path), \
         mock.patch.object(modal_runner, "jobs", {}):
        events = _read_stage_events(JOB_ID)

    ### Then
    assert [e["event"] for e in events] == ["stage_started", "stage_finished"]
    assert events[1]["seconds"] == 412, "the duration a finished stage reports is what elapsed shows"


def test_stage_events_are_empty_before_the_first_stage_ships(tmp_path):
    """Scenario: the job directory exists but run_all.sh has not emitted a stage yet.

    Slice: remote stage progress.
    Given a job directory with no stages.jsonl in it,
    When the gateway reads this job's stage events,
    Then it returns an empty list rather than raising, so a just-submitted job still polls cleanly.
    """
    ### Given
    (tmp_path / JOB_ID).mkdir()

    ### When
    with mock.patch.object(modal_runner, "DATA_ROOT", tmp_path), \
         mock.patch.object(modal_runner, "jobs", {}):
        events = _read_stage_events(JOB_ID)

    ### Then
    assert events == []


def test_vol_sync_swallows_any_exception():
    """Scenario: _vol_sync is called when no Modal Volume is mounted (off-Modal or test env).

    Slice: raw-file volume lifecycle.
    Given a Volume-like object whose action raises an arbitrary exception,
    When _vol_sync is called with it,
    Then no exception propagates — the local filesystem is already coherent off Modal and a
    raised exception here would abort the job rather than logging a soft warning.
    """
    ### Given
    class _BrokenVolume:
        def commit(self):
            raise RuntimeError("no volume mounted")

    ### When / Then (must not raise)
    modal_runner._vol_sync("commit", _BrokenVolume())


def test_vol_sync_calls_the_supplied_volume_not_the_default():
    """Scenario: _vol_sync is called with an explicit Volume (e.g. raw_vol instead of vol).

    Slice: raw-file volume lifecycle.
    Given a tracked volume and the module-level vol replaced with a separate sentinel,
    When _vol_sync is called with the tracked volume,
    Then only the tracked volume's method is called — raw_vol commits must not land on vol.
    """
    ### Given
    calls: list[str] = []

    class _TrackedVolume:
        def reload(self):
            calls.append("reload")

    ### When
    modal_runner._vol_sync("reload", _TrackedVolume())

    ### Then
    assert calls == ["reload"]


def test_vol_sync_falls_back_to_module_vol_when_none_supplied():
    """Scenario: _vol_sync is called without an explicit Volume (the job-results vol path).

    Slice: raw-file volume lifecycle.
    Given the module-level vol is replaced with a tracked sentinel,
    When _vol_sync is called with no volume argument,
    Then the module-level vol's method is invoked — existing callers must not silently no-op.
    """
    ### Given
    calls: list[str] = []

    class _TrackedVol:
        def commit(self):
            calls.append("commit")

    ### When
    with mock.patch.object(modal_runner, "vol", _TrackedVol()):
        modal_runner._vol_sync("commit")

    ### Then
    assert calls == ["commit"]


def test_raw_cache_mount_is_a_persistent_path_not_a_tempdir():
    """Scenario: the raw-file cache is pointed at the Modal Volume mount, not an ephemeral tempdir.

    Slice: raw-file volume lifecycle.
    Given the module constants for the two Volume mounts,
    When they are inspected,
    Then RAW_CACHE_MOUNT is a stable absolute path distinct from VOLUME_MOUNT, and neither is a
    temp-directory prefix — the original regression was PRIDEMAP_RAW_DIR pointing at mkdtemp().
    """
    ### Given / When
    raw = modal_runner.RAW_CACHE_MOUNT
    data = modal_runner.VOLUME_MOUNT

    ### Then
    assert raw.startswith("/"), "must be an absolute path mountable by Modal"
    assert not raw.startswith("/tmp"), "must not be an ephemeral tempdir — that was the regression"
    assert raw != data, "raw-file cache and job-results volume must be separate mounts"


def test_being_unable_to_reach_modal_is_not_a_failed_run():
    """Scenario: a transport error while polling does not kill a healthy job.

    Slice: job status reporting.
    Given a poll that fails because Modal could not be reached at all,
    When the gateway maps that outcome to a status,
    Then it reports "running", because the 16-core search carries on regardless and the card stops
      polling the moment it reads a terminal status — so reporting "error" here marked a healthy job
      dead permanently, with no way back short of a page reload.
    """
    ### Given / When
    status = _status_for(ConnectionError("connection reset by peer"))

    ### Then
    assert status == "running"


def test_a_worker_that_raised_is_still_a_failed_run():
    """Scenario: softening the transport case did not soften real failures.

    Slice: job status reporting.
    Given a poll whose call raises an ordinary exception from the worker itself,
    When the gateway maps that outcome to a status,
    Then it still reports "error" — the counterexample to the test above, without which "treat
      errors as running" would quietly swallow every genuine failure as well.
    """
    ### Given / When
    status = _status_for(RuntimeError("MaxQuant aborted"))

    ### Then
    assert status == "error"


def test_a_search_that_exited_non_zero_is_a_failure():
    """Scenario: a non-zero run_all.sh is reported as failed rather than finished.

    Slice: search outcome.
    Given a run_all.sh that exited 1,
    When the search outcome is judged,
    Then a reason is returned, because the worker used to write "error" into a progress file no
      endpoint reads and then return normally — so the FunctionCall completed cleanly and the job
      reported "done" for a run that had failed.
    """
    ### Given / When
    problem = modal_runner.search_failure(1, None)

    ### Then
    assert problem and "exited 1" in problem


def test_a_search_that_produced_no_protein_table_is_a_failure():
    """Scenario: exit code 0 is not on its own evidence that the search produced anything.

    Slice: search outcome.
    Given a run_all.sh that exited cleanly but wrote no proteinGroups.txt,
    When the search outcome is judged,
    Then a reason is returned, because the results endpoint would otherwise 404 on a job the card
      had already reported as done.
    """
    ### Given / When
    problem = modal_runner.search_failure(0, None)

    ### Then
    assert problem and "proteinGroups.txt" in problem


def test_a_search_that_exited_cleanly_with_a_table_is_a_success():
    """Scenario: the success path is still reachable.

    Slice: search outcome.
    Given a run_all.sh that exited cleanly and wrote a protein table,
    When the search outcome is judged,
    Then no reason is returned — the counterexample that stops the two tests above from being
      satisfied by a rule that calls every run a failure.
    """
    ### Given / When
    problem = modal_runner.search_failure(0, pathlib.Path("/tmp/proteinGroups.txt"))

    ### Then
    assert problem is None


def test_progress_can_be_written_before_its_directory_has_propagated(tmp_path):
    """Scenario: recording progress does not depend on another container's directory arriving.

    Slice: remote stage reporting.
    Given a job directory that does not exist in this container,
    When progress is written to it,
    Then the directory is created and the record lands — the orchestrator commits job_dir before
      spawning the search, but it was observed missing there anyway, and the run then died on
      progress.jsonl with a FileNotFoundError that read as a MaxQuant fault. It failed twice over:
      the `except` that tried to record the error raised the same error again.
    """
    ### Given
    missing = tmp_path / "fc-never-created" / "progress.jsonl"
    assert not missing.parent.exists()

    ### When
    modal_runner._write_progress(missing, "running", "starting run_all.sh")

    ### Then
    assert json.loads(missing.read_text().splitlines()[0])["message"] == "starting run_all.sh"


def test_stage_events_can_be_written_before_their_directory_has_propagated(tmp_path):
    """Scenario: the same guarantee for the stage events the card renders.

    Slice: remote stage reporting.
    Given a job directory that does not exist in this container,
    When a stage event is written to it,
    Then the directory is created and the event lands, because a failure recorded nowhere is the
      state this whole slice exists to remove.
    """
    ### Given
    missing = tmp_path / "fc-never-created" / "phases.jsonl"

    ### When
    modal_runner._write_stage_event(missing, "stage_failed", "search_dda", "it broke")

    ### Then
    assert json.loads(missing.read_text().splitlines()[0])["stage"] == "search_dda"


def test_a_failed_commit_is_reported_rather_than_swallowed(capsys):
    """Scenario: a commit that fails says so, on every host.

    Slice: durable stage markers.
    Given a Volume whose commit raises,
    When it is committed,
    Then False is returned and a warning is printed — resume depends on that commit landing, and an
      earlier version gated this warning on an unverified MODAL_TASK_ID env var, which would have
      re-hidden every commit failure behind a guess.
    """
    ### Given
    class _Boom:
        def commit(self):
            raise RuntimeError("volume unavailable")

    ### When
    ok = modal_runner._vol_commit(_Boom(), what="raw cache")

    ### Then
    assert ok is False
    assert "raw cache commit failed" in capsys.readouterr().out


def test_the_download_phase_is_visible_before_the_search_starts(tmp_path):
    """Scenario: the orchestrator's own phases reach the stage list.

    Slice: remote stage reporting.
    Given a job directory holding only the orchestrator's phases file, as during phase 1,
    When the stage events are read,
    Then the download stage's events come back, because stages.jsonl is shipped only by phase 2 and
      until then the card showed a bare "running" with an empty stage list — for up to two hours.
    """
    ### Given
    job_dir = tmp_path / JOB_ID
    job_dir.mkdir()
    (job_dir / "phases.jsonl").write_text(
        '{"ts":"2026-10-18T09:00:00Z","event":"run_started","stage":"","message":"pipeline start"}\n'
        '{"ts":"2026-10-18T09:00:01Z","event":"stage_started","stage":"download","message":"Download"}\n'
    )

    ### When
    with mock.patch.object(modal_runner, "_job_dir", return_value=job_dir):
        events = _read_stage_events(JOB_ID)

    ### Then
    assert [e["event"] for e in events] == ["run_started", "stage_started"]
    assert events[1]["stage"] == "download"


def test_the_scripts_own_stages_follow_the_orchestrators_phases(tmp_path):
    """Scenario: both sources are read, in the order that lets the later one win.

    Slice: remote stage reporting.
    Given a job directory holding both the orchestrator's phases and run_all.sh's own stages,
    When the stage events are read,
    Then the phases come first and the script's events after, so that run_all.sh's own run_started
      supersedes the orchestrator's — current_run_events keeps only events from the last one, which
      is how the real stage records replace the coarse phase ones without the two contradicting.
    """
    ### Given
    job_dir = tmp_path / JOB_ID
    job_dir.mkdir()
    (job_dir / "phases.jsonl").write_text(
        '{"ts":"2026-10-18T09:00:00Z","event":"run_started","stage":"","message":"pipeline start"}\n'
    )
    (job_dir / "stages.jsonl").write_text(
        '{"ts":"2026-10-18T09:05:00Z","event":"run_started","stage":"","message":"pipeline start"}\n'
        '{"ts":"2026-10-18T09:05:01Z","event":"stage_started","stage":"preflight","message":"Check"}\n'
    )

    ### When
    with mock.patch.object(modal_runner, "_job_dir", return_value=job_dir):
        events = _read_stage_events(JOB_ID)

    ### Then
    assert len(events) == 3, "both files must be read"
    assert events[-1]["stage"] == "preflight", "the script's own stages must come last"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
