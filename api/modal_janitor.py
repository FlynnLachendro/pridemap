"""
Scheduled cleanup of the Modal resources the MaxQuant pipeline leaves behind.

Deploy:   modal deploy api/modal_janitor.py       # installs the 6-hourly schedule
Dry run:  modal run api/modal_janitor.py          # prints the plan, deletes nothing
Apply:    modal run api/modal_janitor.py::sweep_now
Report:   modal run api/modal_janitor.py::usage_now

Deliberately a SEPARATE app from pridemap-maxquant. The runner is the thing that spends money and
must stay deployable on its own schedule; a janitor that shared its app would be redeployed every
time the search changed, and a broken janitor would block deploying a fix to the search.

WHAT IT WILL DELETE — only storage, and only storage that regenerates itself on the next request:

  maxquant-scratch  `<name>.index` files and `<name>/` directories sitting next to a .raw file on
                    pridemap-raw. MaxQuant's own per-file intermediates, which leaked onto the
                    volume because the kit symlinked raw/ at the volume mount (fixed by
                    _stage_raw_local, so these are now historical — but a few GB of them are
                    already there, and anything rolled back would make more). MaxQuant rebuilds
                    them from the .raw whenever it needs them.
  stale-lock        `.downloading` lock files older than the download timeout. Left by a container
                    that died; _download_raw already treats these as takeable, and removing them
                    saves the next run a six-minute wait.
  job-dir           Job directories on pridemap-runs for jobs that have FINISHED and are older than
                    the retention window. proteinGroups.txt and run.log live here, so this is the
                    one category with real user-visible content — hence the longest default TTL.
  raw-cache         Cached .raw files untouched for longer than the raw window. Re-downloadable
                    from PRIDE by download_raw.sh on the next request, which is the whole point of
                    have() being content-addressed. The slowest to regenerate, so the longest TTL.
                    NOT implemented here: modal_runner.prune_raw_cache already owns this, and a
                    second eviction rule for the same files would be a second answer to "is this
                    still wanted". The sweep calls it, passing the accessions it knows are in use.

WHAT IT WILL NEVER DO:
  * cancel or interrupt a running FunctionCall — it spends no compute and stops none;
  * touch a job directory whose call is still running, or that it could not get a status for;
  * touch any file of an accession that has a LIVE download lock, or a running job;
  * delete a volume, a Dict, a Secret, or the deployed apps themselves.

Compute is not swept because Modal already does it: containers scale to zero on their own, and the
only durable compute cost here would be a stuck FunctionCall, which is a decision for a human with
the Stop button rather than for a cron.
"""

# /// script
# requires-python = ">=3.11"
# dependencies = ["modal>=0.73"]
# ///

import dataclasses
import json
import pathlib
import shutil
import time

import modal

app = modal.App("pridemap-janitor")

# The same names modal_runner.py uses — same names mean the same objects. create_if_missing stays
# False: a janitor that CREATES the volume it is asked to clean would silently sweep an empty new
# volume and report success while the real one filled up under a typo.
vol = modal.Volume.from_name("pridemap-runs")
raw_vol = modal.Volume.from_name("pridemap-raw")

VOLUME_MOUNT = "/data"
RAW_CACHE_MOUNT = "/raw-cache"

# Retention windows. Ordered by how expensive the thing is to regenerate: scratch is seconds,
# a job directory is a whole search, a raw file is a multi-GB download from PRIDE.
SCRATCH_TTL_S = 6 * 3600         # MaxQuant intermediates: 6 h after last write
LOCK_TTL_S = 7200                # matches _download_raw's own timeout and stale-lock rule
JOB_TTL_S = 7 * 86400            # finished job directories: 7 days
RAW_TTL_DAYS = 14                # passed to prune_raw_cache, which owns raw eviction (days, not s)

SCHEDULE = modal.Period(hours=6)

image = modal.Image.debian_slim().add_local_python_source("modal_runner", "modal_web")


# ----------------------------------------------------------------------------- the plan (pure)

@dataclasses.dataclass(frozen=True)
class Entry:
    """One thing on a volume, as the planner sees it. No Modal types, so the rules are testable."""
    path: str
    kind: str          # "maxquant-scratch" | "stale-lock" | "job-dir" | "raw-cache"
    mtime: float
    size: int
    accession: str = ""
    job_id: str = ""
    is_dir: bool = False


@dataclasses.dataclass(frozen=True)
class Candidate:
    path: str
    kind: str
    reason: str
    size: int
    is_dir: bool


