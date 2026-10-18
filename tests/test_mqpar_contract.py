# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "pydantic>=2"]
# ///
"""Contract tests for the generated MaxQuant mqpar.xml.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/omics.py mqpar_xml() — the structural invariants MaxQuant enforces at RUN time.

Why this file exists: MaxQuant 2.0.3 reads several mqpar fields without checking for null, and each
wrong one aborts a run 10 to 37 minutes in, after the raw download and often after the search itself
has finished. `MaxQuantCmd --dryrun` catches none of them, because building the job graph never
dereferences those fields. Six real bugs were found this way, one failed run at a time.

Every assertion below encodes a failure that actually happened, named in the test. "Absent", "empty"
and "sized to match another field" are three different things to MaxQuant, so each case is pinned
rather than normalised into something tidier-looking.

run:  uv run tests/test_mqpar_contract.py
      (or: uv run pytest tests/test_mqpar_contract.py)

For the deeper check that parses the config with MaxQuant's own libraries rather than with
ElementTree, see tools/mqpar-validate/.
"""
from __future__ import annotations

import pathlib
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api.omics import (  # noqa: E402
    FRAGMENTATION_TYPES,
    MSMS_PARAMS,
    RESTRICT_MODS,
    mqpar_xml,
    search_plan,
)

# A Thermo DDA label-free deposition: the shape the kit generates most often, and the one every
# invariant below was observed against (PXD046787, 24 Orbitrap runs, human).
TAGS = {
    "instrument_family": "orbitrap",
    "labelling": "label_free",
    "acquisition": "dda",
    "organism": "Homo sapiens",
}
RECIPE = {
    "enzyme": {"value": ["Trypsin/P"], "missed_cleavages": 2},
    "fixed_modifications": {"value": ["Carbamidomethyl (C)"]},
    "variable_modifications": {"value": ["Oxidation (M)", "Acetyl (Protein N-term)"]},
    "fasta": {"uniprot_proteome": "UP000005640", "organism": "Homo sapiens", "url": None},
}
RUNS = ["2019_12_13_Daria_01.raw", "2019_12_13_Daria_02.raw"]


def _generated(run_names: list[str] = RUNS) -> ET.Element:
    """The real generator output, parsed. No fixture XML: a fixture would drift from the generator."""
    ctx = {"accession": "PXD046787", "t": TAGS, "r": RECIPE, "raw": [], "run_labels": {}}
    plan = search_plan(TAGS, run_names)
    return ET.fromstring(mqpar_xml(ctx, plan, run_names))


def _require(root: ET.Element, tag: str) -> ET.Element:
    """The element, or a readable assertion. Never a None that turns a missing field into a TypeError
    three lines later — a crash reports as an error rather than as "this invariant is broken"."""
    el = root.find(tag)
    assert el is not None, f"<{tag}> is absent; MaxQuant dereferences it without a null check"
    return el


def _children(root: ET.Element, tag: str) -> int:
    return len(list(_require(root, tag)))


def _text(root: ET.Element, tag: str) -> str:
    return (_require(root, tag).text or "").strip()


def test_label_mods_is_sized_to_multiplicity():
    """Scenario: a label-free group still carries one empty label, not zero.

    Slice: mqpar contract — labelMods.

    Given a label-free deposition, when the config is generated, then labelMods holds exactly
    <multiplicity> entries, because MaxQuant allocates its apl name table as
    [fragmentationTypes x massAnalysers, multiplicity] and then indexes the label list by that same
    multiplicity counter. Zero entries means l[0] on an empty array: "Index was outside the bounds
    of the array" during MS/MS preparation, job 8 of 49.
    """
    ### Given
    root = _generated()
    group = _require(root, "parameterGroups/parameterGroup")

    ### When
    multiplicity = int(_text(group, "multiplicity"))
    labels = _children(group, "labelMods")

    ### Then
    assert multiplicity == 1, "a label-free group is multiplicity 1"
    assert labels == multiplicity, (
        f"labelMods has {labels} entries but multiplicity is {multiplicity}; "
        "MaxQuant indexes the label list by the multiplicity counter"
    )


