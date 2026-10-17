# napkin — pridemap

## Mistakes / gotchas
- 2026-10-17: `uv run api/main.py` auto-reloads on every save. Adding a `File(...)`/`UploadFile` route while python-multipart isn't installed made the reload crash (FastAPI checks at route registration), taking :8000 down until the next save. Guard new-dependency routes (`try: import multipart`) and import pandas/numpy lazily inside handlers so a half-finished edit never kills the running API. New PEP 723 deps only land after the user restarts the server.
- numpy views from `df[cols].to_numpy()` can be read-only; `np.array(..., copy=True)` before in-place NaN masking.

## What works
- Standalone API test without touching the live server: load api/main.py via importlib + `fastapi.testclient` under `uv run --with pandas --with numpy --with scipy --with openpyxl --with python-multipart --with httpx ...`.
- Odetta's `analysis/tese_lysoip_analysis.py` runs in ~3 s on the 41 MB batch-2 CSV with `--prefix Set1_Brain` (also Set2_Heart, Set3_Muscle present).
- 2026-10-17 (omics): OmicsDI `/ws/dataset/search` returns `title`/`description` (not `name`); `organism:"..."` field filter only hits MetaboLights-style records, so free-text `"Mus musculus" AND liver AND omics_type:"Transcriptomics"` per type is what finds GEO/ArrayExpress. `NOT omics_type:"Proteomics"` and `sortfield=citation_count&order=descending` work; `sortfield=download_count` 404s.
- api/omics.py is loaded by main.py at import: write new router code to the scratchpad, `py_compile`, then cp into api/ so a syntax error never takes :8000 down.
- 2026-10-17 (collections/saved): PRIDE v3 `/projects` ignores sortFields/sortDirection and lists oldest-first; `/projects/count` overshoots the real list by ~500, so the last few pages are empty. check_alerts.py walks back from the end and skips empty pages without counting them. `/search/projects` sorts but returns a different JSON shape (organismsPart, no identifiedPTMStrings) so build_db.row can't consume it.
- Built-in browser pane hidden => CSS animations never finish, so base-ui dialogs stay mounted with data-closed/data-ending-style after closing. Not a bug; drive tests with javascript_tool and skip closed dialogs.
- main.browse() can be called as a plain function (pass size= explicitly; its Query default is a Query object otherwise). collections_api.browse_matches does that for saved-search checks so totals match the Browse page.
