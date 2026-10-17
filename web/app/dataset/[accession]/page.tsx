import Link from "next/link";
import { dataset, METHOD_TAGS, pretty, TAG_LABEL } from "@/lib/api";
import { Correct } from "@/components/correct";
import { CompareToggle } from "@/components/compare-toggle";
import { Reanalyse } from "@/components/reanalyse";
import { Collapsible, Expandable } from "@/components/expandable";
import { CompareFromHere } from "@/components/compare-from-here";


const ORDER = ["organism", "sample_type", "tissue", "cell_line", "disease", ...METHOD_TAGS, "depth_proteins"];

function Bar({ c }: { c: number }) {
  const col = c >= 0.85 ? "bg-ok" : c >= 0.6 ? "bg-peak" : "bg-warn";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs tabular text-muted-foreground" title={`confidence ${Math.round(c * 100)}%`}>
      <span className="inline-block w-16 h-1.5 rounded bg-muted overflow-hidden"><span className={`block h-full ${col}`} style={{ width: `${Math.round(c * 100)}%` }} /></span>
      {Math.round(c * 100)}%
    </span>
  );
}

export default async function DatasetPage({ params }: { params: Promise<{ accession: string }> }) {
  const { accession } = await params;
  let d;
  try { d = await dataset(accession); } catch { return <p className="text-warn">Unknown accession {accession}.</p>; }
  const p = d.project;
  const ev = Object.fromEntries(d.evidence.map((e) => [e.tag, e]));
  const list = (k: string) => (Array.isArray(p[k]) ? (p[k] as string[]) : []);

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      <div className="grid gap-5 min-w-0">
        <div>
          <div className="flex gap-3 items-baseline flex-wrap text-sm">
            <span className="font-mono font-semibold text-teal">{p.accession}</span>
            <span className="text-muted-foreground tabular">{p.publication_date}</span>
            {p.organic_downloads ? <span className="text-muted-foreground tabular">{p.organic_downloads.toLocaleString()} downloads</span> : null}
            <a className="underline text-muted-foreground" href={d.pride_url} target="_blank" rel="noreferrer">PRIDE ↗</a>
            {d.paper_url && <a className="underline text-muted-foreground" href={d.paper_url} target="_blank" rel="noreferrer">paper ↗</a>}
            {d.tagged && <CompareToggle accession={p.accession} />}
          </div>
          <h1 className="text-2xl sm:text-3xl mt-1">{p.title}</h1>
          {d.tags.summary_one_line && <p className="mt-2 text-lg text-muted-foreground">{String(d.tags.summary_one_line)}</p>}
        </div>

        {!d.tagged && (
          <div className="rounded-xl border border-peak bg-peak-soft/40 p-4 text-sm">
            This dataset isn&apos;t tagged yet. Only the raw PRIDE fields below are available. Run <code>uv run tag.py --accessions</code> to tag it.
          </div>
        )}

        {d.tagged && (
          <section className="rounded-xl border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b flex items-center justify-between">
              <h2 className="text-lg">Tags, with the sentence each came from</h2>
              <span className="text-xs text-muted-foreground">tagged by {String(d.tags.model ?? "Claude")}</span>
            </div>
            <div className="divide-y">
              {ORDER.map((k) => {
                const e = ev[k];
                const v = d.tags[k];
                return (
                  <div key={k} className="grid sm:grid-cols-[150px_1fr] gap-x-4 gap-y-1 px-4 py-3">
                    <div className="label pt-0.5">{TAG_LABEL[k] ?? k}</div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-3 flex-wrap">
                        <span className={`font-semibold ${v === null || v === undefined ? "text-muted-foreground" : ""}`}>{k === "depth_proteins" && typeof v === "number" ? v.toLocaleString() : pretty(v)}</span>
                        {e && <Bar c={e.confidence} />}
                        {e && <span className="pill">{e.source}</span>}
                        <Correct accession={p.accession} tag={k} current={v === null || v === undefined ? "" : String(v)} />
                      </div>
                      {e?.evidence && <div className="text-sm text-muted-foreground italic mt-1 border-l-2 pl-3"><Expandable text={`“${e.evidence}”`} lines={1} /></div>}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        <div id="reanalyse" data-tour="reanalyse">
          <Collapsible title="Reanalyse this dataset" summary="files on PRIDE and the search parameters to use" defaultOpen={false}>
            <Reanalyse accession={p.accession} />
          </Collapsible>
        </div>

        <Collapsible title="What PRIDE says" summary="the submitter's own description, protocols and fields">
          <div className="grid gap-3 text-sm">
            <Field label="Description" text={p.description} />
            <Field label="Sample processing protocol" text={p.sample_protocol} />
            <Field label="Data processing protocol" text={p.data_protocol} />
            <div className="grid sm:grid-cols-2 gap-3 pt-1">
              {["organisms", "organism_parts", "diseases", "instruments", "experiment_types", "quant_methods", "softwares", "keywords"].map((k) => (
                <div key={k}>
                  <div className="label">{k.replace("_", " ")}</div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {list(k).length ? list(k).map((x) => <span key={x} className="pill">{x}</span>) : <span className="text-xs text-muted-foreground">empty</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </Collapsible>
      </div>

      <aside className="grid gap-4 content-start">
        <div className="rounded-xl border bg-card p-4 text-sm grid gap-2">
          <h2 className="text-lg">Paper</h2>
          {d.paper ? (
            <>
              <p>{d.paper.title ?? "—"}</p>
              <p className="text-xs text-muted-foreground">
                {d.paper.is_open_access ? `Open access · Methods section read (${(d.paper.methods_chars ?? 0).toLocaleString()} chars)` : "Not open access, tagged from PRIDE metadata only"}
              </p>
              {d.paper_url && <a className="underline text-xs" href={d.paper_url} target="_blank" rel="noreferrer">Europe PMC ↗</a>}
              {d.paper.is_open_access ? <a className="underline text-xs" href={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/dataset/${p.accession}/methods`} target="_blank" rel="noreferrer">extracted Methods text ↗</a> : null}
            </>
          ) : (
            <p className="text-xs text-muted-foreground">No PubMed link on this dataset.</p>
          )}
        </div>
        {d.tagged && <CompareFromHere accession={p.accession} />}
        <div className="rounded-xl border bg-card p-4 text-sm grid gap-2">
          <h2 className="text-lg">Find similar</h2>
          <div className="flex flex-wrap gap-1.5">
            {["organism", "tissue", "enrichment", "acquisition", "labelling"].map((k) =>
              d.tags[k] ? (
                <Link key={k} href={`/?${k}=${encodeURIComponent(String(d.tags[k]))}`} className="pill bg-teal-soft text-teal px-2.5 py-1 hover:brightness-95">
                  {TAG_LABEL[k]}: {pretty(d.tags[k])}
                </Link>
              ) : null
            )}
          </div>
        </div>
        {d.corrections.length > 0 && (
          <div className="rounded-xl border bg-card p-4 text-sm grid gap-2">
            <h2 className="text-lg">Corrections reported</h2>
            {d.corrections.map((c) => (
              <p key={c.id} className="text-xs"><b>{TAG_LABEL[c.tag] ?? c.tag}</b> → {c.suggested_value ?? "?"} {c.note && <span className="text-muted-foreground">· {c.note}</span>}</p>
            ))}
          </div>
        )}
      </aside>
    </div>
  );
}

function Field({ label, text }: { label: string; text: string | null | undefined }) {
  if (!text) return null;
  return (
    <div>
      <div className="label">{label}</div>
      <Expandable text={text} lines={3} className="mt-1" />
    </div>
  );
}