#: TTL per kind, so adding a kind cannot forget to give it a retention window. A kind absent from
#: here is never swept — fail-closed, and the reason "raw-cache" is absent: prune_raw_cache owns it.
TTL_BY_KIND = {
    "maxquant-scratch": SCRATCH_TTL_S,
    "stale-lock": LOCK_TTL_S,
    "job-dir": JOB_TTL_S,
}


def plan_cleanup(
    entries: "list[Entry]",
    *,
    now: float,
    running_job_ids: "frozenset[str]",
    busy_accessions: "frozenset[str]",
    ttl_by_kind: "dict[str, int] | None" = None,
) -> "list[Candidate]":
    """Which entries are safe to delete, and why. The whole decision, with no I/O in it.

    Three guards, each of which has to be able to say no on its own:

    `running_job_ids` protects live work. A job directory is where the running container is writing
    its log and stage events; deleting it mid-run does not cancel the job, it just blinds the UI to
    a search that keeps billing. The caller derives this set from _call_status, which answers
    "running" when Modal cannot be reached — so an outage keeps everything rather than sweeping it.

    `busy_accessions` protects a download in flight. A fresh .downloading lock means a container is
    curl-ing into that directory right now; removing its partial file or its lock would hand the
    next run a corrupt resume. Note this also means a live lock protects the whole accession, which
    is why the lock's own TTL is checked before it joins this set and not after.

    A kind with no TTL is never swept. That is the fail-closed default for a kind added here
    without a retention decision having been made for it.
    """
    ttls = TTL_BY_KIND if ttl_by_kind is None else ttl_by_kind
    out: list[Candidate] = []
    for e in entries:
        ttl = ttls.get(e.kind)
        if ttl is None:
            continue
        age = now - e.mtime
        if age < ttl:
            continue
        if e.job_id and e.job_id in running_job_ids:
            continue
        if e.accession and e.accession in busy_accessions:
            continue
        out.append(
            Candidate(
                path=e.path,
                kind=e.kind,
                reason=f"{e.kind}: last written {age / 3600:.1f} h ago (keep for {ttl / 3600:.0f} h)",
                size=e.size,
                is_dir=e.is_dir,
            )
        )
    return out


def summarise(candidates: "list[Candidate]") -> dict:
    """Totals per kind — what gets printed and what the report file records."""
    by_kind: dict[str, dict] = {}
    for c in candidates:
        slot = by_kind.setdefault(c.kind, {"count": 0, "bytes": 0})
        slot["count"] += 1
        slot["bytes"] += c.size
    return {
        "total_count": len(candidates),
        "total_gb": round(sum(c.size for c in candidates) / 1024**3, 2),
        "by_kind": by_kind,
    }


# ----------------------------------------------------------------------------- scanning (I/O)

