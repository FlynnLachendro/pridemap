"""
Modal serverless runner for MaxQuant.

Deploy:  modal deploy api/modal_runner.py
Verify:  curl $MQ_SERVER_URL/health

Contract (consumed by api/omics.py lines 2276-2487):
  GET  /health                → {"ok": true}
  POST /run                   → {"job_id": "..."}   multipart field: kit (zip)
  GET  /status/{job_id}       → {"status": "running"|"done"|"error", "log": "...", "progress": [...],
                                 "cpu": {...}|null}
                                 progress carries run_all.sh's own stage events, which is what lets
                                 the caller show stages, elapsed time and ETA for a remote job.
                                 cpu is the search container's own CPU/memory measurement (see
                                 CpuSampler) — null until the search phase starts writing it.
  GET  /results/{job_id}      → proteinGroups.txt (TSV)
  DELETE /job/{job_id}        → {"deleted": bool, "cancelled": bool, "problems": [...]}
  GET  /jobs/{accession}      → {"job_id": "..."|null}   (Phase 2: durable lookup)
  GET  /jobs                  → {"jobs": [{"accession", "job_id", "status"}, ...]}
"""

# /// script
# requires-python = ">=3.11"
# dependencies = ["modal>=0.73"]
# ///

import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Mapping

import modal
import modal.exception
from modal import Secret, Volume

# ----------------------------------------------------------------------------- module-level objects (all four required)

app     = modal.App("pridemap-maxquant")
vol     = Volume.from_name("pridemap-runs", create_if_missing=True)
raw_vol = Volume.from_name("pridemap-raw",  create_if_missing=True)
jobs    = modal.Dict.from_name("pridemap-jobs", create_if_missing=True)
secret  = Secret.from_name("pridemap-mq-token")   # provides MQ_TOKEN

# Where job directories live. In a Modal container this is the Volume mount point; off Modal
# (a local run, a test, a plain host serving the same gateway) nothing is mounted at /data and it
# may not even be writable, so the root is overridable. One owner, so the worker that writes a job
# directory and the gateway that reads it can never diverge.
VOLUME_MOUNT   = "/data"       # where both @app.function decorators mount `vol` (job results)
RAW_CACHE_MOUNT = "/raw-cache" # where run_maxquant mounts `raw_vol` (cached raw files, cross-job)
DATA_ROOT = pathlib.Path(os.environ.get("PRIDEMAP_DATA_ROOT", VOLUME_MOUNT))

# Single source of truth for container sizing. Change one number here to resize both the Modal
# decorator and the log lines that describe the phase; the runtime also patches mqpar.xml's
# numThreads to os.cpu_count() so the kit's static value is never wrong by accident.
#
# Why 8 and not 16. Measured on a 2-file run (Modal call fc-01M58HFAWP9KNBCEM528M7HQPQ, 16 CPU):
# feature detection 893 s + deisotoping 596 s + MS/MS preparation 190 s = 1679 s of a 1826 s run,
# i.e. 92% of wall clock. Those three phases parallelise ONE THREAD PER RAW FILE, not across
# numThreads — proof: the two files' feature detection finished at 619 s and 893 s (ratio 1.44)
# against raw sizes of 1.607 GB and 2.421 GB (ratio 1.51), so they ran concurrently, each bound by
# its own file. Fourteen of sixteen cores were idle for the dominant phase. The phases that DO fan
# out (creating protein files, digesting proteins) measured 26 s and 22 s at 8 workers, which is
# noise against 1679 s. So the second eight cores buy nothing on a small run and cost double.
#
# This number is not the lever on wall clock. With n raw files the floor is the LARGEST SINGLE FILE
# on ONE thread, whatever the core count. The lever is keeping MaxQuant's scratch off the network
# Volume — see _stage_raw_local — and, for bigger runs, more raw files to spread over more cores.
# cpu_report() exists so the next run measures what it actually used rather than being guessed at.
#: Modal's accepted ephemeral_disk range in MiB, enforced at deploy time. Named here because two
#: separate attempts to derive the value from measured usage were rejected by the platform, and the
#: bound is the reason — not the measurement.
MODAL_DISK_MIN_MB = 524288    # 512 GiB
MODAL_DISK_MAX_MB = 3145728   # 3 TiB

MAXQUANT_SEARCH_CPU    = 8       # measured peak busy: 10.09 cores (bursting on host beyond reservation is normal); keep 8
MAXQUANT_SEARCH_MEMORY = 20480   # MB (20 GB); measured peak RSS: 13.4 GB; 1.5× headroom → 20 GB  (was 64 GB)
# Set explicitly rather than inherited. MaxQuant's scratch lands on container-local disk.
#
# This is NOT right-sized from telemetry and cannot be: Modal enforces 512 GiB as the LOWER bound
# for ephemeral_disk, so the measured peak (~15 GB on a two-run job) is irrelevant to the value.
# Deriving it from the measurement produced 40960 and then 20480 MiB and both were rejected at
# deploy time. Raise it only for a dataset whose raw set plus intermediates exceeds 512 GiB.
MAXQUANT_SEARCH_DISK   = MODAL_DISK_MIN_MB
MAXQUANT_DOWNLOAD_CPU    = 1     # measured peak: 0.22 busy cores (network-bound); 1.25× headroom → 1
MAXQUANT_DOWNLOAD_MEMORY = 1024  # MB (1 GB); measured peak: 0.1 GB RSS; 1.5× headroom → 1 GB


def _vol_sync(action: str, v: "Volume | None" = None) -> None:
    """Best-effort Volume commit/reload for vol (default) or any other Volume.

    Reads only. A reload that fails costs nothing — the next one will pick the data up — so it stays
    silent. For WRITES use _vol_commit: a commit that fails silently is how durable state stops
    being durable without anyone finding out.
    """
    try:
        getattr(v or vol, action)()
    except Exception:
        pass


def _vol_commit(v: "Volume | None" = None, *, what: str = "volume") -> bool:
    """Commit a Volume, reporting failure. Returns True when the data is durable.

    Separated from _vol_sync because resume depends on this specific call succeeding. The stage
    markers run_all.sh writes to .pipeline/ become durable only when a commit lands; if one fails,
    the next container re-runs every stage from scratch and nothing anywhere says why. "Best effort"
    is the right policy for a reload and the wrong one for the write that a guarantee rests on.
    """
    try:
        (v or vol).commit()
        return True
    except Exception as exc:
        # Reported unconditionally. An earlier version gated this on a MODAL_TASK_ID env var to stay
        # quiet off Modal — an env var that was never verified, which would have re-hidden every
        # commit failure behind a guess. _vol_commit only runs inside @app.function bodies, so
        # there is no off-Modal caller to stay quiet for.
        print(f"WARNING: {what} commit failed — resume may re-run stages: {exc}", flush=True)
        return False


# ----------------------------------------------------------------------------- CPU / memory telemetry
#
# "Is MaxQuant using the threads we passed in?" was unanswerable: numThreads is patched to the core
# count and logged, which proves what we ASKED for and nothing about what ran. These three pure
# functions turn two cgroup samples into that answer, and cpu_report() is what the UI draws.
#
# cgroup, not /proc/stat: inside a Modal container /proc/stat is the HOST's, so a busy neighbour
# reads as our own work. The cgroup counters are scoped to this container, which is also exactly
# what Modal bills. Where there is no cgroup (macOS, a plain test host) every reader returns None
# and the report is omitted rather than invented.
#
# Modal containers are cgroup **v1**, established by probing one rather than by assuming:
#   /sys/fs/cgroup/cpuacct/cpuacct.usage        910000000        (nanoseconds)
#   /sys/fs/cgroup/memory/memory.usage_in_bytes  55934976
#   /sys/fs/cgroup/cpu.stat                     FileNotFoundError
# The first version of this read only the v2 paths, so a live container reported its core count and
# no usage at all — the readout looked present and measured nothing. v1 is therefore tried first,
# with v2 kept second so a future Modal migration does not silently blind this again.

#: One report file per phase. A single cpu.json meant whichever container wrote last won, so the
#: download worker's figures erased the search worker's and neither could be attributed. Each pillar
#: is declared per function, so each function reports separately.
CPU_REPORT_FILE = "cpu.json"            # legacy single-phase name, still read for in-flight jobs
CPU_REPORT_PHASES = ("search", "download")


def cpu_report_file(phase: str) -> str:
    return f"cpu-{phase}.json"
CGROUP_V1_CPUACCT = "/sys/fs/cgroup/cpuacct/cpuacct.usage"        # nanoseconds
CGROUP_V2_CPU_STAT = "/sys/fs/cgroup/cpu.stat"                    # usage_usec key
CGROUP_MEM_FILES = (
    "/sys/fs/cgroup/memory/memory.usage_in_bytes",                # v1
    "/sys/fs/cgroup/memory.current",                              # v2
)


def parse_cgroup_usage_usec(text: str) -> int | None:
    """Cumulative CPU microseconds from a cgroup v2 cpu.stat body, or None if absent."""
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def parse_cpuacct_usage_usec(text: str) -> int | None:
    """Cumulative CPU microseconds from a cgroup v1 cpuacct.usage body.

    The file holds NANOseconds as a bare integer. Converting here rather than at the call site keeps
    every caller working in one unit — mixing the two silently reports a thousand times too much CPU.
    """
    try:
        return int(text.strip()) // 1000
    except (ValueError, AttributeError):
        return None


def busy_cores(prev_usec: int, cur_usec: int, elapsed_s: float) -> float | None:
    """Cores busy on average between two cpu.stat samples taken elapsed_s apart.

    One core pinned for the whole interval is 1.0. Returns None when the interval is degenerate or
    the counter went backwards (a container restart), because a wrong number here would be read as
    a measurement.
    """
    if elapsed_s <= 0 or cur_usec < prev_usec:
        return None
    return (cur_usec - prev_usec) / (elapsed_s * 1_000_000)


