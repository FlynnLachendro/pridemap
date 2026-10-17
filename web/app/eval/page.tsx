import Link from "next/link";
import { API, TAG_LABEL } from "@/lib/api";
import { MarkChecked } from "@/components/mark-checked";

type Bench = Record<string, { ok: number; fail: number; cost: number; median_seconds: number; agreement: Record<string, number>; avg_agreement: number; null_rate: number | null }>;
type Report = { rows: number; overall: { n: number; agree: number; accuracy: number | null }; per_tag: Record<string, { n: number; agree: number; accuracy: number; misses: { accession: string; gold: string; model: string | null }[] }> };
type Out = { bench: Bench | null; report: Report | null; tagged_by_model: Record<string, number> };
type Correction = { id: number; accession: string; title: string | null; tag: string; suggested_value: string | null; note: string | null; created_at: string; current_value: string | number | null; model: string | null };
type Corrections = { total: number; corrections: Correction[]; command: string };
type CorrStats = { total: number; accessions: number; by_tag: Record<string, number>; applied: number };

const TAGS = ["organism", "sample_type", "enrichment", "acquisition", "labelling", "enzyme", "fractionation", "instrument_family", "search_engine"];
const pct = (x: number | null | undefined) => (x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`);

export default async function EvalPage() {
  let d: Out | null = null;
  try { d = await (await fetch(`${API}/eval`, { cache: "no-store" })).json(); } catch { d = null; }
  let corr: Corrections | null = null;
  let cstats: CorrStats | null = null;
  try {
    [corr, cstats] = await Promise.all([
      fetch(`${API}/corrections?limit=200`, { cache: "no-store" }).then((r) => r.json()),
      fetch(`${API}/corrections/stats`, { cache: "no-store" }).then((r) => r.json()),
    ]);
  } catch { corr = null; cstats = null; }
  return (
    <div className="grid gap-8 max-w-5xl">
      <MarkChecked />
      <div className="grid gap-3">
        <h1 className="text-4xl">How much to trust the tags</h1>
        <div className="grid sm:grid-cols-3 gap-3 text-sm">
          <div className="card p-4 grid gap-1"><div className="font-semibold">What a tag is</div><p className="text-muted-foreground">A model read each dataset&apos;s PRIDE protocol text and the paper&apos;s Methods, then filled 14 fixed fields (enrichment, acquisition, labelling…). Every value carries a confidence and the sentence it came from. Nothing here is PRIDE&apos;s own metadata.</p></div>
          <div className="card p-4 grid gap-1"><div className="font-semibold">What “agreement” means</div><p className="text-muted-foreground">Two models given the same 200 datasets: the share of datasets where they chose the same value for a field. The reference is Claude Sonnet 5 (the most careful model we ran), not human truth. 93% agreement means 7 in 100 datasets would get a different enrichment or acquisition label from the cheaper model.</p></div>
          <div className="card p-4 grid gap-1"><div className="font-semibold">What is still missing</div><p className="text-muted-foreground">A human benchmark. 50 datasets are laid out in <code>gold/gold_50.csv</code> for two bioinformaticians to label by hand; once filled, the “humans vs model” table below becomes the number to quote. Until then, treat the tags as a strong first pass and check the quoted sentence.</p></div>
        </div>
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
        <p className="text-sm text-muted-foreground">50 datasets chosen to cover every vocabulary value. Humans label each field without seeing the model&apos;s answer; accuracy = share of fields where the model matches the human. Misses are listed per field so you can see <i>how</i> it is wrong (e.g. DIA called DDA) rather than only how often.</p>
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

      <section className="grid gap-3" data-tour="corrections">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <h2 className="text-2xl">Corrections from users</h2>
          {cstats && (
            <span className="text-sm text-muted-foreground tabular">
              {cstats.total} correction{cstats.total === 1 ? "" : "s"} on {cstats.accessions} dataset{cstats.accessions === 1 ? "" : "s"}
              {cstats.applied > 0 && <> · {cstats.applied} re-tagged</>}
            </span>
          )}
        </div>
        {cstats && cstats.total > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(cstats.by_tag).map(([t, n]) => <span key={t} className="pill">{TAG_LABEL[t] ?? t} <b className="tabular">{n}</b></span>)}
          </div>
        )}
        {corr && corr.corrections.length > 0 ? (
          <div className="rounded-xl border bg-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b"><th className="text-left p-3 label">dataset</th><th className="text-left p-3 label">tag</th><th className="text-left p-3 label">model said</th><th className="text-left p-3 label">user said</th><th className="text-left p-3 label">note</th><th className="text-right p-3 label">when</th></tr></thead>
              <tbody>
                {corr.corrections.map((c) => (
                  <tr key={c.id} className="border-b last:border-0 align-top">
                    <td className="p-3 font-mono text-xs"><Link className="underline" href={`/dataset/${c.accession}`}>{c.accession}</Link></td>
                    <td className="p-3 text-xs">{TAG_LABEL[c.tag] ?? c.tag}</td>
                    <td className="p-3 text-xs text-muted-foreground">{c.current_value === null || c.current_value === undefined ? "null" : String(c.current_value)}{c.model?.endsWith("(corrected)") && <span className="pill ml-1">re-tagged</span>}</td>
                    <td className="p-3 text-xs font-semibold">{c.suggested_value ?? "null"}</td>
                    <td className="p-3 text-xs text-muted-foreground max-w-xs">{c.note ?? ""}</td>
                    <td className="p-3 text-xs text-muted-foreground tabular text-right whitespace-nowrap">{c.created_at.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{corr ? "No corrections yet. Every tag on a dataset page has a “wrong?” button." : "API not reachable."}</p>
        )}
        <div className="note">
          <div className="grid gap-1">
            <b>Re-tag with corrections pinned</b>
            <code className="text-xs">{corr?.command ?? "uv run apply_corrections.py"}</code>
            <span className="text-xs text-muted-foreground">writes <code>gold/from_corrections.csv</code>, re-tags each corrected dataset with the corrections as ground truth; <code>--dry</code> to preview</span>
          </div>
        </div>
      </section>

      <section className="grid gap-3">
        <h2 className="text-2xl" data-tour="bench">Model vs model (200 datasets, same inputs)</h2>
        {d?.bench ? (
          <>
            <p className="text-sm text-muted-foreground">Five models tagged the same 200 datasets from the same inputs. Each percentage is agreement with Claude Sonnet 5 on that field; <b>avg</b> is the mean over the nine fields. <b>ok</b> = datasets that returned a valid schema; <b>cost / 1k</b> = what tagging 1,000 datasets costs at list price; <b>s / item</b> = median seconds per dataset. The cheapest model within a few points of the reference did the bulk of the archive.</p>
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
