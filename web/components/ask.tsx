"use client";
import { useEffect, useState, useTransition } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ask, search, TAG_LABEL, pretty, type AskOut, type Filters, type Result, type Stats } from "@/lib/api";
import { ResultCard } from "@/components/result-card";
import { Button } from "@/components/ui/button";

const EXAMPLES = [
  "I want to validate phosphorylated MFF in mouse liver",
  "Human plasma, label-free, DIA, from 2020 onwards",
  "Which datasets could show a low-abundance kinase in human brain?",
  "Mouse heart proteome, total protein, not phospho-enriched",
  "Interactome / pull-down data in HEK293 cells",
];

const FILTER_ORDER = ["organism", "tissue", "cell_line", "disease", "sample_type", "enrichment", "acquisition", "labelling", "enzyme", "fractionation", "instrument_family", "search_engine", "since", "q"];
const FLAGS: Record<string, string> = { low_abundance: "low-abundance protein", wants_absolute_quant: "needs absolute amounts", wants_unmodified: "wants unmodified protein" };

export function Ask({ stats }: { stats: Stats | null }) {
  const params = useSearchParams();
  const router = useRouter();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [out, setOut] = useState<AskOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pending, start] = useTransition();

  // deep-link from the map: /?organism=...&enrichment=...
  useEffect(() => {
    const f: Filters = {};
    params.forEach((v, k) => { if (k !== "q") f[k] = v; });
    const qq = params.get("q");
    if (Object.keys(f).length && !qq) {
      start(async () => {
        try {
          const r = await search(f);
          setOut({ question: "", parser: "filters", interpretation: "Filters from the map.", filters: r.filters, total: r.total, results: r.results });
        } catch (e) { setErr(String(e)); }
      });
    } else if (qq) {
      setQ(qq);
      run(qq);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function run(question: string) {
    if (!question.trim()) return;
    setErr(null);
    start(async () => {
      try {
        const r = await ask(question);
        setOut(r);
        router.replace(`/?q=${encodeURIComponent(question)}`, { scroll: false });
      } catch (e) { setErr(String(e)); }
    });
  }

  function dropFilter(k: string) {
    if (!out) return;
    const f = { ...out.filters };
    delete f[k];
    start(async () => {
      try {
        const r = await search(f);
        setOut({ ...out, filters: r.filters, total: r.total, results: r.results, relaxed: [...(out.relaxed ?? []), k] });
      } catch (e) { setErr(String(e)); }
    });
  }

  return (
    <div className="grid gap-6">
      <section className="grid gap-3">
        <h1 className="text-3xl sm:text-4xl font-extrabold">Which PRIDE dataset could actually detect my protein?</h1>
        <p className="text-muted-foreground max-w-2xl">
          Ask in plain words. We turn it into filters over tags a model read out of each dataset&apos;s protocol and paper, rank what&apos;s left, and show the sentence each tag came from.
        </p>
        <form
          className="flex gap-2 flex-col sm:flex-row"
          onSubmit={(e) => { e.preventDefault(); run(q); }}
        >
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. phosphorylated MFF in mouse liver, low abundance"
            className="flex-1 h-12 rounded-xl border bg-card px-4 text-base outline-none focus:ring-2 ring-teal"
            aria-label="Your question"
          />
          <Button type="submit" disabled={pending} className="h-12 px-6 rounded-xl bg-teal text-white hover:bg-teal/90 font-bold">
            {pending ? "Searching…" : "Ask"}
          </Button>
        </form>
        <div className="flex flex-wrap gap-1.5 text-xs">
          <span className="text-muted-foreground py-1">Try:</span>
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" onClick={() => { setQ(ex); run(ex); }} className="rounded-full border px-2.5 py-1 hover:bg-secondary">
              {ex}
            </button>
          ))}
        </div>
        {stats && (
          <p className="text-xs text-muted-foreground tabular">
            {stats.tagged.toLocaleString()} of {stats.projects.toLocaleString()} PRIDE datasets tagged so far · {stats.papers_with_methods.toLocaleString()} with the paper&apos;s Methods read
          </p>
        )}
      </section>

      {err && <p className="text-warn text-sm">Couldn&apos;t reach the API: {err}. Is <code>uv run api/main.py</code> running?</p>}

      {out && (
        <section className="grid gap-4">
          <div className="rounded-xl border bg-card p-4 grid gap-2">
            <p className="text-sm">
              <span className="font-bold">How I read it:</span> {out.interpretation}{" "}
              <span className="text-xs text-muted-foreground">({out.parser === "llm" ? "Claude" : out.parser === "rules" ? "rule-based parser, no API key" : "map"})</span>
            </p>
            <div className="flex flex-wrap gap-1.5">
              {FILTER_ORDER.filter((k) => out.filters[k] !== undefined && out.filters[k] !== null && out.filters[k] !== "").map((k) => (
                <span key={k} className="inline-flex items-center gap-1 rounded-full bg-teal-soft text-teal px-2.5 py-1 text-xs font-bold">
                  <span className="opacity-70">{TAG_LABEL[k] ?? k}</span> {pretty(out.filters[k])}
                  <button type="button" onClick={() => dropFilter(k)} aria-label={`remove ${k}`} className="ml-0.5 opacity-60 hover:opacity-100">×</button>
                </span>
              ))}
              {Object.entries(FLAGS).filter(([k]) => out.filters[k]).map(([k, label]) => (
                <span key={k} className="rounded-full bg-peak-soft text-peak px-2.5 py-1 text-xs font-bold">{label}</span>
              ))}
            </div>
            {out.relaxed && out.relaxed.length > 0 && (
              <p className="text-xs text-muted-foreground">Dropped to find matches: {out.relaxed.map((k) => TAG_LABEL[k] ?? k).join(", ")}.</p>
            )}
          </div>

          <div className="flex items-baseline justify-between">
            <h2 className="text-lg font-bold tabular">{out.total} matching dataset{out.total === 1 ? "" : "s"}</h2>
            <span className="text-xs text-muted-foreground">ranked by fit, depth, recency, reuse</span>
          </div>
          {out.results.length === 0 && <p className="text-muted-foreground">Nothing tagged matches yet. Only {stats?.tagged ?? "some"} datasets are tagged so far; try removing a filter.</p>}
          <div className="grid gap-3">
            {out.results.map((r: Result, i: number) => <ResultCard key={r.accession} r={r} rank={i + 1} />)}
          </div>
        </section>
      )}
    </div>
  );
}
