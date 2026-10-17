"""Expert terminology → what it means for a search.

Researchers don't type the words that appear in dataset descriptions. "Immunopeptidomics, presented on all cells" means
HLA class I ligands pulled down with W6/32; "interactome" means AP-MS or proximity labelling; "diGly" means ubiquitin
remnant enrichment. Each concept below carries: regexes that detect it in a question, the phrases to search the
free text for (FTS alternatives), tag filters it implies (hard), tag values it favours (soft score boost), concepts
it argues against (demoted and flagged, not excluded), and a one-line note shown to the user.

Sources: MCP "MHC class I immunopeptidome: past, present and future" (2022); Thermo immunopeptidomics sample-prep
notes (W6/32 = HLA-A/B/C, L243 = HLA-DR); PMC reviews of ubiquitinomics (diGly remnant), proximity labelling
(BioID/TurboID/APEX), surfaceome and secretome; Kitata et al. 2023 (DIA/SWATH/diaPASEF); MCP targeted-acquisition
primer (PRM/SRM/MRM).
"""
import re

TERMS: dict[str, dict] = {
    # ---------------------------------------------------------------- antigen presentation
    "immunopeptidomics": {
        "label": "immunopeptidomics",
        "detect": r"immunopeptid|ligandom|\bHLA\b|\bMHC\b|\bHLA[\s-]*(peptid|ligand|bound|associated|eluted)|\bMHC[\s-]*(peptid|ligand|bound|associated|eluted)|eluted (peptide|ligand)|neoantigen|neoepitope|t.cell epitope|antigen presentation|presented (peptides?|on)|peptide[\s-]MHC|peptide[\s-]HLA",
        "fts": ["immunopeptidome", "immunopeptidomics", "immunopeptidomic", "ligandome", "HLA peptides", "MHC peptides", "HLA ligands", "MHC ligands",
                "HLA-associated peptides", "MHC-associated peptides", "HLA-bound peptides", "MHC-bound peptides", "eluted ligands", "eluted peptides",
                "neoantigen", "neoantigens", "neoepitope", "neoepitopes", "peptide-MHC", "peptide-HLA", "HLA immunoprecipitation", "MHC immunoprecipitation", "HLA peptidome", "MHC peptidome", "HLA-peptidome"],
        "boost": {"enrichment": ["ap_ms", "other"]},
        "note": "peptides eluted from HLA/MHC molecules (HLA in human, MHC generally; H-2 in mouse), pulled down with an anti-HLA antibody",
    },
    "mhc_class_i": {
        "label": "HLA/MHC class I",
        "detect": r"class[\s-]*[1I](?![I1])|\bHLA[\s-]*[ABC]\b|\bHLA[\s-]*I\b|\bMHC[\s-]*I\b|\bH-?2[KDL]b?\b|W6/32|all (nucleated )?cells|every (nucleated )?cell|CD8|cytotoxic T|beta.?2.?microglobulin|β2m",
        "fts": ["class I", "HLA-I", "MHC-I", "HLA class I", "MHC class I", "HLA-A", "HLA-B", "HLA-C", "HLA-ABC", "W6/32", "H-2Kb", "H-2Db", "H-2Kd", "H-2Dd", "HLA-I peptides", "MHC-I peptides"],
        "conflicts": ["mhc_class_ii"],
        "note": "class I = presented on all nucleated cells, 8–12-mers, HLA-A/B/C (W6/32 antibody), seen by CD8 T cells",
    },
    "mhc_class_ii": {
        "label": "HLA/MHC class II",
        "detect": r"class[\s-]*(II|2)\b|\bHLA[\s-]*D[RQP]|\bMHC[\s-]*II\b|\bI-A[bd]\b|\bI-E[bd]\b|L243|antigen.presenting cells|\bAPCs?\b|dendritic cell|CD4|helper T",
        "fts": ["class II", "HLA-II", "MHC-II", "HLA class II", "MHC class II", "HLA-DR", "HLA-DQ", "HLA-DP", "L243", "I-Ab", "HLA-II peptides", "MHC-II peptides"],
        "conflicts": ["mhc_class_i"],
        "note": "class II = professional antigen-presenting cells (dendritic cells, B cells, macrophages), 13–25-mers, HLA-DR/DQ/DP (L243 antibody), seen by CD4 T cells",
    },
    # ---------------------------------------------------------------- PTM enrichment
    "phospho": {
        "label": "phosphoproteomics",
        "detect": r"phospho|\bpTyr\b|\bpSer\b|\bpThr\b|\bTiO2\b|\bIMAC\b|Fe-?NTA|kinase substrate|signal(l)?ing cascade",
        "fts": ["phosphoproteome", "phosphoproteomics", "phosphopeptide", "phosphopeptides", "phosphorylation", "phosphosite", "phosphosites", "TiO2", "IMAC", "Fe-NTA", "pTyr", "phosphotyrosine"],
        "filters": {"enrichment": "phospho"},
        "note": "phosphopeptide enrichment (TiO2, IMAC, Fe-NTA); sees phosphosites, not total protein",
    },
    "ubiquitin": {
        "label": "ubiquitinomics",
        "detect": r"ubiquit|\bdi-?gly\b|K-?ε-?GG|K-?GG|UbiSite|\bTUBEs?\b|degradation targets|proteasom",
        "fts": ["ubiquitinome", "ubiquitylome", "ubiquitination", "ubiquitylation", "diGly", "di-Gly", "K-ε-GG", "K-GG", "ubiquitin remnant", "UbiSite", "TUBE", "tandem ubiquitin binding"],
        "filters": {"enrichment": "ubiquitin"},
        "note": "K-ε-GG (diGly) remnant antibody enrichment after trypsin; maps ubiquitination sites",
    },
    "glyco": {
        "label": "glycoproteomics",
        "detect": r"glyco|\bHILIC\b|lectin|sialyl|O-?GlcNAc|N-?linked|O-?linked",
        "fts": ["glycoproteome", "glycoproteomics", "glycopeptide", "glycopeptides", "glycosylation", "N-glycosylation", "O-glycosylation", "O-GlcNAc", "HILIC", "lectin", "sialylated", "glycan"],
        "filters": {"enrichment": "glyco"},
        "note": "glycopeptide enrichment (HILIC, lectin, hydrazide); N-linked, O-linked or O-GlcNAc",
    },
    "acetyl": {
        "label": "acetylomics",
        "detect": r"acetyl|\bKac\b",
        "fts": ["acetylome", "acetylation", "acetylated", "lysine acetylation", "Kac"],
        "filters": {"enrichment": "acetyl"},
        "note": "acetyl-lysine antibody enrichment",
    },
    "methyl": {
        "label": "methylation",
        "detect": r"methylom|methylat|\bKme\b|arginine methyl",
        "fts": ["methylome", "methylation", "methylated", "lysine methylation", "arginine methylation"],
        "boost": {"enrichment": ["other"]},
        "note": "methyl-lysine/arginine enrichment; tagged as 'other' enrichment",
    },
    "redox": {
        "label": "redox / cysteine proteomics",
        "detect": r"redox|S-?nitros|cysteine oxid|sulfenyl|thiol|iodoTMT|OxICAT",
        "fts": ["redox proteomics", "cysteine oxidation", "S-nitrosylation", "sulfenylation", "thiol", "iodoTMT", "OxICAT", "reactive cysteine"],
        "note": "cysteine-reactive chemistry; often tagged as 'other' enrichment",
    },
    # ---------------------------------------------------------------- sub-proteomes and interactions
    "interactome": {
        "label": "interactome / AP-MS",
        "detect": r"interactom|pull.?down|co-?ip\b|co-?immunoprecip|\bAP-?MS\b|affinity purif|TAP-?tag|protein.protein interaction|\bPPI\b|binding partners|interactors|BioPlex",
        "fts": ["interactome", "pull-down", "pulldown", "co-immunoprecipitation", "co-IP", "AP-MS", "affinity purification", "TAP-tag", "protein-protein interaction", "interactors", "binding partners", "BioPlex"],
        "filters": {"enrichment": "ap_ms"},
        "note": "affinity purification of a bait and its binding partners (AP-MS, co-IP, pull-down)",
    },
    "proximity": {
        "label": "proximity labelling",
        "detect": r"proximity.label|\bBioID\b|TurboID|miniTurbo|\bAPEX2?\b|biotin ligase|\bBirA\b",
        "fts": ["proximity labeling", "proximity labelling", "BioID", "TurboID", "miniTurbo", "APEX", "APEX2", "biotin ligase", "BirA", "biotinylated proteins", "streptavidin"],
        "filters": {"enrichment": "proximity_labelling"},
        "note": "BioID / TurboID / APEX biotinylate neighbours in situ; streptavidin pull-down",
    },
    "secretome": {
        "label": "secretome",
        "detect": r"secretom|secreted protein|conditioned medi",
        "fts": ["secretome", "secreted proteins", "conditioned media", "conditioned medium", "secretomics"],
        "filters": {"enrichment": "secretome"},
        "note": "proteins released into culture medium or body fluid",
    },
    "surfaceome": {
        "label": "surfaceome / membrane",
        "detect": r"surfaceom|cell.surface|surface protein|plasma membrane|membrane proteom|\bCSC\b|glycocapture|biotinylation of surface",
        "fts": ["surfaceome", "cell surface proteome", "cell surface capture", "surface proteins", "plasma membrane proteome", "membrane proteomics", "membrane proteins", "cell surface biotinylation"],
        "filters": {"enrichment": "membrane"},
        "note": "cell-surface or membrane enrichment (cell surface capture, biotinylation, membrane fractions)",
    },
    "extracellular_vesicles": {
        "label": "extracellular vesicles",
        "detect": r"exosom|extracellular vesicle|\bEVs?\b|microvesicle",
        "fts": ["exosome", "exosomes", "extracellular vesicles", "extracellular vesicle", "microvesicles", "EV proteome"],
        "note": "EV / exosome preparations; a sub-proteome, usually tagged 'other' enrichment",
    },
    "nterminomics": {
        "label": "N-terminomics / degradomics",
        "detect": r"N-?terminom|degradom|\bTAILS\b|protease substrate|neo-?N-?termin",
        "fts": ["N-terminomics", "degradomics", "TAILS", "protease substrates", "neo-N-termini", "N-terminal peptides"],
        "note": "protease substrate discovery by N-terminal peptide enrichment",
    },
    "crosslinking": {
        "label": "crosslinking MS",
        "detect": r"cross-?link|\bXL-?MS\b|\bDSSO\b|\bDSS\b|\bBS3\b|\bDSBU\b|structural proteom",
        "fts": ["crosslinking", "cross-linking", "XL-MS", "DSSO", "DSS", "BS3", "DSBU", "crosslinked peptides", "structural proteomics"],
        "note": "chemical crosslinking for structure and interfaces; needs a crosslink search engine",
    },
    "hdx": {
        "label": "HDX-MS",
        "detect": r"\bHDX\b|hydrogen.deuterium",
        "fts": ["HDX-MS", "hydrogen-deuterium exchange", "hydrogen deuterium exchange", "deuterium uptake"],
        "note": "conformational dynamics; not a bottom-up identification dataset",
    },
    "thermal": {
        "label": "thermal proteome profiling",
        "detect": r"thermal proteome|\bTPP\b|\bCETSA\b|thermal shift|melting curve",
        "fts": ["thermal proteome profiling", "TPP", "CETSA", "thermal shift", "melting curves", "target engagement"],
        "note": "drug target engagement via melting behaviour; usually TMT",
    },
    "lip": {
        "label": "limited proteolysis",
        "detect": r"\bLiP-?MS\b|limited proteolysis|proteinase K",
        "fts": ["limited proteolysis", "LiP-MS", "proteinase K", "structural changes"],
        "note": "LiP-MS: structural changes read out by partial digestion",
    },
    "single_cell": {
        "label": "single-cell proteomics",
        "detect": r"single.cell proteom|\bSCoPE\b|nanoPOTS|\bscp\b|single cells?\b",
        "fts": ["single-cell proteomics", "single cell proteomics", "SCoPE-MS", "SCoPE2", "nanoPOTS", "single cells", "low-input proteomics"],
        "note": "single-cell or low-input workflows (SCoPE-MS, nanoPOTS, carrier channels)",
    },
    "spatial": {
        "label": "spatial proteomics",
        "detect": r"spatial proteom|laser.capture|\bLCM\b|deep visual proteom|\bDVP\b|microdissect",
        "fts": ["spatial proteomics", "laser capture microdissection", "LCM", "deep visual proteomics", "microdissected", "regions of interest"],
        "note": "proteomics of microdissected regions; note Visium and other spatial transcriptomics are not in PRIDE",
    },
    "metaproteomics": {
        "label": "metaproteomics",
        "detect": r"metaproteom|microbiome|microbiota|gut bacteria",
        "fts": ["metaproteomics", "metaproteome", "microbiome", "microbiota", "gut bacteria"],
        "note": "mixed-species samples; database search needs a metagenome or multi-species FASTA",
    },
    "clinical_cohort": {
        "label": "patient cohort",
        "detect": r"patients?\b|cohort|clinical|biopsy|biopsies|\bFFPE\b|tumou?r|cancer|carcinoma",
        "fts": ["patients", "cohort", "clinical", "biopsies", "biopsy", "FFPE", "tumor", "tumour", "cancer", "carcinoma"],
        "note": "human clinical material; check consent/EGA access and FFPE handling",
    },
    "ffpe": {
        "label": "FFPE tissue",
        "detect": r"\bFFPE\b|formalin.fixed|paraffin",
        "fts": ["FFPE", "formalin-fixed", "paraffin-embedded", "archival tissue"],
        "note": "formalin-fixed paraffin-embedded archival tissue",
    },
    "organoid": {
        "label": "organoids / iPSC",
        "detect": r"organoid|\biPSCs?\b|induced pluripotent|stem cell.derived",
        "fts": ["organoid", "organoids", "iPSC", "iPSCs", "induced pluripotent stem cells", "stem cell-derived"],
        "note": "stem-cell derived models",
    },
    "xenograft": {
        "label": "PDX / xenograft",
        "detect": r"\bPDX\b|xenograft|patient.derived",
        "fts": ["PDX", "xenograft", "xenografts", "patient-derived"],
        "note": "mixed human/mouse material: use a combined FASTA when searching",
    },
    # ---------------------------------------------------------------- acquisition and quantification
    "dia": {
        "label": "DIA",
        "detect": r"\bDIA\b|data.independent|\bSWATH\b|diaPASEF|\bHRM\b|library.free|DIA-?NN|Spectronaut",
        "fts": ["DIA", "data-independent acquisition", "data independent acquisition", "SWATH", "diaPASEF", "DIA-NN", "Spectronaut", "library-free"],
        "filters": {"acquisition": "DIA"},
        "note": "data-independent acquisition: all precursors fragmented in windows; DIA-NN/Spectronaut, deeper for low-abundance peptides",
    },
    "dda": {
        "label": "DDA",
        "detect": r"\bDDA\b|data.dependent|shotgun|top-?\d+ method|ddaPASEF",
        "fts": ["DDA", "data-dependent acquisition", "data dependent acquisition", "shotgun proteomics", "ddaPASEF"],
        "filters": {"acquisition": "DDA"},
        "note": "data-dependent acquisition: the most intense precursors are picked; MaxQuant/FragPipe/Mascot",
    },
    "targeted": {
        "label": "targeted (PRM/SRM)",
        "detect": r"\bPRM\b|\bSRM\b|\bMRM\b|targeted (proteom|assay|MS)|parallel reaction|selected reaction|multiple reaction|Skyline|heavy peptide|AQUA peptide",
        "fts": ["PRM", "SRM", "MRM", "parallel reaction monitoring", "selected reaction monitoring", "multiple reaction monitoring", "targeted proteomics", "Skyline", "heavy peptides", "AQUA"],
        "filters": {"acquisition": "PRM_SRM"},
        "note": "a pre-defined target list only; not a discovery dataset",
    },
    "topdown": {
        "label": "top-down",
        "detect": r"top-?down|intact protein|proteoform",
        "fts": ["top-down", "top down proteomics", "intact proteins", "proteoforms", "intact mass"],
        "filters": {"acquisition": "top_down"},
        "note": "intact proteins, proteoform-level; bottom-up search tools don't apply",
    },
    "tmt": {
        "label": "TMT / isobaric",
        "detect": r"\bTMT|isobaric|tandem mass tag|multiplex",
        "fts": ["TMT", "TMTpro", "TMT10plex", "TMT11plex", "TMT16plex", "TMT18plex", "isobaric", "tandem mass tag", "multiplexed"],
        "filters": {"labelling": "TMT"},
        "note": "isobaric multiplexing: relative ratios between channels, not absolute amounts",
    },
    "itraq": {
        "label": "iTRAQ",
        "detect": r"iTRAQ",
        "fts": ["iTRAQ", "iTRAQ 4-plex", "iTRAQ 8-plex"],
        "filters": {"labelling": "iTRAQ"},
        "note": "isobaric multiplexing (older sibling of TMT)",
    },
    "silac": {
        "label": "SILAC",
        "detect": r"SILAC|stable isotope labell?ing by amino|heavy lysine|heavy arginine|pSILAC|NeuCode",
        "fts": ["SILAC", "stable isotope labeling by amino acids", "heavy lysine", "heavy arginine", "pulsed SILAC", "pSILAC", "NeuCode"],
        "filters": {"labelling": "SILAC"},
        "note": "metabolic labelling in culture; pairs of light/heavy peptides",
    },
    "label_free": {
        "label": "label-free",
        "detect": r"label.free|\bLFQ\b|MaxLFQ|\biBAQ\b|spectral count",
        "fts": ["label-free", "label free", "LFQ", "MaxLFQ", "iBAQ", "spectral counting"],
        "filters": {"labelling": "label_free"},
        "note": "no labels: intensities compared across runs (LFQ, iBAQ)",
    },
    "absolute_quant": {
        "label": "absolute quantification",
        "detect": r"absolute quant|copy number|copies per cell|\bAQUA\b|\bPSAQ\b|concentration",
        "fts": ["absolute quantification", "copy numbers", "copies per cell", "AQUA", "PSAQ", "protein concentration"],
        "note": "absolute amounts need spiked standards or proteomic-ruler style estimates",
    },
    # ---------------------------------------------------------------- instruments
    "timstof": {
        "label": "timsTOF / PASEF",
        "detect": r"timsTOF|PASEF|Bruker|ion mobility|\bTIMS\b",
        "fts": ["timsTOF", "timsTOF Pro", "timsTOF HT", "timsTOF SCP", "PASEF", "Bruker", "ion mobility", "TIMS"],
        "filters": {"instrument_family": "timstof"},
        "note": "Bruker trapped ion mobility instruments (PASEF)",
    },
    "orbitrap": {
        "label": "Orbitrap",
        "detect": r"Orbitrap|Q.?Exactive|Exploris|Lumos|Fusion|Astral|Eclipse|Velos|Elite|Thermo",
        "fts": ["Orbitrap", "Q Exactive", "Exploris", "Lumos", "Fusion", "Astral", "Eclipse", "Velos", "Elite"],
        "filters": {"instrument_family": "orbitrap"},
        "note": "Thermo Orbitrap family",
    },
    "qtof": {
        "label": "Q-TOF",
        "detect": r"Q-?TOF|TripleTOF|ZenoTOF|Synapt|Impact II|Sciex|Waters",
        "fts": ["Q-TOF", "TripleTOF", "ZenoTOF", "Synapt", "Impact II", "Sciex", "Waters"],
        "filters": {"instrument_family": "qtof"},
        "note": "Sciex / Waters / Bruker quadrupole time-of-flight",
    },
    # ---------------------------------------------------------------- sample kinds
    "biofluid": {
        "label": "biofluid",
        "detect": r"\bplasma\b|\bserum\b|\bCSF\b|cerebrospinal|\burine\b|saliva|sweat|tears|synovial|ascites|bronchoalveolar|\bBALF?\b|milk",
        "fts": ["plasma", "serum", "cerebrospinal fluid", "CSF", "urine", "saliva", "synovial fluid", "ascites", "bronchoalveolar lavage", "milk"],
        "filters": {"sample_type": "biofluid"},
        "note": "body fluids: dominated by a few abundant proteins unless depleted; low-abundance proteins are hard",
    },
    "cell_line_generic": {
        "label": "cell line",
        "detect": r"cell lines?\b|\bHeLa\b|HEK-?293|\bA549\b|MCF-?7|Jurkat|\bK562\b|U2OS|HepG2|THP-?1|\bRAW264",
        "fts": ["cell line", "HeLa", "HEK293", "A549", "MCF-7", "Jurkat", "K562", "U2OS", "HepG2", "THP-1", "RAW264.7"],
        "filters": {"sample_type": "cell_line"},
        "note": "immortalised cell lines",
    },
}

