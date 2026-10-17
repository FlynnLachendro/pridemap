# napkin — pridemap

## Mistakes / gotchas
- 2026-10-17: `uv run api/main.py` auto-reloads on every save. Adding a `File(...)`/`UploadFile` route while python-multipart isn't installed made the reload crash (FastAPI checks at route registration), taking :8000 down until the next save. Guard new-dependency routes (`try: import multipart`) and import pandas/numpy lazily inside handlers so a half-finished edit never kills the running API. New PEP 723 deps only land after the user restarts the server.
- numpy views from `df[cols].to_numpy()` can be read-only; `np.array(..., copy=True)` before in-place NaN masking.

## What works
- Standalone API test without touching the live server: load api/main.py via importlib + `fastapi.testclient` under `uv run --with pandas --with numpy --with scipy --with openpyxl --with python-multipart --with httpx ...`.
- Odetta's `analysis/tese_lysoip_analysis.py` runs in ~3 s on the 41 MB batch-2 CSV with `--prefix Set1_Brain` (also Set2_Heart, Set3_Muscle present).
