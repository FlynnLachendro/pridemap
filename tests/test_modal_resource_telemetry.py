# /// script
# requires-python = ">=3.11"
# dependencies = ["modal>=1.5", "pytest>=8"]
# ///
"""Contract tests for the CPU/memory readout and the Modal resource janitor's cleanup rules.

Author:  PRIDE Map
Created: 2026-10-18
Scope:   api/modal_runner.py  — parse_cgroup_usage_usec, busy_cores, cpu_report
         api/modal_janitor.py — plan_cleanup, summarise

Why this file exists: both of these answer questions whose wrong answers are invisible.

The CPU readout exists because "is MaxQuant using the threads we passed in" could not be answered
from the logs — numThreads is patched and logged, which only proves what was REQUESTED. The first
container to report its own figures then showed `cores: 24, allocated: 8`: os.cpu_count() sees the
HOST, not the reservation, so a percentage computed against it understates use by 3x and the mqpar
patch was sizing MaxQuant's thread pool from the wrong number entirely. The denominator is the whole
point of the test below.

The janitor decides what to delete from volumes that hold the only copy of a finished search. Its
guards are the kind that are never exercised until the day they matter — a sweep that removes the
job directory of a running job does not cancel the job, it just blinds the UI to one that keeps
billing. So each guard is tested for refusing on its own, not merely in combination.

run:  uv run tests/test_modal_resource_telemetry.py
      (or: uv run pytest tests/test_modal_resource_telemetry.py)
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api.modal_janitor import (  # noqa: E402
    Candidate,
    Entry,
    plan_cleanup,
    summarise,
)
from api.modal_runner import (  # noqa: E402
    busy_cores,
    cpu_report,
    parse_cgroup_usage_usec,
    parse_cpuacct_usage_usec,
    raw_names_in_mqpar,
)

HOUR = 3600.0
NOW = 1_700_000_000.0

#: The retention windows the tests reason about, named so a test reads as a rule rather than maths.
TTLS = {"maxquant-scratch": 6 * HOUR, "stale-lock": 2 * HOUR, "job-dir": 7 * 86400}


# --------------------------------------------------------------------------- cgroup parsing

def test_usage_usec_is_read_from_a_cpu_stat_body():
    """Scenario: a cgroup v2 cpu.stat body is parsed for cumulative CPU time.

    Slice: CPU readout — the sample source.
    Given a cpu.stat body in the kernel's key-space-value format,
    When usage is parsed from it,
    Then the usage_usec line is returned and the surrounding keys are ignored.
    """
    ### Given
    body = "usage_usec 123456789\nuser_usec 100000000\nsystem_usec 23456789\nnr_periods 0\n"

    ### When
    usage = parse_cgroup_usage_usec(body)

    ### Then
    assert usage == 123456789


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("", id="empty file"),
        pytest.param("user_usec 1\nsystem_usec 2\n", id="no usage_usec key"),
        pytest.param("usage_usec not-a-number\n", id="unparseable value"),
    ],
)
def test_unreadable_cpu_stat_reports_nothing_rather_than_zero(body):
    """Scenario: cpu.stat cannot be understood, on a host without cgroup v2 or a changed format.

    Slice: CPU readout — absent measurement.
    Given a cpu.stat body with no usable usage figure,
    When usage is parsed from it,
    Then None is returned, because "not measured" and "idle" must stay different answers.
    """
    ### Given / When
    usage = parse_cgroup_usage_usec(body)

    ### Then
    assert usage is None


def test_cgroup_v1_cpuacct_nanoseconds_become_microseconds():
    """Scenario: the file a real Modal container actually exposes is read.

    Slice: CPU readout — the source Modal really has.
    Given the exact cpuacct.usage body probed from a live Modal container,
    When it is parsed,
    Then 910000000 nanoseconds are reported as 910000 microseconds.

    Modal containers are cgroup v1: cpuacct.usage exists and cpu.stat raises FileNotFoundError.
    The first version of the sampler read only v2, so a live run reported its core count and no
    usage at all — a readout that looked present and measured nothing. The unit matters as much as
    the path: cpuacct is nanoseconds, cpu.stat is microseconds, and mixing them overstates CPU by
    1000x, which would read as a container using more cores than exist.
    """
    ### Given
    body = "910000000\n"

    ### When
    usec = parse_cpuacct_usage_usec(body)

    ### Then
    assert usec == 910_000


@pytest.mark.parametrize(
    "body", [pytest.param("", id="empty"), pytest.param("nonsense\n", id="unparseable")],
)
def test_unreadable_cpuacct_reports_nothing(body):
    """Scenario: cpuacct.usage is absent or unreadable.

    Slice: CPU readout — absent measurement.
    Given a cpuacct body with no usable integer,
    When it is parsed,
    Then None is returned rather than zero.
    """
    ### Given / When / Then
    assert parse_cpuacct_usage_usec(body) is None


# --------------------------------------------------------------------------- busy cores

def test_one_core_pinned_for_the_interval_reads_as_one_core():
    """Scenario: a single thread saturates one core for a whole sampling interval.

    Slice: CPU readout — the unit of the figure.
    Given two samples 10 s apart that differ by 10 s of CPU time,
    When busy cores are computed,
    Then the answer is 1.0, so the number on the card reads as cores and not as a ratio.
    """
    ### Given
    prev, cur, elapsed = 0, 10_000_000, 10.0

    ### When
    busy = busy_cores(prev, cur, elapsed)

    ### Then
    assert busy == pytest.approx(1.0)


def test_two_raw_files_on_two_threads_read_as_two_cores_of_sixteen():
    """Scenario: the measured shape of a real run — MaxQuant's early phases on one thread per file.

    Slice: CPU readout — the finding the readout exists to surface.
    Given 30 s of CPU time accrued over a 15 s interval,
    When busy cores are computed and reported against a 16-core reservation,
    Then it reads 2 cores at 12%, which is the "14 cores idle" case the readout must make visible.

    12 and not 13: two of sixteen cores is 12.5%, and round() breaks that tie to even. The exact
    percent is not what this asserts — that 2.0 busy cores on a 16-core box is reported as a small
    fraction, rather than as the full utilisation the patched numThreads would imply, is.
    """
    ### Given
    busy = busy_cores(0, 30_000_000, 15.0)

    ### When
    report = cpu_report(cores=16, allocated=16, busy=busy, rss_bytes=None, memory_mb=65536)

    ### Then
    assert report["busy_cores"] == pytest.approx(2.0)
    assert report["busy_pct"] == 12


@pytest.mark.parametrize(
    "prev, cur, elapsed, why",
    [
        pytest.param(0, 10, 0.0, "no interval to measure over", id="zero interval"),
        pytest.param(0, 10, -5.0, "clock went backwards", id="negative interval"),
        pytest.param(100, 50, 10.0, "counter reset under us", id="counter went backwards"),
    ],
)
def test_a_degenerate_interval_reports_nothing(prev, cur, elapsed, why):
    """Scenario: the two samples cannot support a rate.

    Slice: CPU readout — refusing to invent a figure.
    Given a pair of samples that cannot yield a rate because {why},
    When busy cores are computed,
    Then None is returned rather than a number that would be read as a measurement.
    """
    ### Given / When
    busy = busy_cores(prev, cur, elapsed)

    ### Then
    assert busy is None, why


def test_percentage_is_measured_against_the_reservation_not_the_host():
    """Scenario: Modal lands an 8-core reservation on a 24-core host.

    Slice: CPU readout — the denominator.
    Given 8 cores reserved while os.cpu_count() sees 24, and 4 cores busy,
    When the report is built,
    Then the percentage is 50% of the 8 reserved, and the host's 24 is reported separately.

    This is the bug the first live container exposed: `cores: 24, allocated: 8`. Dividing by the
    host count turns a container at half its reservation into one at 17%, which argues for cutting
    cores that are in fact saturated — and the same wrong number was sizing mqpar's numThreads.
    """
    ### Given / When
    report = cpu_report(cores=24, allocated=8, busy=4.0, rss_bytes=None, memory_mb=65536)

    ### Then
    assert report["cores"] == 8, "the reported core count is what we reserved"
    assert report["host_cores"] == 24, "the host's count is kept, but not used as the denominator"
    assert report["busy_pct"] == 50


def test_the_report_names_the_phase_that_produced_it():
    """Scenario: two containers with different allocations both report figures for one job.

    Slice: resource sizing — attribution.
    Given a report from the download worker's 2-core/4 GB container,
    When the report is built,
    Then it carries phase="download" alongside that container's own allocation.

    Every pillar — cpu, memory, ephemeral_disk, timeout — is declared per function, so a figure
    with no owner cannot size anything. 8.7 GB peak means "cut the search worker's 64 GB" or "the
    4 GB download worker is about to OOM" depending entirely on which container reported it.
    """
    ### Given / When
    report = cpu_report(
        cores=18, allocated=2, busy=0.4, rss_bytes=None, memory_mb=4096, phase="download",
    )

    ### Then
    assert report["phase"] == "download"
    assert report["cores"] == 2, "sized against the download worker's reservation, not the host"
    assert report["memory_gb"] == 4.0


def test_disk_is_reported_as_peak_against_the_declared_ceiling():
    """Scenario: the ephemeral disk that raw staging and MaxQuant intermediates land on.

    Slice: resource sizing — the disk pillar.
    Given 12 GiB used at peak on a 512 GiB filesystem,
    When the report is built,
    Then both the peak and the total are present so the ceiling can be judged against the need.

    ephemeral_disk was set to 512 GiB because that is Modal's default, with nothing measured behind
    it. Staging raw files locally made it load-bearing rather than incidental. The PEAK matters, not
    the current figure: MaxQuant deletes intermediates as it goes, so sampling current use
    under-reports the ceiling the allocation actually has to cover.
    """
    ### Given
    gib = 1024 ** 3

    ### When
    report = cpu_report(
        cores=8, allocated=8, busy=None, rss_bytes=None, memory_mb=65536,
        disk_used_bytes=8 * gib, peak_disk_used_bytes=12 * gib, disk_total_bytes=512 * gib,
    )

    ### Then
    assert report["disk_used_gb"] == 8.0
    assert report["peak_disk_used_gb"] == 12.0
    assert report["disk_total_gb"] == 512.0


def test_absent_figures_are_omitted_rather_than_defaulted():
    """Scenario: a host where neither CPU nor memory accounting can be read.

    Slice: CPU readout — the card's "nothing to show" path.
    Given no busy figure and no RSS,
    When the report is built,
    Then the sizing is still reported and no busy or memory key appears at all.
    """
    ### Given / When
    report = cpu_report(cores=8, allocated=8, busy=None, rss_bytes=None, memory_mb=65536)

    ### Then
    assert report["memory_gb"] == 64.0
    assert "busy_cores" not in report and "busy_pct" not in report
    assert "rss_gb" not in report
    assert "disk_used_gb" not in report and "disk_total_gb" not in report


# --------------------------------------------------------------------------- raw staging selection

def test_only_the_runs_own_raw_files_are_named_for_staging():
    """Scenario: a two-file selection out of a dataset whose cache holds many more.

    Slice: raw staging — what gets copied to local disk.
    Given an mqpar.xml whose filePaths name two raw files,
    When the raw names are read from it,
    Then just those two basenames are returned, with the kit directory prefix stripped.

    The raw cache is keyed by ACCESSION and a run is a SELECTION, so the two diverge: a live run
    logged "copying 3 file(s)" for a two-file search, and on the 24-file dataset the same code
    would copy all 57.6 GB onto a disk sized for the run it was asked to do.
    """
    ### Given
    mqpar = (
        "<filePaths>"
        "<string>__KIT_DIR__/raw/2019_12_13_Daria_01.raw</string>"
        "<string>__KIT_DIR__/raw/2019_12_13_Daria_05.raw</string>"
        "</filePaths>"
    )

    ### When
    names = raw_names_in_mqpar(mqpar)

    ### Then
    assert names == {"2019_12_13_Daria_01.raw", "2019_12_13_Daria_05.raw"}


def test_an_mqpar_naming_no_raw_files_yields_an_empty_selection():
    """Scenario: the file paths cannot be read, so no selection can be derived.

    Slice: raw staging — the "cannot tell" case.
    Given an mqpar.xml with no .raw entries,
    When the raw names are read,
    Then the set is empty, which the caller must treat as "stage everything".

    Returning an empty set and having the caller stage nothing would hand MaxQuant a selection with
    missing inputs — a wrong result. Staging too much is merely slow.
    """
    ### Given / When
    names = raw_names_in_mqpar("<filePaths></filePaths>")

    ### Then
    assert names == set()


# --------------------------------------------------------------------------- janitor rules

def _entry(kind: str, *, age_s: float, **kw) -> Entry:
    """One scanned thing, aged relative to NOW so the tests read in hours rather than timestamps."""
    return Entry(path=f"/data/{kw.get('job_id') or kind}", kind=kind, mtime=NOW - age_s,
                 size=kw.pop("size", 1024), **kw)


def _plan(entries, **kw) -> list[Candidate]:
    return plan_cleanup(
        entries,
        now=NOW,
        running_job_ids=kw.pop("running_job_ids", frozenset()),
        busy_accessions=kw.pop("busy_accessions", frozenset()),
        ttl_by_kind=kw.pop("ttl_by_kind", TTLS),
    )


def test_scratch_older_than_its_window_is_swept():
    """Scenario: MaxQuant intermediates left on the raw volume after a search ended.

    Slice: janitor — the ordinary case.
    Given a scratch entry last written well beyond its retention window,
    When the cleanup is planned,
    Then it is a candidate and the reason names the kind and the age.
    """
    ### Given
    entries = [_entry("maxquant-scratch", age_s=12 * HOUR, accession="PXD046787")]

    ### When
    plan = _plan(entries)

    ### Then
    assert [c.kind for c in plan] == ["maxquant-scratch"]
    assert "maxquant-scratch" in plan[0].reason


def test_anything_inside_its_window_is_left_alone():
    """Scenario: a sweep runs while a search is still writing scratch.

    Slice: janitor — the age guard.
    Given a scratch entry written an hour ago, inside its six-hour window,
    When the cleanup is planned,
    Then nothing is swept.
    """
    ### Given
    entries = [_entry("maxquant-scratch", age_s=1 * HOUR, accession="PXD046787")]

    ### When
    plan = _plan(entries)

    ### Then
    assert plan == []


def test_a_running_jobs_directory_is_never_swept_however_old():
    """Scenario: a long search whose job directory has not been touched for longer than the window.

    Slice: janitor — the running-job guard.
    Given a job directory far beyond its retention window whose call is still running,
    When the cleanup is planned,
    Then it is not a candidate.

    Deleting it would not stop the job. It would remove the run.log and stage events the UI reads,
    so a search that keeps billing becomes a search nobody can see.
    """
    ### Given
    entries = [_entry("job-dir", age_s=30 * 86400, job_id="fc-live", is_dir=True)]

    ### When
    plan = _plan(entries, running_job_ids=frozenset({"fc-live"}))

    ### Then
    assert plan == []


def test_a_busy_accession_is_never_swept_however_old():
    """Scenario: a dataset with a download in flight, or a running search reading its raw files.

    Slice: janitor — the busy-accession guard.
    Given an old scratch entry for an accession the caller reports as busy,
    When the cleanup is planned,
    Then it is not a candidate, independently of the running-job guard.
    """
    ### Given
    entries = [_entry("maxquant-scratch", age_s=30 * 86400, accession="PXD046787")]

    ### When
    plan = _plan(entries, busy_accessions=frozenset({"PXD046787"}))

    ### Then
    assert plan == []


def test_a_kind_with_no_retention_window_is_never_swept():
    """Scenario: a new entry kind reaches the planner before anyone decided how long to keep it.

    Slice: janitor — fail-closed default.
    Given an entry of a kind absent from the TTL table,
    When the cleanup is planned,
    Then it is not a candidate.

    This is what keeps raw .raw files safe here: prune_raw_cache owns their eviction, so "raw-cache"
    is deliberately absent from the table and must never be swept by this planner as well.
    """
    ### Given
    entries = [_entry("raw-cache", age_s=365 * 86400, accession="PXD046787")]

    ### When
    plan = _plan(entries)

    ### Then
    assert plan == []


def test_a_stale_lock_is_swept_even_though_it_names_an_accession():
    """Scenario: a download container died and left its lock behind.

    Slice: janitor — stale locks.
    Given a lock older than the download timeout, recorded with no accession,
    When the cleanup is planned,
    Then it is swept — a lock guarded by its own accession could never be removed.
    """
    ### Given
    entries = [_entry("stale-lock", age_s=4 * HOUR, size=0)]

    ### When
    plan = _plan(entries, busy_accessions=frozenset({"PXD046787"}))

    ### Then
    assert [c.kind for c in plan] == ["stale-lock"]


def test_the_summary_totals_per_kind_in_whole_gigabytes():
    """Scenario: a sweep is reported to whoever is deciding whether it reclaimed anything.

    Slice: janitor — the report.
    Given two scratch candidates of 1 GiB each and one job directory of 2 GiB,
    When the candidates are summarised,
    Then the counts and sizes are grouped per kind and totalled.
    """
    ### Given
    gib = 1024 ** 3
    candidates = [
        Candidate(path="/a", kind="maxquant-scratch", reason="", size=gib, is_dir=False),
        Candidate(path="/b", kind="maxquant-scratch", reason="", size=gib, is_dir=True),
        Candidate(path="/c", kind="job-dir", reason="", size=2 * gib, is_dir=True),
    ]

    ### When
    report = summarise(candidates)

    ### Then
    assert report["total_count"] == 3
    assert report["total_gb"] == 4.0
    assert report["by_kind"]["maxquant-scratch"] == {"count": 2, "bytes": 2 * gib}
    assert report["by_kind"]["job-dir"] == {"count": 1, "bytes": 2 * gib}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