# longer phrases first so the detection regexes don't collide on short tokens
ORDER = list(TERMS)
_COMPILED = {k: re.compile(v["detect"], re.I) for k, v in TERMS.items()}


def detect(question: str) -> list[str]:
    """Concept ids mentioned in a question, in a stable order. 'presented on all cells' → mhc_class_i, etc."""
    q = question or ""
    found = [k for k in ORDER if _COMPILED[k].search(q)]
    # class I stated and class II only via the generic "APC"/"CD4" words? keep both; conflicts are handled by the caller
    return found


def parse_ids(s: str | None) -> list[str]:
    return [t.strip() for t in (s or "").split(",") if t.strip() in TERMS]


def fts_groups(ids: list[str]) -> list[list[str]]:
    """One AND-group per concept, each an OR of its phrases."""
    return [TERMS[i]["fts"] for i in ids if TERMS[i].get("fts")]


def avoid_for(ids: list[str]) -> list[str]:
    """Concepts that argue against the ones asked for (e.g. class II when class I was asked), unless also asked for."""
    out = []
    for i in ids:
        for c in TERMS[i].get("conflicts", []):
            if c not in ids and c not in out:
                out.append(c)
    return out


def hard_filters(ids: list[str]) -> dict:
    f = {}
    for i in ids:
        for k, v in TERMS[i].get("filters", {}).items():
            f.setdefault(k, v)
    return f


def boosts(ids: list[str]) -> dict[str, list[str]]:
    b: dict[str, list[str]] = {}
    for i in ids:
        for k, vals in TERMS[i].get("boost", {}).items():
            b.setdefault(k, []).extend(v for v in vals if v not in b.get(k, []))
    return b


def info(ids: list[str]) -> list[dict]:
    return [{"id": i, "label": TERMS[i]["label"], "note": TERMS[i]["note"], "conflicts": TERMS[i].get("conflicts", []),
             "phrases": TERMS[i].get("fts", []) if not TERMS[i].get("filters") else [], "filters": TERMS[i].get("filters", {})} for i in ids if i in TERMS]


def glossary_for_prompt() -> str:
    return "\n".join(f"- {k}: {v['label']} — {v['note']}" for k, v in TERMS.items())


def mentions(text: str, ids: list[str]) -> dict[str, str]:
    """Which of the given concepts a dataset's text mentions, with the matched phrase."""
    out = {}
    low = (text or "").lower()
    for i in ids:
        for ph in TERMS[i].get("fts", []):
            if ph.lower() in low:
                out[i] = ph
                break
    return out
