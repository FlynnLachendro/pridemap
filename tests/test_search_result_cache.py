# /// script
# requires-python = ">=3.11"
# dependencies = ["modal>=1.5", "pytest>=8"]
# ///
"""Contract tests for the content-addressed search result cache.

Author:  PRIDE Map
Created: 2026-10-19
Scope:   api/modal_runner.py — normalise_mqpar, search_cache_key, cached_results, store_results

Why this file exists: a result cache has exactly two interesting failure modes and both are silent.

If the key is too SENSITIVE it never hits, and the cache is dead weight nobody notices — which is
what hashing the kit zip or mqpar.xml verbatim would have given us, because <filePaths> carries the
per-container kit directory and <numThreads> is rewritten at container start. Every run would look
new. The first group of tests pins exactly which fields are allowed to vary.

If the key is too LOOSE it returns the wrong science, which is far worse than being slow: a protein
table computed with different enzymes, modifications or FDR thresholds would be served as though it
matched. The second group pins that each of those changes the key.

The third group covers the artifact gate. Persistent stage markers were tried in this pipeline and
reverted because a `<stage>.done` file outlived the products it described, so the next run skipped
work whose outputs no longer existed. A cache entry here must be valid only when its table is
readable, never because a flag says so.

run:  uv run tests/test_search_result_cache.py
      (or: uv run pytest tests/test_search_result_cache.py)
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api import modal_runner  # noqa: E402
from api.modal_runner import (  # noqa: E402
    cached_results,
    normalise_mqpar,
    search_cache_key,
    sha256_file,
    store_results,
)

#: A minimal mqpar with one of every field the key has an opinion about.
MQPAR = """<?xml version="1.0" encoding="utf-8"?>
<MaxQuantParams>
   <fastaFiles><FastaFileInfo>
      <fastaFilePath>/kit-abc/fasta/UP000005640.fasta</fastaFilePath>
      <taxonomyId>9606</taxonomyId>
   </FastaFileInfo></fastaFiles>
   <fixedCombinedFolder>/tmp/mq-job-aaa</fixedCombinedFolder>
   <numThreads>16</numThreads>
   <filePaths>
      <string>/tmp/mq-job-aaa/raw/a.raw</string>
      <string>/tmp/mq-job-aaa/raw/b.raw</string>
   </filePaths>
   <parameterGroups><parameterGroup>
      <lfqMode>1</lfqMode>
      <matchBetweenRuns>True</matchBetweenRuns>
      <enzymes><string>Trypsin/P</string></enzymes>
      <maxMissedCleavages>2</maxMissedCleavages>
      <variableModifications><string>Oxidation (M)</string></variableModifications>
      <fixedModifications><string>Carbamidomethyl (C)</string></fixedModifications>
   </parameterGroup></parameterGroups>
   <peptideFdr>0.01</peptideFdr>
   <proteinFdr>0.01</proteinFdr>
   <minPepLen>7</minPepLen>
