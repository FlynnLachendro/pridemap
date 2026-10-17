// The guided tour, second person: what you do, what you see. Steps are grouped by route; the Tour component
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
  { route: "/", el: '[data-tour="search"]', title: "Start with your question", text: `You have a protein of interest and want public data that could show it. Type it as you'd say it: “${TOUR_Q}”. Keywords and accessions work here too.`, before: "type-question" },
  { route: "/", el: '[data-tour="ask"]', title: "Your question becomes filters", text: "You see how it was read: mouse, liver, phospho. The protein card tells you MFF gives 10 tryptic peptides and sits on the mitochondrial outer membrane, so a phospho dataset could detect it. Protein names aren't in dataset text, so that part isn't a filter." },
  { route: "/", el: '[data-tour="filters"]', title: "Narrow by how the data was made", text: "Every filter is a tag read from the protocol or the paper, with the sentence behind it. You tick DIA because it catches more low‑abundance peptides than DDA.", before: "tick-dia" },
  { route: "/", el: '[data-tour="result-0"]', title: "See why each dataset fits", text: "Green chips say why a dataset fits your question, amber chips say what to watch. Click any chip and you get the sentence it came from." },
  { route: "/", el: '[data-tour="shortlist-0"]', title: "Shortlist the candidates", text: "You shortlist two: a multi‑tissue TMT atlas and a label‑free liver phosphoproteome.", before: "shortlist-two" },
  { route: "/", el: '[data-tour="compare-bar"]', title: "Your shortlist follows you", text: "From here you can compare them side by side or carry them into the workflow. First: can they be pooled in one analysis?", next: `/compare?a=${TOUR_A},${TOUR_B}&tour=6`, side: "top" },
  { route: "/compare", el: '[data-tour="verdict"]', title: "Comparable or not", text: "You get a verdict and the exact method difference that would break a cross‑dataset comparison, with every tag side by side underneath.", next: `/flow?q=${encodeURIComponent(TOUR_Q)}&tour=7` },
  { route: "/flow", el: '[data-tour="candidate-0"]', title: "You confirm each candidate", text: "A three‑sentence case for each shortlisted dataset, written against your question. You read it and tick the ones you'll actually use.", before: "tick-candidate" },
  { route: "/flow", el: '[data-tour="setup-links"]', title: "Set up the database search", text: "Each confirmed dataset gets a set‑up sheet.", next: `/dataset/${TOUR_B}?tour=9#reanalyse` },
  { route: `/dataset/${TOUR_B}`, el: '[data-tour="reanalyse"]', title: "What to type into the search software", text: "The files PRIDE holds, and the parameters derived from the tags: DIA‑NN for this DIA dataset, enzyme, fixed and variable modifications, quantification, FASTA. Each line cites the sentence it came from. The search itself runs on your machine.", next: "/analyse?tour=10" },
  { route: "/analyse", el: '[data-tour="example"]', title: "Analyse the protein table", text: "Your search gave you a protein table. You load it (here, an example lyso‑IP table).", before: "load-example" },
  { route: "/analyse", el: '[data-tour="groups"]', title: "Pick group A and group B", text: "Lyso‑IP against mock beads. You run it.", before: "set-groups-run" },
  { route: "/analyse", el: '[data-tour="results"]', title: "Your first look", text: "8,223 proteins tested, 225 significant, volcano plot, sortable table. The pandas step, without pandas." },
  { route: "/analyse", el: '[data-tour="report"]', title: "Take it to the paper", text: "One zip: a Methods paragraph written from these exact parameters, every tested protein, the parameters, a script that reproduces the result, and software versions.", next: "/eval?tour=14" },
  { route: "/eval", el: '[data-tour="bench"]', title: "Know how much to trust it", text: "Five models on the same 200 datasets: cost, speed, agreement. Humans vs model on a hand‑labelled set. Every tag in the app shows its confidence and evidence.", next: "/mcp?tour=15" },
  { route: "/mcp", el: '[data-tour="agent"]', title: "Let your own assistant do all of this", text: "Every step you just took is also a tool an AI agent can call through MCP. Here Claude is answering a question by searching, reading a dataset and comparing, live, with the same evidence you saw.", before: "run-agent", next: "/tour/done" },
];