def cpu_report(
    *,
    cores: int,
    allocated: int,
    busy: float | None,
    rss_bytes: int | None,
    memory_mb: int,
    peak_busy: float | None = None,
    peak_rss_bytes: int | None = None,
    phase: str = "",
    disk_used_bytes: int | None = None,
    disk_total_bytes: int | None = None,
    peak_disk_used_bytes: int | None = None,
) -> dict:
    """The /status payload describing one container's CPU, memory and disk use.

    Rounded here rather than in the UI so the card and the log agree on the figures, and so the
    numbers that get pasted into a sizing decision are the numbers that were measured.

    `phase` names which container this came from, because every resource pillar is sized per
    function and a figure with no owner cannot size anything: 8.7 GB peak means "lower the search
    worker's 64 GB" or "the 4 GB download worker is about to OOM" depending entirely on who
    reported it.
    """
    # `cores` is the RESERVATION, because that is what is paid for and what busy_pct should be read
    # against. `host_cores` is what os.cpu_count() saw, kept because the two differ (a cpu=8
    # container reported 24) and a percentage quoted against the wrong one is worse than none.
    report: dict = {
        "cores": allocated,
        "host_cores": cores,
        "allocated": allocated,
        "memory_gb": round(memory_mb / 1024, 1),
        "ts": time.time(),
    }
    if phase:
        report["phase"] = phase
    if busy is not None:
        report["busy_cores"] = round(busy, 2)
        report["busy_pct"] = round(100 * busy / allocated) if allocated else None
    if peak_busy is not None:
        report["peak_busy_cores"] = round(peak_busy, 2)
    if rss_bytes is not None:
        report["rss_gb"] = round(rss_bytes / 1024**3, 1)
    if peak_rss_bytes is not None:
        report["peak_rss_gb"] = round(peak_rss_bytes / 1024**3, 1)
    # Disk is the pillar that was sized by guess: ephemeral_disk was set to 512 GiB because that is
    # Modal's default, with no measurement of what a run actually needs. Staging raw files locally
    # made it load-bearing rather than incidental, so it gets measured like the others.
    if disk_used_bytes is not None:
        report["disk_used_gb"] = round(disk_used_bytes / 1024**3, 1)
    if peak_disk_used_bytes is not None:
        report["peak_disk_used_gb"] = round(peak_disk_used_bytes / 1024**3, 1)
    if disk_total_bytes is not None:
        report["disk_total_gb"] = round(disk_total_bytes / 1024**3, 1)
    return report


def _read_int_file(path: str) -> int | None:
    try:
        return int(pathlib.Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def _cgroup_usage_usec() -> int | None:
    """This container's cumulative CPU time in microseconds, from whichever cgroup version exists.

    v1 first because that is what Modal containers actually present; v2 second so the readout keeps
    working if that changes. Both absent means "cannot measure", which the report states rather
    than papering over with a zero.
    """
    for path, parse in (
        (CGROUP_V1_CPUACCT, parse_cpuacct_usage_usec),
        (CGROUP_V2_CPU_STAT, parse_cgroup_usage_usec),
    ):
        try:
            usage = parse(pathlib.Path(path).read_text())
        except OSError:
            continue
        if usage is not None:
            return usage
    return None


def _cgroup_memory_bytes() -> int | None:
    """This container's current memory use, from whichever cgroup version exists."""
    for path in CGROUP_MEM_FILES:
        value = _read_int_file(path)
        if value is not None:
            return value
    return None


def _disk_usage(path: "pathlib.Path | None") -> "tuple[int | None, int | None]":
    """(used, total) bytes on the filesystem holding `path`, or (None, None).

    Measures the EPHEMERAL disk, which is why the caller passes its own work dir rather than "/":
    the raw staging copy and every MaxQuant intermediate land there, and that is the figure
    ephemeral_disk has to cover.
    """
    if path is None:
        return None, None
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None, None
    return usage.used, usage.total


class CpuSampler:
    """Turns repeated polls into a cpu_report dict. Holds the previous sample; that is all its state.

    Separate from the shipper thread that drives it so the sampling interval is whatever the caller
    already ticks on, and so a failure to read a cgroup file degrades to "no figures this tick"
    rather than killing the thread that also ships stage events.

    One of these per function, each labelled with its `phase`. Every resource pillar — cpu, memory,
    ephemeral_disk, timeout — is declared per function, so sizing any of them needs figures
    attributed to the function that produced them. Before this, only the search worker reported
    anything, which is why three of the four containers were still sized by assumption.
    """

    def __init__(
        self, *, allocated: int, memory_mb: int, phase: str = "",
        disk_path: "pathlib.Path | None" = None,
    ) -> None:
        self.allocated = allocated
        self.memory_mb = memory_mb
        self.phase = phase
        self.disk_path = disk_path
        self.cores = os.cpu_count() or allocated
        self._prev_usec = _cgroup_usage_usec()
        self._prev_at = time.monotonic()
        self._peak_busy: float | None = None
        self._peak_rss: int | None = None
        self._peak_disk: int | None = None

    def sample(self) -> dict:
        cur_usec, cur_at = _cgroup_usage_usec(), time.monotonic()
        busy = None
        if cur_usec is not None and self._prev_usec is not None:
            busy = busy_cores(self._prev_usec, cur_usec, cur_at - self._prev_at)
        if cur_usec is not None:
            self._prev_usec, self._prev_at = cur_usec, cur_at
        if busy is not None:
            self._peak_busy = busy if self._peak_busy is None else max(self._peak_busy, busy)

        rss = _cgroup_memory_bytes()
        if rss is not None:
            self._peak_rss = rss if self._peak_rss is None else max(self._peak_rss, rss)

        disk_used, disk_total = _disk_usage(self.disk_path)
        if disk_used is not None:
            self._peak_disk = disk_used if self._peak_disk is None else max(self._peak_disk, disk_used)

        return cpu_report(
            cores=self.cores,
            allocated=self.allocated,
            busy=busy,
            rss_bytes=rss,
            memory_mb=self.memory_mb,
            peak_busy=self._peak_busy,
            peak_rss_bytes=self._peak_rss,
            phase=self.phase,
            disk_used_bytes=disk_used,
            disk_total_bytes=disk_total,
            peak_disk_used_bytes=self._peak_disk,
        )


# ----------------------------------------------------------------------------- result cache
#
# Two deliberate choices, both reactions to how this went wrong before.
#
# ARTIFACT-GATED, NOT MARKER-GATED. Persistent stage markers were tried and reverted — see the long
# comment in _run_maxquant_search. A `<stage>.done` file on the volume claimed "this work is
# finished" while the work's PRODUCTS lived in an ephemeral work dir that was empty on the next run,
# so the second run skipped stages and handed MaxQuant paths to files nothing had created. A marker
# that can outlive its artifact is a lie the pipeline cannot detect. Here an entry exists if and
# only if its table is readable: there is no separate completion flag that can disagree with disk.
#
# KEYED ON CONTENT, NOT ON THE KIT. Hashing the kit zip or mqpar.xml verbatim would never hit:
# <filePaths> carries the per-container kit directory and <numThreads> is rewritten at container
# start, so both differ on every run while describing identical science. The key is the raw inputs
# plus the parameters that actually change the result.

SEARCH_CACHE_SUBDIR = "cache/search"
#: mqpar fields that differ between runs WITHOUT changing the science: absolute paths, and the
#: thread count we patch per container. Everything else in the file is a search parameter.
MQPAR_VOLATILE_TAGS = ("filePaths", "fixedCombinedFolder", "fixedSearchFolder", "numThreads")
#: Tags holding a PATH whose basename still identifies the input. Blanking these would be wrong —
#: searching a different proteome is different science — but keeping the absolute path would be too:
#: it contains the per-container kit directory, so the key would never match between runs. A test
#: caught this: the first version left fastaFilePath alone and every single run was a cache miss,
#: which is the failure mode where a cache quietly does nothing at all.
MQPAR_PATH_IDENTITY_TAGS = ("fastaFilePath",)
#: The table whose presence defines a cache entry as valid.
CACHE_RESULT_FILE = "proteinGroups.txt"


def normalise_mqpar(text: str) -> str:
    """mqpar.xml reduced to the fields that change the result, for hashing.

    Volatile tags are blanked rather than removed, so a parameter file that stops specifying one is
    not hashed identically to one that specifies it differently. Path-identity tags keep their
    basename, which is the part that names the input rather than the container that held it.
    """
    import re as _re
    out = text
    for tag in MQPAR_VOLATILE_TAGS:
        out = _re.sub(rf"<{tag}>.*?</{tag}>", f"<{tag}/>", out, flags=_re.DOTALL | _re.IGNORECASE)
        out = _re.sub(rf"<{tag}\s*/>", f"<{tag}/>", out, flags=_re.IGNORECASE)
    for tag in MQPAR_PATH_IDENTITY_TAGS:
        out = _re.sub(
            rf"<{tag}>([^<]*)</{tag}>",
            lambda m, _t=tag: f"<{_t}>{pathlib.PurePath(m.group(1).strip()).name}</{_t}>",
            out,
            flags=_re.IGNORECASE,
        )
    return _re.sub(r"\s+", " ", out).strip()


def search_cache_key(raw_ids: "list[tuple[str, str]]", mqpar_text: str) -> str:
    """The cache key for one search: its raw inputs plus its search parameters.

    `raw_ids` is (filename, content hash) per raw file. Content rather than name+size, because the
    key asserts the science is identical and a re-downloaded or repaired file can carry the same
    name and length. Hashing costs about 2 s for 3.8 GB at the throughput staging measures, against
    a search measured in tens of minutes.

    Keyed on the WHOLE SET, never per file. MaxQuant's LFQ and iBAQ normalisation is global across
    the files in a run, so a result computed over a different set is not interchangeable even for
    the files in common. Caching per file and recombining would be a faster wrong answer.
    """
    h = hashlib.sha256()
    for name, digest in sorted(raw_ids):
        h.update(name.encode())
        h.update(b"\0")
        h.update(digest.encode())
        h.update(b"\0")
    h.update(b"mqpar\0")
    h.update(normalise_mqpar(mqpar_text).encode())
    return h.hexdigest()[:32]


def sha256_file(path: pathlib.Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def search_cache_dir(key: str) -> pathlib.Path:
    return DATA_ROOT / SEARCH_CACHE_SUBDIR / key


def cached_results(key: str) -> "pathlib.Path | None":
    """The cached table directory for this key, or None when there is no usable entry."""
    txt = search_cache_dir(key) / "txt"
    return txt if (txt / CACHE_RESULT_FILE).is_file() else None


def store_results(key: str, txt_dir: pathlib.Path, manifest: dict) -> bool:
    """Publish one search's tables into the cache. True when the entry is readable afterwards.

    Staged through a temporary directory and moved into place, so a container that dies mid-copy
    cannot leave a partial entry that the next run would treat as a hit.
    """
    dest = search_cache_dir(key)
    if (dest / "txt" / CACHE_RESULT_FILE).is_file():
        return True   # already published, by this run or a concurrent one
    staging = dest.parent / f".incoming-{key}-{os.getpid()}"
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        shutil.copytree(txt_dir, staging / "txt")
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=1))
        os.replace(staging, dest)
        return True
    except OSError as exc:
        print(f"WARNING: could not cache results for {key}: {exc}", flush=True)
        shutil.rmtree(staging, ignore_errors=True)
        return False


