import { stats } from "@/lib/api";


export default async function About() {
  const s = await stats().catch(() => null);
  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  return (
    <article className="prose-sm max-w-3xl grid gap-6">
      <div>
        <h1 className="text-3xl font-extrabold">How PRIDE Map works</h1>
        <p className="text-muted-foreground mt-2">Built in a weekend at Reinvent the Wheel (Science Works / Inherent Labs, Oct 2026) by Flynn Lachendro with Charlotte Ellison, Juri Westendorf and Ahmed Dawoud. MIT licence.</p>
      </div>

      <section className="grid gap-2">
        <h2 className="text-xl font-bold">The problem</h2>
        <p>PRIDE holds {s ? s.projects.toLocaleString() : "42,000+"} mass‑spectrometry proteomics datasets. Whether one of them can answer your question depends on how the sample was prepared and how the instrument was run: enrichment, DDA vs DIA, labelling, enzyme, fractionation, instrument. Those live in free‑text protocol paragraphs or in the paper&apos;s Methods section, not in fields you can filter on. So PRIDE&apos;s search returns hundreds of hits and you open twenty papers by hand.</p>
      </section>

      <section className="grid gap-2">
        <h2 className="text-xl font-bold">What we do</h2>
        <ol className="list-decimal pl-5 grid gap-1">
          <li>Crawl every PRIDE project&apos;s metadata through the public REST API (no raw files, ~250 MB of JSON).</li>
          <li>Fetch the open‑access Methods section of each dataset&apos;s paper from Europe PMC.</li>
          <li>A Claude model reads both and fills a fixed schema of tags, each with a confidence, a source, and the verbatim sentence it came from.</li>
          <li>Everything lands in one SQLite file. A FastAPI service exposes search, ask, map, dataset and SDRF export, with OpenAPI docs at <a className="underline" href={`${api}/docs`}>{api}/docs</a> so agents can drive it too.</li>
          <li>This UI turns a plain question into those filters, ranks the matches, and shows why each one fits or might not.</li>
        </ol>
      </section>

      <section className="grid gap-2">
        <h2 className="text-xl font-bold">The tags</h2>
        <p>Aligned to SDRF‑Proteomics so they export as a standard TSV. organism · sample type · tissue · cell line · disease · enrichment (none / phospho / ubiquitin / glyco / acetyl / AP‑MS / proximity labelling / secretome / membrane) · acquisition (DDA / DIA / PRM‑SRM / top‑down / MALDI) · labelling (label‑free / TMT / iTRAQ / SILAC / dimethyl) · enzyme · fractionation · instrument family · proteins identified · search engine.</p>
      </section>

      <section className="grid gap-2">
        <h2 className="text-xl font-bold">Status</h2>
        {s ? (
          <ul className="list-disc pl-5">
            <li className="tabular">{s.tagged.toLocaleString()} of {s.projects.toLocaleString()} datasets tagged</li>
            <li className="tabular">{s.papers_with_methods.toLocaleString()} papers&apos; Methods sections read</li>
            <li className="tabular">{s.corrections} corrections reported by users</li>
          </ul>
        ) : <p className="text-warn">API not reachable.</p>}
        <p>Tagging the full archive is a matter of running <code>uv run tag.py</code> for a few hours. Every tag is correctable from the dataset page; corrections feed the next tagging round.</p>
      </section>

      <section className="grid gap-2">
        <h2 className="text-xl font-bold">Run it yourself</h2>
        <pre className="rounded-lg border bg-card p-3 text-xs overflow-x-auto">{`git clone https://github.com/FlynnLachendro/pridemap && cd pridemap
uv run crawl_pride.py && uv run build_db.py        # all PRIDE metadata → data/pride.db
uv run fetch_methods.py --limit 500               # Methods sections from Europe PMC
ANTHROPIC_API_KEY=... uv run tag.py --limit 500   # Claude tags
uv run api/main.py                                # http://localhost:8000/docs
cd web && npm i && npm run dev                    # http://localhost:3000`}</pre>
      </section>
    </article>
  );
}
