"use client";
import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { API, ask, pretty, remember, TAG_LABEL, TOUR_Q_HISTORY_KEY, type AskOut, type Dropped, type Result } from "@/lib/api";
import { ResultCard } from "@/components/result-card";
import { ProteinCard } from "@/components/protein-card";
import { CompareToggle } from "@/components/compare-toggle";
import { CardSkeleton } from "@/components/skeleton";
import { SaveSearch } from "@/components/save-search";

type Facets = Record<string, [string, number][]>;
type BrowseRow = Result & { tagged: boolean; raw?: { organisms: string[]; organism_parts: string[]; instruments: string[]; experiment_types: string[] } };
type BrowseOut = { filters: Record<string, string | number | boolean>; sort: string; page: number; size: number; total: number; counts: { tagged: number; untagged: number }; results: BrowseRow[]; facets: Facets };

const FACETS: { key: string; label: string; search?: boolean }[] = [
  { key: "organism", label: "Organism", search: true },
  { key: "tissue", label: "Tissue", search: true },
  { key: "sample_type", label: "Sample type" },
  { key: "enrichment", label: "Enrichment" },
  { key: "acquisition", label: "Acquisition" },
  { key: "labelling", label: "Labelling" },
  { key: "enzyme", label: "Enzyme" },
  { key: "fractionation", label: "Fractionation" },
  { key: "instrument_family", label: "Instrument" },
  { key: "year", label: "Year" },
];
const MULTI = new Set(["organism", "tissue", "sample_type", "enrichment", "acquisition", "labelling", "enzyme", "fractionation", "instrument_family", "year"]);
const SEP: Record<string, string> = { organism: "|", tissue: "|", cell_line: "|", disease: "|" };
const sep = (k: string) => SEP[k] ?? ",";
const paramKey = (k: string) => (k === "year" ? "years" : k);
const SORTS = [["relevance", "relevance"], ["downloads", "most reused"], ["date", "newest"], ["depth", "most proteins"]];
const EXAMPLES = ["I want to validate phosphorylated MFF in mouse liver", "Human plasma, label-free, DIA, from 2020 onwards", "liver", "PXD007653", "Interactome / pull-down data in HEK293 cells"];

function looksLikeQuestion(s: string) {
  const w = s.trim().split(/\s+/);
  return w.length >= 5 || /\?|\b(want|which|could|show|find|datasets?|looking|need|validate|compare)\b/i.test(s);
}