# ----------------------------------------------------------------------------- raw staging (the real speed lever)

def raw_names_in_mqpar(mqpar_text: str) -> "set[str]":
    """The raw file names a kit's mqpar.xml actually searches.

    Needed because the raw cache is per ACCESSION while a run is per SELECTION: the cache directory
    for PXD046787 holds every file ever downloaded for it, so staging the directory wholesale copies
    57.6 GB for a two-file search. Reading the names from <filePaths> stages only what this run
    opens. An empty set means "could not tell", which the caller treats as "take everything" rather
    than as "take nothing" — searching fewer files than asked for would be a wrong result, where
    copying too many is only wasteful.
    """
    import re as _re
    return {
        pathlib.PurePath(p).name
        for p in _re.findall(r"<string>([^<]*\.raw)</string>", mqpar_text, _re.IGNORECASE)
    }


def _stage_raw_local(
    accession: str, dest_root: pathlib.Path, log: Callable[[str], None],
    wanted: "set[str] | None" = None,
) -> pathlib.Path | None:
    """Copy this accession's cached raw files from raw_vol onto container-local disk.

    THE reason the search was slow. MaxQuant writes its per-file intermediates NEXT TO each raw
    file — `<name>.index` and a `<name>/` directory holding n0/p0 — and the kit's download_raw.sh
    symlinks raw/<name> at $PRIDEMAP_RAW_DIR, which was the raw_vol mount. So feature detection and
    deisotoping, the two write-heavy phases and 81% of the run, were doing random writes across a
    network filesystem that Modal documents as "optimized for write-once, read-many", where a write
    inside a file can cost a rewrite of the whole file. `modal volume ls pridemap-raw PXD046787`
    shows the evidence: 2019_12_13_Daria_01.index and 2019_12_13_Daria_01/ sitting beside the .raw.

    Pointing PRIDEMAP_RAW_DIR at the returned directory puts all of that on the container's
    ephemeral SSD. The copy itself is a sequential read of a few GB — seconds to a minute — against
    phases measured in tens of minutes.

    Returns the staging root to use as PRIDEMAP_RAW_DIR, or None to leave the caller on raw_vol:
    a missing cache (nothing downloaded yet, so download_raw.sh must run for real) or a copy that
    fails part way, where continuing on the network volume is slow but correct and aborting the run
    would not be. Symlinks are preserved as symlinks-to-local rather than resolved, because that is
    the exact shape run_all.sh already works with.
    """
    src_dir = pathlib.Path(RAW_CACHE_MOUNT) / accession
    if not src_dir.is_dir():
        log(f"raw staging: {src_dir} not present — leaving PRIDEMAP_RAW_DIR on {RAW_CACHE_MOUNT}")
        return None

    raws = sorted(p for p in src_dir.glob("*.raw") if p.is_file())
    if wanted:
        selected = [p for p in raws if p.name in wanted]
        if selected:
            skipped = len(raws) - len(selected)
            if skipped:
                log(f"raw staging: {len(selected)} of {len(raws)} cached file(s) are in this run's "
                    f"selection — skipping {skipped} not searched here")
            raws = selected
        else:
            # The names did not line up with the cache. Copying everything is slower but cannot
            # make the search wrong, which is the right way round to be wrong here.
            log(f"raw staging: none of {sorted(wanted)} matched the cache — staging all "
                f"{len(raws)} cached file(s)")
    if not raws:
        log(f"raw staging: no .raw files in {src_dir} — leaving PRIDEMAP_RAW_DIR on {RAW_CACHE_MOUNT}")
        return None

    dest_dir = dest_root / accession
    dest_dir.mkdir(parents=True, exist_ok=True)
    total = sum(p.stat().st_size for p in raws)
    log(f"raw staging: copying {len(raws)} file(s), {total / 1024**3:.1f} GB → {dest_dir} (local SSD)")
    started = time.monotonic()
    try:
        for p in raws:
            target = dest_dir / p.name
            if target.exists() and target.stat().st_size == p.stat().st_size:
                continue   # a retry in the same container; the bytes are already here
            shutil.copyfile(p, target)
    except OSError as exc:
        # Not fatal. The volume path still works, so say what was lost and carry on.
        log(f"raw staging FAILED ({exc}) — falling back to {RAW_CACHE_MOUNT}; "
            f"MaxQuant scratch will go to the network volume and the search will be slower")
        return None

    secs = time.monotonic() - started
    rate = total / 1024**2 / secs if secs > 0 else 0
    log(f"raw staging: done in {secs:.0f} s ({rate:.0f} MB/s) — "
        f"MaxQuant intermediates will be written to local disk, not the volume")
    return dest_root


# ----------------------------------------------------------------------------- container images

# Lightweight image for the ASGI web gateway.
#
# Versions are pinned: an unpinned pip_install resolves at BUILD time, so the image's behaviour can
# change with no commit. That is how the missing-multipart bug became a hard failure — a Starlette
# release turned absent form support into an AssertionError inside request.form().
#
# python-multipart is required by Starlette's request.form() and is an implicit dependency of a
# feature, not of any import in this repo, so no import scan can find it missing.
#
# The build assertion checks the *capability* rather than a package name: Starlette sets
# parse_options_header to None when multipart support is unavailable, and that is the exact symbol
# whose None-ness produced the 500. Asserting `import multipart` instead would track a deprecated
# alias and can pass while form parsing is broken (or fail while it works).
web_image = (
    modal.Image.debian_slim()
    .pip_install("fastapi==0.143.5", "uvicorn==0.54.0", "python-multipart==0.0.32")
    .run_commands(
        'python -c "from starlette.requests import parse_options_header as p; '
        'assert p is not None, \'form parsing unavailable: python-multipart missing\'"'
    )
    .add_local_python_source("modal_web")   # the gateway module, imported inside web() only
)

# Heavy MaxQuant image for the compute worker.
#
# Built from docker/maxquant/Dockerfile (a multi-stage build) so Modal constructs the image
# at deploy time without a separate registry push.  The Dockerfile replicates what
# preflight.sh does locally: copies MaxQuant assemblies from the biocontainers image onto
# the .NET SDK base (mcr.microsoft.com/dotnet/sdk:3.1-bullseye), giving exact parity with
# the local macOS Docker setup.
#
# Why the SDK base is required: MaxQuant's DotNetCore31Installed() checks `dotnet --info`
# for a line starting with "3.1" (an installed SDK).  The biocontainers image has .NET
# runtime only; no such line exists and the run fails immediately with
# "Please install .NET Core 3.1 SDK" — before mqpar.xml is even read.
# The SDK image outputs "3.1.426 [/usr/share/dotnet/sdk]" which satisfies the check.
#
# The SDK image (Debian Bullseye) ships curl and ca-certificates, so the static-curl
# fetch and CA-bundle workarounds the old image required are not needed here.
_DOCKERFILE = pathlib.Path(__file__).parent.parent / "docker" / "maxquant" / "Dockerfile"

