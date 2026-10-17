"use client";
import { useEffect, useState } from "react";
import { API } from "@/lib/api";

type P = { found: boolean; name: string; accession?: string; protein_name?: string; gene?: string; organism?: string; length?: number; mass_kda?: number;
  tissue_specificity?: string | null; function?: string; locations?: string[]; tryptic_peptides_usable?: number; detectability?: string; url?: string; error?: string };

export function ProteinCard({ name, organism, tissue }: { name: string; organism?: string; tissue?: string }) {
  const [p, setP] = useState<P | null>(null);
  useEffect(() => {
    let live = true;
    const u = new URLSearchParams({ name, ...(organism ? { organism } : {}) });
    fetch(`${API}/protein?${u}`).then((r) => r.json()).then((d) => { if (live) setP(d); }).catch(() => {});
    return () => { live = false; };
  }, [name, organism]);
  if (!p) return null;
  if (!p.found) return <p className="text-xs text-faint font-mono">No reviewed UniProt entry for “{name}”{organism ? ` in ${organism}` : ""}.</p>;
  const tissueHit = tissue && p.tissue_specificity ? p.tissue_specificity.toLowerCase().includes(tissue.toLowerCase()) : null;
  const level = p.detectability?.split(":")[0];
  return (
    <div className="rounded-xl border bg-card p-4 grid gap-2 text-sm">
      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="label">Your protein</span>
        <span className="font-semibold">{p.protein_name}</span>
        <span className="font-mono text-xs text-muted-foreground">{p.gene} · {p.organism} · {p.length} aa · {p.mass_kda} kDa · <a className="underline" href={p.url} target="_blank" rel="noreferrer">UniProt {p.accession}</a></span>
      </div>
      <div className="flex flex-wrap gap-1.5 text-xs">
        <span className={`pill ${level === "good" ? "bg-ok-soft text-ok" : level === "fair" ? "bg-peak-soft text-peak" : "bg-warn-soft text-warn"}`}>detectability {p.detectability}</span>
        {tissueHit === true && <span className="pill bg-ok-soft text-ok">UniProt reports expression in {tissue}</span>}
        {tissueHit === false && <span className="pill bg-peak-soft text-peak">UniProt&apos;s tissue note doesn&apos;t mention {tissue}</span>}
        {p.locations?.map((l) => <span key={l} className="pill">{l}</span>)}
      </div>
      {p.tissue_specificity && <p className="text-muted-foreground"><span className="label mr-2">Tissue</span>{p.tissue_specificity}</p>}
      {p.function && <p className="text-muted-foreground line-clamp-2"><span className="label mr-2">Function</span>{p.function}</p>}

    </div>
  );
}
