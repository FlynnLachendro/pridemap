"use client";
import { useEffect, useState } from "react";
import { markSetupSeen } from "@/lib/progress-store";
import { API } from "@/lib/api";

type Param = { value: unknown; why?: string; tag?: string; confidence?: number | null; evidence?: string | null; missed_cleavages?: number; from_pride_ptms?: string[] };
type Out = {
  accession: string; tagged: boolean; has_submitter_results: boolean;
  files: { total: number; all_ftp_url: string; groups: Record<string, { count: number; bytes: number; examples: { file_name: string; size_bytes: number; ftp: string | null }[] }> };
  recipe: null | { tool: Param; enzyme: Param; fixed_modifications: Param; variable_modifications: Param; quantification: Param; fdr: Param; instrument_preset: Param; fasta: { uniprot_proteome: string | null; organism: string | null; url: string | null; note: string }; notes: string[] };
  plan: null | {
    vendor_label: string; acquisition: string; tag_acquisition: string | null; raw_count: number;
    engine: string | null; engine_label: string | null; why: string | null; output_table: string | null;
    maxquant_offered: boolean; maxquant_reason: string | null; caveat: string | null; conflict: string | null;
    convert: { from: string; to: string; tool: string; url: string } | null; kit_url: string;
    mixed: boolean; runs_dda: number; runs_dia: number;
    stages: { id: string; label: string; detail: string; where: string; substages?: { name: string; detail: string }[] }[];
    tracks: { kind: string; engine: string; engine_label: string; run_count: number; output_table: string; config: string }[];
  };
};

