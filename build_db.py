# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Flatten data/raw/page_*.json into data/pride.db (SQLite + FTS5). Idempotent: upserts by accession."""
import glob
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).parent
DB = ROOT / "data" / "pride.db"
RAW = ROOT / "data" / "raw"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  accession TEXT PRIMARY KEY,
  title TEXT, description TEXT, sample_protocol TEXT, data_protocol TEXT,
  organisms TEXT, organism_parts TEXT, diseases TEXT, instruments TEXT,
  experiment_types TEXT, quant_methods TEXT, softwares TEXT, ptms TEXT,
  keywords TEXT, project_tags TEXT, countries TEXT,
  pubmed_id INTEGER, doi TEXT, submission_date TEXT, publication_date TEXT,
  downloads INTEGER, organic_downloads INTEGER, submission_type TEXT, raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_pubmed ON projects(pubmed_id);
CREATE INDEX IF NOT EXISTS idx_projects_pubdate ON projects(publication_date);
CREATE INDEX IF NOT EXISTS idx_projects_cover ON projects(accession, organic_downloads, publication_date, pubmed_id);
CREATE INDEX IF NOT EXISTS idx_projects_untagged ON projects(organic_downloads DESC, accession, organisms, publication_date);
CREATE INDEX IF NOT EXISTS idx_projects_dl ON projects(organic_downloads);
CREATE TABLE IF NOT EXISTS papers (
  pubmed_id INTEGER PRIMARY KEY, pmcid TEXT, is_open_access INTEGER, title TEXT,
  methods_text TEXT, fetched_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS tags (
  accession TEXT PRIMARY KEY,
  organism TEXT, taxon_id INTEGER, sample_type TEXT, tissue TEXT, cell_line TEXT, disease TEXT,
  enrichment TEXT, acquisition TEXT, labelling TEXT, enzyme TEXT, fractionation TEXT,
  instrument_family TEXT, depth_proteins INTEGER, search_engine TEXT, summary_one_line TEXT,
  model TEXT, tagged_at TEXT, input_tokens INTEGER, output_tokens INTEGER, raw_json TEXT
);
CREATE TABLE IF NOT EXISTS tag_evidence (
  accession TEXT, tag TEXT, value TEXT, confidence REAL, source TEXT, evidence TEXT,
  PRIMARY KEY (accession, tag)
);
CREATE INDEX IF NOT EXISTS idx_ev_acc ON tag_evidence(accession);
CREATE INDEX IF NOT EXISTS idx_tags_enrichment ON tags(enrichment);
CREATE INDEX IF NOT EXISTS idx_tags_organism ON tags(organism);
CREATE INDEX IF NOT EXISTS idx_tags_sample ON tags(sample_type);
CREATE INDEX IF NOT EXISTS idx_tags_acq ON tags(acquisition);
CREATE INDEX IF NOT EXISTS idx_tags_lab ON tags(labelling);
CREATE INDEX IF NOT EXISTS idx_tags_enz ON tags(enzyme);
CREATE INDEX IF NOT EXISTS idx_tags_frac ON tags(fractionation);
CREATE INDEX IF NOT EXISTS idx_tags_inst ON tags(instrument_family);
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT, accession TEXT, tag TEXT, suggested_value TEXT,
  note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE VIRTUAL TABLE IF NOT EXISTS projects_fts USING fts5(
  accession UNINDEXED, title, description, sample_protocol, data_protocol, keywords,
  tokenize='porter unicode61'
);
"""


def names(p, key):
    return [x.get("name") or x.get("value") for x in p.get(key) or [] if (x.get("name") or x.get("value"))]


def first_pubmed(p):
    for r in p.get("references") or []:
        if r.get("pubmedID"):
            return int(r["pubmedID"])
    return None


def first_doi(p):
    for r in p.get("references") or []:
        if r.get("doi"):
            return r["doi"]
    return p.get("doi")


def row(p):
    return (
        p["accession"],
        p.get("title"),
        p.get("projectDescription"),
        p.get("sampleProcessingProtocol"),
        p.get("dataProcessingProtocol"),
        json.dumps(names(p, "organisms")),
        json.dumps(names(p, "organismParts")),
        json.dumps(names(p, "diseases")),
        json.dumps(names(p, "instruments")),
        json.dumps(names(p, "experimentTypes")),
        json.dumps(names(p, "quantificationMethods")),
        json.dumps(names(p, "softwares")),
        json.dumps(names(p, "identifiedPTMStrings")),
        json.dumps(p.get("keywords") or []),
        json.dumps(p.get("projectTags") or []),
        json.dumps(p.get("countries") or []),
        first_pubmed(p),
        first_doi(p),
        p.get("submissionDate"),
        p.get("publicationDate"),
        p.get("totalFileDownloads"),
        p.get("organicCount"),
        p.get("submissionType"),
        json.dumps(p),
    )


def main():
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    files = sorted(glob.glob(str(RAW / "page_*.json")))
    n = 0
    for f in files:
        try:
            page = json.load(open(f))
        except Exception as e:
            print(f"skip {f}: {e}", file=sys.stderr)
            continue
        rows = [row(p) for p in page if p.get("accession")]
        con.executemany(
            "INSERT OR REPLACE INTO projects VALUES (" + ",".join("?" * 24) + ")", rows
        )
        n += len(rows)
    # rebuild FTS from projects (cheap at this size)
    con.execute("DELETE FROM projects_fts")
    con.execute(
        "INSERT INTO projects_fts(accession,title,description,sample_protocol,data_protocol,keywords) "
        "SELECT accession,title,description,sample_protocol,data_protocol,keywords FROM projects"
    )
    con.commit()
    total = con.execute("SELECT count(*) FROM projects").fetchone()[0]
    with_pm = con.execute("SELECT count(*) FROM projects WHERE pubmed_id IS NOT NULL").fetchone()[0]
    print(f"pages={len(files)} inserted={n} projects={total} with_pubmed={with_pm}")


if __name__ == "__main__":
    main()