def _tree_size(p: pathlib.Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def _newest_mtime(p: pathlib.Path) -> float:
    """The most recent mtime anywhere under p, so an actively-written tree never looks idle.

    A directory's own mtime only tracks its direct children, so a job directory whose log is being
    appended to inside combined/ can look untouched for days. Taking the maximum over the tree is
    what makes "nothing has happened here for a week" mean it.
    """
    try:
        newest = p.stat().st_mtime
    except OSError:
        return 0.0
    try:
        for f in p.rglob("*"):
            try:
                newest = max(newest, f.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return newest


def scan_raw_cache(root: pathlib.Path) -> "tuple[list[Entry], set[str]]":
    """Entries on the raw cache volume, and the accessions with a live download lock.

    Layout written by download_raw.sh and _download_raw:
      <accession>/<name>.raw          the cached download
      <accession>/<name>.index        MaxQuant scratch (leaked — see module docstring)
      <accession>/<name>/             MaxQuant scratch (n0, p0, …)
      <accession>/.downloading        the download lock
    """
    entries: list[Entry] = []
    busy: set[str] = set()
    if not root.is_dir():
        return entries, busy

    for acc_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        accession = acc_dir.name
        lock = acc_dir / ".downloading"
        if lock.exists():
            try:
                lock_age = time.time() - lock.stat().st_mtime
            except OSError:
                lock_age = 0.0
            if lock_age < LOCK_TTL_S:
                # Held by a live container. Protect the whole accession and move on — nothing in
                # here is safe to touch while a curl is resuming into it.
                busy.add(accession)
                continue
            entries.append(Entry(
                path=str(lock), kind="stale-lock", mtime=lock.stat().st_mtime, size=0,
                accession="",   # the lock IS the stale thing; guarding it by its own accession
                                # would make it permanently undeletable
            ))

        raw_names = {p.stem for p in acc_dir.glob("*.raw")}
        for child in sorted(acc_dir.iterdir()):
            if child.name == ".downloading":
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            # Scratch is identified by belonging to a raw file we hold, never by extension alone:
            # a stray .index with no .raw beside it is something else's and is left alone.
            if child.is_dir() and child.name in raw_names:
                entries.append(Entry(
                    path=str(child), kind="maxquant-scratch", mtime=_newest_mtime(child),
                    size=_tree_size(child), accession=accession, is_dir=True,
                ))
            elif child.suffix == ".index" and child.stem in raw_names:
                entries.append(Entry(
                    path=str(child), kind="maxquant-scratch", mtime=stat.st_mtime,
                    size=stat.st_size, accession=accession,
                ))
            # .raw files are deliberately NOT emitted: prune_raw_cache evicts those, and listing
            # them here would put a second TTL on the same bytes.
    return entries, busy


def scan_job_dirs(root: pathlib.Path, job_ids_in_registry: "dict[str, str]") -> "list[Entry]":
    """Job directories on the runs volume, tagged with the job id they belong to.

    The directory name IS the FunctionCall id for every job spawned since the id unification, which
    is what lets the planner ask whether that call is still running. A directory whose name is not a
    call id (the legacy "<accession>-<timestamp>" shape) carries no job_id and is swept purely on
    age — correct, because no running call can be writing to one.
    """
    if not root.is_dir():
        return []
    entries: list[Entry] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        entries.append(Entry(
            path=str(d), kind="job-dir", mtime=_newest_mtime(d), size=_tree_size(d),
            accession=job_ids_in_registry.get(d.name, ""), job_id=d.name, is_dir=True,
        ))
    return entries


def delete_candidates(candidates: "list[Candidate]") -> "tuple[int, list[str]]":
    """Delete each candidate. Returns (deleted count, problems).

    Problems are collected rather than raised: one unremovable path must not stop the sweep, and a
    janitor that reports success while leaving things behind is worse than one that says what it
    could not do.
    """
    deleted, problems = 0, []
    for c in candidates:
        p = pathlib.Path(c.path)
        try:
            if c.is_dir:
                shutil.rmtree(p, ignore_errors=False)
            else:
                p.unlink(missing_ok=True)
            deleted += 1
        except OSError as exc:
            problems.append(f"{c.path}: {exc}")
    return deleted, problems


# ----------------------------------------------------------------------------- the sweep

@app.function(
    image=image,
    volumes={VOLUME_MOUNT: vol, RAW_CACHE_MOUNT: raw_vol},
    cpu=1,
    memory=2048,
    timeout=3600,
)
def sweep(dry_run: bool = False) -> dict:
    """One cleanup pass. Returns the report it also prints and writes to the runs volume.

    dry_run exists because the first question about any cleanup job is "what would it have done",
    and the only trustworthy answer comes from the same code path that does it.
    """
    # Imported in the container, not at module scope: importing modal_runner here at deploy time
    # would define its app too, and a file with two apps cannot be deployed by name alone. The
    # import is for _call_status — which encodes which exception means "still running", a
    # distinction that has already cost one bug and must not be re-derived here.
    import modal_runner

    vol.reload()
    raw_vol.reload()
    started = time.time()

    try:
        registry = {jid: acc for acc, jid in modal_runner.jobs.items()}
    except Exception as exc:
        # Fail closed: with no registry we cannot tell a running job's directory from a dead one.
        print(f"janitor: ABORT — cannot read the jobs registry ({exc}); nothing swept", flush=True)
        return {"ok": False, "error": f"jobs registry unreadable: {exc}"}

    raw_entries, busy_accessions = scan_raw_cache(pathlib.Path(RAW_CACHE_MOUNT))
    job_entries = scan_job_dirs(pathlib.Path(VOLUME_MOUNT), registry)

    # Status is asked once per job id and reused, because each call is a network round trip and the
    # answer cannot change in a way that makes deleting safe mid-sweep.
    running = frozenset(
        e.job_id for e in job_entries
        if e.job_id and modal_runner._call_status(e.job_id) == "running"
    )
    # A running job also protects its accession's raw files: the search reads them for hours after
    # the download lock is gone, and re-downloading mid-search is not a recoverable state.
    busy = frozenset(busy_accessions | {registry[j] for j in running if j in registry})

    candidates = plan_cleanup(
        raw_entries + job_entries,
        now=time.time(),
        running_job_ids=running,
        busy_accessions=busy,
    )
    report = {
        "ok": True,
        "dry_run": dry_run,
        "scanned": len(raw_entries) + len(job_entries),
        "protected_running_jobs": sorted(running),
        "protected_accessions": sorted(busy),
        **summarise(candidates),
    }

    for c in candidates:
        print(f"janitor: {'would delete' if dry_run else 'delete'} {c.path} — {c.reason}", flush=True)

    if dry_run:
        report["deleted"] = 0
    else:
        deleted, problems = delete_candidates(candidates)
        report["deleted"] = deleted
        if problems:
            report["problems"] = problems
        try:
            raw_vol.commit()
        except Exception as exc:
            report.setdefault("problems", []).append(f"raw cache commit failed: {exc}")

        # Raw eviction is prune_raw_cache's job, not ours — called, not reimplemented, so there is
        # one answer to "is this dataset still wanted". `skip` is what stops it deleting the input
        # of a running search, which it has no way to know about on its own.
        try:
            report["raw_cache"] = modal_runner.prune_raw_cache.remote(
                max_age_days=RAW_TTL_DAYS, skip=sorted(busy),
            )
        except Exception as exc:
            report.setdefault("problems", []).append(f"prune_raw_cache failed: {exc}")

    report["seconds"] = round(time.time() - started, 1)
    print(f"janitor: {json.dumps(report)}", flush=True)

    # The report lands on the volume so the history is readable without scrolling Modal's logs —
    # which is where the question "when did my results disappear" gets answered.
    try:
        log_path = pathlib.Path(VOLUME_MOUNT) / ".janitor" / "sweeps.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(json.dumps({"ts": time.time(), **report}) + "\n")
        vol.commit()
    except Exception as exc:
        print(f"janitor: WARNING could not record the sweep: {exc}", flush=True)

    return report


@app.function(image=image, schedule=SCHEDULE, volumes={VOLUME_MOUNT: vol, RAW_CACHE_MOUNT: raw_vol},
              cpu=1, memory=2048, timeout=3600)
def scheduled_sweep() -> dict:
    """The cron entry point. Thin on purpose: the schedule is the only thing it adds.

    Separate from sweep() so the sweep stays callable with dry_run=True by hand. A scheduled Modal
    function takes no arguments, so a single function could never be both.
    """
    return sweep.local(dry_run=False)


@app.function(image=image, volumes={VOLUME_MOUNT: vol, RAW_CACHE_MOUNT: raw_vol}, cpu=1, memory=2048)
def usage() -> dict:
    """What the two volumes currently hold, and which jobs the registry thinks exist.

    The other half of "help us manage our resources": the sweep says what it removed, this says
    what is there. Read-only, so it is safe to run whenever the bill looks wrong.
    """
    import modal_runner

    vol.reload()
    raw_vol.reload()

    raw_entries, busy = scan_raw_cache(pathlib.Path(RAW_CACHE_MOUNT))
    job_entries = scan_job_dirs(pathlib.Path(VOLUME_MOUNT), {})

    def total(kind: str) -> dict:
        picked = [e for e in raw_entries + job_entries if e.kind == kind]
        return {"count": len(picked), "gb": round(sum(e.size for e in picked) / 1024**3, 2)}

    # Raw files are not in `entries` (prune_raw_cache owns them), so they are measured directly —
    # and they are usually the largest thing on either volume, so omitting them would make this
    # report answer the cost question wrongly.
    raw_root = pathlib.Path(RAW_CACHE_MOUNT)
    raws = list(raw_root.glob("*/*.raw")) if raw_root.is_dir() else []
    raw_bytes = 0
    for p in raws:
        try:
            raw_bytes += p.stat().st_size
        except OSError:
            continue

    try:
        registry = list(modal_runner.jobs.items())
        job_states = {jid: modal_runner._call_status(jid) for _, jid in registry}
    except Exception as exc:
        registry, job_states = [], {"error": str(exc)}

    report = {
        "raw_cache": {"count": len(raws), "gb": round(raw_bytes / 1024**3, 2)},
        "maxquant_scratch": total("maxquant-scratch"),
        "job_dirs": total("job-dir"),
        "downloading_now": sorted(busy),
        "registry": [{"accession": acc, "job_id": jid, "status": job_states.get(jid)}
                     for acc, jid in registry],
    }
    print(json.dumps(report, indent=2), flush=True)
    return report


@app.local_entrypoint()
def main() -> None:
    """`modal run api/modal_janitor.py` — a dry run. Deleting takes an explicit ::sweep_now."""
    print(json.dumps(sweep.remote(dry_run=True), indent=2))


@app.local_entrypoint()
def sweep_now() -> None:
    """`modal run api/modal_janitor.py::sweep_now` — the same pass, actually deleting."""
    print(json.dumps(sweep.remote(dry_run=False), indent=2))


@app.local_entrypoint()
def usage_now() -> None:
    """`modal run api/modal_janitor.py::usage_now` — current volume usage, deletes nothing."""
    usage.remote()