# Modal's post-build Python probe reports only "unable to determine the version of Python
# installed in the Image" — it names neither of the two binaries it looks for and runs after the
# build, so its output carries no diagnostics. Seven deploys were spent guessing which was
# missing. This asserts the contract *inside* the build, where stdout reaches the deploy log,
# and dumps the environment once so a later image fault has a baseline to diff against rather
# than needing another deploy to inspect.
#
# It must stay ONE logical line.  run_commands() emits each string as a `RUN` instruction into a
# Dockerfile that Modal parses itself, and that parser rejects an embedded newline: a `for ... done`
# written across lines failed the whole build with `could not parse Dockerfile: expected
# any_breakable` pointing at `done`.  Hence the `;`-joined fragments below, and `echo` rather than
# `printf` — a literal `\n` in a RUN line is at the mercy of the Dockerfile escape handling.
_IMAGE_PROBE = " ".join(
    [
        "set -eu;",
        'echo "=== PATH ==="; echo "$PATH";',
        'echo "=== python/pip resolution (Modal needs python and pip, not the 3-suffixed names) ===";',
        'for b in python python3 pip pip3; do echo "$b -> $(command -v "$b" || echo MISSING)"; done;',
        'echo "=== versions ==="; python --version 2>&1 || true; pip --version 2>&1 || true;',
        'echo "=== dotnet SDKs (DotNetCore31Installed() needs a line starting 3.1) ===";',
        "dotnet --list-sdks 2>&1 || true;",
        'echo "=== /opt/maxquant ==="; ls /opt/maxquant 2>&1 | head -20 || true;',
        'echo "=== assert Modal image contract ===";',
        "missing='';",
        'command -v python >/dev/null 2>&1 || missing="$missing python";',
        'command -v pip >/dev/null 2>&1 || missing="$missing pip";',
        'if [ -n "$missing" ]; then',
        'echo "FATAL: not on PATH:$missing" >&2;',
        'echo "Modal requires both python and pip on PATH. This base ships python3 only;" >&2;',
        'echo "add_python= on from_dockerfile is what supplies them." >&2;',
        "exit 1; fi;",
        'echo "OK: python and pip both resolve"',
    ]
)

image = (
    # add_python installs a reproducible standalone Python build. The alternative — apt plus the
    # frozen get-pip.py for 3.9 — depends on Debian 11's debian-security, which has already
    # dropped python3-pip (404) once during this work.
    modal.Image.from_dockerfile(_DOCKERFILE, add_python="3.12")
    .run_commands(_IMAGE_PROBE)
    .run_commands(
        # Prove TLS actually works against the two hosts this pipeline fetches from, at
        # BUILD time. A range request keeps it to a few bytes; -f makes an HTTP error a failure.
        "curl -fsS -r 0-99 -o /dev/null "
        "https://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD046787/2019_12_13_Daria_01.raw",
        "curl -fsS -o /dev/null https://rest.uniprot.org/uniprotkb/P01308.fasta",
    )
    .run_commands("which maxquant")   # fail at BUILD time if binary is missing
)

# ----------------------------------------------------------------------------- worker

# ----------------------------------------------------------------------------- download worker (cheap)