export function Browse() {
  const params = useSearchParams();
  const router = useRouter();
  const [pending, start] = useTransition();
  const [out, setOut] = useState<BrowseOut | null>(null);
  const [askOut, setAskOut] = useState<AskOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState(params.get("q") ?? "");
  const [view, setView] = useState<"cards" | "table">("cards");
  const [facetFind, setFacetFind] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [recent, setRecent] = useState<string[]>([]);
  const lastUrl = useRef("");

  // all state lives in the URL so links from the map / dataset pages and sharing just work
  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    params.forEach((v, k) => { if (v) f[k] = v; });
    return f;
  }, [params]);

  useEffect(() => {
    // read after paint (localStorage is client-only; avoids a hydration mismatch and a sync setState in the effect body)
    const id = window.setTimeout(() => { try { setRecent((JSON.parse(localStorage.getItem(TOUR_Q_HISTORY_KEY) ?? "[]") as { q: string }[]).map((h) => h.q)); } catch {} }, 0);
    return () => window.clearTimeout(id);
  }, [askOut, filters]);

  const push = useCallback((next: Record<string, string | undefined>) => {
    const p = new URLSearchParams();
    Object.entries(next).forEach(([k, v]) => { if (v !== undefined && v !== "") p.set(k, v); });
    router.replace(`/?${p.toString()}`, { scroll: false });
  }, [router]);

  const load = useCallback((f: Record<string, string>) => {
    const p = new URLSearchParams();
    Object.entries(f).forEach(([k, v]) => { if (k !== "ask" && k !== "tour" && v) p.set(k, v); });
    if (!p.get("size")) p.set("size", "20");
    const url = `${API}/browse?${p.toString()}`;
    if (url === lastUrl.current) return;
    lastUrl.current = url;
    start(async () => {
      try {
        const r = await fetch(url);
        if (!r.ok) throw new Error(await r.text());
        setOut(await r.json());
        setErr(null);
      } catch (e) { setErr(String(e)); }
    });
  }, []);

  useEffect(() => { load(filters); }, [filters, load]);

  function submit(text: string) {
    const t = text.trim();
    if (!t) { push({}); setAskOut(null); return; }
    remember(t);
    if (looksLikeQuestion(t)) {
      start(async () => {
        try {
          const a = await ask(t);
          setAskOut(a);
          const next: Record<string, string | undefined> = { ask: t };
          Object.entries(a.filters).forEach(([k, v]) => { if (v !== null && v !== undefined && v !== false && typeof v !== "boolean") next[k === "since" ? "since" : k] = String(v); });
          push(next);
        } catch (e) { setErr(String(e)); }
      });
    } else {
      setAskOut(null);
      push({ ...filters, q: t, page: undefined });
    }
  }

  function toggleFacet(key: string, value: string) {
    const pk = paramKey(key);
    const cur = (filters[pk] ?? "").split(sep(key)).filter(Boolean);
    const next = cur.includes(value) ? cur.filter((x) => x !== value) : (MULTI.has(key) ? [...cur, value] : [value]);
    push({ ...filters, [pk]: next.join(sep(key)) || undefined, page: undefined });
  }
  const isOn = (key: string, value: string) => (filters[paramKey(key)] ?? "").split(sep(key)).includes(value);
  const activeChips = Object.entries(filters).filter(([k]) => !["page", "size", "sort", "tagged", "view", "ask", "q", "tour"].includes(k));

  const total = out?.total ?? 0;
  const page = Number(filters.page ?? 1);
  const size = Number(filters.size ?? 20);
  const pages = Math.max(1, Math.ceil(total / size));

  return (
    <div className="grid gap-5">
      <div>
        <h1 className="text-3xl sm:text-4xl">Search PRIDE by how the data was made</h1>
      </div>

      <form className="search-box" data-tour="search" onSubmit={(e) => { e.preventDefault(); submit(q); }} role="search">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" className="text-teal shrink-0" aria-hidden><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by keyword, accession, or ask a question in plain words" aria-label="search" />
        {q && <button type="button" onClick={() => { setQ(""); setAskOut(null); push({ sort: filters.sort }); }} className="btn btn-ghost btn-sm" aria-label="clear">clear</button>}
        <button type="submit" disabled={pending} className="btn btn-primary">{pending ? <><span className="spinner mr-2" />Searching…</> : "Search"}</button>
      </form>
      <div className="flex flex-wrap gap-1.5 items-center text-xs">
        <span className="text-faint font-mono">Try:</span>
        {EXAMPLES.map((ex) => <button key={ex} type="button" onClick={() => { setQ(ex); submit(ex); }} className="pill hover:bg-surface-2">{ex}</button>)}
      </div>
      {recent.length > 0 && (
        <div className="flex flex-wrap gap-1.5 items-center text-xs -mt-2">
          <span className="text-faint font-mono">Recent:</span>
          {recent.slice(0, 5).map((r) => <button key={r} type="button" onClick={() => { setQ(r); submit(r); }} className="pill hover:bg-surface-2">{r}</button>)}
        </div>
      )}

      {err && <p className="text-warn text-sm">API error: {err}</p>}

      {askOut && (
        <div className="card p-4 grid gap-2 text-sm" data-tour="ask">
          <p><span className="label mr-2">How I read it</span>{askOut.interpretation} <span className="text-faint font-mono text-xs">({askOut.parser === "llm" ? "Claude" : "rules"})</span></p>
          {askOut.dropped && askOut.dropped.length > 0 && (
            <div className="grid gap-1.5">
              {askOut.dropped.map((d: Dropped) => (
                <div key={d.key} className="note">
                  <span aria-hidden>ⓘ</span>
                  <span>
                    {d.key === "q"
                      ? <><b>{pretty(d.value)}</b> isn&apos;t used as a filter: protein names aren&apos;t in dataset descriptions. The card below says whether these datasets&apos; methods could detect it; confirming it&apos;s there means reanalysing the data.</>
                      : <><b>{TAG_LABEL[d.key] ?? d.key}: {pretty(d.value)}</b> was set aside so you still see results — {d.why}.</>}
                  </span>
                </div>
              ))}
            </div>
          )}
          {(askOut.filters.q || askOut.dropped?.some((d) => d.key === "q")) && (
            <ProteinCard name={String(askOut.filters.q ?? askOut.dropped?.find((d) => d.key === "q")?.value ?? "")} organism={askOut.filters.organism ? String(askOut.filters.organism) : undefined} tissue={askOut.filters.tissue ? String(askOut.filters.tissue) : undefined} />
          )}
        </div>
      )}

      <div className="grid lg:grid-cols-[240px_1fr] gap-6 items-start">
        <aside className="grid gap-5 content-start lg:sticky lg:top-0 lg:max-h-[calc(100dvh-4rem)] lg:overflow-y-auto lg:pr-2 lg:-mr-2 lg:pb-8" data-tour="filters">
          <div className="flex items-center justify-between">
            <span className="label">Filters</span>
            {activeChips.length > 0 && <button type="button" className="text-xs underline text-muted-foreground" onClick={() => push({ q: filters.q, sort: filters.sort })}>clear all</button>}
          </div>
          <label className="flex items-center gap-2 text-sm font-sans">
            <input type="checkbox" checked={filters.tagged === "only"} onChange={(e) => push({ ...filters, tagged: e.target.checked ? "only" : undefined, page: undefined })} />
            tagged datasets only
          </label>
          {FACETS.map((fc) => {
            const vals = out?.facets?.[fc.key] ?? [];
            const find = (facetFind[fc.key] ?? "").toLowerCase();
            const shown = vals.filter(([v]) => !find || v.toLowerCase().includes(find));
            const lim = open[fc.key] ? shown : shown.slice(0, 6);
            return (
              <div key={fc.key} className="grid gap-1" data-tour={`facet-${fc.key}`}>
                <div className="label">{fc.label}</div>
                {fc.search && <input value={facetFind[fc.key] ?? ""} onChange={(e) => setFacetFind({ ...facetFind, [fc.key]: e.target.value })} placeholder={`find ${fc.label.toLowerCase()}…`} className="input h-8 text-xs" />}
                <ul className="grid gap-0.5">
                  {lim.map(([v, n]) => (
                    <li key={v}>
                      <label className="flex items-center gap-2 text-sm px-1 py-0.5 rounded-md hover:bg-surface-2 cursor-pointer">
                        <input type="checkbox" checked={isOn(fc.key, v)} onChange={() => toggleFacet(fc.key, v)} data-tour={`facet-${fc.key}-${v}`} />
                        <span className="truncate flex-1">{pretty(v)}</span>
                        <span className="tabular text-faint text-xs">{n}</span>
                      </label>
                    </li>
                  ))}
                  {lim.length === 0 && <li className="text-xs text-faint px-1">none</li>}
                </ul>
                {shown.length > 6 && <button type="button" className="text-xs underline text-muted-foreground text-left px-1" onClick={() => setOpen({ ...open, [fc.key]: !open[fc.key] })}>{open[fc.key] ? "show fewer" : `show more (${shown.length - 6})`}</button>}
              </div>
            );
          })}

        </aside>

        <div className={`grid gap-3 min-w-0 ${pending ? "busy" : ""}`}>
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-xl tabular">{total.toLocaleString()} dataset{total === 1 ? "" : "s"}</h2>
            {out && <span className="text-xs font-mono text-faint">{out.counts.tagged.toLocaleString()} tagged · {out.counts.untagged.toLocaleString()} not yet</span>}
            <div className="ml-auto flex items-center gap-2 text-xs font-sans">
              <div className="flex border rounded-md overflow-hidden">
                <button type="button" onClick={() => setView("cards")} className={`px-2.5 py-1 ${view === "cards" ? "bg-surface-2 font-semibold" : ""}`}>Cards</button>
                <button type="button" onClick={() => setView("table")} className={`px-2.5 py-1 border-l ${view === "table" ? "bg-surface-2 font-semibold" : ""}`}>Table</button>
              </div>
              <label className="flex items-center gap-1">sort
                <select value={filters.sort ?? "relevance"} onChange={(e) => push({ ...filters, sort: e.target.value, page: undefined })} className="input h-8 text-xs">
                  {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </label>
              {(activeChips.length > 0 || filters.q) && out && <SaveSearch key={JSON.stringify(activeChips) + (filters.q ?? "")} filters={filters} total={total} />}
            </div>
          </div>
          {activeChips.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {activeChips.map(([k, v]) => v.split(sep(k === "years" ? "year" : k)).map((one) => (
                <button key={k + one} type="button" onClick={() => toggleFacet(k === "years" ? "year" : k, one)} className="pill bg-teal-soft text-teal">
                  <span className="opacity-70">{TAG_LABEL[k] ?? (k === "years" ? "year" : k)}</span> {pretty(one)} <span className="opacity-60">×</span>
                </button>
              )))}
            </div>
          )}

          {!out && !err && <><CardSkeleton /><CardSkeleton /><CardSkeleton /></>}
          {out && out.results.length === 0 && <p className="text-muted-foreground">Nothing matches. Remove a filter or try a keyword.</p>}

          {out && view === "cards" && out.results.map((r, i) => r.tagged
            ? <div key={r.accession} data-tour={`result-${i}`}><ResultCard r={r} rank={(page - 1) * size + i + 1} /></div>
            : <UntaggedCard key={r.accession} r={r} rank={(page - 1) * size + i + 1} />)}

          {out && view === "table" && (
            <div className="card overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="border-b">{["accession", "dataset", "organism", "tissue", "enrichment", "acq.", "label", "year", "downloads", ""].map((h) => <th key={h} className="label text-left p-2 whitespace-nowrap">{h}</th>)}</tr></thead>
                <tbody>
                  {out.results.map((r) => (
                    <tr key={r.accession} className="border-b last:border-0 align-top">
                      <td className="p-2 font-mono text-xs"><Link className="text-teal hover:underline" href={`/dataset/${r.accession}`}>{r.accession}</Link>{!r.tagged && <div className="pill text-[10px] mt-1">not tagged yet</div>}</td>
                      <td className="p-2 max-w-md"><div className="line-clamp-2">{r.summary ?? r.title}</div></td>
                      <td className="p-2 text-xs">{r.tagged ? pretty(r.tags.organism) : r.raw?.organisms.slice(0, 2).join(", ")}</td>
                      <td className="p-2 text-xs">{r.tagged ? pretty(r.tags.tissue) : r.raw?.organism_parts.slice(0, 2).join(", ")}</td>
                      <td className="p-2 text-xs">{r.tagged ? pretty(r.tags.enrichment) : "—"}</td>
                      <td className="p-2 text-xs">{r.tagged ? pretty(r.tags.acquisition) : "—"}</td>
                      <td className="p-2 text-xs">{r.tagged ? pretty(r.tags.labelling) : "—"}</td>
                      <td className="p-2 text-xs tabular">{r.publication_date?.slice(0, 4)}</td>
                      <td className="p-2 text-xs tabular text-right">{r.downloads?.toLocaleString() ?? "—"}</td>
                      <td className="p-2">{r.tagged && <CompareToggle accession={r.accession} />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {out && pages > 1 && (
            <div className="flex items-center gap-2 text-sm font-mono justify-center py-2">
              <button type="button" disabled={page <= 1} className="btn btn-ghost" onClick={() => push({ ...filters, page: String(page - 1) })}>← prev</button>
              <span className="tabular text-faint">page {page} of {pages.toLocaleString()}</span>
              <button type="button" disabled={page >= pages} className="btn btn-ghost" onClick={() => push({ ...filters, page: String(page + 1) })}>next →</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function UntaggedCard({ r, rank }: { r: BrowseRow; rank: number }) {
  return (
    <article className="card enter p-4 grid gap-2 opacity-90">
      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="text-xs font-mono text-faint w-5">{rank}</span>
        <Link href={`/dataset/${r.accession}`} className="font-mono text-sm text-teal hover:underline">{r.accession}</Link>
        <span className="pill text-[10px]">not tagged yet</span>
        <span className="text-xs text-faint tabular">{r.publication_date?.slice(0, 4)}</span>
        {r.downloads ? <span className="text-xs text-faint tabular">{r.downloads.toLocaleString()} downloads</span> : null}
        <a href={r.pride_url} target="_blank" rel="noreferrer" className="ml-auto text-xs underline text-muted-foreground">PRIDE ↗</a>
      </div>
      <h3 className="text-base leading-snug"><Link href={`/dataset/${r.accession}`} className="hover:underline">{r.title}</Link></h3>
      <div className="flex flex-wrap gap-1.5 text-xs">
        {r.raw?.organisms.slice(0, 3).map((o) => <span key={o} className="pill">{o}</span>)}
        {r.raw?.organism_parts.slice(0, 3).map((o) => <span key={o} className="pill">{o}</span>)}
        {r.raw?.instruments.slice(0, 2).map((o) => <span key={o} className="pill text-faint">{o}</span>)}
        {r.raw?.experiment_types.slice(0, 2).map((o) => <span key={o} className="pill text-faint">{o}</span>)}
      </div>

    </article>
  );
}
