# PRIDE Map — handover for Mani's Claude

You are picking up PRIDE Map from Flynn Lachendro's Claude Code session at ~17:00 on Saturday 17 Oct 2026. Mani (an AI engineer) is now driving, until the pitch at 10:15–12:15 on Sunday 18 Oct 2026. Submission (public repo URL + commit hash) is due **Sunday 10:00**. Read this whole file before touching anything. The human‑readable version with more context is `HANDOVER.html` (open it in a browser).

## 1. What this is, in five sentences

PRIDE (EBI's proteomics archive) holds 41,548 mass‑spectrometry datasets, but whether a dataset can answer a researcher's question depends on *how it was run* (enrichment, DDA vs DIA, labelling, enzyme, fractionation, instrument), and none of that is a structured field in PRIDE. PRIDE Map had an LLM read each dataset's protocol text plus the paper's open‑access Methods section and emit 14 closed‑vocabulary tags, each with a confidence, a source and the verbatim sentence it came from. A FastAPI + SQLite backend and a Next.js front end put a PRIDE‑style browse with method filters, plain‑English questions, a treemap, comparability verdicts, database‑search set‑up sheets, a downstream analysis step, evaluation pages, collections, saved searches and an MCP server over those tags. Everything the UI does is also an HTTP endpoint and an MCP tool. It was built from zero during the hackathon (first commit Friday night); 27,063 of 41,548 datasets are tagged as of this handover and the tagger is still running.

## 2. Hard constraints (do not break these)

- **Repo:** https://github.com/FlynnLachendro/pridemap (public, MIT). Submission is the repo URL + a commit hash. No pre‑hackathon commits exist and none may be added. Keep pushing to `main`.
- **Secrets:** `.env` holds `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `HF_TOKENS`, `HF_TOKENS2`. It is git‑ignored. **Never print, echo, cat or commit key values.** Never redirect with `>` into `.env` (that wiped it once); append with `>>` or edit with a text editor. Flynn sends Mani the `.env` contents out of band (not via git, not via chat logs).
- **Package management:** `uv run <script>.py` for every Python script (PEP 723 inline deps, no venv, no pip). `npm` inside `web/` and `demo/`.
- **Git:** short single‑line commit messages, no `Co-Authored-By`, no multi‑paragraph bodies. Commit and push whenever something works. Don't force‑push.
- **No destructive ops** (`rm -rf`, dropping tables, recreating `data/pride.db`) unless there is no alternative and Mani agrees. The database took hours and ~$50 to build.
- **Style decisions already made by Flynn (don't revisit):** original teal palette, light mode only (no dark mode), sidebar layout, one obvious search box on the landing page, no explanatory blurbs/fluff copy, headings in Instrument Sans (not a heavy serif), body in Source Sans 3, mono in JetBrains Mono.
- **Tour datasets:** the guided tour depends on `PXD055942` and `PXD029247` existing in the DB and `PXD029247` surviving the DIA filter (`web/lib/tour.ts`). Don't re‑tag or delete them.

## 3. Getting it running on Mani's machine (15 minutes)

```bash
git clone https://github.com/FlynnLachendro/pridemap && cd pridemap
# paste the .env Flynn sends you into ./.env (4 lines, KEY=value). Without it: /ask falls back to a rule parser,
# the agent console on /mcp does not work, tagging does not work. Everything else runs.
./dev.sh          # first run: builds data/pride.db from data/demo/*.jsonl.gz (~20 s), installs web deps, starts API :8000 + web :3000

# want the complete DB (all 41,548 projects, PRIDE file listings, raw PRIDE JSON) instead of the shards?
PRIDEMAP_DATA_SOURCE=release ./dev.sh     # 276 MB download from the GitHub release, 1.3 GB on disk
```

- `./dev.sh` kills anything on ports 8000/3000, starts `uv run api/main.py` and `npm run dev` in `web/`, Ctrl‑C stops both.
- Needs `uv` (https://docs.astral.sh/uv/) and Node 20+. The API's first `/browse` is slow (~2 s) while a warmer thread fills the cache; after that the landing page is ~10 ms.
- **Three ways to get `data/pride.db`, all through `uv run fetch_db.py`** (`./dev.sh` runs it for you):
  - `--source demo` (the `./dev.sh` default): the bundle in `data/demo/` — 18 gzipped shards, 177 MB in the repo — holds every *tagged* dataset (41,361 projects, 26,618 papers' Methods text, the tags and 537,693 evidence rows). No download, ~20 s, 1.05 GB on disk. Missing only the ~190 untagged projects, the cached PRIDE file listings and the raw PRIDE JSON per project.
  - `--source release`: `pride.db.zst` off the [v1.0-hackathon release](https://github.com/FlynnLachendro/pridemap/releases/tag/v1.0-hackathon) — the whole 41,548-project SQLite file Flynn built. 276 MB download, 1.36 GB on disk, ~2 min. Resumes if the wifi drops; verified with SQLite before it replaces anything; a corrupt download is discarded, not cached.
  - no flag (`--source auto`): release if reachable, shards if not. Use this if you don't want to think about it.
  - Still want it from PRIDE itself (the only way to pick up datasets added since the release)? `uv run crawl_pride.py && uv run build_db.py` (~60 min, no keys needed), then a tagger.
- `uv run fetch_db.py --dry-run` tells you what it would do without downloading anything. It never touches an existing `data/pride.db` unless you pass `--force`.
- Ask Flynn for the latest `data/demo/` or `data/pride.db` (1.2 GB) when the tagging run finishes (see §7); or just `git pull`, Flynn will push the refreshed bundle.

Verify: http://localhost:3000 shows "Search PRIDE by how the data was made" with facet counts; http://localhost:8000/docs lists the API; click **▶ Take the tour** in the sidebar and let it run end to end (≈2.5 min, 25 steps, ends on `/tour/done`).

## 4. Repo map

```
crawl_pride.py        PRIDE v3 REST /projects?page=&pageSize=100 → data/raw/page_*.json (all metadata, no raw files)
build_db.py           raw JSON → data/pride.db (SQLite, WAL). SCHEMA lives here (projects, papers, tags, tag_evidence,
                      corrections, projects_fts FTS5, covering indexes). load_demo.py imports SCHEMA from it.
select_slice.py       picks a diverse slice of accessions to tag first (data/slice.txt)
fetch_methods.py      Europe PMC full-text XML → papers.methods_text (open access only, ~64% hit rate)
tag.py                THE tagger prompt + Pydantic schema + closed VOCAB + render()/load(). Claude Sonnet 5 via
                      structured outputs (client.messages.parse). Resume-safe. Its .env loader OVERRIDES the environment.
tag_oai.py            same prompt/schema through any OpenAI-compatible endpoint: --provider hf (Hugging Face router →
                      deepseek-ai/DeepSeek-V4.1-Flash:deepinfra, thinking off) or openrouter. Rotates HF_TOKENS, HF_TOKENS2, …
                      when one runs dry; retries once if a value is outside VOCAB; skips already-tagged accessions unless --force.
tag_import.py         import tag JSON produced elsewhere (same schema)
tag_bench.py          benchmark N models on the same 200 datasets via OpenRouter → eval/bench.json (shown on /eval)
make_gold_template.py gold/gold_50.csv with blank columns for human labels   eval_tags.py → eval/report.json (humans vs model)
apply_corrections.py  turns "wrong?" corrections into gold rows + re-tags those datasets with corrections pinned as truth
check_alerts.py       pulls the last N days of PRIDE projects and re-checks every saved search (for cron)
export_demo.py        every tagged dataset → data/demo/*.jsonl.gz shards (<40 MB each)   load_demo.py loads them
fetch_db.py           THE way to get data/pride.db without crawling: GitHub release asset (resumable, verified)
                      with the data/demo shards as fallback. --source auto|release|demo, --dry-run, --force.
                      Composes load_demo.main() for the demo path; dev.sh calls it (PRIDEMAP_DATA_SOURCE).
dev.sh                run API + web together
api/main.py           FastAPI app: /stats /vocab /search /ask /browse /map /compare /dataset/{acc} (+/why /reanalyse
                      /files.txt /methods /correct) /export.sdrf.tsv /eval /protein /analyse/* ; SCORE_SQL ranking,
                      fts_query(), facets() single pass + 60 s cache, _BROWSE_CACHE 120 s + startup warmer thread
api/collections_api.py  /collections CRUD + export.sdrf.tsv + download.sh + accessions.txt ; /saved CRUD + /check + /seen
api/omics.py          /dataset/{acc}/omics (OmicsDI, cached in DB) ; /dataset/{acc}/quantms.zip ; /corrections, /corrections/stats
api/terms.py          EXPERT TERMINOLOGY MAP: ~45 concepts (immunopeptidomics, class I/II, interactome, diGly, DIA, TMT, timsTOF, biofluid…) each with
                      detect regex, FTS phrases, implied hard filters, soft boosts, conflicts, note. detect()/info()/mentions()/glossary_for_prompt().
                      main.py: fts_expr() ANDs one OR-group per text-only concept; apply_terms() sets filters/terms/avoid/prefer; soften_unstated() turns
                      parser guesses about methods the question never named into `prefer` (ranking boost) instead of filters; search() adds bm25 (title x8)
                      + CASE boosts (boost params are PREPENDED to the WHERE params: SELECT placeholders bind first) + 'mentions X' fits / 'also mentions Y' watch.
api/tools.py          ONE list of agent tools (search_datasets, ask_question, get_dataset, compare_datasets, setup_sheet,
                      protein_info, why_candidate) + call() ; used by both the MCP server and the in-app agent
api/mcp_server.py     MCP server over api/tools.py: `uv run api/mcp_server.py` (stdio) or `--http` (streamable HTTP :8010/mcp).
                      mcp SDK 2.x: `from mcp.server.mcpserver import MCPServer`. Needs the API running (PRIDEMAP_API env to override).
api/agent_api.py      GET /mcp/tools ; POST /agent streams ndjson events (start, tool_call, tool_result, text, done, error)
                      from a manual Anthropic tool loop (Sonnet 5, effort low) → the console on /mcp
analysis/             Odetta Antico's lyso-IP scripts + example CSV (analysis/data). Wired into /analyse/odetta.
gold/                 gold_50.csv: 50 datasets, human columns still EMPTY (see §8)
eval/                 bench.json (5-model benchmark, committed, read by /eval) ; report.json (humans vs model, git-ignored
                      until the gold set is filled)
demo/record_tour.mjs  Playwright: runs the tour headless at 1440×900 and records demo/videos/*.webm (backup video)
web/                  Next.js 16 (app router, Tailwind 4, shadcn/base-ui, driver.js, recharts). See §5.
PLAN.html             the plan/story document in 7 "levels" (also Flynn's Desktop copy). SPEC.md: the original build spec.
HANDOVER.html         the human handover (this file's big sibling). README.md: public readme.
```

Git‑ignored and therefore **not on a fresh clone**: `data/raw/`, `data/pride.db`, `*.log`, `.env`, `eval/report.json`, `data/inputs/`, `data/tags_json/`, `demo/videos/`, `demo/node_modules/`.

## 5. Front end map (`web/`)

- `app/layout.tsx` fonts + `<LoadingBar/>` + `<Shell>` (sidebar nav) + `<CompareBar/>` + `<Suspense><Tour/></Suspense>`.
- `app/globals.css` all tokens and component classes (`.card .btn .btn-primary .btn-sm .btn-ghost .input .pill .label .note .skeleton .spinner .enter .search-box .tour-status .loading-bar`), micro‑animations, `main > div { animation: route-in }`, `.tour-busy .driver-popover { opacity: 0 }`. Token names are historical (`--walnut` is the teal `#0E7C86`).
- Pages: `/` browse (`components/browse.tsx`: search box that detects keyword / accession / question, facets with counts, cards or table, sort, paging, untagged rows with a badge, Recent chips, Save search), `/dataset/[acc]` (collapsibles: tags with evidence, Reanalyse set‑up sheet + quantms kit, OmicsDI links, raw PRIDE fields, paper, "wrong?"), `/compare?a=X,Y` (`compare-view.tsx`: verdict, match bar, per‑tag rows), `/flow` (workflow: candidates with an LLM‑written case, confirm ticks, set‑up links), `/analyse` (upload or example table → groups → Welch t‑test/BH FDR → volcano → report bundle zip), `/map` (treemap), `/eval`, `/collections`, `/collections/[id]`, `/saved`, `/mcp` (explainer + live agent console + tool list + connect snippets), `/about`, `/tour/done`.
- **Workflow rail** (`components/workflow-rail.tsx`, state in `lib/progress-store.ts`): Find → Confirm → Set up → Analyse → Check at the top of `<main>` on every page; current step from the route, done steps from localStorage (`pridemap.compare`, `pridemap.confirmed`, `pridemap.setup-seen` set by `reanalyse.tsx`, `pridemap.analysed` set by `analyse-view.tsx`, `pridemap.checked` set by `/eval`). Sidebar switch writes `pridemap.rail=off` to hide it. Tour step "The loop follows you" anchors on `data-tour="rail"`.
- **Collections entry points**: `components/add-to-collection.tsx` (folder button on result cards and the dataset page; dialog with membership ticks + create), sidebar sub‑list with counts and "+ New collection" (`CollectionDialog allowEmpty`), the shortlist bar is an "Unsaved collection" with an inline name field (`compare-bar.tsx`), collections list has inline rename and a find box. Shared client cache `lib/collections-store.ts` (module cache + `pridemap-collections-change` event; initial state is always null to avoid hydration mismatches). `/collections` list now returns `accessions`; `POST /collections` accepts an empty list.
- Sidebar: `NAV_GROUPS` (Find / Work / Yours / Trust) with lucide icons; classes `.nav-group .nav-link .nav-sub .nav-sub-link .switch` in globals.css. Landing hero band `.hero`. Empty states `components/empty-state.tsx` (`.empty`).
- Client state in localStorage: `pridemap.compare` (shortlist), `pridemap.confirmed`, `pridemap.setup-seen`, `pridemap.analysed`, `pridemap.checked`, `pridemap.rail`, `pridemap.history` (recent searches). Collections and saved searches are server‑side tables in `pride.db`.
- `lib/api.ts` has `API` (`NEXT_PUBLIC_API_URL`, default http://localhost:8000) and all types/fetchers.
- `lib/tour.ts` the 25 tour steps (route, `data-tour` selector, title, text, optional `before` action, `next` href, popover side/align, scroll mode). `components/tour.tsx` is the driver.js runner: reads `?tour=N`, runs the steps for the current route, performs `ACTIONS` (typing the question, ticking DIA, shortlisting, saving a search/collection via the API, confirming a candidate, loading the example table, running the analysis, running the agent), scrolls the **nearest scrollable ancestor** (the app scrolls inside `<main>`, not the window), hides the popover while an action runs (`body.tour-busy`), hops routes with `router.push(href?tour=N)`. `TourButton` clears localStorage and does a full page load of `/?tour=0`.
- Anchors are `data-tour="…"` attributes across components (search, ask, mentions, rail, facet‑acquisition, facet‑acquisition‑DIA, result‑0, save‑search, shortlist‑0, compare‑bar, verdict, collection, candidate‑0, confirm‑PXD…, why‑ready, setup‑links, reanalyse, quantms, omics, loaded, groups, group‑b, run, results, report, map, bench, agent‑run, agent‑log, agent‑done).

## 6. Things that bit us (so you don't repeat them)

1. **Anthropic structured outputs** reject nested/optional‑heavy schemas with "Schema is too complex" → every model passed to `messages.parse(output_format=…)` is flat with all fields required (`AskOut` in main.py, `Tags` in tag.py).
2. **SQLite "database is locked"** when the tagger, the paper fetcher and the API ran together → WAL mode plus `timeout=120` on every `sqlite3.connect`. Keep that on any new connection.
3. **FastAPI** route functions must not take `**kwargs` (422s).
4. **Next.js 16**: `cacheComponents` is incompatible with `force-dynamic` pages → it's off. The `react-hooks/set-state-in-effect` lint rule → use `setTimeout(…, 0)` inside effects or derive state.
5. **Hidden browser tabs throttle timers**, so the tour looks broken in a background/preview tab. Verify the tour with `node demo/record_tour.mjs` (Playwright, headless) and look at frames, not in a hidden tab.
6. **The tour scrolls `<main>`**, not `window`. `window.scrollTo` is a no‑op here.
7. **Browse/facet caches** (120 s / 60 s) mean newly loaded or re‑tagged data shows up after a restart of `api/main.py` or within two minutes.
8. **HF router**: the model id is `deepseek-ai/DeepSeek-V4.1-Flash:deepinfra`; pass `chat_template_kwargs: {thinking: false}` or it reasons for ages; a 402/429 on one token → `tag_oai.py` rotates to the next `HF_TOKENSn`.
9. **Anthropic key**: the one in `.env` is from Flynn's "Default" org (the other org has no credit). `tag.py`'s `.env` loader overrides shell env on purpose, because a stale `ANTHROPIC_API_KEY` in `~/.zshrc` 401'd.
10. **Dev server CSS watcher** occasionally misses a save of `globals.css` → `touch web/app/globals.css`.
11. **Impeccable** (design skill) is installed as a plugin (`~/.claude/plugins/marketplaces/impeccable`); its detector ran clean on the UI pass: `.../scripts/impeccable detect --json <files>`.
12. The browse response for the **landing page** is served from `_BROWSE_CACHE`; the warmer runs at startup. Don't add per‑request work above the cache.

## 7. The tagging run (still running on Flynn's laptop)

- Process: `caffeinate -i uv run tag_oai.py --provider hf --limit 40000 --workers 16`, log `tag_hf2.log` (one line per 50: `done/total fails=… ~$… min`). At handover: ~13.5k of the remaining 28k done, ~166/min, ~$15 spent of the HF credits, ETA ~90 min (≈18:45).
- It is resume‑safe: if it dies, rerun the same command; it skips accessions already in `tags`.
- When it finishes (Flynn will do this if he's around, otherwise Mani with the DB):
  ```bash
  uv run export_demo.py            # refresh data/demo/*.jsonl.gz (≈15 s)
  git add data/demo && git commit -m "data: full tagged archive" && git push
  ```
  then update the numbers in README.md "Status" and in HANDOVER.html §"Numbers".
- Cost so far: Sonnet 5 for the first 1,661 (~$33), DeepSeek V4.1 Flash for the rest (~$1.5 per 1,000; whole archive ≈ $60). Every `tags` row stores `model`.

## 7b. Saturday 16:00–17:00 additions (team feedback round)

- Search quality: `api/terms.py` + `/terms`; Ask card shows understood terms with notes, phrases searched, include/drop, and 'ranked up, not filtered' preferences; a staged progress list while the question runs (borrowed from the partner site *locus*). URL params `terms`, `avoid`, `prefer` carry it, so saved searches and the browse page keep it.
- Analyse from PRIDE: `GET /dataset/{acc}/results` (candidate tables; raw-only datasets get a note) and `POST /analyse/from_pride {accession, file_name}` (fetches from PRIDE FTP over https into `data/uploads/<ACC>_<file>.<ext>`, unzips the best table from archives, 400 MB cap). UI: `FromPride` panel at the top of `/analyse` (`?acc=PXD…` prefills), Workflow step 4 links `Analyse PXD… →` per confirmed dataset.
- Eval page: three explainer cards (what a tag is, what agreement means and against what, what is missing) + captions on both tables.
- Dataset page `BackLink`; sidebar collections list collapsible (`pridemap.side-collections`), count badge on the entry.
- Full data: tagging finished Saturday evening (41,361 of 41,548; ~180 model failures, ~800 projects newer than the crawl). `data/demo/*.jsonl.gz` carries all of it; `pride.db.zst` is a GitHub release asset with the untagged projects and caches too. `uv run fetch_db.py` installs either (or `zstd -d pride.db.zst -o data/pride.db` by hand).

## 8. Open work, in priority order

1. **Gold set → humans vs model number.** `gold/gold_50.csv` has 50 datasets with empty human columns. Charlotte Ellison and Juri Westendorf (the bioinformaticians) were going to fill it. When any rows are filled: `uv run eval_tags.py` → `eval/report.json` → the Eval page's "Humans vs model" section and the pitch's trust line. Without it the pitch uses the 5‑model benchmark only (which is fine, but a human number is better).
2. **Dry‑run the pitch** against the script in §10 and time it. Decide who speaks. Run the tour once on the demo laptop with Wi‑Fi off to confirm it works offline (everything is local except: UniProt protein card, Europe PMC links, OmicsDI panel, and the `/ask` LLM parse + agent console, which need internet + the key). The tour's `/ask` step falls back to the rule parser offline; the agent step will fail offline → have `demo/videos/*.webm` ready (record one on the demo laptop: `cd demo && npm i && npx playwright install chromium && node record_tour.mjs`).
3. **README "Status" numbers** are stale (says 1,671 tagged). Update when the run finishes.
4. Retry the ~180 tagging failures: `uv run tag_oai.py --provider hf --limit 1000` (resume-safe) once HF credit allows; re-crawl to pick up the ~800 projects PRIDE added since Friday (`uv run crawl_pride.py && uv run build_db.py`, then tag).
5. Nice‑to‑haves if time: a `/health` endpoint; email delivery for `check_alerts.py`; mobile layout polish; a one‑page "architecture" diagram in README.

## 9. Numbers you can quote

| thing | value |
|---|---|
| PRIDE projects crawled | 41,548 |
| tagged at handover | 27,063 (1,661 by Claude Sonnet 5, rest by DeepSeek V4.1 Flash; run continuing) |
| papers with Methods text | 26,724 of ~41.5k (open access only) |
| evidence rows | 351,923 (one per tag per dataset, with quote + confidence + source) |
| tags per dataset | 14 (organism, taxon_id, sample_type, tissue, cell_line, disease, enrichment, acquisition, labelling, enzyme, fractionation, instrument_family, depth_proteins, search_engine) + one‑line summary |
| benchmark (200 datasets, agreement with Sonnet 5 / $ per 1,000 / fails) | DeepSeek V4.1 Flash 93% / $1.47 / 6 · Haiku 5.5 90% / $1.33 / 1 · Kimi K2.6 92% / $4.29 / 30 · GPT‑5.4 mini 89% / $6.24 / 7 · Gemini 3.8 Flash 93% / $9.31 / 2 · Sonnet 5 reference $24 |
| landing page latency | ~10 ms cached, 10–400 ms filtered (SQL‑side ranking/paging, covering indexes) |
| tour | 25 steps, 10 pages, ≈2.5 min, fully automatic |
| MCP tools | 7 (search_datasets, ask_question, get_dataset, compare_datasets, setup_sheet, protein_info, why_candidate) |

## 10. The pitch (3 min demo + 2 min judge Q; judged Impact / Technical / Presentation, 10 each)

Problem (30 s, ideally Charlotte): "I needed a public dataset to check my protein. PRIDE search gave me 800 hits. I opened 20 papers. Three were usable, and I still didn't know which search settings to use."
Then either press **Take the tour** and talk over it, or drive by hand:
1. Browse: type "I want to validate phosphorylated MFF in mouse liver". Chips appear; the protein card says MFF gives 10 tryptic peptides, mitochondrial outer membrane. Open a green chip → the sentence from the paper. Tick DIA in the facet sidebar.
2. Shortlist two (PXD055942, PXD029247) → Compare: "Not directly comparable: TMT vs label‑free" with the match bar. "This is the check Odetta does by hand before any batch comparison."
3. Workflow: the written case per candidate, tick one, open its set‑up sheet: DIA‑NN, enzyme, mods, FASTA, the raw files, each line with its quote. "What you'd type into the search software." The quantms kit is one Nextflow command.
4. Analyse: load the example table, lyso‑IP vs mock, volcano, download the report bundle (Methods paragraph + results + reproduce.py).
5. Trust: Eval page, five models, cheap model within 3 points of Sonnet at 1/16 the cost, N of 41,548 tagged.
6. MCP: "Everything you just clicked, your assistant can do mid‑conversation with the same evidence" — show the agent console answering live (needs internet + key).
7. Close: repo, MIT, `/docs`, three commands to run.

Likely judge questions: *How accurate are the tags?* (benchmark + evidence quotes + confidence + corrections loop; gold set if filled). *Does it hallucinate?* (every tag carries the verbatim sentence; `source` says metadata/paper/inferred; "wrong?" button; inferred tags are flagged). *Can it run the reanalysis?* (no, MaxQuant/Mascot are desktop; it produces the set‑up sheet and a quantms/Nextflow kit). *Why not PRIDE's own search?* (keyword only over prose; none of these are fields). *Cost to do the whole archive?* (~$60 with DeepSeek, ~$1,000 with Sonnet). *What about datasets that contain protein X?* (PRIDE's protein‑evidence API was retired; we search UniProt synonyms over text and label it "mentions"; roadmap). *Licence of the data?* (EBI terms of use; we store metadata and open‑access Methods text only).

## 11. Team

Flynn Lachendro (built it; reachable by Mani directly), Charlotte Ellison (problem owner, protein ID / Mascot, writes the problem line), Juri Westendorf, Ahmed Dawoud (Southampton bioinformatics), Odetta Antico (Dundee, downstream analysis; her scripts in `analysis/`), Mani Sarkar (final build push, feature work and submission). Hackathon: *Reinvent the Wheel: a tools‑for‑science hack*, Science Works / Inherent Labs, Pillar VC, King's Cross.

## 12. How to verify you haven't broken anything

```bash
cd web && npx tsc --noEmit -p . && npx eslint .        # types + lint (clean at handover)
curl -s localhost:8000/stats | head -c 300             # API up, counts sane
curl -s 'localhost:8000/browse?acquisition=DIA&enrichment=phospho&organism=Mus%20musculus' | head -c 300
cd demo && node record_tour.mjs                        # full tour runs to /tour/done, video in demo/videos/
```
There is no pytest suite; the tour recording is the integration test. Last commit at handover: see `git log -1`.