</MaxQuantParams>
"""

RAWS = [("a.raw", "aaa111"), ("b.raw", "bbb222")]


# --------------------------------------------------------------------------- the key must still hit

def test_a_different_container_produces_the_same_key():
    """Scenario: the identical search runs again in a fresh Modal container.

    Slice: result cache — the key must survive what changes every run.
    Given the same raw files and the same parameters, but a new kit directory and the thread count
      rewritten from 16 to 8 as the runtime does,
    When the key is computed for both,
    Then the keys are equal, so the second run can reuse the first one's tables.

    This is the test that stops the cache being dead weight. Hashing mqpar.xml verbatim would make
    every run unique: the kit dir is per-container and numThreads is patched at container start.
    """
    ### Given
    rerun = (
        MQPAR.replace("/kit-abc/", "/kit-zzz/")
        .replace("/tmp/mq-job-aaa", "/tmp/mq-job-zzz")
        .replace("<numThreads>16</numThreads>", "<numThreads>8</numThreads>")
    )

    ### When
    first, second = search_cache_key(RAWS, MQPAR), search_cache_key(RAWS, rerun)

    ### Then
    assert first == second


def test_reordered_raw_files_produce_the_same_key():
    """Scenario: the same two files arrive in a different order.

    Slice: result cache — set semantics.
    Given the same raw files listed in reverse,
    When the key is computed,
    Then it is unchanged, because a search over a set does not depend on how the set was listed.
    """
    ### Given / When / Then
    assert search_cache_key(RAWS, MQPAR) == search_cache_key(list(reversed(RAWS)), MQPAR)


def test_whitespace_and_formatting_do_not_change_the_key():
    """Scenario: the kit generator reflows mqpar.xml without changing any value.

    Slice: result cache — formatting is not science.
    Given the same parameters with collapsed whitespace,
    When the key is computed,
    Then it is unchanged.
    """
    ### Given
    reflowed = " ".join(MQPAR.split())

    ### When / Then
    assert search_cache_key(RAWS, MQPAR) == search_cache_key(RAWS, reflowed)


# --------------------------------------------------------------------------- the key must not over-hit

@pytest.mark.parametrize(
    "old, new, what",
    [
        pytest.param("<string>Trypsin/P</string>", "<string>LysC</string>", "enzyme", id="enzyme"),
        pytest.param("<peptideFdr>0.01</peptideFdr>", "<peptideFdr>0.05</peptideFdr>",
                     "peptide FDR", id="peptide FDR"),
        pytest.param("<proteinFdr>0.01</proteinFdr>", "<proteinFdr>0.05</proteinFdr>",
                     "protein FDR", id="protein FDR"),
        pytest.param("<minPepLen>7</minPepLen>", "<minPepLen>6</minPepLen>",
                     "minimum peptide length", id="minPepLen"),
        pytest.param("<lfqMode>1</lfqMode>", "<lfqMode>0</lfqMode>", "LFQ mode", id="lfqMode"),
        pytest.param("<matchBetweenRuns>True</matchBetweenRuns>",
                     "<matchBetweenRuns>False</matchBetweenRuns>", "match between runs",
                     id="matchBetweenRuns"),
        pytest.param("<string>Oxidation (M)</string>", "<string>Phospho (STY)</string>",
                     "variable modification", id="variable mod"),
        pytest.param("<string>Carbamidomethyl (C)</string>", "<string>Propionamide (C)</string>",
                     "fixed modification", id="fixed mod"),
        pytest.param("<maxMissedCleavages>2</maxMissedCleavages>",
                     "<maxMissedCleavages>3</maxMissedCleavages>", "missed cleavages",
                     id="missedCleavages"),
        pytest.param("UP000005640.fasta", "UP000000589.fasta", "FASTA identity", id="fasta"),
        pytest.param("<taxonomyId>9606</taxonomyId>", "<taxonomyId>10090</taxonomyId>",
                     "taxonomy", id="taxonomy"),
    ],
)
def test_changing_a_search_parameter_changes_the_key(old, new, what):
    """Scenario: a parameter that changes the protein table is edited.

    Slice: result cache — refusing to serve the wrong science.
    Given a parameter file differing only in {what},
    When the key is computed,
    Then it differs, so the earlier tables are not served for a different search.

    Being slow is recoverable. Returning a table computed under different search parameters as
    though it matched is not, so each of these is pinned individually rather than trusting that
    "we hash the whole file" covers them.
    """
    ### Given
    assert old in MQPAR, f"fixture does not contain {old!r}"
    changed = MQPAR.replace(old, new)

    ### When / Then
    assert search_cache_key(RAWS, MQPAR) != search_cache_key(RAWS, changed), what


def test_changing_a_raw_files_content_changes_the_key():
    """Scenario: a raw file is re-downloaded and its bytes differ.

    Slice: result cache — inputs are identified by content.
    Given the same filename with a different content hash,
    When the key is computed,
    Then it differs.

    Content rather than name+size: a truncated-then-repaired download keeps its name and can keep
    its length, and the key is a claim about the bytes that were searched.
    """
    ### Given
    repaired = [("a.raw", "aaa111-different"), ("b.raw", "bbb222")]

    ### When / Then
    assert search_cache_key(RAWS, MQPAR) != search_cache_key(repaired, MQPAR)


def test_adding_a_raw_file_changes_the_key_for_the_whole_set():
    """Scenario: a third file is added to a two-file selection.

    Slice: result cache — LFQ is global, so the key is over the whole set.
    Given a third raw file added to the selection,
    When the key is computed,
    Then it differs from the two-file key.

    This is not merely "the inputs changed". MaxQuant's LFQ and iBAQ normalisation runs across all
    files in a search, so the two original files' abundances are themselves different in a
    three-file run. Reusing the two-file tables for any part of a three-file search would be a
    faster wrong answer.
    """
    ### Given
    bigger = RAWS + [("c.raw", "ccc333")]

    ### When / Then
    assert search_cache_key(RAWS, MQPAR) != search_cache_key(bigger, MQPAR)


def test_volatile_tags_are_blanked_not_deleted():
    """Scenario: a parameter file stops specifying a volatile field altogether.

    Slice: result cache — normalisation must not conflate absent with different.
    Given one file specifying a thread count and another omitting the tag entirely,
    When both are normalised,
    Then the normalised forms differ, because "unset" is not the same document as "set to 8".
    """
    ### Given
    with_threads = "<p><numThreads>8</numThreads><minPepLen>7</minPepLen></p>"
    without = "<p><minPepLen>7</minPepLen></p>"

    ### When
    a, b = normalise_mqpar(with_threads), normalise_mqpar(without)

    ### Then
    assert "8" not in a, "the thread count itself must not survive normalisation"
    assert a != b


# --------------------------------------------------------------------------- the artifact gate

def test_an_entry_counts_only_when_its_table_is_readable(tmp_path, monkeypatch):
    """Scenario: a cache directory exists but holds no protein table.

    Slice: result cache — artifact gating.
    Given a cache directory and manifest for a key, but no proteinGroups.txt,
    When the cache is consulted,
    Then it reports a miss.

    This is the whole reason the entry has no completion flag. Persistent `<stage>.done` markers
    were tried in this pipeline and reverted: the marker outlived the products it described, so the
    next run skipped work whose outputs no longer existed and handed MaxQuant missing paths. An
    entry that is valid only when its table is readable cannot drift from reality that way.
    """
    ### Given
    monkeypatch.setattr(modal_runner, "DATA_ROOT", tmp_path)
    d = tmp_path / modal_runner.SEARCH_CACHE_SUBDIR / "deadbeef"
    (d / "txt").mkdir(parents=True)
    (d / "manifest.json").write_text('{"key": "deadbeef"}')

    ### When
    hit = cached_results("deadbeef")

    ### Then
    assert hit is None


def test_a_stored_result_is_found_again(tmp_path, monkeypatch):
    """Scenario: a search completes and its tables are published, then a later run looks them up.

    Slice: result cache — the round trip.
    Given a directory of output tables stored under a key,
    When the cache is consulted for that key,
    Then the tables are returned and every file is present.
    """
    ### Given
    monkeypatch.setattr(modal_runner, "DATA_ROOT", tmp_path)
    produced = tmp_path / "combined" / "txt"
    produced.mkdir(parents=True)
    (produced / "proteinGroups.txt").write_text("Protein IDs\tIntensity\nP12345\t1000\n")
    (produced / "peptides.txt").write_text("Sequence\nPEPTIDE\n")

    ### When
    stored = store_results("abc123", produced, {"key": "abc123", "completed_at": "2026-10-19"})
    hit = cached_results("abc123")

    ### Then
    assert stored is True
    assert hit is not None
    assert (hit / "proteinGroups.txt").read_text().startswith("Protein IDs")
    assert (hit / "peptides.txt").exists(), "all output tables are cached, not just one"
    manifest = json.loads((hit.parent / "manifest.json").read_text())
    assert manifest["completed_at"] == "2026-10-19"


def test_storing_twice_is_idempotent(tmp_path, monkeypatch):
    """Scenario: two containers finish the same search and both try to publish.

    Slice: result cache — concurrent publication.
    Given a key that is already published,
    When another result is stored under it,
    Then the call succeeds and the existing table is left intact.
    """
    ### Given
    monkeypatch.setattr(modal_runner, "DATA_ROOT", tmp_path)
    first = tmp_path / "first" / "txt"
    first.mkdir(parents=True)
    (first / "proteinGroups.txt").write_text("first")
    store_results("same", first, {"key": "same"})
    second = tmp_path / "second" / "txt"
    second.mkdir(parents=True)
    (second / "proteinGroups.txt").write_text("second")

    ### When
    stored = store_results("same", second, {"key": "same"})

    ### Then
    assert stored is True
    hit = cached_results("same")
    assert hit is not None and (hit / "proteinGroups.txt").read_text() == "first"


def test_no_partial_entry_is_left_when_publication_fails(tmp_path, monkeypatch):
    """Scenario: the source directory vanishes part way through publication.

    Slice: result cache — failure must not create a hit.
    Given a source table directory that does not exist,
    When storing is attempted,
    Then it reports failure and the cache still reports a miss.

    A half-copied entry would be indistinguishable from a complete one on the next lookup, which is
    the artifact-gating equivalent of a stale marker.
    """
    ### Given
    monkeypatch.setattr(modal_runner, "DATA_ROOT", tmp_path)

    ### When
    stored = store_results("ghost", tmp_path / "not-there" / "txt", {"key": "ghost"})

    ### Then
    assert stored is False
    assert cached_results("ghost") is None


def test_content_hash_distinguishes_files_of_equal_length(tmp_path):
    """Scenario: two raw files of identical size holding different data.

    Slice: result cache — why hashing content beats name+size.
    Given two files of the same length and different bytes,
    When each is hashed,
    Then the digests differ.
    """
    ### Given
    a, b = tmp_path / "a.raw", tmp_path / "b.raw"
    a.write_bytes(b"\x01" * 4096)
    b.write_bytes(b"\x02" * 4096)

    ### When / Then
    assert a.stat().st_size == b.stat().st_size
    assert sha256_file(a) != sha256_file(b)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
