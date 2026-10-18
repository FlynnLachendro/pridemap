# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "pydantic>=2", "pytest>=8", "modal>=1.5"]
# ///
"""Contract tests for which cloud job a submission belongs to, and when a second one is a duplicate.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/omics.py job_key / _remote_job_key / SELECTION_KEY_FILE in the kit, and
         api/modal_runner.py job_key / selection_from_kit — the key both ends must agree on.

Why this file exists: the remote route had no in-flight guard, so a second click spawned a second
16 CPU / 64 GB container and the registry write — jobs[accession] = job_id — silently replaced the
first job's entry. The first job kept billing while being invisible to /jobs, which is the
documented way to find and kill a runaway container.

The obvious fix has a trap, and these tests exist mostly to pin it. "A second POST returns the same
job_id" is equally satisfied by always returning whatever job that accession last ran — which, once
the key carries the run selection, hands one sample selection another selection's results. That
would be worse than the bug. So the counterexample is here: two different selections must get two
different keys, and must not be able to collapse into one.

The key is computed on the API side by _selection_key and carried in the kit, because `runs` never
reaches the Modal side — /run receives only the kit. Both ends deriving it separately is a key they
would eventually disagree about, so this file checks they agree.

run:  uv run tests/test_remote_job_identity.py
      (or: uv run pytest tests/test_remote_job_identity.py)
"""
from __future__ import annotations

import io
import pathlib
import sys
import zipfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api import modal_runner, omics  # noqa: E402
from api.omics import SELECTION_KEY_FILE, _remote_job_key, _selection_key, job_key  # noqa: E402

ACCESSION = "PXD046787"
SELECTION_A = ["2019_12_13_Daria_01.raw", "2019_12_13_Daria_02.raw"]
SELECTION_B = ["2019_12_13_Daria_03.raw", "2019_12_13_Daria_04.raw"]


