# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""Fetch the open-access Methods section for each dataset's paper from Europe PMC into papers table.

usage: uv run fetch_methods.py [--limit N] [--accessions data/slice.txt] [--concurrency 8]
Skips pubmed_ids already in papers. Non-OA papers are recorded with is_open_access=0 so we don't retry.
"""
import argparse
import asyncio
import datetime as dt
import pathlib
import re
import sqlite3
import xml.etree.ElementTree as ET

import httpx

ROOT = pathlib.Path(__file__).parent
DB = ROOT / "data" / "pride.db"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
METHODS_RE = re.compile(r"method|materials|experimental|procedure|star", re.I)
PROTEO_RE = re.compile(r"sample|mass spec|proteom|lc-ms|digest|peptide|chromatograph|acquisition|data analysis|database search", re.I)
MAX_CHARS = 14000


def sec_text(sec: ET.Element) -> str:
    parts = []
    for el in sec.iter():
        if el.tag in ("p", "title") and el.text is None and len(el) == 0:
            continue
        if el.tag in ("p", "title", "label"):
            parts.append("".join(el.itertext()).strip())
    return "\n".join(x for x in parts if x)


def extract_methods(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    body = root.find(".//body")
    if body is None:
        return ""
    chosen = []
    for sec in body.iter("sec"):
        t = sec.find("title")
        title = "".join(t.itertext()) if t is not None else ""
        if METHODS_RE.search(title):
            chosen.append(sec)
    if not chosen:
        for sec in body.iter("sec"):
            t = sec.find("title")
            title = "".join(t.itertext()) if t is not None else ""
            if PROTEO_RE.search(title):
                chosen.append(sec)
    # de-nest: drop secs that are descendants of another chosen sec
    ids = set(map(id, chosen))
    out = []
    for sec in chosen:
        anc = False
        for other in chosen:
            if other is not sec and any(c is sec for c in other.iter("sec")):
                anc = True
                break
        if not anc:
            out.append(sec)
    text = "\n\n".join(sec_text(s) for s in out)
    return text[:MAX_CHARS]


async def one(client: httpx.AsyncClient, sem: asyncio.Semaphore, pmid: int):
    async with sem:
        try:
            r = await client.get(f"{EPMC}/search", params={"query": f"EXT_ID:{pmid} AND SRC:MED", "format": "json", "resultType": "lite"})
            r.raise_for_status()
            res = r.json().get("resultList", {}).get("result", [])
            if not res:
                return pmid, None, 0, None, "", "not in europepmc"
            m = res[0]
            pmcid, oa, title = m.get("pmcid"), m.get("isOpenAccess") == "Y", m.get("title")
            if not (pmcid and oa):
                return pmid, pmcid, 0, title, "", None
            r2 = await client.get(f"{EPMC}/{pmcid}/fullTextXML")
            if r2.status_code != 200:
                return pmid, pmcid, 1, title, "", f"fulltext {r2.status_code}"
            return pmid, pmcid, 1, title, extract_methods(r2.text), None
        except Exception as e:
            return pmid, None, None, None, "", f"{type(e).__name__}: {e}"[:200]


async def main(limit, accessions, concurrency):
    con = sqlite3.connect(DB)
    q = "SELECT DISTINCT p.pubmed_id FROM projects p LEFT JOIN papers pp ON pp.pubmed_id=p.pubmed_id WHERE p.pubmed_id IS NOT NULL AND pp.pubmed_id IS NULL"
    params = ()
    if accessions:
        accs = [a.strip() for a in pathlib.Path(accessions).read_text().split() if a.strip()]
        q += f" AND p.accession IN ({','.join('?' * len(accs))})"
        params = tuple(accs)
    q += " ORDER BY p.organic_downloads DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    pmids = [r[0] for r in con.execute(q, params)]
    print(f"to fetch: {len(pmids)}")
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "pridemap/0.1 (hackathon; flynnlachendro@googlemail.com)"}) as client:
        tasks = [one(client, sem, p) for p in pmids]
        done = 0
        oa = 0
        for coro in asyncio.as_completed(tasks):
            pmid, pmcid, is_oa, title, methods, err = await coro
            con.execute(
                "INSERT OR REPLACE INTO papers VALUES (?,?,?,?,?,?,?)",
                (pmid, pmcid, is_oa, title, methods, dt.datetime.now(dt.UTC).isoformat(), err),
            )
            done += 1
            oa += 1 if methods else 0
            if done % 25 == 0:
                con.commit()
                print(f"{done}/{len(pmids)} with_methods={oa}", flush=True)
        con.commit()
    print(f"done {done} with_methods={oa}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--accessions", default=None)
    ap.add_argument("--concurrency", type=int, default=8)
    a = ap.parse_args()
    asyncio.run(main(a.limit, a.accessions, a.concurrency))
