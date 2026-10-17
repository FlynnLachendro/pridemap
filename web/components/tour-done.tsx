"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { API, TOUR_Q_HISTORY_KEY } from "@/lib/api";
import { useCompare } from "@/lib/compare-store";

const PLACES = [
  { href: "/", title: "Browse", when: "Start here with keywords, an accession, or a question. Filters are the method tags." },
  { href: "/map", title: "Map", when: "Get a feel for what exists: every tagged dataset by organism, sample, enrichment, acquisition." },
  { href: "/compare", title: "Compare", when: "Before pooling datasets in one analysis: will the methods let you?" },
  { href: "/flow", title: "Workflow", when: "Run the whole loop with your shortlist: confirm, set up the search, analyse, check." },
  { href: "/analyse", title: "Analyse", when: "Protein table in, fold changes, FDR, volcano and a publication bundle out." },
  { href: "/eval", title: "Eval", when: "See how far to trust the tags: humans vs model, model vs model." },
  { href: "/mcp", title: "MCP / agents", when: "Plug all of this into Claude Desktop, Claude Code or your own agent, and watch one use it." },
  { href: "/collections", title: "Collections", when: "Name a shortlist, share the link, export it as SDRF or a download script." },
  { href: "/saved", title: "Saved searches", when: "Save a question and see which datasets have landed in PRIDE since." },
];

const ROADMAP = [
  { title: "Search by protein", text: "“Which datasets actually contain MFF?” using PRIDE's peptide/protein evidence (PRIDE Peptidome) and spectrum USIs, not just the method tags." },
  { title: "One‑click reanalysis", text: "Hand the set‑up sheet to quantms / Nextflow and run the search in the cloud instead of on your laptop." },
  { title: "Cross‑omics links", text: "The matching transcriptomics or metabolomics datasets via OmicsDI for the same tissue and condition." },
  { title: "Corrections feed the model", text: "Every “wrong?” you file becomes a gold label and a prompt fix on the next tagging run." },
  { title: "An MCP server", text: "Let your own AI agent use PRIDE Map's search, compare and set‑up sheets directly." },
];

export function TourDone() {
  const [history, setHistory] = useState<{ q: string; at: string }[]>([]);
  const { sel } = useCompare();
  useEffect(() => {
    const id = window.setTimeout(() => { try { setHistory(JSON.parse(localStorage.getItem(TOUR_Q_HISTORY_KEY) ?? "[]")); } catch {} }, 0);
    return () => window.clearTimeout(id);
  }, []);
  return (
    <div className="grid gap-8 max-w-4xl">
      <div className="card p-6 grid gap-2 border-ok bg-ok-soft/30 enter">
        <div className="label text-ok">Tour finished</div>
        <h1 className="text-3xl">That&apos;s the whole loop</h1>
        <p className="text-muted-foreground">Question → filters → shortlist → compare → confirm → set up the search → analyse → report → check the model. Everything you saw is a URL, so you can jump straight back to any step.</p>
      </div>

      <section className="grid gap-3">
        <h2 className="text-xl">Where to go next</h2>
        <div className="grid sm:grid-cols-2 gap-3">
          {PLACES.map((p) => (
            <Link key={p.href} href={p.href} className="card card-hover p-4 grid gap-1 no-underline">
              <span className="font-bold">{p.title} →</span>
              <span className="text-sm text-muted-foreground">{p.when}</span>
            </Link>
          ))}
        </div>
      </section>

      {(history.length > 0 || sel.length > 0) && (
        <section className="grid gap-3">
          <h2 className="text-xl">Pick up where you left off</h2>
          {sel.length > 0 && (
            <div className="card p-4 grid gap-2 text-sm">
              <div className="label">Your shortlist</div>
              <div className="flex flex-wrap gap-1.5">{sel.map((a) => <Link key={a} href={`/dataset/${a}`} className="pill font-mono">{a}</Link>)}</div>
              <div className="flex gap-2 flex-wrap pt-1">
                {sel.length >= 2 && <Link href={`/compare?a=${sel.join(",")}`} className="btn btn-sm">Compare them</Link>}
                <Link href="/flow" className="btn btn-sm btn-primary">Continue in Workflow →</Link>
              </div>
            </div>
          )}
          {history.length > 0 && (
            <div className="card p-4 grid gap-2 text-sm">
              <div className="label">Recent searches</div>
              <div className="flex flex-wrap gap-1.5">{history.map((h) => <Link key={h.q + h.at} href={`/?q=${encodeURIComponent(h.q)}`} className="pill">{h.q}</Link>)}</div>
            </div>
          )}
        </section>
      )}

      <section className="grid gap-3">
        <h2 className="text-xl">What we&apos;d build next</h2>
        <div className="grid sm:grid-cols-2 gap-3">
          {ROADMAP.map((r) => (
            <div key={r.title} className="card p-4 grid gap-1">
              <span className="font-bold">{r.title}</span>
              <span className="text-sm text-muted-foreground">{r.text}</span>
            </div>
          ))}
        </div>
      </section>

      <div className="flex flex-wrap gap-2">
        <Link href="/?tour=0" className="btn btn-primary">▶ Run the tour again</Link>
        <a href={`${API}/docs`} className="btn">API docs</a>
        <a href="https://github.com/FlynnLachendro/pridemap" className="btn">Code on GitHub</a>
        <Link href="/about" className="btn btn-ghost">How it works</Link>
      </div>
    </div>
  );
}