@app.function(
    image=image,
    volumes={VOLUME_MOUNT: vol, RAW_CACHE_MOUNT: raw_vol},
    secrets=[secret],
    cpu=MAXQUANT_DOWNLOAD_CPU,
    memory=MAXQUANT_DOWNLOAD_MEMORY,
    timeout=7200,   # 2 h — enough for any current public PRIDE dataset
)
def _download_raw(kit_bytes: bytes, accession: str, job_id: str) -> None:
    """Download raw files for one accession into raw_vol. Cheap container (~10× less than MaxQuant).

    Runs only download_raw.sh. raw_vol.reload() at start means an already-cached accession
    finishes in seconds — have() returns true for each file and curl is never invoked.
    raw_vol is committed every 15 s so an interrupted container leaves a partial file that
    the next run resumes via curl -C - rather than restarting.
    """
    job_dir = DATA_ROOT / job_id
    log_path = job_dir / "run.log"

    def _log(msg: str) -> None:
        print(msg, flush=True)
        try:
            with log_path.open("a") as f:
                f.write(msg + "\n")
        except OSError:
            pass   # job_dir may not be visible before the first reload

    _vol_sync("reload")        # see the job_dir the orchestrator created and committed
    _vol_sync("reload", raw_vol)
    _log(f"raw cache: {RAW_CACHE_MOUNT} (reload attempted — failures are swallowed off Modal)")
    _log(f"container CPUs: {MAXQUANT_DOWNLOAD_CPU} reserved, {os.cpu_count()} visible on the host")

    # This phase's own resource figures. Downloading is I/O bound, so 2 CPU and 4 GB were a guess
    # that nothing had ever checked — and the guess is per function, so the search worker's numbers
    # say nothing about it. Reported under its own phase name so the two cannot be confused.
    dl_sampler = CpuSampler(
        allocated=MAXQUANT_DOWNLOAD_CPU, memory_mb=MAXQUANT_DOWNLOAD_MEMORY, phase="download",
    )
    dl_cpu_path = job_dir / cpu_report_file("download")

    def _ship_dl_cpu() -> dict:
        report = dl_sampler.sample()
        try:
            dl_cpu_path.write_text(json.dumps(report))
        except OSError:
            pass   # diagnostic only; must never break the download
        return report

    # Concurrent-download guard: two containers downloading the same accession simultaneously
    # both run `curl -C -` against the same partial file on raw_vol, producing a corrupt result.
    # A lock file on raw_vol prevents the second container from starting its own curl run.
    # The lock's mtime is the evidence of liveness — anything older than the download timeout
    # (7200 s) was left by a crashed container and is ignored.
    _LOCK_TIMEOUT = 7200  # matches this function's Modal timeout
    cache_dir = pathlib.Path(RAW_CACHE_MOUNT) / accession
    cache_dir.mkdir(exist_ok=True)
    lock_file = cache_dir / ".downloading"

    def _take_lock() -> bool:
        """Create the lock, or False if someone else holds it. O_EXCL makes this a single step.

        exists()-then-touch() was two steps with a gap: both containers could see no lock and both
        proceed, which is the exact race the lock exists to prevent — two curls resuming the same
        partial file on the same volume, producing a corrupt raw file that fails deep inside
        MaxQuant hours later.
        """
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.close(fd)
        return True

    lock_acquired = _take_lock()
    if not lock_acquired:
        age = time.time() - lock_file.stat().st_mtime
        if age >= _LOCK_TIMEOUT:
            # Left by a container that died. Its mtime is the evidence.
            _log(f"stale lock for {accession} (age {age:.0f} s) — taking it over")
            lock_file.unlink(missing_ok=True)
            lock_acquired = _take_lock()
        else:
            _log(f"another container is downloading {accession} (lock age {age:.0f} s) — waiting")
            for _ in range(72):   # up to 6 min (72 × 5 s)
                time.sleep(5)
                _vol_sync("reload", raw_vol)
                if not lock_file.exists():
                    break
            else:
                # The wait expired with the lock still held. Returning here handed the 64 GB search
                # worker a raw set that may be half-downloaded, and it failed deep inside MaxQuant
                # with nothing pointing back at this moment. Failing here is cheaper and legible.
                raise RuntimeError(
                    f"timed out after 6 min waiting for another container to finish downloading "
                    f"{accession}; refusing to start the search on a possibly-incomplete raw set"
                )
            _log("concurrent download finished — raw_vol reloaded, skipping to search")
            return   # search worker will reload and have() will find the files

    if not lock_acquired:  # pragma: no cover - takeover raced with another container
        raise RuntimeError(f"could not acquire the download lock for {accession}")
    _vol_commit(raw_vol, what="raw cache")   # make the lock visible before any curl runs

    work_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"dl-{job_id}-"))
    with zipfile.ZipFile(io.BytesIO(kit_bytes)) as zf:
        zf.extractall(work_dir)

    dl_script = work_dir / "download_raw.sh"
    if not dl_script.exists():
        raise FileNotFoundError("download_raw.sh not found in kit")
    dl_script.chmod(0o755)

    env = {**os.environ, "PRIDEMAP_RAW_DIR": RAW_CACHE_MOUNT}

    _stop = threading.Event()

    def _ship() -> None:
        while not _stop.wait(15):
            _ship_dl_cpu()
            _vol_commit()
            _vol_commit(raw_vol, what="raw cache")

    t = threading.Thread(target=_ship, daemon=True)
    t.start()
    try:
        proc = subprocess.Popen(
            ["bash", str(dl_script)],
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            _log(line.rstrip())
        proc.wait()
        _vol_commit(raw_vol, what="raw cache")
        _vol_commit()
        if proc.returncode != 0:
            raise RuntimeError(f"download_raw.sh exited {proc.returncode}")
    finally:
        _stop.set()
        if lock_acquired:
            lock_file.unlink(missing_ok=True)   # release lock so concurrent jobs can proceed
        _log(f"resources used (download): {json.dumps(_ship_dl_cpu())}")
        _vol_commit(raw_vol, what="raw cache")
        _vol_commit()


# ----------------------------------------------------------------------------- MaxQuant worker (expensive)

@app.function(
    image=image,
    volumes={VOLUME_MOUNT: vol, RAW_CACHE_MOUNT: raw_vol},
    secrets=[secret],
    cpu=MAXQUANT_SEARCH_CPU,
    memory=MAXQUANT_SEARCH_MEMORY,
    ephemeral_disk=MAXQUANT_SEARCH_DISK,
    timeout=86400,  # 24 h
)
def _run_maxquant_search(
    kit_bytes: bytes, accession: str, job_id: str, force: bool = False,
) -> None:
    """Run MaxQuant. Raw files are already in raw_vol — download_raw.sh's have() skips them.

    The expensive 64 GB container only starts after _download_raw has committed all raw files,
    so it spends zero time waiting for downloads.

    `force` recomputes even when an identical input set is already cached, and still republishes
    the result. Default False: the point of the cache is that a stray click, a reload, or a second
    tab costs a container start rather than another 45-minute search.
    """
    job_dir = DATA_ROOT / job_id
    log_path = job_dir / "run.log"
    progress_path = job_dir / "progress.jsonl"
    cpu_path = job_dir / cpu_report_file("search")

    # Reload before first write so the log lines from _download_raw are visible in the file
    # we append to, and so the raw files committed by _download_raw are on-disk.
    _vol_sync("reload")
    _vol_sync("reload", raw_vol)

    # Then ensure the directory, because the reload does not always bring it. The orchestrator
    # creates and commits job_dir before spawning this function, but it was observed missing here
    # anyway — _log swallows OSError, so the only symptom was the run dying on progress.jsonl with
    # a FileNotFoundError that read as a MaxQuant fault. Creating it is idempotent and removes the
    # dependency on cross-container directory propagation entirely.
    job_dir.mkdir(parents=True, exist_ok=True)

    def _log(msg: str) -> None:
        print(msg, flush=True)
        try:
            with log_path.open("a") as f:
                f.write(msg + "\n")
        except OSError:
            pass

    _log(f"raw cache: {RAW_CACHE_MOUNT} (reloaded — raw files should already be present)")

    # run_all.sh writes its own stage events next to itself in the container-local work dir.
    # Copying that file to the Volume on every sync is what lets /status report stages,
    # elapsed time and ETA while the search runs.
    kit_progress: list[pathlib.Path] = []

    def _ship_stages() -> None:
        for src in kit_progress:
            try:
                (job_dir / "stages.jsonl").write_bytes(src.read_bytes())
            except OSError:
                pass

    # The same 15 s tick that ships stage events also answers "how many cores is MaxQuant actually
    # using", because that question needs two samples an interval apart and this is the interval we
    # already have. A failed write is dropped: the readout is diagnostic, and losing a tick of it
    # must never take down the thread the stage display depends on.
    # disk_path is set after the work dir exists (below); until then the sampler reports cpu/memory
    # only. ephemeral_disk is what the staging copy and MaxQuant's intermediates consume, so the
    # figure has to come from the filesystem holding the work dir rather than from "/".
    sampler = CpuSampler(
        allocated=MAXQUANT_SEARCH_CPU, memory_mb=MAXQUANT_SEARCH_MEMORY, phase="search",
    )

    def _ship_cpu(report: "dict | None" = None) -> dict:
        report = sampler.sample() if report is None else report
        try:
            cpu_path.write_text(json.dumps(report))
        except OSError:
            pass
        return report

    _stop_shipper = threading.Event()

    def _shipper() -> None:
        # raw_vol is NOT committed here any more. It used to be, every 15 s for the whole search —
        # committing a volume MaxQuant was actively writing scratch into, for raw files that cannot
        # change during a search. Pure overhead, and the exact commit contention Modal's volume docs
        # warn against. With _stage_raw_local in place the search container only ever READS raw_vol,
        # so there is nothing of ours to commit.
        while not _stop_shipper.wait(15):
            _ship_stages()
            _ship_cpu()
            _vol_commit()

    shipper = threading.Thread(target=_shipper, daemon=True)
    shipper.start()

    try:
        work_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"mq-{job_id}-"))
        sampler.disk_path = work_dir   # now the ephemeral disk can be measured, not assumed
        kit_progress.append(work_dir / "progress.jsonl")

        with zipfile.ZipFile(io.BytesIO(kit_bytes)) as zf:
            zf.extractall(work_dir)
        _log("kit unpacked")

        # Patch mqpar.xml numThreads to the ALLOCATION, not to os.cpu_count().
        #
        # These are not the same number and assuming they were is a live bug: with cpu=8 a container
        # reported os.cpu_count() == 24, because Modal exposes the host's cores (and allows bursting
        # to request + 16) rather than the reservation. Sizing MaxQuant's thread pool from 24 on an
        # 8-core reservation oversubscribes it 3:1 in exactly the Andromeda phases that DO fan out,
        # which is where the threads were supposed to help. It previously went unnoticed only because
        # cpu=16 happened to land on 16-core hosts, so the two numbers agreed by coincidence.
        #
        # MAXQUANT_SEARCH_CPU is what we reserve and therefore what we are guaranteed to get, so it
        # is what the thread count is sized from. Both numbers are logged because their disagreement
        # is the thing worth seeing.
        import re as _re
        host_cpus = os.cpu_count() or MAXQUANT_SEARCH_CPU
        actual_cpus = MAXQUANT_SEARCH_CPU
        _log(f"container CPUs: {actual_cpus} reserved, {host_cpus} visible on the host "
             f"(numThreads is sized from the reservation)")
        # Also the source of which raw files this run searches — see raw_names_in_mqpar — and of the
        # parameter half of the cache key. Read BEFORE the numThreads patch, so the key is over what
        # the kit asked for; normalise_mqpar blanks numThreads regardless, but hashing the original
        # keeps the key independent of the container that happened to run it.
        wanted_raws: set[str] = set()
        mqpar_text = ""
        mqpar_files = list(work_dir.rglob("mqpar.xml"))
        if mqpar_files:
            mqpar_path = mqpar_files[0]
            mqpar_text = mqpar_path.read_text(errors="replace")
            wanted_raws = raw_names_in_mqpar(mqpar_text)
            m = _re.search(r"<numThreads>(\d+)</numThreads>", mqpar_text)
            old_threads = m.group(1) if m else "not found"
            patched = _re.sub(r"<numThreads>\d+</numThreads>", f"<numThreads>{actual_cpus}</numThreads>", mqpar_text)
            mqpar_path.write_text(patched)
            _log(f"mqpar.xml numThreads: {old_threads} → {actual_cpus} (patched, file: {mqpar_path.relative_to(work_dir)})")
        else:
            _log("mqpar.xml: not found in kit — numThreads not patched")

        run_script = work_dir / "run_all.sh"
        if not run_script.exists():
            raise FileNotFoundError("run_all.sh not found in kit")
        run_script.chmod(0o755)

        # NO stage markers across runs, deliberately. run_all.sh's .pipeline/<id>.done markers stay
        # in this container's work dir and die with it.
        #
        # Backing them with the Volume was tried and reverted. A marker asserts "this stage's work
        # is done", but the work PRODUCTS — raw/ symlinks, fasta/, combined/ — live in this
        # ephemeral work dir, which is new and empty on every run. So a second run skipped download
        # and fasta, then handed MaxQuant paths to files nothing had created:
        #
        #   [2/5] ✓ already done — skipping (delete .pipeline/download.done to redo)
        #   Error creating a RawFile instance from /tmp/mq-.../raw/..._01.raw:
        #   Path does not exist, neither as a file nor as a folder.
        #
        # Persistent markers over ephemeral artifacts is a lie the pipeline cannot detect.
        #
        # The resume that actually matters is content-addressed and already here: download_raw.sh's
        # have() finds each file in raw_vol at full size and skips the curl, so re-running costs
        # seconds rather than tens of GB. Markers would only have saved that have() check and a
        # ~10 s FASTA fetch.
        #
        # Stage the raw files onto local disk FIRST, then point the kit at wherever they ended up.
        # download_raw.sh's have() checks name+size in $PRIDEMAP_RAW_DIR, so a successful stage makes
        # the download stage a no-op against local copies and every MaxQuant intermediate it writes
        # beside them lands on ephemeral SSD. A failed stage returns None and this falls back to the
        # volume mount — the behaviour before this change, slower but correct.
        raw_root = (
            _stage_raw_local(accession, work_dir / "raw-local", _log, wanted_raws)
            or pathlib.Path(RAW_CACHE_MOUNT)
        )

        # Cache lookup, after staging because the key is over the raw file CONTENT and the staged
        # copies are on local disk — hashing them there costs seconds rather than streaming GB back
        # off the network volume. A hit means an identical input set was searched with identical
        # parameters, so re-running would burn ~45 min to produce the same table.
        cache_key = ""
        raw_ids: list[tuple[str, str]] = []
        raw_dir = raw_root / accession
        try:
            raw_files = sorted(p for p in raw_dir.glob("*.raw") if p.is_file())
            if wanted_raws:
                raw_files = [p for p in raw_files if p.name in wanted_raws] or raw_files
            if raw_files and mqpar_files:
                started = time.monotonic()
                raw_ids = [(p.name, sha256_file(p)) for p in raw_files]
                cache_key = search_cache_key(raw_ids, mqpar_text)
                _log(f"cache key {cache_key} over {len(raw_ids)} raw file(s) "
                     f"(hashed in {time.monotonic() - started:.0f} s)")
        except OSError as exc:
            # No key means no caching, which is only slow. Guessing a key would be wrong.
            _log(f"cache key could not be computed ({exc}) — this run will not be cached")

        if cache_key and not force:
            hit = cached_results(cache_key)
            if hit is not None:
                out_dir = job_dir / "combined" / "txt"
                out_dir.mkdir(parents=True, exist_ok=True)
                for src in sorted(hit.glob("*")):
                    if src.is_file():
                        shutil.copyfile(src, out_dir / src.name)
                manifest = {}
                try:
                    manifest = json.loads((hit.parent / "manifest.json").read_text())
                except (OSError, json.JSONDecodeError):
                    pass
                when, src_job = manifest.get("completed_at", "an earlier run"), manifest.get("job_id", "unknown")
                # Said out loud, in the log the UI shows. Serving cached science silently is how a
                # stale number ends up in a paper; the user has to know to reach for Force re-run.
                _log(f"CACHE HIT {cache_key} — reusing results from {when} (job {src_job}). "
                     f"MaxQuant was not run. Use force to recompute.")
                _write_progress(progress_path, "done", f"complete (reused cached results from {when})")
                return
            _log(f"cache miss {cache_key} — running MaxQuant")
        elif force:
            _log(f"force requested — running MaxQuant even though key {cache_key or '(none)'} may be cached")

        env = {
            **os.environ,
            "PRIDEMAP_RAW_DIR": str(raw_root),
            "MAXQUANT_CMD": "maxquant",
            # The tools are in the image; preflight must not warn about a Docker daemon that is not
            # meant to exist here, in a log the UI now shows the user.
            "PRIDEMAP_TOOLS_PREINSTALLED": "1",
        }

        _log("starting run_all.sh (download stage will be skipped — files already in raw_vol)")
        _write_progress(progress_path, "running", "starting run_all.sh")
        proc = subprocess.Popen(
            ["bash", str(run_script)],
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            _log(line.rstrip())
        proc.wait()

        # Every failure below RAISES. Writing "error" into progress.jsonl and returning normally is
        # what made a dead run report as finished: the FunctionCall completed without an exception,
        # so _call_status saw .get() succeed and answered "done". The progress file it wrote instead
        # is not read by any endpoint. Modal's own call state is the only channel the UI's status
        # comes through, so a failure has to travel on that channel to be seen at all.
        protein_src = _find_protein_groups(work_dir) if proc.returncode == 0 else None
        problem = search_failure(proc.returncode, protein_src)
        if problem:
            _write_progress(progress_path, "error", problem)
            _log(f"FAILED: {problem}")
            raise RuntimeError(problem)

        assert protein_src is not None  # search_failure returns a reason when it is None
        out_dir = job_dir / "combined" / "txt"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "proteinGroups.txt").write_bytes(protein_src.read_bytes())
        _log("proteinGroups.txt copied to volume")

        # Populate the cache only now — AFTER search_failure() passed. A run that produced no
        # protein table must never leave an entry behind for the next run to trust.
        if cache_key:
            stored = store_results(
                cache_key,
                protein_src.parent,
                {
                    "key": cache_key,
                    "accession": accession,
                    "job_id": job_id,
                    "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "raw_files": [name for name, _ in raw_ids],
                    "raw_sha256": {name: digest for name, digest in raw_ids},
                    "maxquant_search_cpu": MAXQUANT_SEARCH_CPU,
                },
            )
            _log(f"results cached under {cache_key}" if stored else "results could NOT be cached")

        _write_progress(progress_path, "done", "complete")

    except Exception as exc:
        _log(f"EXCEPTION: {exc}")
        _write_progress(progress_path, "error", str(exc))
        raise  # the caller's status is derived from this propagating, not from the progress file
    finally:
        _stop_shipper.set()
        _ship_stages()
        # One final sample, written AND logged — taking two would leave the second with no interval
        # to measure. The peak is what a sizing decision needs, and the log is where it survives
        # after the job directory is cleaned up.
        _log(f"resources used (search): {json.dumps(_ship_cpu())}")
        _vol_commit()


