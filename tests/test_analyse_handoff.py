# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "uvicorn", "pandas>=2", "numpy>=1.26", "scipy>=1.11", "pytest>=8"]
# ///
"""Contract tests for the search → Analyse handoff.

Author:  PRIDE Map
Created: 2026-10-19
Scope:   api/main.py — _per_run_groups, _describe_two

Why this file exists: the handoff failed silently, and every part of it looked healthy.

A two-run comparison is the common case — two samples out of a deposition is often all anyone has —
and it produced an EMPTY suggested_groups. Nothing errored. The pipeline reported every stage green,
the table was transferred and loaded, and the user was then shown a picker with nothing selected and
a disabled Run button, next to a message telling them to upload by hand the file the app was already
holding. The 48-minute search had succeeded; only the last hop was dead.

Two independent causes, either of which is enough to reproduce it:

  1. Groups were only formed from columns ending in a numeric replicate suffix. MaxQuant names its
     quantification columns "LFQ intensity <experiment>" after the experiment names the kit derives
     from the sample annotations, so no column matched and no group was formed.
  2. Groups of one column were discarded outright, which is exactly what a two-run design produces.

The first group of tests pins that such a table yields one group per run, that the NORMALISED metric
wins over raw intensity (serving un-normalised values as a fold change would be wrong, not merely
less good), and that summary columns carrying no experiment name are not mistaken for runs.

The second group pins that a replicated design is untouched — the inferential path must keep
winning when there is something to infer from, or this fix would quietly downgrade good data.

The third group pins that one column a side actually returns a comparison rather than an error, and
that it is honestly labelled descriptive with null p-values. A fabricated p-value here would be a
far worse failure than the empty picker this replaced.

run:  uv run tests/test_analyse_handoff.py
      (or: uv run pytest tests/test_analyse_handoff.py)
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api.main import _describe_two, _per_run_groups  # noqa: E402

# MaxQuant's real column set for a two-run label-free search, including the summary columns that
# carry no experiment name and the per-run columns that do.
MAXQUANT_TWO_RUN = [
    "Intensity",
    "Intensity M69_Hypoxia_48h",
    "Intensity M70_Normoxia_48h",
    "LFQ intensity M69_Hypoxia_48h",
    "LFQ intensity M70_Normoxia_48h",
    "MS/MS count",
    "Sequence coverage [%]",
]


def _fill(cols: list[str]) -> dict[str, float]:
    return {c: 1.0 for c in cols}


def _two_sample_result():
    """The descriptive comparison of a one-column-a-side table, as /analyse/run returns it."""
    import pandas as pd

    df = pd.DataFrame({
        "Protein IDs": ["P1", "P2", "P3", "P4"],
        "LFQ intensity A": [1000.0, 2000.0, 0.0, 4000.0],
        "LFQ intensity B": [2000.0, 2000.0, 500.0, 0.0],
    })

    class Body:
        id_column = "Protein IDs"
        group_a = ["LFQ intensity A"]
        group_b = ["LFQ intensity B"]
        log_transform = "auto"
        min_valid = 1
        contaminant_column = None

    return _describe_two(Body(), df, n_input=len(df))


# ----------------------------------------------------------------- one group per run


def test_two_run_maxquant_table_yields_one_group_per_run():
    """Scenario: a two-run MaxQuant search hands its protein table back.

    Slice: the suggestion step the Analyse page pre-selects its groups from.

    Given MaxQuant has written one quantification column per experiment,
    When the groups are suggested,
    Then there is one group per run, so the comparison has an A and a B to compare.
    """
    ### Given
    cols = MAXQUANT_TWO_RUN

    ### When
    groups = _per_run_groups(cols, _fill(cols))

    ### Then
    assert len(groups) == 2
    assert [g["stem"] for g in groups] == ["M69_Hypoxia_48h", "M70_Normoxia_48h"]
    assert all(len(g["columns"]) == 1 for g in groups)


def test_normalised_lfq_wins_over_raw_intensity():
    """Scenario: the table carries both raw and LFQ-normalised columns.

    Slice: metric preference inside the suggestion step.

    Given both "Intensity <exp>" and "LFQ intensity <exp>" are present,
    When the groups are suggested,
    Then the LFQ columns are chosen, because a fold change built from un-normalised intensities
    compares loading differences as though they were biology.
    """
    ### Given
    cols = MAXQUANT_TWO_RUN

    ### When
    groups = _per_run_groups(cols, _fill(cols))

    ### Then
    chosen = [c for g in groups for c in g["columns"]]
    assert chosen == ["LFQ intensity M69_Hypoxia_48h", "LFQ intensity M70_Normoxia_48h"]


def test_summary_columns_are_not_mistaken_for_runs():
    """Scenario: the table carries a bare "Intensity" total alongside the per-run columns.

    Slice: separator handling in the metric match.

    Given a bare "Intensity" column with no experiment name after it,
    When the groups are suggested,
    Then it is not offered as a run, because it is a total over all runs and comparing it against
    one run would be meaningless.
    """
    ### Given
    cols = MAXQUANT_TWO_RUN

    ### When
    groups = _per_run_groups(cols, _fill(cols))

    ### Then
    assert "Intensity" not in [c for g in groups for c in g["columns"]]
    assert "Intensity" not in [g["stem"] for g in groups]


def test_ibaq_is_used_when_no_lfq_was_computed():
    """Scenario: the search ran without label-free quantification.

    Slice: the fallback order of the metric preference list.

    Given the table has iBAQ and raw Intensity but no LFQ columns,
    When the groups are suggested,
    Then iBAQ is chosen, since it is the more comparable of the two that remain.
    """
    ### Given
    cols = ["iBAQ A_ctrl", "iBAQ B_treated", "Intensity A_ctrl", "Intensity B_treated"]

    ### When
    groups = _per_run_groups(cols, _fill(cols))

    ### Then
    assert [c for g in groups for c in g["columns"]] == ["iBAQ A_ctrl", "iBAQ B_treated"]


def test_a_single_run_yields_no_groups():
    """Scenario: only one run was searched.

    Slice: the lower bound of the suggestion step.

    Given there is one quantification column,
    When the groups are suggested,
    Then nothing is suggested, because there is no second group to compare against and inventing
    one would produce a comparison of a sample with itself.
    """
    ### Given
    cols = ["LFQ intensity Only_one"]

    ### When
    groups = _per_run_groups(cols, _fill(cols))

    ### Then
    assert groups == []


def test_table_without_recognisable_quant_columns_yields_no_groups():
    """Scenario: the uploaded table is not a search-engine protein table.

    Slice: the guard that keeps the suggestion step from guessing.

    Given no column matches any known quantification metric,
    When the groups are suggested,
    Then nothing is suggested rather than an arbitrary pair of numeric columns being paired up.
    """
    ### Given
    cols = ["Score", "Sequence coverage [%]", "MS/MS count"]

    ### When
    groups = _per_run_groups(cols, _fill(cols))

    ### Then
    assert groups == []


# ----------------------------------------------------------------- replicated designs are untouched


def test_replicate_suffixed_columns_are_left_to_the_stem_grouping():
    """Scenario: a properly replicated design is analysed.

    Slice: the boundary between stem grouping and the per-run fallback.

    Given columns named "<stem>.<n>" that the replicate grouping already handles,
    When the per-run fallback is asked about them,
    Then it declines, because those columns carry no metric prefix — so a design with real
    replicates can never be silently downgraded to a descriptive one-column comparison.
    """
    ### Given
    cols = ["Ctrl.1", "Ctrl.2", "Ctrl.3", "Treated.1", "Treated.2", "Treated.3"]

    ### When
    groups = _per_run_groups(cols, _fill(cols))

    ### Then
    assert groups == []


# ----------------------------------------------------------------- one column a side is answerable


def test_two_samples_produce_fold_changes_rather_than_an_error():
    """Scenario: the pipeline hands back a table with one run a side.

    Slice: /analyse/run's descriptive branch.

    Given one column in each group,
    When the comparison runs,
    Then rows with fold changes come back, so the user sees the comparison the search paid for.
    """
    ### Given / When
    out = _two_sample_result()

    ### Then
    assert out["rows"], "a two-sample comparison must still return rows"
    assert out["summary"]["comparison"] == "descriptive"


def test_no_p_values_are_invented_for_a_two_sample_comparison():
    """Scenario: a reader looks for significance in a two-sample result.

    Slice: the honesty contract of the descriptive branch.

    Given there is no within-group variance to test,
    When the comparison runs,
    Then every p and q is null and nothing is counted as significant, because a fabricated p-value
    would be a worse failure than showing no statistics at all.
    """
    ### Given / When
    out = _two_sample_result()

    ### Then
    assert all(r["p"] is None and r["q"] is None for r in out["rows"])
    assert out["summary"]["n_sig_q05"] == 0
    assert out["summary"]["n_tested"] == 0


def test_detection_only_proteins_are_reported():
    """Scenario: a protein is seen in one sample and not the other.

    Slice: the detection half of the descriptive comparison.

    Given P3 is absent from A and P4 absent from B,
    When the comparison runs,
    Then both are reported as one-sided detections — the common real finding when comparing two
    samples, and the part a fold-change-only view would drop.
    """
    ### Given / When
    out = _two_sample_result()

    ### Then
    by_id = {r["id"]: r for r in out["rows"]}
    assert by_id["P3"]["detection"] == "b_only"
    assert by_id["P4"]["detection"] == "a_only"


def test_every_row_carries_the_abundance_its_chart_plots_against():
    """Scenario: the two-sample result is charted.

    Slice: the row contract the MA plot is drawn from.

    Given a volcano cannot be drawn without p-values, so the UI plots fold change against abundance
      instead,
    When the comparison runs,
    Then every row carries the mean on the side it was seen — including the one-sided detections,
    which have no fold change at all and would vanish from the chart if their mean went missing.
    """
    ### Given / When
    out = _two_sample_result()

    ### Then
    for r in out["rows"]:
        if r["detection"] in ("both", "a_only"):
            assert r["mean_a"] is not None, f"{r['id']} seen in A but carries no mean_a"
        if r["detection"] in ("both", "b_only"):
            assert r["mean_b"] is not None, f"{r['id']} seen in B but carries no mean_b"


# ----------------------------------------------------------------- the cache must not dead-end


@pytest.fixture()
def transfer_cache(monkeypatch, tmp_path):
    """The transfer cache with its file redirected, and a controllable set of live uploads.

    upload_path is resolved lazily inside api.omics as `from main import upload_path`, so a stand-in
    module is installed rather than the real api.main imported — that keeps the test off the real
    uploads directory and makes "this upload is gone" something the test can state directly.
    """
    import types

    from api import omics

    live: set[str] = set()

    def upload_path(upload_id: str):
        if upload_id not in live:
            raise RuntimeError("unknown upload_id")
        return tmp_path / f"{upload_id}.txt"

    monkeypatch.setitem(sys.modules, "main", types.SimpleNamespace(upload_path=upload_path))
    monkeypatch.setattr(omics, "_TRANSFER_CACHE_FILE", tmp_path / "transfer_cache.json")
    monkeypatch.setattr(omics, "_transfer_cache", {})
    return omics, live


def test_a_cached_upload_that_still_exists_is_served(transfer_cache):
    """Scenario: the same two runs are compared a second time.

    Slice: the cache read that lets Run skip the search.

    Given a recorded transfer whose upload is still readable,
    When the cache is consulted,
    Then the upload id comes back, so the comparison opens instead of re-running a 48-minute search.
    """
    ### Given
    omics, live = transfer_cache
    omics._save_transfer_entry("key1", "upload1")
    live.add("upload1")

    ### When
    got = omics._cached_upload("key1")

    ### Then
    assert got == "upload1"


def test_a_cached_upload_that_has_been_deleted_is_not_served(transfer_cache):
    """Scenario: the upload was cleaned up after the cache entry was written.

    Slice: the validity check on the cache read.

    Given a recorded transfer whose upload no longer exists,
    When the cache is consulted,
    Then nothing is returned, because serving it would 404 the Analyse page and — since the submit
    path reads the same cache — make Run hand back the same dead id forever.
    """
    ### Given
    omics, _ = transfer_cache
    omics._save_transfer_entry("key1", "gone")

    ### When
    got = omics._cached_upload("key1")

    ### Then
    assert got is None


def test_a_dead_entry_is_evicted_so_the_next_run_proceeds(transfer_cache):
    """Scenario: a user clicks Run after their upload was cleaned up.

    Slice: self-healing of the cache.

    Given a dead entry has just been read,
    When the cache is inspected afterwards,
    Then the entry is gone, so the next submit actually searches rather than returning the dead id
    again — one slow run instead of a dead end only a hand-edited file could clear.
    """
    ### Given
    omics, _ = transfer_cache
    omics._save_transfer_entry("key1", "gone")

    ### When
    omics._cached_upload("key1")

    ### Then
    assert "key1" not in omics._transfer_cache


def test_evicting_one_dead_entry_leaves_the_others(transfer_cache):
    """Scenario: one selection's upload is gone while another's is intact.

    Slice: the blast radius of eviction.

    Given two entries, only one of which is dead,
    When the dead one is read,
    Then the live entry is untouched, so losing one upload cannot cost every other cached
    comparison its result.
    """
    ### Given
    omics, live = transfer_cache
    omics._save_transfer_entry("dead", "gone")
    omics._save_transfer_entry("alive", "upload2")
    live.add("upload2")

    ### When
    omics._cached_upload("dead")

    ### Then
    assert omics._cached_upload("alive") == "upload2"
    assert "dead" not in omics._transfer_cache


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-header", "-p", "no:cacheprovider"]))
