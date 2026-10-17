"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { API, TAG_LABEL } from "@/lib/api";
import { useCompare } from "@/lib/compare-store";

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
        <h1 className="text-3xl font-extrabold">Can I compare these datasets?</h1>
        <p className="text-muted-foreground max-w-2xl">Before running group‑A‑vs‑B stats across datasets, the methods have to match. This lines up the tags side by side and says what would break a comparison.</p>
      </div>
      <form className="flex gap-2 flex-col sm:flex-row" onSubmit={(e) => { e.preventDefault(); go(input); }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="PXD007653, PXD002801" className="flex-1 h-11 rounded-xl border bg-card px-4 font-mono text-sm outline-none focus:ring-2 ring-teal" aria-label="accessions, comma separated" />
        <button type="submit" className="h-11 px-5 rounded-xl bg-teal text-white font-bold">Compare</button>
        {sel.length >= 2 && sel.join(",") !== a && (
          <button type="button" onClick={() => { setInput(sel.join(",")); go(sel.join(",")); }} className="h-11 px-4 rounded-xl border font-bold">Use my {sel.length} picked</button>
        )}
      </form>
      {!a && <p className="text-sm text-muted-foreground">Tip: on the Ask page, hit <b>+ shortlist</b> on two or more results, then use the bar at the bottom.</p>}
      {err && <p className="text-warn text-sm">{err}</p>}

      {out && a && (
        <>
          <div className={`rounded-xl border p-4 ${VERDICT[out.verdict].cls}`}>
            <div className="flex items-center gap-2 font-bold text-lg"><span className={`inline-block w-3 h-3 rounded-full ${VERDICT[out.verdict].dot}`} />{VERDICT[out.verdict].label}</div>
            <p className="mt-1">{out.summary}</p>
          </div>

          {out.issues.length > 0 && (
            <div className="grid gap-2">
              <h2 className="font-bold">Differences that matter</h2>
              {out.issues.map((i) => (
                <div key={i.tag} className="rounded-lg border bg-card p-3 text-sm grid sm:grid-cols-[110px_1fr] gap-2">
                  <span className={`self-start rounded-full px-2 py-0.5 text-xs font-bold ${SEV[i.severity]}`}>{i.severity}</span>
                  <div>
                    <div className="font-bold">{TAG_LABEL[i.tag] ?? i.tag}: {out.accessions.map((acc) => `${acc} = ${i.values[acc]}`).join(" · ")}</div>
                    <div className="text-muted-foreground">{i.why}</div>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="rounded-xl border bg-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left p-3 text-xs uppercase tracking-wide text-muted-foreground">tag</th>
                  {out.accessions.map((acc) => (
                    <th key={acc} className="text-left p-3 align-top min-w-[220px]">
                      <Link href={`/dataset/${acc}`} className="font-mono text-teal hover:underline">{acc}</Link>
                      <div className="text-xs font-normal text-muted-foreground mt-1 line-clamp-3">{out.datasets[acc].summary ?? out.datasets[acc].title}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ORDER.map((tag) => {
                  const cells = out.table[tag];
                  const vals = out.accessions.map((acc) => cells[acc].value);
                  const differ = new Set(vals.filter((v) => v !== null && v !== "—")).size > 1;
                  return (
                    <tr key={tag} className={`border-b last:border-0 ${differ ? "bg-peak-soft/20" : ""}`}>
                      <td className="p-3 text-xs uppercase tracking-wide text-muted-foreground font-bold whitespace-nowrap">{TAG_LABEL[tag] ?? tag}</td>
                      {out.accessions.map((acc) => {
                        const c = cells[acc];
                        return (
                          <td key={acc} className="p-3 align-top">
                            <div className="font-bold">{c.value === null ? <span className="text-muted-foreground font-normal">—</span> : typeof c.value === "number" ? c.value.toLocaleString() : c.value}</div>
                            {c.evidence && <div className="text-xs text-muted-foreground italic mt-0.5 line-clamp-2">“{c.evidence}”</div>}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground">Rows shaded orange differ between datasets. Next step (stretch): pull both result tables from PRIDE and show shared vs unique proteins.</p>
        </>
      )}
    </div>
  );
}
