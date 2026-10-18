# PRIDE Map

**Find the PRIDE proteomics dataset that could actually detect your protein.**

[PRIDE](https://www.ebi.ac.uk/pride/) holds 42,000+ mass‑spectrometry proteomics datasets. Whether one can answer your question depends on *how it was run*: enrichment (phospho? pull‑down? none?), DDA vs DIA, labelling, enzyme, fractionation, instrument. None of those are filterable fields in PRIDE; they sit in free‑text protocol paragraphs or in the paper's Methods section. So researchers get hundreds of hits, open twenty papers by hand, and still pick datasets that could never have seen their protein.

PRIDE Map has a model read each dataset's protocol text **and** the paper's Methods, emit a fixed set of tags (each with a confidence and the verbatim sentence it came from), and puts a plain‑language search, a drill‑down map, and an API over those tags.

Built at *Reinvent the Wheel: a tools‑for‑science hack* (Science Works / Inherent Labs, London, 16–18 Oct 2026) by Flynn Lachendro with Charlotte Ellison, Juri Westendorf, Ahmed Dawoud (University of Southampton) and Mani Sarkar. MIT.

## Quick start

**Prerequisites:** [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node 20+.

```bash
git clone https://github.com/FlynnLachendro/pridemap && cd pridemap
```

### Option A — demo in 2 minutes (no API key, no crawl)

Uses the bundled data in `data/demo/` (every tagged dataset, 41k, gzipped shards; loaded on first run in ~20 s).

```bash
./dev.sh
```

That's it. `dev.sh` builds `data/pride.db` if it doesn't exist (via `uv run fetch_db.py`), then starts both servers.

Want the *complete* database — the ~190 untagged projects, the cached PRIDE file listings and the raw PRIDE
JSON per project — instead of the bundled shards? Point `dev.sh` at the GitHub release:

```bash
PRIDEMAP_DATA_SOURCE=release ./dev.sh     # 276 MB download, 1.3 GB on disk
```

See [Getting the data](#getting-the-data) for the standalone command and the other sources.

| Service | URL |
|---------|-----|
| Web UI  | http://localhost:3000 |
| API / OpenAPI docs | http://localhost:8000/docs |

`/ask` works without an API key (rule‑based filter parser). Add `ANTHROPIC_API_KEY` to a `.env` file in the repo root to enable Claude‑powered natural‑language search.

---

### Option B — full pipeline (tag your own slice)

**1. Environment**

Create `.env` in the repo root:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...   # for tag.py and /ask (Claude)
HF_TOKENS=hf_a,hf_b,...        # for tag_oai.py --provider hf (optional, rotated)
NEXT_PUBLIC_API_URL=http://localhost:8000   # web → API base (default if omitted)
```

**2. Get `data/pride.db`** — one of three ways, cheapest first:

```bash
uv run fetch_db.py                     # release if reachable, else the bundled shards  (recommended)
uv run fetch_db.py --source release    # full DB from the GitHub release: 276 MB, ~2 min
uv run crawl_pride.py && uv run build_db.py   # rebuild from PRIDE: ~250 MB JSON, ~60 min
```

Only the third one re‑reads PRIDE, and it's the only one that picks up datasets added since the release.
Everything below works the same whichever you pick.

Smoke‑check: `sqlite3 data/pride.db "select count(*) from projects;"` — ~42 000 from the release or a crawl,
41 361 from the bundled shards.

**3. Pick a slice and fetch paper Methods**

```bash
uv run select_slice.py --n 300   # → data/slice.txt
uv run fetch_methods.py --accessions data/slice.txt   # → papers table
```

Smoke‑check: `sqlite3 data/pride.db "select count(*) from papers where methods_text is not null;"` — expect ~60 % of the slice.

**4. Tag** (choose one)

```bash
# Claude Sonnet 5 — ~$0.024/dataset, ~$7 for 300
uv run tag.py --accessions data/slice.txt

# DeepSeek V4.1 Flash via HF router — ~$0.0015/dataset, 90–93 % agreement; resumes on restart
uv run tag_oai.py --provider hf --limit 300
```

Smoke‑check: `sqlite3 data/pride.db "select count(*) from tags;"` — should match the number of accessions in `data/slice.txt`.

**5. Serve**

```bash
uv run api/main.py &
cd web && npm i && npm run dev
```

---

### Getting the data

Everything the app serves lives in one SQLite file, `data/pride.db`. There are three ways to get it, and
`uv run fetch_db.py` owns the first two:

| Source | Command | Cost | What you get |
|---|---|---|---|
| **GitHub release** | `uv run fetch_db.py --source release` | 276 MB download, 1.3 GB on disk, ~2 min | All 41,548 projects (incl. the ~190 untagged), 41,361 tagged, 26,724 papers, 537,693 evidence rows, plus the cached PRIDE file listings, collections, saved searches and raw PRIDE JSON per project |
| **Bundled shards** | `uv run fetch_db.py --source demo` | no download, ~20 s | The 41,361 tagged datasets, 26,618 papers, 537,693 evidence rows — from `data/demo/*.jsonl.gz` (18 shards, 177 MB, in the repo) |
| **Crawl PRIDE** | `uv run crawl_pride.py && uv run build_db.py` | ~250 MB JSON, ~60 min | Current project metadata straight from PRIDE, including anything added since the release. No tags until you run a tagger |

`uv run fetch_db.py` with no arguments is `--source auto`: the release if it is reachable, the bundled shards
if it isn't, so a fresh clone works offline. It is safe to re-run — it leaves an existing `data/pride.db`
alone unless you pass `--force`.

```bash
uv run fetch_db.py --dry-run            # what it would do, downloads nothing
uv run fetch_db.py --tag latest         # newest release instead of the pinned v1.0-hackathon
uv run fetch_db.py --force              # replace the database you have
uv run fetch_db.py --keep-archive       # keep pride.db.zst after expanding it
```

The download resumes if the connection drops (just re-run it), the expanded file is checked with SQLite
before it replaces anything, and a corrupt archive is discarded rather than cached. `./dev.sh` calls this
script; `PRIDEMAP_DATA_SOURCE=release ./dev.sh` picks the release instead of the default shards.

`select_slice.py`, `fetch_methods.py`, the taggers and the API all read whichever database you built — no
flags to change.

---

### MCP server (Claude Desktop / Claude Code / Cursor)

```bash
uv run api/mcp_server.py           # stdio — add to your MCP client config
uv run api/mcp_server.py --http    # streamable HTTP on :8010
```

The `/mcp` page in the running web UI explains the available tools and has a live console.

---

## Running MaxQuant

PRIDE Map generates a ready-to-run search kit (scripts + MaxQuant XML configs) for any suitable
dataset. Three paths:

| Path | Guide |
|------|-------|
| **Local** — Docker on your macOS or Linux machine | [docs/maxquant-local.md](docs/maxquant-local.md) |
| **Cloud** — Devin runs MaxQuant on a Linux VM, exposed over a Cloudflare tunnel | [docs/maxquant-cloud-devin.md](docs/maxquant-cloud-devin.md) |
| **Modal** — serverless, laptop can shut down mid-run *(planned)* | [docs/maxquant-modal.md](docs/maxquant-modal.md) |

---

## Handover (Mani, Sat 17 Oct 17:00 → Sun pitch)

- **[HANDOVER.html](HANDOVER.html)** — everything: run it in 15 minutes, repo map, pipeline, API, UI, MCP, the tour, gotchas, numbers, the pitch script and judge Q&A, submission checklist.
- **[mani-claude-handover.md](mani-claude-handover.md)** — the same for an AI assistant; point Claude Code at it first.
- Data: `uv run fetch_db.py` gets you `data/pride.db` either way — the full `pride.db.zst` from the [v1.0-hackathon release](https://github.com/FlynnLachendro/pridemap/releases/tag/v1.0-hackathon) (which also has the backup tour video), or the `data/demo/*.jsonl.gz` shards in the repo if you're offline. `./dev.sh` runs it for you.
- `.env` (API keys) comes from Flynn directly, never via the repo.

## What it does

The loop a researcher actually runs, with a human tick at each step:

- **Ask** — "phosphorylated MFF in mouse liver, low abundance" → filter chips (Claude, rule‑based fallback) → ranked datasets with green *fits* and amber *watch* chips that expand to the sentence in the protocol or paper that justifies them. Facet counts down the side. If nothing matches exactly it drops a filter, says which and why, and offers to put it back.
- **Expert terminology** (`api/terms.py`) — ~45 concepts researchers actually say, mapped to the words authors write: *immunopeptidomics* = HLA/MHC ligandome, eluted peptides, W6/32; *presented on all cells* = class I (HLA‑A/B/C), not class II (HLA‑DR, L243, antigen‑presenting cells); *interactome* = AP‑MS / co‑IP / pull‑down; *diGly* = ubiquitin remnant; DIA/SWATH/diaPASEF, TMT/iTRAQ/SILAC/LFQ, timsTOF/Orbitrap… Each concept adds phrase groups to the full‑text search, a tag filter where one is implied, a ranking boost where only favoured, and flags datasets that mention the conflicting concept ("also mentions class II"). The Ask card shows every term it understood, what it searched for, and lets you drop or include them. Methods the question didn't name (e.g. "validate" ≠ targeted) become ranking preferences, never filters.
- **Map** — a treemap of every tagged dataset by organism → sample type → enrichment → acquisition (or other orders). Click to drill, click a leaf to search it.
- **Dataset card** — every tag with confidence bar, source (PRIDE metadata / paper / inferred) and quote; the raw PRIDE fields; the paper; "wrong?" on every tag records a correction.
- **Compare** — two or more datasets side by side with a verdict (comparable / with caution / not) and the exact method difference that breaks it. The check you need before group stats across datasets.
- **Reanalyse** — on each dataset: the files PRIDE holds (raw, peak lists, submitter results; sizes and links) and the database‑search parameters derived from the tags (tool, enzyme, fixed and variable mods, quantification, FDR, FASTA), each with the sentence it came from. We can't run MaxQuant/Mascot for you; this is the set‑up sheet.
- **Analyse** — start from a confirmed PRIDE dataset (it lists the submitter's result tables on PRIDE: MaxQuant proteinGroups, DIA‑NN reports, Spectronaut/FragPipe tables, even inside a zip, and opens one straight from the FTP) or upload your own; pick group A and B, get log2 fold change, Welch t‑test, Benjamini‑Hochberg FDR and a volcano plot. Odetta Antico's lyso‑IP pipeline (`analysis/`) runs as an option when the columns match. If PRIDE only holds raw files it says so and points at the set‑up sheet.
- **Workflow** — Find → Confirm (a written case per shortlisted candidate, you tick it) → Set up the search → Analyse → Check. The same five steps sit as a rail at the top of every page, lit by where you are and ticked by what you've done (switch it off in the sidebar).
- **Eval** — humans vs model on a 50‑dataset gold set; five models vs each other on 200 datasets (cost, speed, agreement), with a plain explanation of what each number is and isn't (agreement is with Claude Sonnet 5, not with human truth).
- **Take the tour** — a 25‑step guided run of the whole loop (it types, clicks, shortlists, saves and runs the agent for you); `demo/record_tour.mjs` records it as a video.
- **MCP server + agent console** — `uv run api/mcp_server.py` exposes the same search / ask / dataset / compare / set‑up sheet / protein tools to Claude Desktop, Claude Code, Cursor or any MCP client (stdio, or `--http` for streamable HTTP on :8010). The `/mcp` page explains it and has a live console where Claude uses the tools in front of you, each call and result streamed.
- **Collections** — named, shareable shortlists with SDRF export and a download script for all the files. Add from the folder button on any result card or dataset page, name your shortlist in the bar at the bottom, or start one empty from the sidebar. **Saved searches** — save a question, see how many new datasets match since (`check_alerts.py` for cron).
- **Cross‑omics links** (OmicsDI) on every dataset, a **quantms reanalysis kit** (SDRF + params + run.sh) on the set‑up sheet, and **corrections that feed the model** (`apply_corrections.py` re‑tags with human corrections as ground truth).
- **API** — FastAPI with OpenAPI at `/docs`: `/search`, `/ask`, `/browse`, `/terms`, `/map`, `/compare`, `/dataset/{acc}` (+ `/why`, `/reanalyse`, `/results`, `/files.txt`), `/analyse/*` (incl. `/analyse/from_pride`), `/export.sdrf.tsv`, `/eval`. Scripts and agents can use everything without the UI.

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

# 1. data/pride.db: the GitHub release (276 MB, ~2 min), else the bundled shards (no download, ~20 s)
uv run fetch_db.py
#    ... or rebuild it from PRIDE yourself (~250 MB JSON, ~60 min), which also picks up new datasets:
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

Skip steps 1–3 with `uv run fetch_db.py`: it pulls the complete `pride.db.zst` (41,548 projects) from the
GitHub release, or loads the bundled `data/demo/*.jsonl.gz` shards (41,361 tagged datasets, their papers'
Methods text, tags and 537,693 evidence rows, 177 MB in the repo) when the release is out of reach.
`./dev.sh` does this on first run and starts both servers. See [Getting the data](#getting-the-data).

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
fetch_db.py           data/pride.db from the GitHub release, falling back to data/demo (used by dev.sh)
load_demo.py          load the bundled data/demo shards without crawling
api/main.py           FastAPI (+ collections_api.py, omics.py, agent_api.py routers)
api/terms.py          expert terminology map (concept → phrases, implied filters, boosts, conflicts)
api/tools.py          the agent tools, shared by the MCP server and the in-app console
api/mcp_server.py     MCP server (stdio / streamable HTTP)
demo/record_tour.mjs  Playwright: plays and records the in-app tour
web/                  Next.js 16 + Tailwind + shadcn
PLAN.html, SPEC.md    the plan we built from
HANDOVER.html         the full handover (run, architecture, pitch, gotchas); mani-claude-handover.md is the same for an AI assistant
```

## Status and honesty

- Tagged: 41,361 of 41,548 datasets (1,661 by Claude Sonnet 5, the rest by DeepSeek V4.1 Flash through Hugging Face's router, ~$33 + ~$50); ~180 failed the model call and ~800 were added to PRIDE after the crawl. The bundle in `data/demo/` (gzipped shards) carries every tagged dataset; the full SQLite file is attached to the GitHub release as `pride.db.zst`. `uv run fetch_db.py` installs either one. See Eval for the model comparison.
- The first 291 were tagged by Claude Sonnet 5 through Claude Code sessions using the exact prompt and schema in `tag.py`; every row records which model tagged it.
- Methods sections were available for ~64% of datasets (open‑access only). The rest were tagged from PRIDE metadata alone and say so.
- Tags are model output. Confidence and evidence are shown everywhere so you can judge; "wrong?" on any tag records a correction.

## Licence

MIT. PRIDE data is © EMBL‑EBI under its [terms of use](https://www.ebi.ac.uk/about/terms-of-use/).
