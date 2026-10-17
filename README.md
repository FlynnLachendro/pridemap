# PRIDE Map

**Find the PRIDE proteomics dataset that could actually detect your protein.**

[PRIDE](https://www.ebi.ac.uk/pride/) holds 42,000+ mass‑spectrometry proteomics datasets. Whether one can answer your question depends on *how it was run*: enrichment (phospho? pull‑down? none?), DDA vs DIA, labelling, enzyme, fractionation, instrument. None of those are filterable fields in PRIDE; they sit in free‑text protocol paragraphs or in the paper's Methods section. So researchers get hundreds of hits, open twenty papers by hand, and still pick datasets that could never have seen their protein.

PRIDE Map has a model read each dataset's protocol text **and** the paper's Methods, emit a fixed set of tags (each with a confidence and the verbatim sentence it came from), and puts a plain‑language search, a drill‑down map, and an API over those tags.

Built at *Reinvent the Wheel: a tools‑for‑science hack* (Science Works / Inherent Labs, London, 16–18 Oct 2026) by Flynn Lachendro with Charlotte Ellison, Juri Westendorf and Ahmed Dawoud (University of Southampton). MIT.

## What it does

The loop a researcher actually runs, with a human tick at each step:

- **Ask** — "phosphorylated MFF in mouse liver, low abundance" → filter chips (Claude, rule‑based fallback) → ranked datasets with green *fits* and amber *watch* chips that expand to the sentence in the protocol or paper that justifies them. Facet counts down the side. If nothing matches exactly it drops a filter, says which and why, and offers to put it back.
- **Map** — a treemap of every tagged dataset by organism → sample type → enrichment → acquisition (or other orders). Click to drill, click a leaf to search it.
- **Dataset card** — every tag with confidence bar, source (PRIDE metadata / paper / inferred) and quote; the raw PRIDE fields; the paper; "wrong?" on every tag records a correction.
- **Compare** — two or more datasets side by side with a verdict (comparable / with caution / not) and the exact method difference that breaks it. The check you need before group stats across datasets.
- **Reanalyse** — on each dataset: the files PRIDE holds (raw, peak lists, submitter results; sizes and links) and the database‑search parameters derived from the tags (tool, enzyme, fixed and variable mods, quantification, FDR, FASTA), each with the sentence it came from. We can't run MaxQuant/Mascot for you; this is the set‑up sheet.
- **Analyse** — upload a protein table, pick group A and B, get log2 fold change, Welch t‑test, Benjamini‑Hochberg FDR and a volcano plot. Odetta Antico's lyso‑IP pipeline (`analysis/`) runs as an option when the columns match.
- **Workflow** — Find → Confirm (a written case per shortlisted candidate, you tick it) → Set up the search → Analyse → Check.
- **Eval** — humans vs model on a 50‑dataset gold set; five models vs each other on 200 datasets (cost, speed, agreement).
- **MCP server + agent console** — `uv run api/mcp_server.py` exposes the same search / ask / dataset / compare / set‑up sheet / protein tools to Claude Desktop, Claude Code, Cursor or any MCP client (stdio, or `--http` for streamable HTTP on :8010). The `/mcp` page explains it and has a live console where Claude uses the tools in front of you, each call and result streamed.
- **Collections** — named, shareable shortlists with SDRF export and a download script for all the files. **Saved searches** — save a question, see how many new datasets match since (`check_alerts.py` for cron).
- **Cross‑omics links** (OmicsDI) on every dataset, a **quantms reanalysis kit** (SDRF + params + run.sh) on the set‑up sheet, and **corrections that feed the model** (`apply_corrections.py` re‑tags with human corrections as ground truth).
- **API** — FastAPI with OpenAPI at `/docs`: `/search`, `/ask`, `/map`, `/compare`, `/dataset/{acc}` (+ `/why`, `/reanalyse`, `/files.txt`), `/analyse/*`, `/export.sdrf.tsv`, `/eval`. Scripts and agents can use everything without the UI.

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

# 3. tag. Either Claude Sonnet 5 (ANTHROPIC_API_KEY in .env, ~$0.024 per dataset) ...
uv run tag.py --accessions data/slice.txt
#    ... or a cheap model through Hugging Face's router / OpenRouter (~$0.0015 per dataset, 90-93% agreement with Sonnet):
uv run tag_oai.py --provider hf --limit 20000      # HF_TOKENS=hf_a,hf_b in .env; rotates keys, resumes

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
tag_oai.py            same tagger via Hugging Face router / OpenRouter (DeepSeek V4.1 Flash), key rotation
tag_bench.py          benchmark several models on the same 200 datasets → eval/bench.json
make_gold_template.py / eval_tags.py   human gold set + agreement report
analysis/             Odetta's R/Python downstream scripts + example data
load_demo.py          load bundled demo data without crawling
api/main.py           FastAPI (+ collections_api.py, omics.py, agent_api.py routers)
api/tools.py          the agent tools, shared by the MCP server and the in-app console
api/mcp_server.py     MCP server (stdio / streamable HTTP)
demo/record_tour.mjs  Playwright: plays and records the in-app tour
web/                  Next.js 16 + Tailwind + shadcn
PLAN.html, SPEC.md    the plan we built from
```

## Status and honesty

- Tagged so far: 1,671 datasets (most‑downloaded first, every vocab value represented), demo bundle in `data/demo/`. The rest of the archive is ~$60 on DeepSeek V4.1 Flash and a few hours; see Eval for the model comparison.
- The first 291 were tagged by Claude Sonnet 5 through Claude Code sessions using the exact prompt and schema in `tag.py`; the rest by `tag.py` via the API. Every row records which model tagged it.
- Methods sections were available for 59% of the slice (open‑access only). The rest were tagged from PRIDE metadata alone and say so.
- Tags are model output. Confidence and evidence are shown everywhere so you can judge; "wrong?" on any tag records a correction.

## Licence

MIT. PRIDE data is © EMBL‑EBI under its [terms of use](https://www.ebi.ac.uk/about/terms-of-use/).