def test_multiplicity_and_max_labeled_aa_are_present():
    """Scenario: the labelling fields exist even when nothing is labelled.

    Slice: mqpar contract — labelling block.

    Given a label-free deposition, when the config is generated, then multiplicity and maxLabeledAa
    are present, because GroupParams.GetLmods() is called unconditionally and dereferences them —
    a NullReferenceException at deisotoping, job 7, after feature detection has already succeeded.
    """
    ### Given
    group = _require(_generated(), "parameterGroups/parameterGroup")

    ### When / Then
    for tag in ("multiplicity", "maxLabeledAa"):
        assert group.find(tag) is not None, f"<{tag}> is absent; GetLmods() dereferences it"


def test_msms_params_array_covers_every_mass_analyser():
    """Scenario: MaxQuant finds settings for the analyser it reads from the raw file.

    Slice: mqpar contract — msmsParamsArray.

    Given any deposition, when the config is generated, then msmsParamsArray contains one entry per
    mass analyser MaxQuant knows, because it looks them up by the name in the raw file ("FTMS" for an
    Orbitrap). An absent array is an empty dictionary, not a defaulted one: "The given key 'FTMS' was
    not present in the dictionary" at MS/MS preparation, job 8.
    """
    ### Given
    root = _generated()

    ### When
    names = [(e.findtext("Name") or "").strip() for e in _require(root, "msmsParamsArray")]

    ### Then
    assert names == [p[0] for p in MSMS_PARAMS], f"analyser set changed: {names}"
    assert "FTMS" in names, "FTMS is the Orbitrap lookup key; without it an Orbitrap run cannot start"


def test_fragmentation_params_array_covers_every_type():
    """Scenario: the fragmentation lookup table is complete.

    Slice: mqpar contract — fragmentationParamsArray.

    Given any deposition, when the config is generated, then fragmentationParamsArray contains one
    entry per fragmentation type, looked up by name exactly as the analysers are.
    """
    ### Given
    root = _generated()

    ### When
    names = [(e.findtext("Name") or "").strip() for e in _require(root, "fragmentationParamsArray")]

    ### Then
    assert names == FRAGMENTATION_TYPES, f"fragmentation set changed: {names}"


def test_restrict_mods_is_present_and_not_silently_empty():
    """Scenario: protein quantification keeps MaxQuant's own default modification set.

    Slice: mqpar contract — restrictMods.

    Given any deposition, when the config is generated, then restrictMods carries MaxQuant's default
    modifications. Absent, Tables.ToModifications() throws NullReferenceException while the combined
    folder is prepared — job 19, with the search already done. Empty would parse, but it would
    silently change which peptides count towards protein quantification, so emptiness is a bug here
    rather than a harmless default.
    """
    ### Given
    root = _generated()

    ### When
    mods = [(e.text or "").strip() for e in _require(root, "restrictMods")]

    ### Then
    assert mods == RESTRICT_MODS, f"restrictMods drifted from MaxQuant's default: {mods}"
    assert mods, "an empty restrictMods parses but changes the science; it is not a safe default"


def test_reference_channel_is_absent_not_empty():
    """Scenario: the field MaxQuant wants missing stays missing.

    Slice: mqpar contract — referenceChannel.

    Given a label-free deposition, when the config is generated, then referenceChannel is absent.
    Absent, the ReferenceChannels getter yields one entry per parameter group; present-but-empty
    makes that same getter throw IndexOutOfRangeException while modificationSpecificPeptides is
    written — job 48 of 49. This one is pinned because "every empty list should be emitted" is a
    natural-looking tidy-up that reintroduces the failure.
    """
    ### Given
    root = _generated()

    ### When
    el = root.find("referenceChannel")

    ### Then
    assert el is None, (
        "referenceChannel must be ABSENT, not present-and-empty: an empty one makes "
        "MaxQuantParams.get_ReferenceChannels() throw IndexOutOfRangeException at job 48"
    )


def test_first_search_overrides_stay_absent():
    """Scenario: fields on the active first-search path keep the form that is known to work.

    Slice: mqpar contract — first-search overrides.

    Given a label-free deposition, when the config is generated, then enzymesFirstSearch and
    variableModificationsFirstSearch are absent. The first search does run, and absent is the form a
    completed run was observed with; empty may mean "none at all" rather than "inherit the main
    search". On an active path the version known to work wins over the tidier-looking one.
    """
    ### Given
    group = _require(_generated(), "parameterGroups/parameterGroup")

    ### When / Then
    for tag in ("enzymesFirstSearch", "variableModificationsFirstSearch"):
        assert group.find(tag) is None, (
            f"<{tag}> is on the active first-search path and was absent in the run that completed; "
            "do not emit it empty without a run to back the change"
        )


