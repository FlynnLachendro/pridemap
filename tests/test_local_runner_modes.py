# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "pydantic>=2", "pytest>=8"]
# ///
"""Regression tests for the LOCAL runners — native, mono and docker — on a user's own machine.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/omics.py run_all_sh() as it behaves off Modal: the native and docker MaxQuant
         branches, the stage-lifecycle trap, and the kit contents a local user downloads.

Why this file exists: a run of changes was made to fix the Modal cloud path — reachable failure
reporting, a disk check, raw-path resolution, an empty-raw guard, a selection key in the kit. Every
one of them lives in code the LOCAL runners share, because there is one generator and one
run_all.sh. The docker branch in particular had its inline symlink loop replaced by a shared
helper, which is a refactor of working code.

So these tests exercise the local paths specifically, and assert the properties a laptop run
depends on rather than the ones Modal does. They are deliberately separate from the Modal-shaped
tests: a cloud fix that quietly breaks `bash run_all.sh` on a laptop is the regression this project
can least afford, since that is the path a user can actually inspect.

run:  uv run tests/test_local_runner_modes.py
      (or: uv run pytest tests/test_local_runner_modes.py)
"""
from __future__ import annotations

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
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _generated() -> str:
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


def _kit(tmp_path: pathlib.Path, *, symlinked: bool) -> pathlib.Path:
    """A kit whose raw/ holds either real files (a plain local run) or symlinks (a shared cache)."""
    d = tmp_path / "kit"
    d.mkdir()
    (d / "run_all.sh").write_text(_generated())
    (d / "preflight.sh").write_text("exit 0\n")
    (d / "download_fasta.sh").write_text(
        'mkdir -p fasta\nprintf ">x\\nSEQ\\n" > fasta/UP000005640.fasta\nexit 0\n'
    )
    if symlinked:
        cache = tmp_path / "cache"
        cache.mkdir()
        for n in RUNS:
            (cache / n).write_text("RAWDATA")
        (d / "download_raw.sh").write_text(
            "mkdir -p raw\n" + "".join(f'ln -sf "{cache / n}" "raw/{n}"\n' for n in RUNS) + "exit 0\n"
        )
    else:
        (d / "download_raw.sh").write_text(
            "mkdir -p raw\n" + "".join(f'printf RAWDATA > "raw/{n}"\n' for n in RUNS) + "exit 0\n"
        )
    (d / "mqpar.xml").write_text(
        "<MaxQuantParams><filePaths>\n"
        + "".join(f"<string>__KIT_DIR__/raw/{n}</string>\n" for n in RUNS)
        + "</filePaths></MaxQuantParams>\n"
    )
    return d


def _native_mq(d: pathlib.Path) -> pathlib.Path:
    """Stands in for a locally installed MaxQuantCmd: records the paths it was given."""
    mq = d / "fake_mq.sh"
    mq.write_text(
        f"grep -o '<string>[^<]*</string>' \"$1\" | sed 's/<[^>]*>//g' > {d / 'paths_seen.txt'}\n"
        "mkdir -p combined/txt\nprintf 'h\\nrow\\n' > combined/txt/proteinGroups.txt\nexit 0\n"
    )
    mq.chmod(0o755)
    return mq


def _docker_stub(d: pathlib.Path) -> pathlib.Path:
    """Stands in for the docker CLI: records its full argv, then fakes a successful search."""
    bin_dir = d / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {d / "docker_argv.txt"}\n'
        'case "$1" in\n'
        '  info|pull|build|create|cp|rm) exit 0 ;;\n'
        "esac\n"
        # `docker run ... maxquant <config>` — fake the search output the stage checks for.
        f'mkdir -p {d / "combined" / "txt"}\n'
        f'printf \'h\\nrow\\n\' > {d / "combined" / "txt" / "proteinGroups.txt"}\n'
        "exit 0\n"
    )
    docker.chmod(0o755)
    return bin_dir


