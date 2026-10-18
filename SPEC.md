# PRIDE Map — build spec

Hackathon: Reinvent the Wheel (Science Works / Inherent), 16–18 Oct 2026.
Team: Flynn Lachendro (build), Charlotte Ellison, Juri Westendorf, Ahmed Dawoud (Southampton bioinformatics), Mani Sarkar.
Submission: public GitHub repo + commit hash by Sun 10:00. MIT licence. No pre-hackathon commits.
Pitch: 3 min problem + demo, 2 min judge Q. Judged on Impact / Technical execution / Presentation (10 each).

## 1. Problem (one paragraph)
PRIDE (ebi.ac.uk/pride) holds 42,108 mass-spec proteomics datasets. Its search is keyword matching over
free-text protocols plus a few facets. The details that decide whether a dataset can answer a researcher's
question — enrichment (phospho/AP-MS/none), acquisition (DDA/DIA), labelling (label-free/TMT/SILAC), enzyme,
fractionation, instrument family, sample type — are not structured fields. They live in the
sampleProcessingProtocol prose or the paper's Methods section. Result: researchers get hundreds of hits,
open 20 papers by hand, and still pick datasets that could never have detected their protein.

## 2. Solution
1. Crawl all PRIDE project metadata (no raw files). ~250 MB JSON.
2. Fetch the open-access Methods section for each dataset's paper from Europe PMC.
3. A Claude tagger reads metadata + methods and emits a fixed tag schema with per-tag confidence,
   evidence quote and source. Structured outputs, so no JSON failures.
4. SQLite stores projects, papers, tags, evidence. FTS5 for text.
5. FastAPI exposes /search, /ask, /map, /dataset/{id}, /stats, /export.sdrf.tsv, OpenAPI at /docs.
6. Next.js UI: Ask (NL question → filter chips → ranked, explained results), Map (treemap drill-down),
   Dataset (catalogue card with evidence per tag + "report wrong tag").
7. Eval: 50 hand-labelled datasets (gold) vs model; per-tag agreement printed, shown in the UI and the pitch.

## 3. Tag schema (closed vocabularies)
| tag | values |
|---|---|
| organism | NCBI taxon name; taxon_id int |
| sample_type | tissue, cell_line, primary_cells, biofluid, organism_whole, environmental, other |
| tissue | free term from BTO/UBERON or null |
| cell_line | name or null |
| disease | term, "disease free", or null |
| enrichment | none, phospho, ubiquitin, glyco, acetyl, ap_ms, proximity_labelling, secretome, membrane, other |
| acquisition | DDA, DIA, PRM_SRM, top_down, MALDI, other |
| labelling | label_free, TMT, iTRAQ, SILAC, dimethyl, other |
| enzyme | trypsin, trypsin_lysc, lysc, chymotrypsin, gluc, aspn, none, other |
| fractionation | none, high_ph_rp, scx, gel, other |
| instrument_family | orbitrap, timstof, qtof, triple_quad, fticr, ion_trap, other |
| depth_proteins | int or null |
| search_engine | maxquant, proteome_discoverer, fragpipe, diann, spectronaut, mascot, other |
| summary_one_line | ≤140 chars |
Every tag except summary: {value, confidence 0–1, source: metadata|paper|inferred|none, evidence: quote}.

## 4. Data model (SQLite, data/pride.db)
projects(accession PK, title, description, sample_protocol, data_protocol, organisms, organism_parts,
  diseases, instruments, experiment_types, quant_methods, softwares, ptms, keywords, project_tags,
  pubmed_id, doi, submission_date, publication_date, downloads, organic_downloads, raw_json)
papers(pubmed_id PK, pmcid, is_open_access, methods_text, title, fetched_at)
tags(accession PK, <one column per tag value>, summary_one_line, model, tagged_at, input_tokens, output_tokens)
tag_evidence(accession, tag, value, confidence, source, evidence)
corrections(id, accession, tag, suggested_value, note, created_at)
projects_fts(accession, title, description, sample_protocol, data_protocol)  -- FTS5

## 5. API (FastAPI, api/main.py, port 8000)
GET /stats                               counts: projects, papers, tagged; per-tag value histograms
GET /search?organism=&tissue=&enrichment=&acquisition=&labelling=&enzyme=&fractionation=
            &instrument_family=&sample_type=&disease=&since=&q=&limit=&offset=
                                         ranked list with fit reasons
POST /ask {"q": "..."}                   Claude turns q → filters (+ free text) → same ranking; returns
                                         {filters, interpretation, results}
GET /map?by=organism,sample_type,enrichment,acquisition   nested counts for treemap
GET /dataset/{accession}                 project + paper + tags + evidence
POST /dataset/{accession}/correct        {tag, suggested_value, note}
GET /export.sdrf.tsv?<same filters>      SDRF-compatible TSV of tags
GET /docs                                OpenAPI

Ranking: hard filters first; score = 1.0*filter_match + 0.5*log1p(organic_downloads)/10
          + 0.3*recency(0..1 over 2010..now) + 0.4*(depth_proteins known ? min(depth/8000,1) : 0)
          + 0.3*avg(confidence of matched tags). "Watch" reasons: low-confidence tags, TMT when user
          wants absolute quant, DDA when user says "low abundance", phospho-enriched when user wants
          unmodified protein.

## 6. UI (Next.js 15 app router, web/, Tailwind + shadcn, Recharts)
/            Ask: textbox; sample questions; after submit: filter chips (editable, re-query on change),
             interpretation sentence, result cards (summary, fits chips, watch chips, expand → evidence,
             links to PRIDE + paper). Shows "tagged N of 42,108" honestly.
/map         Treemap, drill path breadcrumb, click leaf → / with filters prefilled.
/dataset/[acc] Card: every tag with confidence bar + evidence quote + source badge; raw PRIDE fields;
             paper link; "Report a wrong tag" dialog → POST /correct.
/about       The problem, the method, the eval table, licence, how to run.
API base from NEXT_PUBLIC_API_URL (default http://localhost:8000).

## 7. Scripts (uv run, PEP 723)
crawl_pride.py      → data/raw/page_NNNN.json           (done tonight)
build_db.py         → data/pride.db projects + fts        idempotent
fetch_methods.py    → papers table                        async, 8 concurrent, --limit, --accessions
select_slice.py     → data/slice.txt accessions to tag    default: top by organic downloads, human+mouse, mixed
tag.py              → tags + tag_evidence                 --mode direct|batch, --limit, --model, resume-safe
eval_tags.py        → prints per-tag agreement vs gold/gold_50.csv, writes eval/report.json
make_gold_template.py → gold/gold_50.csv with 50 accessions spanning the vocab, blank columns for C/J

## 8. Overnight order
1. repo (private), LICENSE, .gitignore, SPEC, PLAN, crawl script           commit
2. build_db.py on full crawl                                              commit
3. fetch_methods.py for the slice (300)                                   commit
4. tag.py direct mode on 300 with Sonnet 5, structured output             commit
5. api/ all endpoints, tests with httpx                                   commit
6. web/ Ask → Dataset → Map → About                                       commit
7. eval_tags.py + gold template; README with 3-command run; push          commit
8. If time: MCP server over the API; batch mode for the full archive.

## 9. Saturday hand-offs
C/J: fill gold/gold_50.csv (09:20–11:00). Review tag vocab at 09:00.
A: map drill order; three demo questions with expected answers.
F: everything else, per PLAN.html Level 4.

## 10. Non-goals
No raw-file download. No reanalysis. No user accounts. No Postgres.