def test_inactive_workflow_lists_are_present_and_empty():
    """Scenario: settings for workflows the kit never generates read as empty, not null.

    Slice: mqpar contract — inactive workflow lists.

    Given a label-free DDA deposition, when the config is generated, then the isobaric, crosslink and
    DIA lists are present and empty, so any code path reaching them finds a list rather than null.
    Empty is also what they genuinely are for this workflow.
    """
    ### Given
    group = _require(_generated(), "parameterGroups/parameterGroup")

    ### When / Then
    for tag in ("isobaricLabels", "crosslinkModifications", "crosslinkSites",
                "crosslinkNetworkFiles", "diaLibraryPaths", "diaMsmsPaths",
                "diaPeptidePaths", "diaEvidencePaths"):
        assert _children(group, tag) == 0, f"<{tag}> should be empty for a label-free DDA search"


def test_paths_use_the_kit_directory_placeholder():
    """Scenario: nothing in the config hard-codes the machine it was generated on.

    Slice: mqpar contract — portability.

    Given a deposition, when the config is generated, then every path carries the kit-directory
    placeholder rather than an absolute path, because MaxQuant's workers resolve relative paths from
    their own working directory and run_all.sh substitutes the real directory wherever the kit is
    unpacked.
    """
    ### Given
    xml = mqpar_xml(
        {"accession": "PXD046787", "t": TAGS, "r": RECIPE, "raw": [], "run_labels": {}},
        search_plan(TAGS, RUNS), RUNS)

    ### When
    root = ET.fromstring(xml)
    paths = [(e.text or "") for e in _require(root, "filePaths")]
    fasta = root.findtext(".//fastaFilePath") or ""

    ### Then
    assert paths, "no run paths were generated"
    for p in paths + [fasta]:
        assert p.startswith("__KIT_DIR__/"), f"path is not kit-relative: {p}"


def test_experiment_count_matches_run_count():
    """Scenario: the per-run arrays stay parallel.

    Slice: mqpar contract — per-run arrays.

    Given several runs, when the config is generated, then filePaths, experiments, fractions, ptms
    and paramGroupIndices all have one entry per run, because MaxQuant reads them as parallel arrays
    indexed by run — a short one silently pairs a run with another run's settings.
    """
    ### Given
    runs = ["a.raw", "b.raw", "c.raw"]

    ### When
    root = _generated(runs)

    ### Then
    for tag in ("filePaths", "experiments", "fractions", "ptms", "paramGroupIndices"):
        assert _children(root, tag) == len(runs), f"<{tag}> is not one entry per run"


def test_use_dot_net_core_is_true():
    """Scenario: MaxQuant uses the dotnet runtime path, not the Windows-compat quoted path.

    Slice: mqpar contract — useDotNetCore.

    Given any deposition, when the config is generated, then useDotNetCore is True.
    Without it, QueueingSystem.WorkDispatcher.GetCommandFilename() wraps the subprocess path in
    literal " chars for Windows compatibility: execve receives '"/opt/maxquant/MaxQuantTask.exe"'
    with embedded quotes, which is not a valid path on Linux — ENOENT. The SDK image also requires
    this setting so MaxQuant uses 'dotnet MaxQuantTaskCore.dll' rather than the .exe path.
    """
    ### Given
    root = _generated()

    ### When
    val = _text(root, "useDotNetCore")

    ### Then
    assert val == "True", (
        "<useDotNetCore> must be True; False or absent causes ENOENT on Linux because "
        "GetCommandFilename() wraps the subprocess path in literal quotes"
    )


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:
            # Any exception, not just AssertionError: a missing element used to raise TypeError here,
            # which aborted the whole run and made a reintroduced bug look harmless.
            failures += 1
            label = exc if isinstance(exc, AssertionError) else f"{type(exc).__name__}: {exc}"
            print(f"  FAIL  {name}\n          {label}")
    print(f"\n{'FAILED' if failures else 'OK'}: {failures} failure(s)")
    sys.exit(1 if failures else 0)