const CAT: Record<string, string> = { RAW: "Raw instrument files", PEAK: "Peak lists (already converted)", RESULT: "Submitter's results (protein/peptide tables)", SEARCH: "Submitter's search output", OTHER: "Other", FASTA: "FASTA used", SPECTRUM_LIBRARY: "Spectral library", EXPERIMENTAL_DESIGN: "Experimental design" };
const gb = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${(b / 1e6).toFixed(0)} MB`);
const show = (v: unknown) => (Array.isArray(v) ? v.join(", ") : String(v));

export function Reanalyse({ accession }: { accession: string }) {
  const [out, setOut] = useState<Out | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { markSetupSeen(accession); }, [accession]);
  useEffect(() => {
    let live = true;
    fetch(`${API}/dataset/${accession}/reanalyse`).then(async (r) => { if (!r.ok) throw new Error(await r.text()); return r.json(); })
      .then((d) => { if (live) setOut(d); }).catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, [accession]);
  if (err) return <p className="text-warn text-sm">{err}</p>;
  if (!out) return <p className="text-sm text-muted-foreground">Looking up files on PRIDE…</p>;
  const r = out.recipe;
  const order = ["RESULT", "SEARCH", "PEAK", "RAW"];
  const cats = [...order.filter((c) => out.files.groups[c]), ...Object.keys(out.files.groups).filter((c) => !order.includes(c))];
  return (
    <section className="grid gap-4">
      <div>
        <h2 className="text-lg">Reanalyse this dataset</h2>
        <p className="text-sm text-muted-foreground">
          {out.has_submitter_results ? "Submitter results are on PRIDE; you may not need to re-search." : "No submitter results on PRIDE; search the raw files with the parameters below."}
        </p>
      </div>

      <div className="rounded-xl border bg-card divide-y">
        {cats.map((c) => {
          const g = out.files.groups[c];
          return (
            <div key={c} className="p-3 grid sm:grid-cols-[1fr_auto] gap-2 text-sm">
              <div>
                <div className="font-semibold">{CAT[c] ?? c} <span className="text-muted-foreground font-normal tabular">· {g.count} file{g.count === 1 ? "" : "s"} · {gb(g.bytes)}</span></div>
                <ul className="text-xs text-muted-foreground mt-1 grid gap-0.5">
                  {g.examples.map((f) => (
                    <li key={f.file_name} className="truncate">{f.ftp ? <a className="underline" href={f.ftp}>{f.file_name}</a> : f.file_name} <span className="tabular">({gb(f.size_bytes)})</span></li>
                  ))}
                  {g.count > 3 && <li>… and {g.count - 3} more</li>}
                </ul>
              </div>
              <a className="self-start text-xs underline whitespace-nowrap" href={`${API}${out.files.all_ftp_url}?category=${c}`} target="_blank" rel="noreferrer">all {c} links ↗</a>
            </div>
          );
        })}
        {out.files.total === 0 && <div className="p-3 text-sm text-muted-foreground">PRIDE returned no file list (private or not yet released).</div>}
      </div>

      {r && (
        <div className="rounded-xl border bg-card overflow-hidden">
          <div className="px-4 py-3 border-b flex items-baseline justify-between gap-3 flex-wrap">
            <h3 className="text-base">Search parameters, derived from the tags</h3>
            <span className="text-xs text-muted-foreground">for {show(r.tool.value)}</span>
          </div>
          <div className="divide-y text-sm">
            {([
              ["Tool", r.tool, r.tool.why],
              ["Enzyme", r.enzyme, `missed cleavages: ${r.enzyme.missed_cleavages}`],
              ["Fixed modifications", r.fixed_modifications, null],
              ["Variable modifications", r.variable_modifications, r.variable_modifications.from_pride_ptms?.length ? `PRIDE lists: ${r.variable_modifications.from_pride_ptms.join(", ")}` : "PRIDE lists no modifications"],
              ["Quantification", r.quantification, null],
              ["FDR", r.fdr, null],
              ["Instrument preset", r.instrument_preset, null],
            ] as [string, Param, string | null][]).map(([label, p, extra]) => (
              <div key={label} className="grid sm:grid-cols-[170px_1fr] gap-x-4 gap-y-1 px-4 py-3">
                <div className="label pt-0.5">{label}</div>
                <div>
                  <div className="font-semibold">{show(p.value)}</div>
                  {extra && <div className="text-xs text-muted-foreground">{extra}</div>}
                  {p.tag && <div className="text-xs text-muted-foreground mt-0.5">from <span className="font-semibold">{p.tag.replace("_", " ")}</span>{p.confidence != null && <> · {Math.round(p.confidence * 100)}%</>}{p.evidence && <> · <i>“{p.evidence}”</i></>}</div>}
                </div>
              </div>
            ))}
            <div className="grid sm:grid-cols-[170px_1fr] gap-x-4 gap-y-1 px-4 py-3">
              <div className="label pt-0.5">Protein database</div>
              <div>
                <div className="font-semibold">{r.fasta.organism ?? "unknown organism"}{r.fasta.uniprot_proteome && <> · UniProt {r.fasta.uniprot_proteome}</>}</div>
                <div className="text-xs text-muted-foreground">{r.fasta.note}{r.fasta.url && <> · <a className="underline" href={r.fasta.url}>download FASTA</a></>}</div>
              </div>
            </div>
            {r.notes.length > 0 && (
              <div className="px-4 py-3 text-xs text-muted-foreground grid gap-1">{r.notes.map((n) => <div key={n}>• {n}</div>)}</div>
            )}
          </div>
        </div>
      )}

      {out.plan && out.plan.engine && out.plan.engine !== "quantms" && (
        <div className="rounded-xl border bg-card overflow-hidden" data-tour="searchkit">
          <div className="px-4 py-3 border-b flex items-center justify-between gap-3 flex-wrap">
            <h3 className="text-base">
              Run the search on your machine with {out.plan.tracks.map((t) => t.engine_label).join(" + ")}
            </h3>
            <a className="btn btn-primary btn-sm" href={`${API}${out.plan.kit_url}`} download>Download kit (zip)</a>
          </div>
          <div className="px-4 py-3 grid gap-2 text-sm">
            <p className="text-xs text-muted-foreground">{out.plan.why}</p>
            {out.plan.conflict && <p className="text-warn text-xs">⚠ {out.plan.conflict}</p>}
            {out.plan.caveat && <p className="text-xs text-muted-foreground">{out.plan.caveat}</p>}
            <div className="rounded-md border bg-muted/30 px-3 py-2 grid gap-0.5">
              <code className="text-xs break-all select-all">
                curl -fsSL {API}/dataset/{accession}/pipeline.sh | bash
              </code>
              <span className="text-xs text-muted-foreground">
                Checks your tools and free space first, then runs every stage end to end and uploads the finished
                table back into Analyse. Resumes where it stopped; MaxQuant&apos;s own internal steps are tailed from
                <code> combined/proc/</code> so the long middle is not a black box. Full record in
                <code> progress.jsonl</code>.
              </span>
            </div>
            <div className="label">What you get</div>
            <ul className="grid gap-0.5 text-xs text-muted-foreground">
              <li>• <code>run_all.sh</code>: the whole pipeline in one command, with per-stage progress</li>
              <li>• <code>download_raw.sh</code>: pulls the {out.plan.raw_count} raw files ({out.plan.vendor_label}) from PRIDE&apos;s FTP</li>
              {out.plan.convert && <li>• <code>convert_to_mzml.sh</code>: {out.plan.convert.from} → {out.plan.convert.to} via <a className="underline" href={out.plan.convert.url} target="_blank" rel="noreferrer">{out.plan.convert.tool}</a></li>}
              <li>• <code>download_fasta.sh</code>: the reference proteome, saved where the configs expect it</li>
              {out.plan.tracks.map((t) => (
                <li key={t.kind}>• <code>{t.config}</code>: {t.engine_label} settings for the {t.run_count} {t.kind} run{t.run_count === 1 ? "" : "s"} → <code>{t.output_table}</code> uploads straight into Analyse</li>
              ))}
            </ul>
            {!out.plan.maxquant_offered && out.plan.maxquant_reason && (
              <p className="text-xs text-faint"><span className="font-semibold">Why not MaxQuant:</span> {out.plan.maxquant_reason}</p>
            )}
          </div>
        </div>
      )}

      {out.files.total > 0 && (
        <div className="rounded-xl border bg-card overflow-hidden" data-tour="quantms">
          <div className="px-4 py-3 border-b flex items-center justify-between gap-3 flex-wrap">
            <h3 className="text-base">Reanalyse in the cloud with quantms</h3>
            <a className="btn btn-primary btn-sm" href={`${API}/dataset/${accession}/quantms.zip`} download={`${accession}_quantms.zip`}>Download kit (zip)</a>
          </div>
          <div className="px-4 py-3 grid gap-1 text-sm">
            <div className="label">What you get</div>
            <ul className="grid gap-0.5 text-xs text-muted-foreground">
              <li>• <code>sdrf.tsv</code>: one row per raw file with organism, tissue, enzyme, modifications and FTP link filled from the tags</li>
              <li>• <code>params.yml</code>: quantms settings (FASTA, DDA/DIA, labelling, 1% FDR) {r ? "from the recipe above" : "with unknowns marked not available"}</li>
              <li>• <code>run.sh</code>: <code>nextflow run bigbio/quantms</code>; runs on your machine or cloud, not here</li>
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}
