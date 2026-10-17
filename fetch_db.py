# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "zstandard"]
# ///
"""Get data/pride.db without crawling PRIDE: the full SQLite file from the GitHub release, falling back to the
bundled data/demo/*.jsonl.gz shards.

usage:
  uv run fetch_db.py                      # auto: release if reachable, else the demo shards
  uv run fetch_db.py --source release     # release only; fail loudly if it is unreachable
  uv run fetch_db.py --source demo        # bundled shards only (same as load_demo.py)
  uv run fetch_db.py --dry-run            # say what it would do, download nothing
  uv run fetch_db.py --force              # replace an existing data/pride.db

The release holds all 41,548 projects plus cached PRIDE file lists and raw PRIDE JSON (276 MB download,
1.36 GB on disk, ~2 min). The demo shards hold the 41,361 tagged datasets: nothing to download, ~20 s,
1.05 GB on disk. Either one is enough for ./dev.sh, select_slice.py, fetch_methods.py and the taggers.
"""
import argparse
import gzip
import os
import pathlib
import shutil
import sqlite3
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import load_demo  # noqa: E402
from build_db import DB  # noqa: E402

REPO = "FlynnLachendro/pridemap"
TAG = "v1.0-hackathon"      # "latest" for whatever the newest release is
ASSET = "pride.db.zst"
API = "https://api.github.com"


# ---------------------------------------------------------------- release path

def resolve_asset(repo: str, tag: str, name: str) -> tuple[str, int]:
    """Size and URL of the release asset, so we fail on a bad tag/name before downloading anything.

    A pinned tag resolves by HEAD on the public download URL: the GitHub API allows only 60 anonymous
    calls an hour, which a room full of fresh clones exhausts. Only "latest" needs the API.
    """
    if tag == "latest":
        return _resolve_via_api(repo, "releases/latest", name)
    url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
    r = httpx.head(url, timeout=30, follow_redirects=True)
    if r.status_code == 404:
        raise FileNotFoundError(f"{repo} release {tag} has no asset {name!r} (or no such release)")
    r.raise_for_status()
    return url, int(r.headers.get("content-length") or 0)


def _resolve_via_api(repo: str, path: str, name: str) -> tuple[str, int]:
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.get(f"{API}/repos/{repo}/{path}", headers=headers, timeout=30, follow_redirects=True)
    if r.status_code == 404:
        raise FileNotFoundError(f"no release at {path} in {repo}")
    r.raise_for_status()
    rel = r.json()
    for a in rel.get("assets") or []:
        if a["name"] == name:
            return a["browser_download_url"], a["size"]
    have = ", ".join(a["name"] for a in rel.get("assets") or []) or "none"
    raise FileNotFoundError(f"release {rel.get('tag_name')} has no asset {name!r} (has: {have})")


def download(url: str, dest: pathlib.Path, size: int) -> None:
    """Stream to dest, resuming a part-file from a dropped connection rather than starting over."""
    have = dest.stat().st_size if dest.exists() else 0
    if size > 0 and have == size:
        print(f"  reusing {dest.name} ({mb(size)})")
        return
    if size <= 0 or have > size:     # unknown content-length, or a stale longer part-file: start over
        have = 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    if have:
        print(f"  resuming at {mb(have)} of {mb(size)}")
    t0 = time.time()
    with httpx.stream("GET", url, headers=headers, timeout=60, follow_redirects=True) as r:
        r.raise_for_status()
        mode = "ab" if have and r.status_code == 206 else "wb"
        if mode == "wb":
            have = 0
        with open(dest, mode) as fh:
            done = have
            step = 0
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                if done - step >= max(size // 20, 25 << 20):
                    step = done
                    pct = f"{100 * done // size:3d}%  {mb(done)} / {mb(size)}" if size else mb(done)
                    print(f"  {pct}  {mb(done / max(time.time() - t0, 0.1))}/s", flush=True)
    got = dest.stat().st_size
    if size and got != size:
        raise IOError(f"{dest.name}: got {got} bytes, release says {size}")


def decompress(src: pathlib.Path, dest: pathlib.Path) -> None:
    """Expand the asset by its suffix: .zst, .gz, or an uncompressed .db."""
    if src.suffix == ".zst":
        import zstandard
        with open(src, "rb") as fin, open(dest, "wb") as fout:
            zstandard.ZstdDecompressor().copy_stream(fin, fout, write_size=1 << 20)
    elif src.suffix == ".gz":
        with gzip.open(src, "rb") as fin, open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout, 1 << 20)
    else:
        shutil.copyfile(src, dest)


