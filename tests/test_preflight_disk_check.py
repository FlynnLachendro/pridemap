# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "pydantic>=2", "pytest>=8"]
# ///
"""Contract tests for the disk-space check in the generated preflight.sh.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/omics.py preflight_sh() — the free-space gate that runs before anything is downloaded.

Why this file exists: a Modal Volume reports 2^53 KB free, a sentinel meaning "no meaningful limit".
int(2^53/1048576) is 8589934592, and mawk — Debian's awk, and so the container's — renders values
above 2^31 through OFMT as "8.58993e+09". `[ "8.58993e+09" -ge 10 ]` then fails with "integer
expression expected", the || branch fires, and preflight failed EVERY cloud run on a volume with
effectively unlimited space. macOS's awk prints the integer plainly, which is why it never
reproduced locally.

The fix turns a parse failure into a warning. That makes the obvious test — "the pathological value
no longer fails" — worthless on its own: a check that warns unconditionally passes it while silently
never refusing anything again. So every test here comes with its counterexample, and the stub df
drops a sentinel so "did not fail" can be told apart from "was never consulted".

run:  uv run tests/test_preflight_disk_check.py
      (or: uv run pytest tests/test_preflight_disk_check.py)
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api.omics import preflight_sh, search_plan  # noqa: E402

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

# df -Pk column 4 is free KB. 2^53 is what a Modal Volume actually reported on 2026-10-18.
MODAL_VOLUME_FREE_KB = 2**53
TWO_GB_FREE_KB = 2 * 1024 * 1024

# The MaxQuant licence gate is its own concern (tests/test_maxquant_licence_gate.py). Accepted here so
# the licence cannot decide a verdict that is meant to be about disk space.
LICENCE_ACCEPTED = {"MAXQUANT_ACCEPT_LICENSE": "1"}


def _generated() -> str:
    """The real generator output. No fixture script: a fixture would drift from the generator."""
    ctx = {"accession": "PXD046787", "t": TAGS, "r": RECIPE, "raw": [], "run_labels": {}}
    return preflight_sh(ctx, search_plan(TAGS, RUNS))


def _run_with_df(tmp_path: pathlib.Path, free_kb: int, need_gb: int) -> tuple[int, str, bool]:
    """Run preflight with df stubbed to report `free_kb`, and say whether the stub was consulted.

    Returns (exit code, combined output, df_was_called). Only the disk check is under test, so the
    other probes are stubbed to succeed — otherwise a missing curl on the test machine would decide
    the result instead of the disk arithmetic.
    """
    d = tmp_path / "kit"
    d.mkdir()
    (d / "preflight.sh").write_text(_generated())

    bin_dir = d / "stub-bin"
    bin_dir.mkdir()
    # Header line plus the data line preflight reads with NR==2.
    (bin_dir / "df").write_text(
        "#!/usr/bin/env bash\n"
        f'touch "{d}/df_was_called"\n'
        'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
        f'echo "/dev/stub 99999999 1 {free_kb} 1% /"\n'
    )
    (bin_dir / "df").chmod(0o755)
    # MaxQuant present natively, so the tool probes pass and cannot mask the disk verdict.
    (bin_dir / "maxquant").write_text("#!/usr/bin/env bash\necho MaxQuantCmd 2.0.3.0\n")
    (bin_dir / "maxquant").chmod(0o755)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "PRIDEMAP_MIN_DISK_GB": str(need_gb),
        "MAXQUANT_CMD": "maxquant",
        "HOME": str(d),
        **LICENCE_ACCEPTED,
    }
    proc = subprocess.run(
        ["bash", "preflight.sh"], cwd=d, env=env, capture_output=True, text=True, timeout=120
    )
    return proc.returncode, proc.stdout + proc.stderr, (d / "df_was_called").exists()


def test_the_free_space_conversion_cannot_emit_scientific_notation():
    """Scenario: the generated awk is written so no awk implementation can produce "8.58993e+09".

    Slice: preflight disk check.
    Given the generated preflight script,
    When its free-space conversion is read,
    Then it forces the integer form with printf "%d" and screens the field with a digits-only regex
      first.

    This invariant is asserted on the script TEXT rather than by running it, and deliberately so:
    the defect only appears under mawk, whose OFMT renders values above 2^31 in scientific notation.
    The macOS awk this suite usually runs on prints the same value as a plain integer, so a purely
    behavioural test passes here both before and after the fix and guards nothing. Pinning the form
    is what makes the regression catchable on a developer machine.
    """
    ### Given / When
    script = _generated()

    ### Then
    assert 'printf "%d' in script, (
        'bare print lets mawk render int($4/1048576) through OFMT as 8.58993e+09, which `[ -ge ]` '
        "rejects with 'integer expression expected'"
    )
    assert "/^[0-9]+$/" in script, (
        'printf "%d" coerces a non-numeric field to 0, so the field must be screened BEFORE '
        'conversion — otherwise an unreadable df reads as "0GB free", i.e. full'
    )


def test_a_volume_reporting_unlimited_space_does_not_fail_the_run(tmp_path):
    """Scenario: the 2^53-KB sentinel a Modal Volume reports is read as "plenty", not as a failure.

    Slice: preflight disk check.
    Given a filesystem reporting 2^53 KB free and a 10 GB requirement,
    When preflight runs,
    Then it does not mark the disk bad, because this exact value aborted every cloud run before the
      search could start — the volume had no meaningful limit and the check said it was full.
    """
    ### Given / When
    code, out, df_called = _run_with_df(tmp_path, MODAL_VOLUME_FREE_KB, need_gb=10)

    ### Then
    assert df_called, "df was never consulted — this test would pass for the wrong reason"
    assert "integer expression expected" not in out, "the value must reach `[` in integer form"
    assert "✗ disk" not in out, f"a volume with no limit was called full:\n{out}"
    assert code == 0, f"preflight should pass on an unlimited volume:\n{out}"


def test_a_genuinely_full_disk_is_still_refused(tmp_path):
    """Scenario: the fix did not turn the gate into a rubber stamp.

    Slice: preflight disk check.
    Given a filesystem with 2 GB free and a 10 GB requirement,
    When preflight runs,
    Then the disk is marked bad and preflight exits non-zero — the counterexample without which
      "the pathological value no longer fails" is equally satisfied by a check that never refuses
      anything again.
    """
    ### Given / When
    code, out, df_called = _run_with_df(tmp_path, TWO_GB_FREE_KB, need_gb=10)

    ### Then
    assert df_called, "df was never consulted — this test would pass for the wrong reason"
    assert "✗ disk" in out, f"2GB free against a 10GB need must be refused:\n{out}"
    assert code != 0, "a failing disk check must fail preflight"


def test_enough_space_passes(tmp_path):
    """Scenario: an ordinary roomy disk is accepted and says how much it found.

    Slice: preflight disk check.
    Given a filesystem with 2 GB free and a 1 GB requirement,
    When preflight runs,
    Then the disk is reported ok, so the two tests above are bracketing a gate that actually
      discriminates rather than one stuck on a single answer.
    """
    ### Given / When
    code, out, _ = _run_with_df(tmp_path, TWO_GB_FREE_KB, need_gb=1)

    ### Then
    assert "✓ disk" in out, f"2GB free against a 1GB need must pass:\n{out}"
    assert code == 0


def test_an_unreadable_df_warns_rather_than_blocking_the_run(tmp_path):
    """Scenario: a filesystem whose free space cannot be parsed does not stop the pipeline.

    Slice: preflight disk check.
    Given a df whose Available column is not a number at all,
    When preflight runs,
    Then the check warns and preflight still passes, because treating "cannot tell" as "full" is
      precisely what stopped work that would have succeeded; the run is allowed to proceed and fail
      honestly later if space really does run out.
    """
    ### Given
    d = tmp_path / "kit"
    d.mkdir()
    (d / "preflight.sh").write_text(_generated())
    bin_dir = d / "stub-bin"
    bin_dir.mkdir()
    (bin_dir / "df").write_text(
        "#!/usr/bin/env bash\n"
        'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
        'echo "/dev/stub 99999999 1 somehow-not-a-number 1% /"\n'
    )
    (bin_dir / "df").chmod(0o755)
    (bin_dir / "maxquant").write_text("#!/usr/bin/env bash\necho MaxQuantCmd 2.0.3.0\n")
    (bin_dir / "maxquant").chmod(0o755)

    ### When
    proc = subprocess.run(
        ["bash", "preflight.sh"],
        cwd=d,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "PRIDEMAP_MIN_DISK_GB": "10",
            "MAXQUANT_CMD": "maxquant",
            "HOME": str(d),
            **LICENCE_ACCEPTED,
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = proc.stdout + proc.stderr

    ### Then
    assert "! disk" in out, f"an unreadable df must warn:\n{out}"
    assert "✗ disk" not in out, "cannot-tell must not be reported as full"
    assert proc.returncode == 0, f"a warning must not block the run:\n{out}"


def test_the_docker_probe_is_silent_where_the_tools_are_built_in(tmp_path):
    """Scenario: a Modal container does not warn about a Docker daemon it is not meant to have.

    Slice: preflight tool probes.
    Given a host that declares its tools are preinstalled,
    When preflight runs,
    Then no "docker not available" warning appears, because that line is pure noise in a log the UI
      now puts in front of the user, and noise in a diagnostic is what makes the real line hard to
      find.
    """
    ### Given
    d = tmp_path / "kit"
    d.mkdir()
    (d / "preflight.sh").write_text(_generated())
    bin_dir = d / "stub-bin"
    bin_dir.mkdir()
    (bin_dir / "maxquant").write_text("#!/usr/bin/env bash\necho MaxQuantCmd 2.0.3.0\n")
    (bin_dir / "maxquant").chmod(0o755)

    ### When
    proc = subprocess.run(
        ["bash", "preflight.sh"],
        cwd=d,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "PRIDEMAP_MIN_DISK_GB": "1",
            "MAXQUANT_CMD": "maxquant",
            "PRIDEMAP_TOOLS_PREINSTALLED": "1",
            "HOME": str(d),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = proc.stdout + proc.stderr

    ### Then
    assert "docker not available" not in out, f"the docker warning must be suppressed:\n{out}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