# ----------------------------------------------------------------------------- orchestrator (lightweight)

# The deployer accepts MaxQuant's terms (README › Licence) by adding this, set to exactly "1", to the
# pridemap-mq-token secret. Same name the generated preflight.sh checks (api/omics.py).
MAXQUANT_LICENCE_ENV = "MAXQUANT_ACCEPT_LICENSE"


def _maxquant_licence_problem(env: Mapping[str, str]) -> str | None:
    """Why this workspace may not run MaxQuant yet, or None once the licence has been accepted."""
    if env.get(MAXQUANT_LICENCE_ENV) == "1":
        return None
    return (
        f"MaxQuant licence not accepted: {MAXQUANT_LICENCE_ENV} is not 1 in this Modal workspace. "
        "MaxQuant and the Thermo/Bruker readers it bundles are third-party software under their own "
        "terms (README › Licence). Once you have read them: modal secret create pridemap-mq-token "
        f"MQ_TOKEN=<token> {MAXQUANT_LICENCE_ENV}=1 --force"
    )


@app.function(
    image=image,
    volumes={VOLUME_MOUNT: vol},
    secrets=[secret],
    cpu=1,
    memory=1024,
    timeout=86400,   # Modal's maximum (24 h). _download_raw is capped at 2 h, leaving up to
                     # 22 h for MaxQuant — sufficient for all but the largest public datasets.
)
def run_maxquant(kit_bytes: bytes, accession: str, force: bool = False) -> str:
    """Lightweight orchestrator: sequences download (cheap) then search (expensive).

    `force` is passed straight through to the search so a cached result is recomputed on request.

    Returning this function's own call_id keeps the polling contract unchanged:
    /status maps call_id → "running" while this orchestrator is alive (i.e. while either
    phase is in progress), and → "done" when it returns. The job directory is named after
    this call_id so /results and /logs resolve correctly without any gateway changes.

    Cost model vs the old single-function approach:
      download phase: 2 CPU / 4 GB  (~10× cheaper than 16 CPU / 64 GB)
      search phase:   16 CPU / 64 GB (unchanged)
      orchestrator:   1 CPU / 1 GB  (negligible; it just blocks waiting for children)
    """
    job_id = _job_id_for_this_run(accession)
    job_dir = DATA_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    _vol_commit()   # make job_dir visible to children before they start

    log_path = job_dir / "run.log"
    progress_path = job_dir / "progress.jsonl"

    def _log(msg: str) -> None:
        print(msg, flush=True)
        with log_path.open("a") as f:
            f.write(msg + "\n")

    # Phase events go in their own file. The shipper REPLACES stages.jsonl wholesale with the
    # container's progress.jsonl, so anything written there by the orchestrator would be erased the
    # moment phase 2 starts; _read_stage_events concatenates the two instead.
    phases_path = job_dir / "phases.jsonl"

    _log(f"job_id={job_id}  accession={accession}")

    # preflight.sh refuses this too, but on Modal it only runs after the full download and a search
    # container cold start. The orchestrator carries the same secret, so it can answer up front.
    licence_problem = _maxquant_licence_problem(os.environ)
    if licence_problem:
        _log(f"PREFLIGHT FAILED: {licence_problem}")
        _write_progress(progress_path, "error", licence_problem)
        _write_stage_event(phases_path, "stage_failed", "preflight", licence_problem)
        _vol_commit()
        # Raising, for the same reason as the download failure below: a return reads as "done".
        raise RuntimeError(licence_problem)

    _log(f"phase 1/2: download (cheap container — {MAXQUANT_DOWNLOAD_CPU} CPU / {MAXQUANT_DOWNLOAD_MEMORY // 1024} GB)")
    _write_progress(progress_path, "running", "download phase starting")
    _write_stage_event(phases_path, "run_started", "", "pipeline start")
    _write_stage_event(phases_path, "stage_started", "download", "Download raw files from PRIDE")
    _vol_commit()

    try:
        _download_raw.remote(kit_bytes, accession, job_id)
    except Exception as exc:
        _log(f"DOWNLOAD FAILED: {exc}")
        _write_progress(progress_path, "error", f"download failed: {exc}")
        _write_stage_event(phases_path, "stage_failed", "download", f"download failed: {exc}")
        _vol_commit()
        # Raising, not returning. A return here completed the FunctionCall normally, so _call_status
        # read the job as "done" and the card reported a finished run that had downloaded nothing.
        raise

    _write_stage_event(phases_path, "stage_finished", "download", "completed")
    _vol_sync("reload")   # pull log lines written by the download worker
    _log(f"phase 2/2: MaxQuant search (expensive container — {MAXQUANT_SEARCH_CPU} CPU / {MAXQUANT_SEARCH_MEMORY // 1024} GB)")
    _write_progress(progress_path, "running", "search phase starting")
    _write_stage_event(phases_path, "stage_started", "search_dda", "MaxQuant search starting")
    _vol_commit()

    try:
        _run_maxquant_search.remote(kit_bytes, accession, job_id, force)
    except Exception as exc:
        _log(f"MAXQUANT FAILED: {exc}")
        _write_progress(progress_path, "error", f"maxquant failed: {exc}")
        _write_stage_event(phases_path, "stage_failed", "search_dda", f"maxquant failed: {exc}")
        _vol_commit()
        raise

    return job_id


# ----------------------------------------------------------------------------- cache eviction