def from_release(db: pathlib.Path, repo: str, tag: str, asset: str, keep: bool) -> None:
    url, size = resolve_asset(repo, tag, asset)
    print(f"release {tag}: {asset} ({mb(size)} download, ~1.3 GB expanded)")
    archive = db.parent / asset
    tmp = db.with_name(db.name + ".part")
    download(url, archive, size)
    print(f"  expanding → {db.name}")
    try:
        decompress(archive, tmp)
        counts = verify(tmp)
    except Exception as e:
        # A truncated or corrupt archive is full-size on disk, so a later run would happily "reuse" it.
        # Drop it and leave any existing database untouched.
        archive.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
        raise IOError(f"{asset} is corrupt ({type(e).__name__}: {e}); discarded it, re-run to download again") from e
    tmp.replace(db)
    for sidecar in ("-wal", "-shm"):            # left by opening the .part file to verify it
        tmp.with_name(tmp.name + sidecar).unlink(missing_ok=True)
    if not keep:
        archive.unlink(missing_ok=True)
    report(db, counts)


# ------------------------------------------------------------------ demo path

def from_demo(db: pathlib.Path) -> None:
    shards = sorted((db.parent / "demo").glob("*.jsonl*"))
    if not shards:
        raise FileNotFoundError(f"no shards in {db.parent / 'demo'}")
    print(f"demo shards: {len(shards)} files, {mb(sum(f.stat().st_size for f in shards))}")
    load_demo.main()          # prints its own per-table row counts and "ok → <db>"
    counts = verify(db)
    print(f"  {mb(db.stat().st_size)}  " + "  ".join(f"{k}={v}" for k, v in counts.items()))


# ----------------------------------------------------------------- shared bits

def mb(n: float) -> str:
    return f"{n / 1e6:.0f} MB"


def verify(db: pathlib.Path) -> dict[str, int]:
    """A download is only done when SQLite can read it and the tables we serve from are populated."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("projects", "papers", "tags", "tag_evidence")}
    except sqlite3.DatabaseError as e:
        raise IOError(f"{db} is not a usable SQLite database: {e}") from e
    finally:
        con.close()
    if not counts["projects"] or not counts["tags"]:
        raise IOError(f"{db}: projects={counts['projects']} tags={counts['tags']} — nothing to serve")
    return counts


def report(db: pathlib.Path, counts: dict[str, int]) -> None:
    print(f"ok → {db} ({mb(db.stat().st_size)})")
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.items()))


def ensure(source: str, db: pathlib.Path, repo: str, tag: str, asset: str,
           force: bool, keep: bool, dry_run: bool) -> None:
    if db.exists() and not force:
        print(f"{db} already exists — nothing to do (--force to replace)")
        report(db, verify(db))
        return
    if dry_run:
        plan = {"release": [repo], "demo": [str(db.parent / "demo")], "auto": [repo, str(db.parent / "demo")]}[source]
        print(f"--dry-run: source={source} → would try {' then '.join(plan)} → {db}")
        if source in ("release", "auto"):
            try:
                url, size = resolve_asset(repo, tag, asset)
                print(f"  release {tag} {asset}: {mb(size)} available at {url.split('?')[0]}")
            except Exception as e:
                print(f"  release unavailable: {e}" + ("  → would fall back to demo shards" if source == "auto" else ""))
        if source in ("demo", "auto"):
            shards = sorted((db.parent / "demo").glob("*.jsonl*"))
            print(f"  demo shards: {len(shards)} files, {mb(sum(f.stat().st_size for f in shards))}")
        return

    db.parent.mkdir(parents=True, exist_ok=True)
    if source == "demo":
        from_demo(db)
        return
    try:
        from_release(db, repo, tag, asset, keep)
    except Exception as e:
        if source == "release":
            raise
        print(f"release unavailable ({e}) — falling back to data/demo shards", file=sys.stderr)
        from_demo(db)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["auto", "release", "demo"], default="auto")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--tag", default=TAG, help='release tag, or "latest"')
    ap.add_argument("--asset", default=ASSET)
    ap.add_argument("--db", default=str(DB), help="target SQLite file (release source only)")
    ap.add_argument("--force", action="store_true", help="replace an existing database")
    ap.add_argument("--keep-archive", action="store_true", help="keep the downloaded .zst")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    target = pathlib.Path(a.db)
    if a.source != "release" and target != DB:
        # load_demo.py writes to build_db.DB, so only the release path can honour another target.
        sys.exit(f"--db needs --source release; the demo loader always writes {DB}")
    try:
        ensure(a.source, target, a.repo, a.tag, a.asset, a.force, a.keep_archive, a.dry_run)
    except KeyboardInterrupt:
        sys.exit("\ninterrupted — re-run to resume the download")
    except Exception as e:
        sys.exit(f"{type(e).__name__}: {e}")
