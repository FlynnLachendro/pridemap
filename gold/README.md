# Gold set

Fill the blank tag columns in gold_50.csv by reading the PRIDE page and the paper. Leave a cell empty if you can't tell. The *_model columns are what the model said; don't just copy them.

Allowed values:

- **sample_type**: tissue, cell_line, primary_cells, biofluid, organism_whole, environmental, other
- **enrichment**: none, phospho, ubiquitin, glyco, acetyl, ap_ms, proximity_labelling, secretome, membrane, other
- **acquisition**: DDA, DIA, PRM_SRM, top_down, MALDI, other
- **labelling**: label_free, TMT, iTRAQ, SILAC, dimethyl, other
- **enzyme**: trypsin, trypsin_lysc, lysc, chymotrypsin, gluc, aspn, none, other
- **fractionation**: none, high_ph_rp, scx, gel, other
- **instrument_family**: orbitrap, timstof, qtof, triple_quad, fticr, ion_trap, other
- **search_engine**: maxquant, proteome_discoverer, fragpipe, diann, spectronaut, mascot, other
- **organism**: NCBI scientific name
- **tissue / cell_line / disease**: free text
- **depth_proteins**: integer

Then: `uv run eval_tags.py`

## Corrections from users

`uv run apply_corrections.py` reads the corrections table (the "wrong?" button on every tag), writes `gold/from_corrections.csv`
in this same column layout (only the corrected tags filled), and re-tags each corrected dataset with the corrections pinned as
ground truth (model name gets a "(corrected)" suffix). `--dry` prints what it would do without calling the API or writing.
Then: `uv run eval_tags.py gold/from_corrections.csv`
