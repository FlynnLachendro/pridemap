export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Reason = { tag: string; text: string; confidence: number | null; evidence: string | null; source: string | null };
export type Result = {
  accession: string; title: string; summary: string | null; publication_date: string | null; downloads: number | null;
  pubmed_id: number | null; doi: string | null; pride_url: string;
  tags: Record<string, string | number | null>; fits: Reason[]; watch: Reason[]; score: number;
};
export type Filters = Record<string, string | number | boolean | null | undefined>;
export type Dropped = { key: string; value: string | number; why: string };
export type Facets = Record<string, [string, number][]>;
export type AskOut = { question: string; parser: string; interpretation: string; dropped?: Dropped[]; filters: Filters; total: number; results: Result[]; facets?: Facets };
export type Stats = { projects: number; papers_with_methods: number; tagged: number; corrections: number; histograms: Record<string, Record<string, number>> };
export type Evidence = { tag: string; value: string | null; confidence: number; source: string; evidence: string };
export type Dataset = {
  project: Record<string, unknown> & { accession: string; title: string; description: string; sample_protocol: string; data_protocol: string; publication_date: string; pubmed_id: number | null; organic_downloads: number | null };
  pride_url: string; paper: { pubmed_id: number; pmcid: string | null; is_open_access: number; title: string | null; methods_chars: number | null } | null;
  paper_url: string | null; tags: Record<string, string | number | null>; evidence: Evidence[]; tagged: boolean;
  corrections: { id: number; tag: string; suggested_value: string | null; note: string | null; created_at: string }[];
};
export type MapNode = { name: string; level?: string; value: number; children?: MapNode[] };

async function j<T>(r: Promise<Response>): Promise<T> {
  const res = await r;
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}
export const ask = (q: string) => j<AskOut>(fetch(`${API}/ask`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ q }) }));
export const search = (f: Filters) => {
  // returns results + facet counts for the current filter set
  const p = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => { if (v !== null && v !== undefined && v !== false && v !== "") p.set(k, String(v)); });
  return j<{ filters: Filters; total: number; results: Result[]; facets?: Facets }>(fetch(`${API}/search?${p}`));
};
export const stats = () => j<Stats>(fetch(`${API}/stats`, { next: { revalidate: 30 } }));
export const dataset = (acc: string) => j<Dataset>(fetch(`${API}/dataset/${acc}`, { cache: "no-store" }));
export const map = (by: string) => j<{ levels: string[]; tree: MapNode }>(fetch(`${API}/map?by=${by}`, { cache: "no-store" }));
export const correct = (acc: string, body: { tag: string; suggested_value?: string; note?: string }) =>
  j<{ ok: boolean }>(fetch(`${API}/dataset/${acc}/correct`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }));

export const PRETTY: Record<string, string> = {
  label_free: "label-free", trypsin_lysc: "trypsin + Lys-C", high_ph_rp: "high-pH RP", ap_ms: "AP-MS / IP", proximity_labelling: "proximity labelling",
  PRM_SRM: "targeted PRM/SRM", proteome_discoverer: "Proteome Discoverer", maxquant: "MaxQuant", fragpipe: "FragPipe", diann: "DIA-NN",
  spectronaut: "Spectronaut", mascot: "Mascot", orbitrap: "Orbitrap", timstof: "timsTOF", qtof: "Q-TOF", triple_quad: "triple quad",
  cell_line: "cell line", primary_cells: "primary cells", organism_whole: "whole organism", top_down: "top-down", none: "none",
};
export const pretty = (v: unknown) => (v === null || v === undefined ? "—" : PRETTY[String(v)] ?? String(v));
export const TAG_LABEL: Record<string, string> = {
  organism: "organism", sample_type: "sample type", tissue: "tissue", cell_line: "cell line", disease: "disease", enrichment: "enrichment",
  acquisition: "acquisition", labelling: "labelling", enzyme: "enzyme", fractionation: "fractionation", instrument_family: "instrument",
  search_engine: "search engine", depth_proteins: "proteins identified", since: "since", q: "free text",
};
export const METHOD_TAGS = ["enrichment", "acquisition", "labelling", "enzyme", "fractionation", "instrument_family", "search_engine"];

export const TOUR_Q_HISTORY_KEY = "pridemap.history";
export function remember(q: string) {
  try {
    const cur: { q: string; at: string }[] = JSON.parse(localStorage.getItem(TOUR_Q_HISTORY_KEY) ?? "[]");
    const next = [{ q, at: new Date().toISOString() }, ...cur.filter((h) => h.q !== q)].slice(0, 8);
    localStorage.setItem(TOUR_Q_HISTORY_KEY, JSON.stringify(next));
  } catch {}
}
