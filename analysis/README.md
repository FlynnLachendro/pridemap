# analysis/ — downstream analysis scripts

Drop R and Python scripts here (Odetta). For each script, a few lines at the top (or in this file) saying:

- **Input:** what table it expects (e.g. MaxQuant `proteinGroups.txt`, a CSV of protein × sample intensities, a Mascot export), and the columns it needs.
- **Groups:** how samples are assigned to group A / group B (a column? a separate sample sheet?).
- **Filters:** what gets removed before stats (contaminants, reverse hits, specific binders, min peptides…).
- **Output:** what it produces (table, plot, p-values).

A tiny example input file next to the script helps more than anything else.

The app will call these from an "Analyse" step: pick datasets → pull their result tables from PRIDE → run the script → show the output.
