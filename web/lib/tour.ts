// The guided tour: Charlotte's run-through, then Odetta's. Steps are grouped by route; the Tour component
// runs the steps for the current route and hops to the next route with ?tour=<step index>.
export const TOUR_Q = "I want to validate phosphorylated MFF in mouse liver";
export const TOUR_A = "PXD055942";
export const TOUR_B = "PXD029247";  // mouse liver phospho, label-free DIA (visible after the DIA filter)

export type TourStep = {
  route: string;                 // pathname the step lives on
  el: string;                    // CSS selector to highlight (data-tour attributes)
  title: string;
  text: string;
  before?: string;               // action id to run before highlighting (see tour.tsx)
  next?: string;                 // href to navigate to after this step (cross-page)
  side?: "top" | "bottom" | "left" | "right";
};

export const STEPS: TourStep[] = [
  { route: "/", el: '[data-tour="search"]', title: "Charlotte has a protein of interest", text: `She wants public data that could show MFF phosphorylated in mouse liver. She types it as a sentence: “${TOUR_Q}”.`, before: "type-question" },
  { route: "/", el: '[data-tour="ask"]', title: "The question becomes filters", text: "Mouse, liver, phospho. The protein card says MFF gives 10 tryptic peptides and lives on the mitochondrial outer membrane, so a phospho dataset could see it. Protein names aren’t in dataset text, so that part isn’t a filter." },
  { route: "/", el: '[data-tour="filters"]', title: "Narrow by how the data was made", text: "Every filter is a tag a model read from the protocol or the paper. Charlotte ticks DIA: it sees more low‑abundance peptides than DDA.", before: "tick-dia" },
  { route: "/", el: '[data-tour="result-0"]', title: "Why each dataset fits", text: "Green chips say why it fits, amber chips say what to watch. Each one opens to the sentence it came from." },
  { route: "/", el: '[data-tour="shortlist-0"]', title: "Shortlist candidates", text: "She shortlists two: a multi‑tissue TMT atlas and a label‑free liver phosphoproteome.", before: "shortlist-two" },
  { route: "/", el: '[data-tour="compare-bar"]', title: "Two candidates", text: "Before pooling them in one analysis: are they comparable?", next: `/compare?a=${TOUR_A},${TOUR_B}&tour=6`, side: "top" },
  { route: "/compare", el: '[data-tour="verdict"]', title: "Comparable or not", text: "The verdict names the method difference that would break a cross‑dataset comparison, with the tag values side by side underneath.", next: `/flow?q=${encodeURIComponent(TOUR_Q)}&tour=7` },
  { route: "/flow", el: '[data-tour="candidate-0"]', title: "The human confirms", text: "A three‑sentence case for each candidate, written against her question. She reads it and ticks the one she’ll use.", before: "tick-candidate" },
  { route: "/flow", el: '[data-tour="setup-links"]', title: "Set up the database search", text: "The set‑up sheet for the confirmed dataset.", next: `/dataset/${TOUR_B}?tour=9#reanalyse` },
  { route: `/dataset/${TOUR_B}`, el: '[data-tour="reanalyse"]', title: "What to type into the search software", text: "The files PRIDE holds, and the search parameters derived from the tags: DIA‑NN for this DIA dataset, enzyme, fixed and variable modifications, quantification, FASTA. Each line cites the sentence it came from.", next: "/analyse?tour=10" },
  { route: "/analyse", el: '[data-tour="example"]', title: "Odetta takes over", text: "The search gave her a protein table. She loads it.", before: "load-example" },
  { route: "/analyse", el: '[data-tour="groups"]', title: "Group A vs group B", text: "Lyso‑IP against mock beads. Run.", before: "set-groups-run" },
  { route: "/analyse", el: '[data-tour="results"]', title: "The first look", text: "8,223 proteins tested, 225 significant, volcano plot. The pandas step, in the browser." },
  { route: "/analyse", el: '[data-tour="report"]', title: "For the paper", text: "One zip: a Methods paragraph written from these exact parameters, every tested protein, the parameters, a script that reproduces the result, and software versions.", next: "/eval?tour=14" },
  { route: "/eval", el: '[data-tour="bench"]', title: "How much to trust it", text: "Five models on the same 200 datasets: cost, speed, agreement. And humans vs model on a hand‑labelled set." },
];
