"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { API, pretty, TAG_LABEL } from "@/lib/api";
import { useCompare } from "@/lib/compare-store";

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
    <article className={`rounded-xl border bg-card p-4 grid gap-3 ${confirmed ? "border-ok" : ""}`}>
      <div className="flex items-baseline gap-3 flex-wrap">
        <Link href={`/dataset/${acc}`} className="font-mono text-sm text-teal hover:underline">{acc}</Link>
        <span className="text-sm text-muted-foreground line-clamp-1">{r?.summary ?? r?.title ?? "…"}</span>
      </div>
      <div className="flex flex-wrap gap-1.5 text-xs font-mono">
        {["organism", "tissue", "enrichment", "acquisition", "labelling", "enzyme", "instrument_family"].map((k) => t[k] ? <span key={k} className="rounded-full bg-secondary px-2 py-0.5"><span className="text-faint">{TAG_LABEL[k]} </span>{pretty(t[k])}</span> : null)}
      </div>
      <div className="text-sm leading-relaxed">
        <span className="label mr-2">Why this one</span>
        {why ? <span>{why.why} <span className="text-faint text-xs font-mono">({why.parser === "llm" ? "Claude" : "from tags"})</span></span> : <span className="text-faint">writing…</span>}
      </div>
      {re && (
        <div className="text-xs text-muted-foreground font-mono flex flex-wrap gap-x-4 gap-y-1">
          <span>{re.files.total} files on PRIDE</span>
          <span>{re.has_submitter_results ? "submitter results available" : "no submitter results: re-search needed"}</span>
          {re.recipe && <span>{re.recipe.tool.value} · {re.recipe.enzyme.value.join("+")} · {re.recipe.variable_modifications.value.length} variable mods</span>}
        </div>
      )}
      <label className="flex items-center gap-2 text-sm font-sans">
        <input type="checkbox" checked={confirmed} onChange={(e) => onConfirm(e.target.checked)} className="accent-[var(--ok)]" />
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
        <p className="text-muted-foreground max-w-2xl mt-2">The whole loop a researcher actually runs, with the human in charge at each step. The app does the reading, the ranking and the set‑up; you make the calls.</p>
      </div>

      <Step n={1} title="Find candidates" done={sel.length > 0}>
        <p className="text-sm text-muted-foreground">Ask in plain words. Tags read from each dataset&apos;s protocol and paper do the filtering. Hit <b>+ shortlist</b> on anything promising.</p>
        <div className="flex gap-2 flex-wrap"><Link href={q ? `/?q=${encodeURIComponent(q)}` : "/"} className="rounded-md bg-teal text-primary-foreground px-3 py-1.5 text-sm font-sans font-semibold">Open Ask →</Link><Link href="/map" className="rounded-md border px-3 py-1.5 text-sm font-sans">or browse the Map</Link></div>
        <p className="text-xs font-mono text-faint">{sel.length} shortlisted</p>
      </Step>

      <Step n={2} title="Confirm each candidate" done={confirmedList.length > 0}>
        <p className="text-sm text-muted-foreground">For each shortlisted dataset: a three‑sentence case for it, the tags that matter, and what&apos;s on PRIDE. Tick the ones you&apos;ll actually use.</p>
        {sel.length === 0 && <p className="text-sm text-faint">Nothing shortlisted yet.</p>}
        <div className="grid gap-3">{sel.map((a) => <Candidate key={a} acc={a} q={q} confirmed={!!confirmed[a]} onConfirm={(v) => setC(a, v)} />)}</div>
      </Step>

      <Step n={3} title="Set up the database search" done={false}>
        <p className="text-sm text-muted-foreground">If the submitter uploaded results you can skip the search. Otherwise the dataset page gives you the file list and the search parameters (tool, enzyme, fixed and variable modifications, quantification, FASTA) derived from the tags, each with the sentence it came from. Load them into MaxQuant, FragPipe or DIA‑NN and run it on your machine; we can&apos;t run it for you.</p>
        <div className="flex flex-wrap gap-2">
          {confirmedList.map((a) => <Link key={a} href={`/dataset/${a}#reanalyse`} className="rounded-md border px-3 py-1.5 text-sm font-mono hover:bg-secondary">{a} set‑up sheet →</Link>)}
          {confirmedList.length === 0 && <span className="text-sm text-faint">Confirm a candidate first.</span>}
        </div>
      </Step>

      <Step n={4} title="Analyse the protein table" done={false}>
        <p className="text-sm text-muted-foreground">Upload the protein table (the submitter&apos;s results file, or your own MaxQuant / DIA‑NN output), pick group A and group B, and get log2 fold changes, t‑tests, FDR and a volcano plot. Odetta&apos;s lyso‑IP pipeline runs when the columns match it.</p>
        <Link href="/analyse" className="rounded-md bg-teal text-primary-foreground px-3 py-1.5 text-sm font-sans font-semibold w-fit">Open Analyse →</Link>
      </Step>

      <Step n={5} title="Check the machine's work" done={false}>
        <p className="text-sm text-muted-foreground">Every tag shows its confidence and evidence, and &quot;wrong?&quot; records a correction. The Eval page compares the model to hand labels and to other models, so you know how much to trust steps 1 to 3.</p>
        <Link href="/eval" className="rounded-md border px-3 py-1.5 text-sm font-sans w-fit">Open Eval →</Link>
      </Step>
    </div>
  );
}
