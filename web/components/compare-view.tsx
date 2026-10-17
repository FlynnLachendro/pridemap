"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { API, TAG_LABEL } from "@/lib/api";
import { useCompare } from "@/lib/compare-store";
import { Skeleton } from "@/components/skeleton";

type Cell = { value: string | number | null; confidence: number | null; evidence: string | null };
type Out = {
  accessions: string[];
  datasets: Record<string, { title: string; summary: string | null; publication_date: string | null; pride_url: string }>;
  verdict: "comparable" | "with_caution" | "not_comparable";
  summary: string;
  issues: { tag: string; severity: string; why: string; values: Record<string, string> }[];
  table: Record<string, Record<string, Cell>>;
};

const ORDER = ["organism", "sample_type", "tissue", "cell_line", "disease", "enrichment", "acquisition", "labelling", "enzyme", "fractionation", "instrument_family", "search_engine", "depth_proteins"];
const SEV: Record<string, string> = { blocker: "bg-warn-soft text-warn", major: "bg-peak-soft text-peak", minor: "bg-secondary text-foreground", unknown: "bg-secondary text-muted-foreground" };
const VERDICT = {
  comparable: { label: "Comparable", cls: "border-ok bg-ok-soft/40", dot: "bg-ok" },
  with_caution: { label: "Comparable with caution", cls: "border-peak bg-peak-soft/40", dot: "bg-peak" },
  not_comparable: { label: "Not directly comparable", cls: "border-warn bg-warn-soft/40", dot: "bg-warn" },
};

