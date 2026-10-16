"use client";
import { useState } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { METHOD_TAGS, pretty, TAG_LABEL, type Reason, type Result } from "@/lib/api";

function Chip({ r, kind }: { r: Reason; kind: "fit" | "watch" }) {
  const [open, setOpen] = useState(false);
  const has = !!r.evidence;
  return (
    <span className="inline-block max-w-full">
      <button
        type="button"
        onClick={() => has && setOpen((o) => !o)}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-bold text-left",
          kind === "fit" ? "bg-ok-soft text-ok" : "bg-peak-soft text-peak",
          has && "cursor-pointer hover:brightness-95"
        )}
        aria-expanded={open}
      >
        {kind === "fit" ? "✓" : "!"} {r.text}
        {r.confidence !== null && r.confidence !== undefined && (
          <span className="opacity-70 tabular">{Math.round(r.confidence * 100)}%</span>
        )}
      </button>
      {open && (
        <span className="block mt-1 mb-1 ml-1 pl-3 border-l-2 border-current/30 text-xs text-muted-foreground italic">
          “{r.evidence}” <span className="not-italic opacity-70">· from {r.source}</span>
        </span>
      )}
    </span>
  );
}

export function ResultCard({ r, rank }: { r: Result; rank: number }) {
  const t = r.tags;
  return (
    <article className="rounded-xl border bg-card p-4 sm:p-5 grid gap-3">
      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="text-xs font-bold text-muted-foreground tabular w-5">{rank}</span>
        <Link href={`/dataset/${r.accession}`} className="font-mono text-sm font-semibold text-teal hover:underline">
          {r.accession}
        </Link>
        <span className="text-xs text-muted-foreground tabular">{r.publication_date?.slice(0, 4)}</span>
        {r.downloads ? <span className="text-xs text-muted-foreground tabular">{r.downloads.toLocaleString()} downloads</span> : null}
        <a href={r.pride_url} target="_blank" rel="noreferrer" className="ml-auto text-xs underline text-muted-foreground">PRIDE ↗</a>
        {r.pubmed_id && (
          <a href={`https://europepmc.org/article/MED/${r.pubmed_id}`} target="_blank" rel="noreferrer" className="text-xs underline text-muted-foreground">paper ↗</a>
        )}
      </div>
      <h3 className="text-base sm:text-lg font-bold leading-snug">
        <Link href={`/dataset/${r.accession}`} className="hover:underline">{r.summary ?? r.title}</Link>
      </h3>
      {r.summary && <p className="text-sm text-muted-foreground -mt-1 line-clamp-2">{r.title}</p>}
      <div className="flex flex-wrap gap-1.5 text-xs">
        {["organism", "tissue", "cell_line", "disease", "sample_type"].map((k) =>
          t[k] ? (
            <span key={k} className="rounded-full bg-teal-soft text-teal px-2 py-0.5 font-bold">{pretty(t[k])}</span>
          ) : null
        )}
        {METHOD_TAGS.map((k) =>
          t[k] ? (
            <span key={k} className="rounded-full bg-secondary text-secondary-foreground px-2 py-0.5">
              <span className="opacity-60">{TAG_LABEL[k]} </span>{pretty(t[k])}
            </span>
          ) : null
        )}
      </div>
      {(r.fits.length > 0 || r.watch.length > 0) && (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap gap-1.5">{r.fits.map((x, i) => <Chip key={"f" + i} r={x} kind="fit" />)}</div>
          <div className="flex flex-wrap gap-1.5">{r.watch.map((x, i) => <Chip key={"w" + i} r={x} kind="watch" />)}</div>
        </div>
      )}
    </article>
  );
}
