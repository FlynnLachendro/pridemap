# analysis/ — downstream analysis scripts

Drop R and Python scripts here (Odetta). For each script, a few lines at the top (or in this file) saying:

- **Input:** what table it expects (e.g. MaxQuant `proteinGroups.txt`, a CSV of protein × sample intensities, a Mascot export), and the columns it needs.
- **Groups:** how samples are assigned to group A / group B (a column? a separate sample sheet?).
- **Filters:** what gets removed before stats (contaminants, reverse hits, specific binders, min peptides…).
- **Output:** what it produces (table, plot, p-values).

A tiny example input file next to the script helps more than anything else.

The app will call these from an "Analyse" step: pick datasets → pull their result tables from PRIDE → run the script → show the output.

## What's here

- `tese_lysoip_analysis.py` (Odetta Antico) — LysoTag lyso‑IP analysis for PINK1 TESE mice: tag‑vs‑tag effect, input‑normalised enrichment, mock‑controlled dod, batch comparison. Input: a CSV export with `Genes`, `log2FC`, `-log10 P.Value` and intensity columns named `<prefix>_IP_LTKI.N` etc. Requires pandas, numpy, scipy.
- `tese_lysoip_compare_batches.py` (Odetta Antico) — compares per‑protein TESE effects between two such datasets (e.g. batch 1 vs batch 2): concordance, common hits, batch‑specific hits, scatter plot. Requires matplotlib too.

Both take file paths on the command line; see each file's docstring. Example input CSVs are not in the repo yet.