export function CompareView() {
  const params = useSearchParams();
  const router = useRouter();
  const { sel } = useCompare();
  const [input, setInput] = useState(params.get("a") ?? "");
  const [out, setOut] = useState<Out | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const a = params.get("a") ?? "";

  useEffect(() => {
    if (!a) return;
    let live = true;
    fetch(`${API}/compare?accessions=${encodeURIComponent(a)}`)
      .then(async (r) => { if (!r.ok) throw new Error(await r.text()); return r.json(); })
      .then((d) => { if (live) { setOut(d); setErr(null); } })
      .catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, [a]);

  const go = (v: string) => router.push(`/compare?a=${encodeURIComponent(v.replace(/\s+/g, ""))}`);

  return (
    <div className="grid gap-5">
      <div>
        <h1 className="text-3xl">Can I compare these datasets?</h1>

      </div>
      <form className="flex gap-2 flex-col sm:flex-row" onSubmit={(e) => { e.preventDefault(); go(input); }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="PXD007653, PXD002801" className="input flex-1 h-11 font-mono" aria-label="accessions, comma separated" />
        <button type="submit" className="btn btn-primary btn-lg">Compare</button>
        {sel.length >= 2 && sel.join(",") !== a && (
          <button type="button" onClick={() => { setInput(sel.join(",")); go(sel.join(",")); }} className="btn btn-lg">Use my {sel.length} picked</button>
        )}
      </form>
      {!a && <p className="text-sm text-muted-foreground">Shortlist two or more datasets, or type accessions above.</p>}
      {err && <p className="text-warn text-sm">{err}</p>}
      {a && !out && !err && <div className="card p-4 grid gap-3"><Skeleton className="h-6 w-1/3" /><Skeleton className="h-3 w-full" /><Skeleton className="h-4 w-3/4" /></div>}

      {out && a && (
        <>
          {(() => {
            const sevOf: Record<string, string> = {};
            out.issues.forEach((i) => { sevOf[i.tag] = i.severity; });
            const compared = ORDER.filter((t) => t !== "depth_proteins");
            const same = compared.filter((t) => !sevOf[t] || sevOf[t] === "unknown").length;
            const score = Math.round((same / compared.length) * 100);
            const SEVC: Record<string, string> = { blocker: "var(--warn)", major: "var(--peak)", minor: "var(--faint)", unknown: "var(--border)" };
            return (
              <div data-tour="verdict" className={`rounded-xl border p-4 grid gap-3 ${VERDICT[out.verdict].cls}`}>
                <div className="flex items-center gap-3 flex-wrap">
                  <span className={`inline-block w-3 h-3 rounded-full ${VERDICT[out.verdict].dot}`} />
                  <span className="font-heading font-bold text-xl">{VERDICT[out.verdict].label}</span>
                  <span className="ml-auto font-mono text-sm tabular text-muted-foreground">{same} of {compared.length} method tags match</span>
                </div>
                <div className="flex h-3 rounded-full overflow-hidden border bg-surface" title="one segment per tag: green = same, amber = major difference, red = blocker, grey = unknown">
                  {compared.map((t) => (
                    <div key={t} className="flex-1 border-r last:border-0" style={{ background: sevOf[t] ? SEVC[sevOf[t]] : "var(--ok)" }} title={`${TAG_LABEL[t] ?? t}: ${sevOf[t] ?? "same"}`} />
                  ))}
                </div>
                <p className="text-sm">{out.summary}</p>
                <div className="flex flex-wrap gap-1.5 text-xs">
                  {compared.map((t) => (
                    <span key={t} className="pill" style={{ background: sevOf[t] ? (sevOf[t] === "blocker" ? "var(--warn-soft)" : sevOf[t] === "major" ? "var(--peak-soft)" : "var(--surface-2)") : "var(--ok-soft)", color: sevOf[t] ? (sevOf[t] === "blocker" ? "var(--warn)" : sevOf[t] === "major" ? "var(--peak)" : "var(--muted-text)") : "var(--ok)" }}>
                      {sevOf[t] ? (sevOf[t] === "unknown" ? "?" : "✕") : "✓"} {TAG_LABEL[t] ?? t}
                    </span>
                  ))}
                </div>
                <div className="text-[11px] text-faint font-mono">{score}% match · ✓ same · ✕ amber = major, red = blocker · ? unknown</div>
              </div>
            );
          })()}

          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left p-3 label w-40">tag</th>
                  {out.accessions.map((acc, i) => (
                    <th key={acc} className="text-left p-3 align-top min-w-[220px]">
                      <div className="flex items-center gap-2"><span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: ["var(--teal)", "var(--peak)", "var(--ok)", "#6A4C93", "#8B5E6B", "#5E6C76"][i % 6] }} /><Link href={`/dataset/${acc}`} className="font-mono text-teal hover:underline">{acc}</Link></div>
                      <div className="text-xs font-normal text-muted-foreground mt-1 line-clamp-2">{out.datasets[acc].summary ?? out.datasets[acc].title}</div>
                    </th>
                  ))}
                  <th className="p-3 label text-left w-56">verdict</th>
                </tr>
              </thead>
              <tbody>
                {ORDER.map((tag) => {
                  const cells = out.table[tag];
                  const issue = out.issues.find((i) => i.tag === tag);
                  const sev = issue?.severity;
                  const rowBg = sev === "blocker" ? "bg-warn-soft/40" : sev === "major" ? "bg-peak-soft/40" : sev === "minor" ? "bg-surface-2/60" : "";
                  return (
                    <tr key={tag} className={`border-b last:border-0 ${rowBg}`}>
                      <td className="p-3 label whitespace-nowrap align-top">
                        <span className={`inline-block w-2 h-2 rounded-full mr-2 align-middle ${sev === "blocker" ? "bg-warn" : sev === "major" ? "bg-peak" : sev ? "bg-faint" : "bg-ok"}`} />{TAG_LABEL[tag] ?? tag}
                      </td>
                      {out.accessions.map((acc) => {
                        const c = cells[acc];
                        return (
                          <td key={acc} className="p-3 align-top">
                            <div className="font-semibold">{c.value === null ? <span className="text-muted-foreground font-normal">—</span> : typeof c.value === "number" ? c.value.toLocaleString() : c.value}</div>
                            {c.confidence != null && <div className="h-1 w-16 rounded bg-surface-3 overflow-hidden mt-1" title={`confidence ${Math.round(c.confidence * 100)}%`}><div className="h-full bg-teal" style={{ width: `${Math.round(c.confidence * 100)}%` }} /></div>}
                            {c.evidence && <details className="text-xs text-muted-foreground italic mt-1"><summary className="cursor-pointer not-italic text-faint hover:text-teal">evidence</summary>“{c.evidence}”</details>}
                          </td>
                        );
                      })}
                      <td className="p-3 align-top text-xs">
                        {issue ? <><span className={`pill mr-1 ${SEV[issue.severity]}`}>{issue.severity}</span><span className="text-muted-foreground">{issue.why}</span></> : <span className="text-ok font-semibold">✓ same</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
