// The guided tour, second person: what you do, what you see. Steps are grouped by route; the Tour component
// runs the steps for the current route and hops to the next route with ?tour=<step index>.
export const TOUR_Q = "I want to validate phosphorylated MFF in mouse liver";
export const TOUR_A = "PXD055942";
export const TOUR_B = "PXD029247";  // mouse liver phospho, label-free DIA (visible after the DIA filter)
export const TOUR_COLLECTION = "Tour: mouse liver phospho";
export const TOUR_SAVED = "Tour: mouse liver phospho DIA";

export type TourStep = {
  route: string;                 // pathname the step lives on; "collection" and "saved" resolve at run time
  el: string;                    // CSS selector to highlight (data-tour attributes)
  title: string;
  text: string;
  before?: string;               // action id to run before highlighting (see tour.tsx)
  next?: string;                 // href to navigate to after this step (cross-page); "@collection" / "@saved" resolve at run time
  side?: "top" | "bottom" | "left" | "right";
  align?: "start" | "center" | "end";
  scroll?: "center" | "start";
};

export const STEPS: TourStep[] = [
  // Browse
  { route: "/", el: '[data-tour="search"]', title: "Start with your question", text: `Type it as you'd say it: “${TOUR_Q}”. Keywords and accessions work too.`, before: "type-question" },
  { route: "/", el: '[data-tour="ask"]', title: "Your question becomes filters", text: "Mouse, liver, phospho. The protein card says MFF gives 10 tryptic peptides and sits on the mitochondrial outer membrane, so a phospho dataset could detect it." },
  { route: "/", el: '[data-tour="mentions"]', title: "Datasets that mention the protein", text: "Protein names aren't searchable fields in PRIDE. This searches UniProt's names and synonyms for MFF across titles, descriptions and protocols." },
  { route: "/", el: '[data-tour="facet-acquisition"]', title: "Narrow by how the data was made", text: "Each filter is a tag read from the protocol or the paper. You tick DIA because it sees more low‑abundance peptides than DDA.", before: "tick-dia", side: "right" },
  { route: "/", el: '[data-tour="result-0"]', title: "See why each dataset fits", text: "Green chips: why it fits. Amber: what to watch. Each opens to the sentence it came from.", scroll: "start" },
  { route: "/", el: '[data-tour="rail"]', title: "The loop follows you", text: "Find, confirm, set up, analyse, check. The rail at the top shows where you are and what you've done; the sidebar switch hides it.", scroll: "start", side: "bottom", align: "start" },
  { route: "/", el: '[data-tour="save-search"]', title: "Save the search", text: "Saved searches tell you how many new datasets have landed in PRIDE since, and a script can check on a schedule.", before: "save-search" },
  { route: "/", el: '[data-tour="shortlist-0"]', title: "Shortlist the candidates", text: "You shortlist two: a multi‑tissue TMT atlas and a label‑free liver phosphoproteome.", before: "shortlist-two", scroll: "start" },
  { route: "/", el: '[data-tour="compare-bar"]', title: "Your shortlist is an unsaved collection", text: "Name it to keep it, compare the two, or carry them into the workflow.", side: "top", next: `/compare?a=${TOUR_A},${TOUR_B}&tour=9` },
  // Compare
  { route: "/compare", el: '[data-tour="verdict"]', title: "Comparable or not", text: "A verdict, a match bar, and the exact method difference that would break a cross‑dataset comparison. Every tag side by side underneath.", before: "save-collection", next: "@collection" },
  // Collection
  { route: "collection", el: '[data-tour="collection"]', title: "Collections", text: "Your shortlist, named and shareable: compare all, open in the workflow, export SDRF, or download a script that fetches every file.", scroll: "start", next: `/flow?q=${encodeURIComponent(TOUR_Q)}&tour=11` },
  // Workflow
  { route: "/flow", el: '[data-tour="candidate-0"]', title: "You confirm each candidate", text: "A three‑sentence case for each shortlisted dataset, written against your question. Tick the ones you'll use.", before: "tick-candidate" },
  { route: "/flow", el: '[data-tour="setup-links"]', title: "Set up the database search", text: "Each confirmed dataset gets a set‑up sheet.", next: `/dataset/${TOUR_B}?tour=13#reanalyse` },
  // Dataset
  { route: `/dataset/${TOUR_B}`, el: '[data-tour="reanalyse"]', title: "What to type into the search software", text: "The files PRIDE holds and the parameters derived from the tags: DIA‑NN here, enzyme, fixed and variable modifications, quantification, FASTA. Each line cites its sentence.", scroll: "start" },
  { route: `/dataset/${TOUR_B}`, el: '[data-tour="quantms"]', title: "Or run it in the cloud", text: "A quantms kit: SDRF, parameters and a Nextflow command, generated from the same tags." },
  { route: `/dataset/${TOUR_B}`, el: '[data-tour="omics"]', title: "Other omics on the same system", text: "Transcriptomics, genomics and metabolomics datasets for the same organism and tissue, from OmicsDI.", side: "left", next: "/analyse?tour=16" },
  // Analyse
  { route: "/analyse", el: '[data-tour="loaded"]', title: "Analyse the protein table", text: "Your search gave you a protein table. You load it (here, an example lyso‑IP table): 9,312 rows, sample groups detected from the column names.", before: "load-example" },
  { route: "/analyse", el: '[data-tour="groups"]', title: "Pick group A and group B", text: "Lyso‑IP against mock beads. You run it.", before: "set-groups-run" },
  { route: "/analyse", el: '[data-tour="results"]', title: "Your first look", text: "8,223 proteins tested, 225 significant, volcano plot, sortable table.", scroll: "start", side: "top", align: "end" },
  { route: "/analyse", el: '[data-tour="report"]', title: "Take it to the paper", text: "One zip: a Methods paragraph from these exact parameters, every tested protein, the parameters, a script that reproduces the result, software versions.", next: "/map?tour=20" },
  // Map
  { route: "/map", el: '[data-tour="map"]', title: "The whole archive at a glance", text: "Every tagged dataset by organism, sample type, enrichment and acquisition. Click to drill in, click a leaf to search it.", next: "/eval?tour=21" },
  // Eval
  { route: "/eval", el: '[data-tour="bench"]', title: "Know how much to trust it", text: "Five models on the same 200 datasets: cost, speed, agreement. Humans vs model on a hand‑labelled set. Corrections you file feed the next run.", next: "/mcp?tour=22" },
  // MCP
  { route: "/mcp", el: '[data-tour="agent-log"]', title: "Let your own assistant do all of this", text: "Every step is also a tool an AI agent can call through MCP. Claude is answering a question right now: each tool call and its result appears as it happens.", before: "run-agent", scroll: "start", side: "top", align: "end" },
  { route: "/mcp", el: '[data-tour="agent-done"]', title: "The answer, with the same evidence", text: "Searched, read the dataset, compared. Your assistant can do this mid‑conversation instead of you clicking through PRIDE.", before: "wait-agent", side: "top", align: "end", next: "/tour/done" },
];
