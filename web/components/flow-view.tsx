"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { API, pretty, TAG_LABEL } from "@/lib/api";
import { useCompare } from "@/lib/compare-store";
import { TOUR_B as TOUR_B_ACC } from "@/lib/tour";

type Why = { why: string; parser: string };
type Recipe = { tool: { value: string }; enzyme: { value: string[] }; fixed_modifications: { value: string[] }; variable_modifications: { value: string[] }; quantification: { value: string } };
type Re = { has_submitter_results: boolean; files: { total: number; groups: Record<string, { count: number; bytes: number }> }; recipe: Recipe | null };

const CONFIRM_KEY = "pridemap.confirmed";
function readConfirmed(): Record<string, boolean> { try { return JSON.parse(localStorage.getItem(CONFIRM_KEY) ?? "{}"); } catch { return {}; } }

function Step({ n, title, done, children }: { n: number; title: string; done?: boolean; children: React.ReactNode }) {
  return (
    <section className="grid sm:grid-cols-[56px_1fr] gap-4">
      <div className={`font-mono text-sm w-9 h-9 rounded-full border flex items-center justify-center ${done ? "bg-ok-soft text-ok border-ok" : "bg-card"}`}>{done ? "✓" : n}</div>
      <div className="grid gap-3 min-w-0">
        <h2 className="text-2xl">{title}</h2>
        {children}
      </div>
    </section>
  );
}

function Candidate({ acc, q, confirmed, onConfirm }: { acc: string; q: string; confirmed: boolean; onConfirm: (v: boolean) => void }) {
  const [r, setR] = useState<{ summary: string | null; title: string; tags: Record<string, string | number | null> } | null>(null);
  const [why, setWhy] = useState<Why | null>(null);
  const [re, setRe] = useState<Re | null>(null);
  useEffect(() => {
    let live = true;
    fetch(`${API}/dataset/${acc}`).then((x) => x.json()).then((d) => { if (live) setR({ summary: d.tags?.summary_one_line ?? null, title: d.project?.title ?? acc, tags: d.tags ?? {} }); }).catch(() => {});
    fetch(`${API}/dataset/${acc}/why?q=${encodeURIComponent(q)}`).then((x) => x.json()).then((d) => { if (live) setWhy(d); }).catch(() => {});
    fetch(`${API}/dataset/${acc}/reanalyse`).then((x) => x.json()).then((d) => { if (live) setRe(d); }).catch(() => {});
    return () => { live = false; };
  }, [acc, q]);
  const t = r?.tags ?? {};
  return (
    <article data-tour={`candidate-${acc === TOUR_B_ACC ? 0 : 1}`} className={`card p-4 grid gap-3 ${confirmed ? "border-ok" : ""}`}>
      <div className="flex items-baseline gap-3 flex-wrap">
        <Link href={`/dataset/${acc}`} className="font-mono text-sm text-teal hover:underline">{acc}</Link>
        <span className="text-sm text-muted-foreground line-clamp-1">{r?.summary ?? r?.title ?? "…"}</span>
      </div>
      <div className="flex flex-wrap gap-1.5 text-xs font-mono">
        {["organism", "tissue", "enrichment", "acquisition", "labelling", "enzyme", "instrument_family"].map((k) => t[k] ? <span key={k} className="pill"><span className="text-faint">{TAG_LABEL[k]} </span>{pretty(t[k])}</span> : null)}
      </div>
      <div className="text-sm leading-relaxed">
        <span className="label mr-2">Why this one</span>
        {why ? <span data-tour="why-ready">{why.why} <span className="text-faint text-xs font-mono">({why.parser === "llm" ? "Claude" : "from tags"})</span></span> : <span className="text-faint">writing…</span>}
      </div>
      {re && (
        <div className="text-xs text-muted-foreground font-mono flex flex-wrap gap-x-4 gap-y-1">
          <span>{re.files.total} files on PRIDE</span>
          <span>{re.has_submitter_results ? "submitter results available" : "no submitter results: re-search needed"}</span>
          {re.recipe && <span>{re.recipe.tool.value} · {re.recipe.enzyme.value.join("+")} · {re.recipe.variable_modifications.value.length} variable mods</span>}
        </div>
      )}
      <label className="flex items-center gap-2 text-sm font-sans">
        <input type="checkbox" checked={confirmed} onChange={(e) => onConfirm(e.target.checked)} className="accent-[var(--ok)]" data-tour={`confirm-${acc}`} />
        I&apos;ve read the reasoning and confirm this is a candidate
      </label>
    </article>
  );
}

export function FlowView() {
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const { sel } = useCompare();
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  useEffect(() => { const sync = () => setConfirmed(readConfirmed()); sync(); window.addEventListener("storage", sync); return () => window.removeEventListener("storage", sync); }, []);
  const setC = (acc: string, v: boolean) => { const c = { ...readConfirmed(), [acc]: v }; try { localStorage.setItem(CONFIRM_KEY, JSON.stringify(c)); } catch {} setConfirmed(c); };
  const confirmedList = sel.filter((a) => confirmed[a]);

  return (
    <div className="grid gap-8">
      <div>
        <h1 className="text-4xl">From question to result table</h1>

      </div>

      <Step n={1} title="Find candidates" done={sel.length > 0}>
        <p className="text-sm text-muted-foreground">Search, then <b>+ shortlist</b> anything promising.</p>
        <div className="flex gap-2 flex-wrap"><Link href={q ? `/?q=${encodeURIComponent(q)}` : "/"} className="btn btn-primary">Open Ask →</Link><Link href="/map" className="btn">or browse the Map</Link></div>
        <p className="text-xs font-mono text-faint">{sel.length} shortlisted</p>
      </Step>

      <Step n={2} title="Confirm each candidate" done={confirmedList.length > 0}>
        <p className="text-sm text-muted-foreground">Read the case for each, tick the ones you&apos;ll use.</p>
        {sel.length === 0 && <p className="text-sm text-faint">Nothing shortlisted yet.</p>}
        <div className="grid gap-3">{sel.map((a) => <Candidate key={a} acc={a} q={q} confirmed={!!confirmed[a]} onConfirm={(v) => setC(a, v)} />)}</div>
      </Step>

      <Step n={3} title="Set up the database search" done={false}>
        <p className="text-sm text-muted-foreground">Each confirmed dataset has a set‑up sheet: files on PRIDE plus the search parameters read from its tags. The search itself runs on your machine.</p>
        <div className="flex flex-wrap gap-2" data-tour="setup-links">
          {confirmedList.map((a) => <Link key={a} href={`/dataset/${a}#reanalyse`} className="btn font-mono">{a} set‑up sheet →</Link>)}
          {confirmedList.length === 0 && <span className="text-sm text-faint">Confirm a candidate first.</span>}
        </div>
      </Step>

      <Step n={4} title="Analyse the protein table" done={false}>
        <p className="text-sm text-muted-foreground">Upload the protein table, pick group A and B, get fold changes, FDR and a volcano plot.</p>
        <Link href="/analyse" className="btn btn-primary w-fit">Open Analyse →</Link>
      </Step>

      <Step n={5} title="Check the machine's work" done={false}>
        <p className="text-sm text-muted-foreground">How much to trust the tags: humans vs model, model vs model.</p>
        <Link href="/eval" className="btn w-fit">Open Eval →</Link>
      </Step>
    </div>
  );
}
