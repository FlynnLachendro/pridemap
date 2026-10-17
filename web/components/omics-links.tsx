"use client";
import { useEffect, useState } from "react";
import { API } from "@/lib/api";

type Row = { id: string; source: string; omics_type: string; all_types: string[]; name: string; description: string; organisms: string[]; publication_date: string | null; link: string };
type Out = { accession: string; query: string | null; total: number; cached: boolean; groups: { omics_type: string; datasets: Row[] }[] };

const SOURCE: Record<string, string> = { "biostudies-arrayexpress": "ArrayExpress", arrayexpress: "ArrayExpress", geo: "GEO", metabolights_dataset: "MetaboLights", metabolomics_workbench: "Metabolomics Workbench", massive: "MassIVE", ega: "EGA", eva: "EVA", project: "BioProject", dbgap: "dbGaP", gnps: "GNPS", lincs: "LINCS", expression_atlas: "Expression Atlas" };

export function OmicsLinks({ accession }: { accession: string }) {
  const [out, setOut] = useState<Out | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    fetch(`${API}/dataset/${accession}/omics`).then(async (r) => { if (!r.ok) throw new Error(await r.text()); return r.json(); })
      .then((d) => { if (live) setOut(d); }).catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, [accession]);
  return (
    <div className="rounded-xl border bg-card p-4 text-sm grid gap-3" data-tour="omics">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-lg">Other omics on the same system</h2>
        <span className="text-xs text-muted-foreground">via OmicsDI</span>
      </div>
      {err && <p className="text-warn text-xs">{err}</p>}
      {!out && !err && (
        <div className="grid gap-2" aria-busy="true">
          {[0, 1, 2].map((i) => <div key={i} className="skeleton h-9" />)}
        </div>
      )}
      {out && out.total === 0 && <p className="text-xs text-muted-foreground">None found for this organism and tissue.</p>}
      {out && out.groups.map((g) => (
        <div key={g.omics_type} className="grid gap-1.5 enter">
          <div className="label">{g.omics_type}</div>
          <ul className="grid gap-1.5">
            {g.datasets.map((d) => (
              <li key={`${d.source}/${d.id}`} className="min-w-0">
                <a className="underline font-medium" href={d.link} target="_blank" rel="noreferrer" title={d.description || d.name}>{d.name || d.id}</a>
                <div className="text-xs text-muted-foreground flex gap-1.5 flex-wrap">
                  <span className="font-mono">{d.id}</span>
                  <span>· {SOURCE[d.source] ?? d.source}</span>
                  {d.publication_date && <span className="tabular">· {String(d.publication_date).slice(0, 4)}</span>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