def _run(d: pathlib.Path, env: dict) -> tuple[int, str]:
    proc = subprocess.run(
        ["bash", "run_all.sh"],
        cwd=d,
        env={"PATH": BASE_PATH, "HOME": str(d), "PRIDEMAP_NO_UPLOAD": "1", **env},
        capture_output=True,
        text=True,
        timeout=180,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_a_plain_local_run_with_real_files_is_untouched(tmp_path):
    """Scenario: the ordinary laptop case, where raw/ holds real files and nothing needs resolving.

    Slice: local native runner.
    Given a kit whose raw/ contains real files rather than symlinks into a cache,
    When the native MaxQuant runner runs,
    Then the paths it receives are the kit's own raw/ paths, unchanged — resolve_raw_paths was
      added for the cloud's shared cache and must be a no-op when there is nothing to resolve,
      rather than rewriting a perfectly good local path.
    """
    ### Given
    d = _kit(tmp_path, symlinked=False)
    mq = _native_mq(d)

    ### When
    code, out = _run(d, {"MAXQUANT_CMD": str(mq)})

    ### Then
    assert code == 0, f"a plain local run must still succeed:\n{out}"
    paths = [p for p in (d / "paths_seen.txt").read_text().splitlines() if p.strip()]
    assert paths == [str(d / "raw" / n) for n in RUNS], (
        "a local run's own raw/ paths must be passed through untouched"
    )


def test_the_docker_runner_still_rewrites_paths_and_mounts_their_directories(tmp_path):
    """Scenario: the containerised local runner keeps the behaviour it had before the refactor.

    Slice: local docker runner.
    Given a kit whose raw/ entries are symlinks into a cache outside it, and no native MaxQuant,
    When the docker runner runs,
    Then the config handed to the container names the real paths, and each real path's directory is
      bind-mounted at the same path. This branch's inline loop was replaced by the shared helper, so
      both halves of what it used to do — the rewrite AND the mounts — need pinning; a rewrite
      without the mount produces a path the container cannot see.
    """
    ### Given
    d = _kit(tmp_path, symlinked=True)
    bin_dir = _docker_stub(d)
    cache_dir = tmp_path / "cache"

    ### When
    code, out = _run(d, {"PATH": f"{bin_dir}:{BASE_PATH}"})

    ### Then
    assert code == 0, f"the docker runner must still complete:\n{out}"
    config = (d / "mqpar.docker.xml").read_text()
    for n in RUNS:
        assert str(cache_dir / n) in config, f"{n} should resolve to its real path in the config"
        assert f"/data/raw/{n}" not in config, f"{n} still carries the unresolved container path"

    argv = (d / "docker_argv.txt").read_text()
    run_line = next(line for line in argv.splitlines() if " maxquant " in line)
    assert f"-v {cache_dir}:{cache_dir}" in run_line, (
        f"the real path's directory must be bind-mounted or the container cannot read it:\n{run_line}"
    )
    assert run_line.count(f"-v {cache_dir}:{cache_dir}") == 1, (
        "two files from one cache directory must produce a single mount, not a duplicate"
    )


def test_the_run_pid_file_is_cleaned_up_on_success(tmp_path):
    """Scenario: a finished local run does not look like one still in progress.

    Slice: local run lifecycle.
    Given a kit with a run.pid left in place, as the local API writes when it starts a run,
    When the pipeline completes,
    Then run.pid is gone — the API refuses to start a second run while that file names a live
      process, so leaving it behind would block the next local run. The EXIT trap that removes it
      was replaced wholesale when failure reporting was added, which is why it is pinned here.
    """
    ### Given
    d = _kit(tmp_path, symlinked=False)
    mq = _native_mq(d)
    (d / ".pipeline").mkdir(exist_ok=True)
    (d / ".pipeline" / "run.pid").write_text("424242")

    ### When
    code, out = _run(d, {"MAXQUANT_CMD": str(mq)})

    ### Then
    assert code == 0, out
    assert not (d / ".pipeline" / "run.pid").exists(), "a completed run must clear its pid file"


def test_the_run_pid_file_is_cleaned_up_after_a_failure(tmp_path):
    """Scenario: a failed local run also releases the lock on the next attempt.

    Slice: local run lifecycle.
    Given a kit whose FASTA download fails, with a run.pid in place,
    When the pipeline dies,
    Then run.pid is gone, because a failed run that kept its pid file would make the API report "a
      run is already in progress" for a run that had already stopped.
    """
    ### Given
    d = _kit(tmp_path, symlinked=False)
    mq = _native_mq(d)
    (d / "download_fasta.sh").write_text('echo "no network" >&2\nexit 1\n')
    (d / ".pipeline").mkdir(exist_ok=True)
    (d / ".pipeline" / "run.pid").write_text("424242")

    ### When
    code, _ = _run(d, {"MAXQUANT_CMD": str(mq)})

    ### Then
    assert code != 0
    assert not (d / ".pipeline" / "run.pid").exists(), "a failed run must clear its pid file too"


def test_a_local_rerun_still_skips_the_stages_it_finished(tmp_path):
    """Scenario: marker-based resume still works where the artifacts are genuinely still there.

    Slice: local run lifecycle.
    Given a local kit that has completed its download stage once,
    When it is run again in the same directory,
    Then the download stage is skipped. On a laptop the work directory IS the kit and persists
      between runs, so the marker and the files it certifies agree. That is exactly the condition
      that does NOT hold on Modal, where the work dir is a fresh tempdir each run — which is why
      cross-run markers were dropped there and kept here.
    """
    ### Given
    d = _kit(tmp_path, symlinked=False)
    mq = _native_mq(d)
    _run(d, {"MAXQUANT_CMD": str(mq)})
    assert (d / ".pipeline" / "download.done").exists(), "first run should record the marker"

    ### When
    code, out = _run(d, {"MAXQUANT_CMD": str(mq)})

    ### Then
    assert code == 0, out
    assert "already done — skipping" in out
    assert (d / "raw" / RUNS[0]).exists(), "the artifacts the marker certifies are still present"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