@app.function(
    image=image,
    volumes={RAW_CACHE_MOUNT: raw_vol},
    cpu=1,
    memory=1024,
    timeout=3600,
)
def prune_raw_cache(max_age_days: int = 30, skip: "list[str] | None" = None) -> dict:
    """Remove accession directories from raw_vol that have not been accessed recently.

    Only removes a directory when EVERY raw file inside it is older than max_age_days, so
    an accession that is currently downloading or was recently used is never touched.
    Lock files (.downloading) are ignored when measuring age — only .raw files count.

    `skip` names accessions to leave alone whatever their age. Age alone is not sufficient
    protection: a raw file's mtime is when it was DOWNLOADED, so a dataset cached a month ago and
    being searched right now looks exactly like one nobody wants, and deleting it mid-search pulls
    the input out from under a 24-hour job. The caller knows which jobs are live; this function
    cannot, so it takes the answer rather than guessing. pridemap-janitor passes the accessions of
    every running job and every in-flight download.

    Run manually:  modal run api/modal_runner.py::prune_raw_cache
    Override age:  modal run api/modal_runner.py::prune_raw_cache --max-age-days 7
    """
    _vol_sync("reload", raw_vol)
    protected = set(skip or ())
    cutoff = time.time() - max_age_days * 86400
    cache_root = pathlib.Path(RAW_CACHE_MOUNT)
    removed: list[str] = []
    kept: list[str] = []

    for entry in sorted(cache_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        accession = entry.name
        if accession in protected:
            kept.append(f"{accession} (in use — protected by caller)")
            continue
        raw_files = [f for f in entry.rglob("*") if f.is_file() and not f.name.startswith(".")]
        if not raw_files:
            continue
        newest_mtime = max(f.stat().st_mtime for f in raw_files)
        age_days = (time.time() - newest_mtime) / 86400
        if newest_mtime < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(f"{accession} ({age_days:.0f} days old)")
        else:
            kept.append(f"{accession} ({age_days:.1f} days old)")

    if removed:
        _vol_commit(raw_vol, what="raw cache")

    return {"removed": removed, "kept": kept, "max_age_days": max_age_days}


def _job_id_for_this_run(accession: str) -> str:
    """The id this run's directory is named after.

    It must be the id the CLIENT polls, which is the FunctionCall id that spawn_job() returns:
    /status and /results look under DATA_ROOT/{that id}. Naming the directory anything else makes
    the whole run invisible — run.log never appears (the UI shows "Waiting for output…" for hours)
    and /results 404s even after a successful search.

    Off Modal there is no call id (and older clients may not expose the accessor at all), so the
    fallback keeps local runs working with a directory name that is still unique per run.
    """
    current_id = getattr(modal, "current_function_call_id", lambda: None)
    try:
        call_id = current_id()
    except Exception:
        call_id = None
    return call_id or f"{accession}-{int(time.time())}"


def _write_progress(path: pathlib.Path, status: str, message: str) -> None:
    # Create the directory rather than assuming it. A job directory made and committed by the
    # ORCHESTRATOR container is not reliably visible in the search container after a reload, and
    # when it was missing this line raised FileNotFoundError before run_all.sh had even started —
    # then raised a second time inside the `except` that was trying to record the first. Both
    # tracebacks named progress.jsonl, so the run looked like a MaxQuant failure. A directory is
    # cheap; depending on another container having published one is not.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"status": status, "message": message, "ts": time.time()}) + "\n")


def search_failure(returncode: int, protein_src: "pathlib.Path | None") -> str | None:
    """Why this search should be treated as failed, or None if it succeeded.

    Pulled out of _run_maxquant_search so the rule can be exercised without a Modal container and a
    mounted Volume. The rule is the whole point of the fix: both of these used to write "error" into
    a progress file nobody reads and then return normally, so the FunctionCall completed cleanly and
    the job reported "done". A run that produced no protein table is not a finished run.
    """
    if returncode != 0:
        return f"run_all.sh exited {returncode} — see the run log for the stage that failed"
    if protein_src is None:
        return (
            "run_all.sh reported success but produced no proteinGroups.txt — the search did not "
            "write a protein table"
        )
    return None


def _write_stage_event(path: pathlib.Path, event: str, stage: str, message: str) -> None:
    """Append one run_all.sh-shaped stage event.

    The orchestrator's own phases were invisible: _write_progress writes {status, message} to
    progress.jsonl, which no endpoint reads, and the stage list the UI draws comes from stages.jsonl
    — which only phase 2 ships. So for the whole download phase, up to two hours, the card showed a
    bare "running" with an empty stage list.

    Emitting in the same shape run_all.sh uses means these events reduce through the same
    current_run_events/progress_by_stage/stage_view path as every other stage, rather than needing a
    second reduction that could disagree with the first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)   # same reason as _write_progress
    with path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "event": event,
                    "stage": stage,
                    "message": message,
                }
            )
            + "\n"
        )


def _find_protein_groups(work_dir: pathlib.Path) -> pathlib.Path | None:
    for p in work_dir.rglob("proteinGroups.txt"):
        return p
    return None


# ----------------------------------------------------------------------------- ASGI web endpoints

class _ModalBackend:
    """Modal-side implementation of modal_web.Backend.

    Deliberately free of FastAPI imports: this module is also loaded in the MaxQuant worker image,
    which has no FastAPI installed.
    """

    def spawn_job(self, kit_bytes: bytes, accession: str, force: bool = False) -> str:
        # object_id is read before responding so the caller always receives a pollable id.
        return run_maxquant.spawn(kit_bytes, accession, force).object_id

    def accession_from_kit(self, kit_bytes: bytes) -> str:
        return _accession_from_kit(kit_bytes)

    def job_key_from_kit(self, kit_bytes: bytes, accession: str) -> str:
        return job_key(accession, selection_from_kit(kit_bytes))

    def record_job(self, accession: str, job_id: str) -> None:
        try:
            # Never displace a job that is still running. The old unconditional write orphaned it:
            # /jobs stopped reporting it while it kept billing 16 CPU / 64 GB, and /jobs is exactly
            # what the kill procedure reads to decide what is safe to terminate. The caller is
            # expected to have refused the second submission already; this is the backstop for the
            # races it cannot see.
            existing = jobs.get(accession)
            if existing and existing != job_id and _call_status(existing) == "running":
                print(
                    f"WARNING: refusing to displace running job {existing} for {accession} "
                    f"with {job_id}",
                    flush=True,
                )
                return
            jobs[accession] = job_id
        except Exception as exc:
            # Non-fatal: /jobs/{accession} has a fallback. Logged rather than swallowed because a
            # silent miss makes /jobs under-report, and an empty /jobs is exactly what gets used to
            # decide whether terminating this app's containers would destroy running work.
            print(f"WARNING: jobs dict write failed for {accession}: {exc}", flush=True)

    def job_status(self, job_id: str) -> str:
        return _call_status(job_id)

    def job_log(self, job_id: str) -> str:
        return _read_log(job_id)

    def job_progress(self, job_id: str) -> list[dict]:
        return _read_stage_events(job_id)

    def job_cpu(self, job_id: str) -> dict | None:
        return _read_cpu(job_id)

    def results_path(self, job_id: str) -> pathlib.Path | None:
        job_dir = _job_dir(job_id)
        if job_dir is None:
            return None
        path = job_dir / "combined" / "txt" / "proteinGroups.txt"
        return path if path.exists() else None

    def delete_job(self, job_id: str, purge: bool = False) -> dict:
        # This is the budget stop-button, so every step reports what it actually did. Swallowing a
        # failed cancel here means a 16-core job keeps billing while the caller is told it stopped.
        #
        # It is NOT a delete-my-results button, which is what it used to be. Stop on a finished job
        # cancelled nothing and then rmtree'd the directory, so the first search that ever ran to
        # completion lost its protein table, its log and its measurements — and the loss looked like
        # a reason to run the whole thing again. A completed run's output is now kept unless the
        # caller explicitly asks to purge it; the registry entry is released either way, which is
        # what actually unblocks the next submission.
        problems: list[str] = []

        try:
            modal.FunctionCall.from_id(job_id).cancel()
            cancelled = True
        except Exception as exc:
            cancelled = False
            problems.append(f"cancel failed: {exc}")

        job_dir = _job_dir(job_id)
        has_results = job_dir is not None and (job_dir / "combined" / "txt" / CACHE_RESULT_FILE).is_file()
        kept_results = False

        if job_dir is not None and job_dir.exists():
            if has_results and not purge:
                kept_results = True
                print(
                    f"keeping results for {job_id}: it produced {CACHE_RESULT_FILE}. "
                    f"Pass purge to delete them.",
                    flush=True,
                )
            else:
                shutil.rmtree(job_dir, ignore_errors=True)
                if job_dir.exists():
                    problems.append("volume directory could not be removed")
                try:
                    vol.commit()
                except Exception as exc:
                    problems.append(f"volume commit failed: {exc}")

        try:
            for acc, jid in list(jobs.items()):
                if jid == job_id:
                    del jobs[acc]
        except Exception as exc:
            problems.append(f"jobs dict cleanup failed: {exc}")

        return {
            "deleted": not problems and not kept_results,
            "cancelled": cancelled,
            "kept_results": kept_results,
            "problems": problems,
        }

    def list_jobs(self) -> list[tuple[str, str]]:
        return list(jobs.items())

    def job_for_accession(self, accession: str) -> str | None:
        try:
            return jobs.get(accession)
        except Exception:
            return None

    def resource_advice(self) -> dict:
        """Scan completed job directories and recommend resource allocations.

        Reads cpu-{phase}.json from every job directory on the volume, aggregates
        per-phase peak values across all runs, and returns current constants vs
        recommended values. Headroom: 2× CPU busy cores, 1.5× RSS, 2× disk peak.
        """
        import math

        peaks: dict[str, dict] = {phase: {} for phase in CPU_REPORT_PHASES}
        job_count = 0

        try:
            job_dirs = [p for p in DATA_ROOT.iterdir() if p.is_dir()]
        except (OSError, NotImplementedError) as exc:
            return {"error": f"could not list job directories: {exc}", "jobs_analysed": 0}

        for job_dir in job_dirs:
            found_any = False
            for phase in CPU_REPORT_PHASES:
                try:
                    report = json.loads((job_dir / cpu_report_file(phase)).read_text())
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
                found_any = True
                p = peaks[phase]
                for key in ("peak_busy_cores", "peak_rss_gb", "peak_disk_used_gb"):
                    val = report.get(key)
                    if val is not None:
                        p[key] = max(p.get(key, 0.0), float(val))
            if found_any:
                job_count += 1

        def _nice_cpu(cores: float) -> int:
            return max(1, math.ceil(cores))

        def _nice_mem_mb(gb: float) -> int:
            return max(1024, math.ceil(gb) * 1024)

        def _nice_disk_mb(gb: float) -> int:
            # Clamped to what Modal actually accepts. Unclamped this produced 40960 and then 20480
            # MiB from real measurements, and BOTH were rejected at deploy time with "Function disk
            # request out of bounds: must be between 524288 and 3145728 MiB" — so following the
            # advice broke the deploy twice. A recommendation that cannot be applied is not advice.
            #
            # Note what this means for sizing: ephemeral_disk has a 512 GiB FLOOR, so disk is not a
            # cost lever downwards at all. Measured peak use is ~15 GB on a two-run job, which is 3%
            # of the minimum we are obliged to request. Only a dataset needing more than 512 GiB
            # makes this number worth changing.
            want = max(MODAL_DISK_MIN_MB, math.ceil(gb / 10) * 10 * 1024)
            return min(MODAL_DISK_MAX_MB, want)

        sp, dp = peaks["search"], peaks["download"]
        search_rec: dict = {}
        download_rec: dict = {}

        # CPU headroom: 1.25× peak busy cores, rounded up. MaxQuant numThreads is set to
        # the allocation, so over-allocating wastes money without helping throughput.
        # Memory: 1.5× peak RSS — allows for heap growth between samples.
        # Disk: 2× peak used — combining and writing tables can double the working set.
        if sp.get("peak_busy_cores") is not None:
            search_rec["cpu"] = _nice_cpu(sp["peak_busy_cores"] * 1.25)
        if sp.get("peak_rss_gb") is not None:
            search_rec["memory_mb"] = _nice_mem_mb(sp["peak_rss_gb"] * 1.5)
        if sp.get("peak_disk_used_gb") is not None:
            search_rec["ephemeral_disk_mb"] = _nice_disk_mb(sp["peak_disk_used_gb"] * 2.0)

        if dp.get("peak_busy_cores") is not None:
            download_rec["cpu"] = _nice_cpu(dp["peak_busy_cores"] * 1.25)
        if dp.get("peak_rss_gb") is not None:
            download_rec["memory_mb"] = _nice_mem_mb(dp["peak_rss_gb"] * 1.5)

        return {
            "jobs_analysed": job_count,
            "peaks": peaks,
            "current": {
                "search": {
                    "cpu": MAXQUANT_SEARCH_CPU,
                    "memory_mb": MAXQUANT_SEARCH_MEMORY,
                    "ephemeral_disk_mb": MAXQUANT_SEARCH_DISK,
                },
                "download": {
                    "cpu": MAXQUANT_DOWNLOAD_CPU,
                    "memory_mb": MAXQUANT_DOWNLOAD_MEMORY,
                },
            },
            "recommended": {"search": search_rec, "download": download_rec},
            "headroom": {"cpu": "1.25×", "memory": "1.5×", "disk": "2×"},
            "note": "recommendations improve as more completed jobs accumulate on the volume",
        }


@app.function(
    image=web_image,
    volumes={VOLUME_MOUNT: vol},   # required: /status and /results read run.log and proteinGroups.txt
    secrets=[secret],
    scaledown_window=1800,  # stay warm 30 min after last request (Modal default is 300 s)
    # keep_warm=1,          # uncomment to always keep one container hot (eliminates cold starts, costs ~$0.06/h)
)
@modal.asgi_app(label="pridemap-mq")
def web() -> object:
    # Imported here rather than at module level because the MaxQuant worker image has no FastAPI,
    # and importing it there would break run_maxquant at container start. The handlers themselves
    # live in modal_web, where the FastAPI types ARE module-level and therefore always resolvable
    # by get_type_hints() — see that module's docstring for why that distinction matters.
    import modal_web

    return modal_web.build_app(
        _ModalBackend(),
        expected_token=os.environ.get("MQ_TOKEN", ""),
        fingerprint=modal_web.source_fingerprint(
            [pathlib.Path(__file__), pathlib.Path(modal_web.__file__)]
        ),
    )


# Which exception means "this call has not finished yet" is an implementation detail of the modal
# client version, and getting it wrong is silent: the status simply reads "error" for healthy jobs.
# modal/_functions.py imports no TimeoutError from modal.exception, so its `raise TimeoutError()`
# resolves to the BUILTIN — matching only modal.exception.TimeoutError never fired, and every
# running job fell through to `except Exception` and reported failure seconds after submission.
# Both are listed so either client behaviour maps correctly, and each lookup tolerates its absence
# in older or newer clients (an unresolvable name in an `except` clause raises AttributeError at
# handling time, turning a version skew into a 500).
_PENDING_ERRORS: tuple[type[BaseException], ...] = tuple(
    {TimeoutError, getattr(modal.exception, "TimeoutError", TimeoutError)}
)
# OutputExpiredError subclasses modal's TimeoutError, so it must be caught FIRST: a result that has
# been garbage-collected is a failure to surface, not work still in progress. An empty tuple never
# matches, which is the right behaviour when a client has no such class.
_EXPIRED_ERRORS: tuple[type[BaseException], ...] = tuple(
    c for c in (getattr(modal.exception, "OutputExpiredError", None),) if c is not None
)


# Reaching Modal failed, as opposed to Modal reporting that the job failed. These are transient and
# say nothing about the run: the 16-core search carries on regardless. Reporting "error" for them
# marked healthy jobs as dead permanently, because the card never polls again once it reads a
# terminal status. "running" keeps the next poll coming, which is the only way the truth arrives.
_UNREACHABLE_ERRORS: tuple[type[BaseException], ...] = tuple(
    {
        ConnectionError,
        OSError,  # includes socket.error / ssl errors raised during transport
        getattr(modal.exception, "ConnectionError", ConnectionError),
    }
)


def _call_status(job_id: str) -> str:
    """"running" | "done" | "error" for a spawned call. Shared by /status and /jobs."""
    try:
        modal.FunctionCall.from_id(job_id).get(timeout=0)
        return "done"
    except _EXPIRED_ERRORS:
        return "error"
    except _PENDING_ERRORS:
        return "running"
    except _UNREACHABLE_ERRORS:
        # Deliberately NOT terminal. We could not ask; that is not the same as a failed run.
        return "running"
    except Exception:
        # The function itself raised — a genuine failure, and since _run_maxquant_search now
        # propagates instead of swallowing, this is the path a failed search actually takes.
        return "error"


def _job_dir(job_id: str) -> pathlib.Path | None:
    """The directory holding this job's run.log and results, or None.

    Single owner of job-id → path resolution, so the worker that writes a job directory and the
    /status and /results handlers that read it cannot disagree about where output lives.
    """
    _vol_sync("reload")
    direct = DATA_ROOT / job_id
    if direct.exists():
        return direct

    # Transitional: jobs spawned before the id unification named their directory
    # "{accession}-{timestamp}" while the client was handed the FunctionCall id, so their output is
    # only reachable by reversing the jobs Dict. Delete this branch once no such job is in flight.
    try:
        accession = next((acc for acc, jid in jobs.items() if jid == job_id), None)
    except Exception:
        accession = None
    if accession:
        legacy = sorted(DATA_ROOT.glob(f"{accession}-*"))
        if legacy:
            return legacy[-1]
    return None


def _read_log(job_id: str) -> str:
    job_dir = _job_dir(job_id)
    if job_dir is None:
        return ""
    try:
        return (job_dir / "run.log").read_text(errors="replace")[-8000:]  # last 8 KB
    except FileNotFoundError:
        return ""


def _read_cpu(job_id: str) -> dict | None:
    """This job's resource measurements, or None when there is nothing measured.

    None is a distinct answer from zero usage: a job that has not reached the search phase, a server
    deployed before the readout existed, and a host without a readable cgroup all have nothing to
    report, and the card must say so rather than draw an idle container.

    The search worker's figures are the headline because that is the expensive container, but every
    phase's report is carried under `phases` so the download and orchestrator allocations can be
    sized from their own numbers rather than from the search worker's.
    """
    job_dir = _job_dir(job_id)
    if job_dir is None:
        return None

    def _load(name: str) -> dict | None:
        try:
            report = json.loads((job_dir / name).read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return report if isinstance(report, dict) else None

    phases = {p: r for p in CPU_REPORT_PHASES if (r := _load(cpu_report_file(p))) is not None}
    # Prefer the search worker, fall back to whatever else reported, and finally to the legacy
    # single-phase file so a job already in flight when this deployed still shows figures.
    headline = phases.get("search") or next(iter(phases.values()), None) or _load(CPU_REPORT_FILE)
    if headline is None:
        return None
    return {**headline, "phases": phases} if phases else headline


def _read_stage_events(job_id: str) -> list[dict]:
    """run_all.sh's stage events for this job, oldest first.

    A half-written final line is expected: the file is copied while the script is appending to it,
    so an unparseable line is dropped rather than failing the whole poll.
    """
    job_dir = _job_dir(job_id)
    if job_dir is None:
        return []
    events: list[dict] = []
    # phases.jsonl first: the orchestrator's own download phase, which run_all.sh cannot report
    # because it has not started yet. run_all.sh then emits its own run_started, and
    # current_run_events keeps only events from that point on — so these are superseded exactly when
    # the real stage records arrive, which is the behaviour we want rather than a conflict.
    for name in ("phases.jsonl", "stages.jsonl"):
        try:
            text = (job_dir / name).read_text(errors="replace")
        except FileNotFoundError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


SELECTION_KEY_FILE = "selection_key.txt"


def selection_from_kit(kit_bytes: bytes) -> str:
    """The run-selection key the kit carries, or "" for a whole-dataset run.

    `runs` never reaches this side — /run receives only the kit — so the key is computed by
    _selection_key on the API side and carried in the zip. Reading it here rather than re-deriving
    it is what keeps the two ends agreeing about the registry key.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(kit_bytes)) as zf:
            for name in zf.namelist():
                if pathlib.PurePath(name).name == SELECTION_KEY_FILE:
                    return zf.read(name).decode(errors="replace").strip()
    except Exception:
        pass
    return ""


def job_key(accession: str, selection: str) -> str:
    """The registry key for one dataset + run selection. Mirrors omics.job_key exactly."""
    return f"{accession}:{selection}" if selection else accession


def _accession_from_kit(kit_bytes: bytes) -> str:
    """Extract the dataset accession from the kit zip (best-effort; falls back to timestamp)."""
    try:
        with zipfile.ZipFile(io.BytesIO(kit_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith("mqpar.xml"):
                    text = zf.read(name).decode(errors="replace")
                    # <filePaths> contains paths like …/PXD001234/raw/…
                    import re
                    m = re.search(r"(PXD\d{6})", text)
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return f"unknown-{int(time.time())}"
