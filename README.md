# PRIDE Map

**Find the PRIDE proteomics dataset that could actually detect your protein.**

[PRIDE](https://www.ebi.ac.uk/pride/) holds 42,000+ mass‑spectrometry proteomics datasets. Whether one can answer your question depends on *how it was run*: enrichment (phospho? pull‑down? none?), DDA vs DIA, labelling, enzyme, fractionation, instrument. None of those are filterable fields in PRIDE; they sit in free‑text protocol paragraphs or in the paper's Methods section. So researchers get hundreds of hits, open twenty papers by hand, and still pick datasets that could never have seen their protein.

PRIDE Map has a model read each dataset's protocol text **and** the paper's Methods, emit a fixed set of tags (each with a confidence and the verbatim sentence it came from), and puts a plain‑language search, a drill‑down map, and an API over those tags.

Built at *Reinvent the Wheel: a tools‑for‑science hack* (Science Works / Inherent Labs, London, 16–18 Oct 2026) by Flynn Lachendro with Charlotte Ellison, Juri Westendorf and Ahmed Dawoud (University of Southampton). MIT.

## What it does

- **Ask** — "phosphorylated MFF in mouse liver, low abundance" → filters (organism, tissue, enrichment=phospho, low‑abundance intent) → ranked datasets, each with green *fits* and amber *watch* chips that expand to the sentence in the protocol or paper that justifies them. If nothing matches exactly it relaxes filters and says which.
- **Map** — a treemap of every tagged dataset by organism → sample type → enrichment → acquisition (or other orders). Click to drill, click a leaf to search it. "PRIDE in chunks."
- **Dataset** — a catalogue card: every tag with confidence bar, source (PRIDE metadata / paper / inferred) and quote; the raw PRIDE fields; the paper; "wrong?" on every tag writes a correction.
- **API** — FastAPI with OpenAPI at `/docs`: `/search`, `/ask`, `/map`, `/dataset/{acc}`, `/export.sdrf.tsv` (SDRF‑Proteomics‑compatible), so agents and pipelines can use it without the UI.

## The tags

Aligned to [SDRF‑Proteomics](https://github.com/bigbio/proteomics-sample-metadata) vocab where one exists.

| tag | values |
|---|---|
| organism / taxon_id | NCBI name + id |
| sample_type | tissue · cell_line · primary_cells · biofluid · organism_whole · environmental · other |
| tissue / cell_line / disease | free term or null |
| enrichment | none · phospho · ubiquitin · glyco · acetyl · ap_ms · proximity_labelling · secretome · membrane · other |
| acquisition | DDA · DIA · PRM_SRM · top_down · MALDI · other |
| labelling | label_free · TMT · iTRAQ · SILAC · dimethyl · other |
| enzyme | trypsin · trypsin_lysc · lysc · chymotrypsin · gluc · aspn · none · other |
| fractionation | none · high_ph_rp · scx · gel · other |
| instrument_family | orbitrap · timstof · qtof · triple_quad · fticr · ion_trap · other |
| depth_proteins | integer or null |
| search_engine | maxquant · proteome_discoverer · fragpipe · diann · spectronaut · mascot · other |

Every tag = `{value, confidence 0–1, source: metadata|paper|inferred|none, evidence: verbatim quote}`.

## Run it

Needs [uv](https://docs.astral.sh/uv/) and Node 20+. No venvs, no Postgres; everything is one SQLite file.

```bash
git clone https://github.com/FlynnLachendro/pridemap && cd pridemap

# 1. all PRIDE project metadata (no raw files; ~250 MB JSON, ~60 min) → data/pride.db
uv run crawl_pride.py && uv run build_db.py

# 2. a diverse slice to tag first, and their papers' Methods sections from Europe PMC
uv run select_slice.py --n 300
uv run fetch_methods.py --accessions data/slice.txt

# 3. tag with Claude (structured outputs). Put ANTHROPIC_API_KEY=... in .env or the environment.
uv run tag.py --accessions data/slice.txt           # ~$0.006 per dataset on Sonnet 5

# 4. serve
uv run api/main.py                                  # http://localhost:8000/docs
cd web && npm i && npm run dev                      # http://localhost:3000
```

Skip steps 1–3 to try it on the bundled demo slice (296 tagged datasets, their papers and evidence, in `data/demo/`): `uv run load_demo.py` then step 4.

`/ask` uses Claude to turn the question into filters when `ANTHROPIC_API_KEY` is set, and falls back to a rule‑based parser when it isn't, so the demo never depends on a key.

## Evaluate the tags

```bash
uv run make_gold_template.py --n 50     # gold/gold_50.csv with blank columns for human labels
# ... two bioinformaticians fill it in ...
uv run eval_tags.py                     # per-tag agreement, eval/report.json
```

## Layout

```
crawl_pride.py        PRIDE v3 REST → data/raw/page_*.json
build_db.py           → data/pride.db (projects + FTS5)
select_slice.py       diverse accessions to tag first
fetch_methods.py      Europe PMC → papers.methods_text
tag.py                Claude structured-output tagger (direct mode, resume-safe)
tag_import.py         import tag JSON produced elsewhere (same schema)
make_gold_template.py / eval_tags.py   human gold set + agreement report
load_demo.py          load bundled demo data without crawling
api/main.py           FastAPI
web/                  Next.js 16 + Tailwind + shadcn
PLAN.html, SPEC.md    the plan we built from
```

## Status and honesty

- Tagged so far: the 296‑dataset demo slice (human/mouse‑heavy, every vocab value represented). The full archive is `uv run tag.py` for a few hours, ~$230 on Sonnet 5 or half that on Haiku 4.5.
- The demo slice was tagged by Claude Sonnet 5 through Claude Code sessions using the exact prompt and schema in `tag.py` (an API key wasn't to hand overnight); `tag.py` produces the same output via the API.
- Methods sections were available for 59% of the slice (open‑access only). The rest were tagged from PRIDE metadata alone and say so.
- Tags are model output. Confidence and evidence are shown everywhere so you can judge; "wrong?" on any tag records a correction.

## Licence

MIT. PRIDE data is © EMBL‑EBI under its [terms of use](https://www.ebi.ac.uk/about/terms-of-use/).
