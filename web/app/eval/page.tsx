import { API } from "@/lib/api";

type Bench = Record<string, { ok: number; fail: number; cost: number; median_seconds: number; agreement: Record<string, number>; avg_agreement: number; null_rate: number | null }>;
type Report = { rows: number; overall: { n: number; agree: number; accuracy: number | null }; per_tag: Record<string, { n: number; agree: number; accuracy: number; misses: { accession: string; gold: string; model: string | null }[] }> };
type Out = { bench: Bench | null; report: Report | null; tagged_by_model: Record<string, number> };

const TAGS = ["organism", "sample_type", "enrichment", "acquisition", "labelling", "enzyme", "fractionation", "instrument_family", "search_engine"];
const pct = (x: number | null | undefined) => (x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`);

export default async function EvalPage() {
  let d: Out | null = null;
  try { d = await (await fetch(`${API}/eval`, { cache: "no-store" })).json(); } catch { d = null; }
  return (
    <div className="grid gap-8 max-w-5xl">
      <div>
        <h1 className="text-4xl">How much to trust the tags</h1>

      </div>

      {!d && <p className="text-warn">API not reachable.</p>}

      {d && (
        <section className="grid gap-3">
          <h2 className="text-2xl">Who tagged what</h2>
          <div className="rounded-xl border bg-card overflow-x-auto">
            <table className="w-full text-sm"><thead><tr className="border-b"><th className="text-left p-3 label">model</th><th className="text-right p-3 label">datasets</th></tr></thead>
              <tbody>{Object.entries(d.tagged_by_model).sort((a, b) => b[1] - a[1]).map(([m, n]) => <tr key={m} className="border-b last:border-0"><td className="p-3 font-mono text-xs">{m}</td><td className="p-3 text-right tabular">{n.toLocaleString()}</td></tr>)}</tbody></table>
          </div>
        </section>
      )}

      <section className="grid gap-3">
        <h2 className="text-2xl">Humans vs model (gold set)</h2>
        {d?.report && d.report.overall.n > 0 ? (
          <>
            <p className="text-sm">Across {d.report.rows} hand‑labelled datasets, the model agreed with the bioinformaticians on <b className="tabular">{pct(d.report.overall.accuracy)}</b> of {d.report.overall.n} labelled tags.</p>
            <div className="rounded-xl border bg-card overflow-x-auto">
              <table className="w-full text-sm"><thead><tr className="border-b"><th className="text-left p-3 label">tag</th><th className="text-right p-3 label">n</th><th className="text-right p-3 label">agree</th><th className="text-left p-3 label">example miss</th></tr></thead>
                <tbody>{Object.entries(d.report.per_tag).map(([t, r]) => <tr key={t} className="border-b last:border-0"><td className="p-3 font-mono text-xs">{t}</td><td className="p-3 text-right tabular">{r.n}</td><td className="p-3 text-right tabular">{pct(r.accuracy)}</td><td className="p-3 text-xs text-muted-foreground">{r.misses[0] ? `${r.misses[0].accession}: human said "${r.misses[0].gold}", model said "${r.misses[0].model ?? "null"}"` : ""}</td></tr>)}</tbody></table>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Not run yet. Charlotte and Juri fill <code>gold/gold_50.csv</code>, then <code>uv run eval_tags.py</code> writes the report that appears here.</p>
        )}
      </section>

      <section className="grid gap-3">
        <h2 className="text-2xl">Model vs model (200 datasets, same inputs)</h2>
        {d?.bench ? (
          <>
            <p className="text-sm text-muted-foreground">Agreement with Claude Sonnet 5&apos;s tags on the same 200 datasets.</p>
            <div className="rounded-xl border bg-card overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="border-b"><th className="text-left p-3 label">model</th><th className="text-right p-3 label">ok</th><th className="text-right p-3 label">cost / 1k</th><th className="text-right p-3 label">s / item</th>{TAGS.map((t) => <th key={t} className="text-right p-3 label">{t.replace("_", " ").slice(0, 8)}</th>)}<th className="text-right p-3 label">avg</th></tr></thead>
                <tbody>{Object.entries(d.bench).sort((a, b) => b[1].avg_agreement - a[1].avg_agreement).map(([m, r]) => (
                  <tr key={m} className="border-b last:border-0"><td className="p-3 font-mono text-xs">{m}</td><td className="p-3 text-right tabular">{r.ok}</td><td className="p-3 text-right tabular">${(r.cost / r.ok * 1000).toFixed(2)}</td><td className="p-3 text-right tabular">{r.median_seconds.toFixed(1)}</td>{TAGS.map((t) => <td key={t} className="p-3 text-right tabular">{pct(r.agreement[t])}</td>)}<td className="p-3 text-right tabular font-semibold">{pct(r.avg_agreement)}</td></tr>
                ))}</tbody>
              </table>
            </div>
            <p className="text-xs text-faint">Sonnet 5: about $24 per 1,000.</p>
          </>
        ) : <p className="text-sm text-muted-foreground">Not run yet: <code>uv run tag_bench.py run --model …</code> then <code>report</code>.</p>}
      </section>
    </div>
  );
}