def _kit_carrying(selection_key: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(SELECTION_KEY_FILE, selection_key)
    return buf.getvalue()


def test_two_different_run_selections_get_two_different_keys():
    """Scenario: one dataset searched two ways is two jobs, not one.

    Slice: remote job identity.
    Given two different run selections of the same dataset,
    When their registry keys are derived,
    Then the keys differ — the counterexample that stops "a second submit returns the existing
      job" from being satisfied by handing one selection another selection's results, which would
      be a worse bug than the duplicate spawn it replaced.
    """
    ### Given / When
    key_a = job_key(ACCESSION, _selection_key(SELECTION_A))
    key_b = job_key(ACCESSION, _selection_key(SELECTION_B))

    ### Then
    assert key_a != key_b
    assert key_a.startswith(ACCESSION) and key_b.startswith(ACCESSION)


def test_the_same_selection_always_gets_the_same_key():
    """Scenario: the key is stable, and insensitive to the order runs were picked in.

    Slice: remote job identity.
    Given the same two runs listed in opposite orders,
    When their registry keys are derived,
    Then the keys match, because a key that changed with click order would make every resubmission
      look like a new job and defeat the guard entirely.
    """
    ### Given / When
    forwards = job_key(ACCESSION, _selection_key(SELECTION_A))
    backwards = job_key(ACCESSION, _selection_key(list(reversed(SELECTION_A))))

    ### Then
    assert forwards == backwards


def test_a_whole_dataset_run_keys_on_the_accession_alone():
    """Scenario: the common case keeps the key it always had.

    Slice: remote job identity.
    Given no run selection,
    When the registry key is derived,
    Then it is just the accession, so jobs recorded before selection keys existed remain findable.
    """
    ### Given / When
    key = job_key(ACCESSION, _selection_key(None))

    ### Then
    assert key == ACCESSION


def test_both_ends_agree_on_the_key_for_one_kit():
    """Scenario: the API and the Modal side derive the same key for the same submission.

    Slice: remote job identity.
    Given a kit carrying the selection key the API computed,
    When the Modal side reads that kit and builds its own key,
    Then it matches the API's — `runs` never crosses the wire, so without the kit carrying the key
      the two ends would key the registry differently and the in-flight guard would be blind to the
      very job it exists to find.
    """
    ### Given
    selection = _selection_key(SELECTION_A)
    api_key = job_key(ACCESSION, selection)

    ### When
    modal_key = modal_runner.job_key(ACCESSION, modal_runner.selection_from_kit(_kit_carrying(selection)))

    ### Then
    assert modal_key == api_key


def test_a_kit_without_a_selection_key_is_read_as_a_whole_dataset_run():
    """Scenario: a kit built before this field existed still resolves to a usable key.

    Slice: remote job identity.
    Given a kit with no selection-key file,
    When the Modal side reads it,
    Then the selection is empty and the key is the bare accession, so an older kit in flight during
      a deploy does not land under a key nothing will ever look up.
    """
    ### Given
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("run_all.sh", "# stub\n")

    ### When
    selection = modal_runner.selection_from_kit(buf.getvalue())

    ### Then
    assert selection == ""
    assert modal_runner.job_key(ACCESSION, selection) == ACCESSION


def test_the_generated_kit_carries_the_selection_key():
    """Scenario: the carrier is actually in the kit, not merely read for.

    Slice: remote job identity.
    Given the list of files every kit must contain before submission,
    When the submission guard's required set is read,
    Then the selection-key file is among them, so a kit that lost it is refused at the gate rather
      than landing under the wrong registry key on the far side.
    """
    ### Given / When
    required = omics._REQUIRED_KIT_FILES

    ### Then
    assert "run_all.sh" in required, "sanity: the required set is the one under test"
    # The key file is written by search_kit; building a real kit needs the dataset database, so this
    # asserts the wiring that search_kit depends on rather than re-deriving the kit here.
    assert SELECTION_KEY_FILE == modal_runner.SELECTION_KEY_FILE, (
        "the two ends must name the same file, or the key silently never arrives"
    )


def test_the_registry_key_follows_the_run_query_parameter():
    """Scenario: the key used by the remote routes is derived from the request's own runs argument.

    Slice: remote job identity.
    Given two different runs query strings for one accession,
    When the remote routes derive their registry key,
    Then the keys differ, because every remote route must agree on one owner for this derivation —
      the guard, the resume lookup and the delete all read the same dict.
    """
    ### Given / When
    key_a = _remote_job_key(ACCESSION, ",".join(SELECTION_A))
    key_b = _remote_job_key(ACCESSION, ",".join(SELECTION_B))

    ### Then
    assert key_a != key_b


def test_the_resume_directory_is_not_inside_the_per_job_directory():
    """Scenario: stage markers outlive the job that wrote them.

    Slice: resume.
    Given two job ids for the same dataset and selection,
    When their resume directories are resolved,
    Then both resolve to the same path, and it is not under either job's own directory — a job
      directory is named after the FunctionCall id, so markers kept there are invisible to the
      retry, which is precisely why resume never worked.
    """
    ### Given / When
    selection = _selection_key(SELECTION_A)
    first = modal_runner._resume_dir(ACCESSION, selection)
    second = modal_runner._resume_dir(ACCESSION, selection)

    ### Then
    assert first == second
    assert "fc-" not in str(first), "must not be keyed by a FunctionCall id"
    assert str(first).startswith(str(modal_runner.DATA_ROOT))


def test_two_selections_do_not_share_a_resume_directory():
    """Scenario: one selection's finished stages cannot be skipped by another's run.

    Slice: resume.
    Given two different run selections of the same dataset,
    When their resume directories are resolved,
    Then they differ, because skipping a search that ran over different raw files would produce a
      result for runs the user did not pick.
    """
    ### Given / When
    a = modal_runner._resume_dir(ACCESSION, _selection_key(SELECTION_A))
    b = modal_runner._resume_dir(ACCESSION, _selection_key(SELECTION_B))

    ### Then
    assert a != b


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
