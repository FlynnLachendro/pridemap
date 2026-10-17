"use client";
import { useState } from "react";

/** Long text with a clamped preview and a show more / less toggle. */
export function Expandable({ text, lines = 3, className = "" }: { text: string | null | undefined; lines?: number; className?: string }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  const long = text.length > lines * 110;
  return (
    <div className={className}>
      <p className={`whitespace-pre-wrap leading-relaxed ${open ? "" : `line-clamp-${lines}`}`} style={open ? undefined : { display: "-webkit-box", WebkitLineClamp: lines, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{text}</p>
      {long && <button type="button" onClick={() => setOpen((o) => !o)} className="text-xs underline text-muted-foreground mt-1 hover:text-teal">{open ? "show less" : "show more"}</button>}
    </div>
  );
}

/** A section whose body is hidden until opened; header shows a one-line summary. */
export function Collapsible({ title, summary, defaultOpen = false, children, id }: { title: string; summary?: React.ReactNode; defaultOpen?: boolean; children: React.ReactNode; id?: string }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="card overflow-hidden" id={id}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-surface-2">
        <span className={`inline-block transition-transform duration-150 ${open ? "rotate-90" : ""}`} aria-hidden>▸</span>
        <span className="font-bold">{title}</span>
        {summary && <span className="text-xs text-muted-foreground truncate ml-1">{summary}</span>}
        <span className="ml-auto text-xs text-faint font-mono">{open ? "hide" : "show"}</span>
      </button>
      {open && <div className="border-t px-4 py-3">{children}</div>}
    </section>
  );
}
