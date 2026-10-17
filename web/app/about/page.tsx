import { stats } from "@/lib/api";

export default async function About() {
  const s = await stats().catch(() => null);
  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  return (
    <article className="max-w-3xl grid gap-8">
      <div>
        <h1 className="text-4xl">How PRIDE Map works</h1>
        <p className="text-muted-foreground mt-2">Built in a weekend at Reinvent the Wheel (Science Works / Inherent Labs, London, Oct 2026) by Flynn Lachendro with Charlotte Ellison, Juri Westendorf, Ahmed Dawoud and Odetta Antico. MIT licence. Code at <a className="underline" href="https://github.com/FlynnLachendro/pridemap">github.com/FlynnLachendro/pridemap</a>.</p>
      </div>

      <section className="grid gap-2">
        <h2 className="text-2xl">The problem</h2>
        <p>PRIDE holds {s ? s.projects.toLocaleString() : "42,000+"} mass‑spectrometry proteomics datasets. Whether one can answer your question depends on how the sample was prepared and how the instrument was run: enrichment, DDA vs DIA, labelling, enzyme, fractionation, instrument. Those live in free‑text protocol paragraphs or in the paper&apos;s Methods section, not in fields you can filter on. So PRIDE&apos;s search returns hundreds of hits and you open twenty papers by hand, and you still can&apos;t tell whether two datasets are comparable or what search settings to use.</p>
      </section>

      <section className="grid gap-2">
        <h2 className="text-2xl">What the app is</h2>
        <ol className="list-decimal pl-5 grid gap-1.5">
          <li><b>Crawl.</b> Every PRIDE project&apos;s metadata through the public REST API: titles, descriptions, the sample‑processing and data‑processing paragraphs, organism, instrument, PubMed link. No raw files, ever.</li>
          <li><b>Read the paper.</b> The open‑access Methods section from Europe PMC, where there is one (about 60%).</li>
          <li><b>Tag.</b> A language model reads both and fills a fixed 14‑tag form. Every tag carries a confidence, a source (PRIDE metadata, paper, inferred) and the verbatim sentence it came from. No embeddings; one careful read per dataset with a strict output schema.</li>
          <li><b>Store.</b> One SQLite file with full‑text search. A FastAPI service with OpenAPI docs at <a className="underline" href={`${api}/docs`}>{api}/docs</a>, so scripts and agents can use everything without the UI.</li>
          <li><b>Use.</b> This site.</li>
        </ol>
      </section>

      <section className="grid gap-2">
        <h2 className="text-2xl">The loop, step by step</h2>
        <dl className="grid gap-3">
          <div><dt className="font-semibold">Ask</dt><dd className="text-muted-foreground">A question in plain words becomes filter chips (Claude, with a rule‑based fallback), results come back ranked with &quot;fits&quot; and &quot;watch&quot; reasons that expand to the evidence sentence, and facet counts let you narrow further. Protein names can&apos;t be filtered on, because they aren&apos;t in the metadata; the app says so rather than pretending.</dd></div>
          <div><dt className="font-semibold">Map</dt><dd className="text-muted-foreground">All tagged datasets as a treemap by organism, sample type, enrichment and acquisition. Click to drill, click a leaf to search it.</dd></div>
          <div><dt className="font-semibold">Dataset card</dt><dd className="text-muted-foreground">Every tag with confidence bar and quote; the raw PRIDE fields; the paper; a &quot;wrong?&quot; button that records a correction.</dd></div>
          <div><dt className="font-semibold">Compare</dt><dd className="text-muted-foreground">Two or more datasets side by side with a verdict: comparable, with caution, or not, and exactly which method difference breaks it (label‑free vs TMT, DDA vs DIA, different enrichment…). This is the check you need before running group stats across datasets.</dd></div>
          <div><dt className="font-semibold">Reanalyse</dt><dd className="text-muted-foreground">The files PRIDE holds (raw, peak lists, submitter results) with sizes and links, plus the database‑search parameters derived from the tags: tool, enzyme, fixed and variable modifications, quantification, FDR, FASTA. We can&apos;t run MaxQuant or Mascot for you; this is the set‑up sheet so your search matches how the data was made.</dd></div>
          <div><dt className="font-semibold">Analyse</dt><dd className="text-muted-foreground">Upload a protein table, pick group A and B, get log2 fold change, Welch t‑test, Benjamini‑Hochberg FDR and a volcano plot. Odetta&apos;s lab pipeline runs when the columns match it. This is the pandas/R step, done in the browser.</dd></div>
          <div><dt className="font-semibold">Workflow</dt><dd className="text-muted-foreground">The five steps strung together with a shortlist, a written case for each candidate, and a human tick before anything moves on.</dd></div>
          <div><dt className="font-semibold">Eval</dt><dd className="text-muted-foreground">Humans vs model on a gold set, and five models vs each other on 200 datasets, so you know how far to trust the tags.</dd></div>
        </dl>
      </section>

      <section className="grid gap-2">
        <h2 className="text-2xl">Status</h2>
        {s ? (
          <ul className="list-disc pl-5">
            <li className="tabular">{s.tagged.toLocaleString()} of {s.projects.toLocaleString()} datasets tagged</li>
            <li className="tabular">{s.papers_with_methods.toLocaleString()} papers&apos; Methods sections read</li>
            <li className="tabular">{s.corrections} corrections reported by users</li>
          </ul>
        ) : <p className="text-warn">API not reachable.</p>}
        <p>Tagging the rest of the archive costs about $60 on a cheap model and a few hours; the pipeline is one command. Every tag is correctable from the dataset page; corrections feed the next tagging round.</p>
      </section>

      <section className="grid gap-2">
        <h2 className="text-2xl">Run it yourself</h2>
        <pre className="rounded-lg border bg-card p-3 text-xs overflow-x-auto">{`git clone https://github.com/FlynnLachendro/pridemap && cd pridemap
uv run load_demo.py      # bundled tagged slice, no crawl needed
./dev.sh                 # API :8000/docs, web :3000`}</pre>
      </section>
    </article>
  );
}
