# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "pydantic>=2", "pytest>=8"]
# ///
"""Contract tests for the raw-file paths MaxQuant is handed, and the count of what was downloaded.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/omics.py _stage_lib() resolve_raw_paths and the search/download stage bodies in
         run_all_sh() — the paths that reach the search engine, on every runner.

Why this file exists: ThermoFisher's RawFileReaderAdapter.FileFactory() sets IsError=True when
handed a SYMLINK path. SelectInstrument then throws, and MaxQuant reports

    NoSelectedDeviceException: RunHeaderEx

with no mention of symlinks anywhere. The identical file at its real path reads fine.
download_raw.sh builds raw/ with `ln -sf` from a shared cache, so every runner hands MaxQuant
symlinks.

The resolution existed, but ONLY inside the docker branch. Modal runs the native branch, where it
was absent — so the same failure kept recurring on the cloud path while local Docker runs were
fine, and it read as a MaxQuant problem rather than a path problem. That is the thing these tests
pin: not "the docker branch resolves paths" but "EVERY branch does".

Beside it, the download stage counted raw/ with `find -type f`, which never matches a symlink. It
printed "0 files in raw/" and then declared success — a false all-clear sitting directly above the
real failure.

run:  uv run tests/test_raw_path_resolution.py
      (or: uv run pytest tests/test_raw_path_resolution.py)
"""
from __future__ import annotations

import pathlib
import re
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
    return run_all_sh(ctx, search_plan(TAGS, RUNS))


def _kit(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kit whose raw/ is built from symlinks into a cache outside it, as Modal's /raw-cache is."""
    d = tmp_path / "kit"
    d.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in RUNS:
        (cache / name).write_text("RAWDATA")

    (d / "run_all.sh").write_text(_generated())
    (d / "preflight.sh").write_text("exit 0\n")
    (d / "download_raw.sh").write_text(
        "mkdir -p raw\n"
        + "".join(f'ln -sf "{cache / n}" "raw/{n}"\n' for n in RUNS)
        + "exit 0\n"
    )
    (d / "download_fasta.sh").write_text(
        'mkdir -p fasta\nprintf ">x\\nSEQ\\n" > fasta/UP000005640.fasta\nexit 0\n'
    )
    (d / "mqpar.xml").write_text(
        "<MaxQuantParams><filePaths>\n"
        + "".join(f"<string>__KIT_DIR__/raw/{n}</string>\n" for n in RUNS)
        + "</filePaths></MaxQuantParams>\n"
    )
    # Stands in for MaxQuant: records the paths it was handed, then succeeds.
    seen = d / "paths_seen.txt"
    (d / "fake_mq.sh").write_text(
        f"grep -o '<string>[^<]*</string>' \"$1\" | sed 's/<[^>]*>//g' > {seen}\n"
        "mkdir -p combined/txt\nprintf 'h\\nrow\\n' > combined/txt/proteinGroups.txt\nexit 0\n"
    )
    (d / "fake_mq.sh").chmod(0o755)
    return d


def _run(d: pathlib.Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["bash", "run_all.sh"],
        cwd=d,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "MAXQUANT_CMD": str(d / "fake_mq.sh"),
            "PRIDEMAP_NO_UPLOAD": "1",
            "HOME": str(d),
        },
        capture_output=True,
        text=True,
        timeout=180,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_maxquant_is_handed_real_paths_not_symlinks(tmp_path):
    """Scenario: the search engine receives the file, not a link to it.

    Slice: raw path resolution.
    Given a kit whose raw/ entries are symlinks into a cache outside it,
    When the pipeline runs the native MaxQuant runner,
    Then every path in the config it receives is a real file rather than a symlink — handing the
      Thermo reader a symlink makes it set IsError and report NoSelectedDeviceException, which is
      the failure that kept recurring on Modal.
    """
    ### Given
    d = _kit(tmp_path)

    ### When
    code, out = _run(d)

    ### Then
    assert code == 0, f"the stubbed run should succeed:\n{out}"
    paths = [p for p in (d / "paths_seen.txt").read_text().splitlines() if p.strip()]
    assert len(paths) == len(RUNS), f"expected one path per run, got {paths}"
    for p in paths:
        path = pathlib.Path(p)
        assert not path.is_symlink(), f"MaxQuant was handed a symlink, which its reader refuses: {p}"
        assert path.is_file(), f"MaxQuant was handed a path that is not a readable file: {p}"


def test_every_maxquant_runner_resolves_paths_not_only_the_container(tmp_path):
    """Scenario: the resolution is not missing from any branch.

    Slice: raw path resolution.
    Given the generated script's three MaxQuant runners — native, mono and docker,
    When their bodies are read,
    Then each calls resolve_raw_paths before invoking the engine. The original fix lived only in
      the docker branch; Modal takes the native branch, so cloud runs kept failing while local
      Docker runs were fine, and the symptom looked like a MaxQuant bug rather than a missing call.
    """
    ### Given
    script = _generated()
    start = script.index("if stage search_dda")
    body = script[start : script.index("\nfi", start)]

    ### When
    runners = re.findall(r'^\s*(?:el)?if \[ -n "\$MQ" \]|^\s*elif command -v (?:mono|docker)', body, re.M)
    calls = body.count("resolve_raw_paths")

    ### Then
    assert len(runners) == 3, f"expected native, mono and docker runners, found {runners}"
    assert calls == len(runners), (
        f"{len(runners)} runners but {calls} resolve_raw_paths calls — a runner is missing it, "
        "which is exactly how the native (Modal) path was left broken"
    )


def test_a_symlinked_raw_directory_is_not_reported_as_empty(tmp_path):
    """Scenario: the download stage counts what it actually produced.

    Slice: download reporting.
    Given a raw/ directory filled with symlinks,
    When the download stage reports how many files it fetched,
    Then it counts them, because `find -type f` never matches a symlink and the stage printed
      "0 files in raw/" and then declared success — a false all-clear directly above the real
      failure.
    """
    ### Given
    d = _kit(tmp_path)

    ### When
    _, out = _run(d)

    ### Then
    assert f"{len(RUNS)} files in raw/" in out, f"expected a truthful count:\n{out}"
    assert "0 files in raw/" not in out


def test_an_empty_raw_directory_stops_the_run(tmp_path):
    """Scenario: a download that produced nothing fails rather than handing nothing to the search.

    Slice: download reporting.
    Given a download script that exits cleanly without fetching anything,
    When the pipeline runs,
    Then the download stage fails with a reason and the search never starts, because an empty raw/
      can only fail later, deep inside MaxQuant, where the cause is unrecognisable.
    """
    ### Given
    d = _kit(tmp_path)
    (d / "download_raw.sh").write_text("mkdir -p raw\nexit 0\n")  # succeeds, fetches nothing

    ### When
    code, out = _run(d)

    ### Then
    assert code != 0, f"an empty raw/ must stop the run:\n{out}"
    assert "raw/ is empty" in out
    assert not (d / "paths_seen.txt").exists(), "the search must not have been invoked"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
