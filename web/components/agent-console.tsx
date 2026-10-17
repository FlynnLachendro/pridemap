"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import { API } from "@/lib/api";

type Ev =
  | { type: "start"; model: string; tools: string[] }
  | { type: "tool_call"; id: string; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; ok: boolean; result?: unknown; error?: string }
  | { type: "text"; text: string; final: boolean }
  | { type: "done"; steps: number; usage?: { in: number; out: number }; truncated?: boolean }
  | { type: "error"; message: string };

export const AGENT_EXAMPLES = [
  "Which mouse liver dataset would best show phosphorylated MFF, and is it comparable with PXD055942?",
  "I need two human plasma DIA label-free datasets from 2021 onwards that I could pool. Check they're comparable.",
  "What would I type into DIA-NN to reanalyse PXD029247?",
];

const accLink = (s: string) => s.split(/(PXD\d{6})/g).map((part, i) => /^PXD\d{6}$/.test(part) ? <Link key={i} href={`/dataset/${part}`} className="font-mono text-teal hover:underline">{part}</Link> : <span key={i}>{part}</span>);

function Json({ v }: { v: unknown }) {
  const [open, setOpen] = useState(false);
  const s = JSON.stringify(v, null, 1);
  return (
    <div>
      <pre className={`text-[11px] leading-snug font-mono bg-surface-2 rounded-md p-2 overflow-x-auto ${open ? "" : "max-h-24 overflow-hidden"}`}>{s}</pre>
      {s.length > 300 && <button type="button" onClick={() => setOpen((o) => !o)} className="text-xs underline text-muted-foreground">{open ? "collapse" : `expand (${s.length.toLocaleString()} chars)`}</button>}
    </div>
  );
}

export function AgentConsole({ compact = false }: { compact?: boolean }) {
  const [q, setQ] = useState(AGENT_EXAMPLES[0]);
  const [events, setEvents] = useState<Ev[]>([]);
  const [running, setRunning] = useState(false);
  const abort = useRef<AbortController | null>(null);

  async function run(question: string) {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setEvents([]);
    setRunning(true);
    try {
      const r = await fetch(`${API}/agent`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ q: question }), signal: ctl.signal });
      const reader = r.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) if (line.trim()) setEvents((e) => [...e, JSON.parse(line)]);
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setEvents((ev) => [...ev, { type: "error", message: String(e) }]);
    } finally { setRunning(false); }
  }

  const calls = events.filter((e) => e.type === "tool_call").length;
  const final = events.find((e) => e.type === "text" && e.final) as Extract<Ev, { type: "text" }> | undefined;
  const done = events.find((e) => e.type === "done") as Extract<Ev, { type: "done" }> | undefined;

  return (
    <div className="grid gap-3" data-tour="agent">
      <form className="search-box" onSubmit={(e) => { e.preventDefault(); run(q); }}>
        <span className="font-mono text-xs text-teal shrink-0">agent ›</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="question for the agent" />
        <button type="submit" className="btn btn-primary" disabled={running} data-tour="agent-run">{running ? <><span className="spinner mr-2" />Working…</> : "Run"}</button>
      </form>
      {!compact && (
        <div className="flex flex-wrap gap-1.5 text-xs items-center">
          <span className="text-faint font-mono">Try:</span>
          {AGENT_EXAMPLES.map((ex) => <button key={ex} type="button" onClick={() => { setQ(ex); run(ex); }} className="pill hover:bg-surface-2">{ex}</button>)}
        </div>
      )}

      {events.length > 0 && (
        <div className="card overflow-hidden">
          <div className="px-4 py-2 border-b flex items-center gap-3 text-xs font-mono text-muted-foreground">
            <span className={`inline-block w-2 h-2 rounded-full ${running ? "bg-peak animate-pulse" : done ? "bg-ok" : "bg-faint"}`} />
            {running ? "Claude is working with the tools…" : done ? `finished in ${done.steps} round${done.steps === 1 ? "" : "s"}, ${calls} tool call${calls === 1 ? "" : "s"}` : "stopped"}
            {done?.usage && <span className="ml-auto tabular">{done.usage.in.toLocaleString()} in · {done.usage.out.toLocaleString()} out tokens</span>}
          </div>
          <ol className="divide-y">
            {events.map((e, i) => {
              if (e.type === "tool_call") return (
                <li key={i} className="px-4 py-3 grid gap-1 enter">
                  <div className="flex items-center gap-2 text-sm"><span className="pill bg-teal-soft text-teal">tool</span><span className="font-mono font-semibold">{e.name}</span><span className="text-faint text-xs">called with</span></div>
                  <Json v={e.args} />
                </li>
              );
              if (e.type === "tool_result") return (
                <li key={i} className="px-4 py-3 grid gap-1 enter bg-surface/60">
                  <div className="flex items-center gap-2 text-sm"><span className={`pill ${e.ok ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn"}`}>{e.ok ? "result" : "error"}</span><span className="font-mono text-xs text-muted-foreground">{e.name}</span></div>
                  {e.ok ? <Json v={e.result} /> : <div className="text-sm text-warn">{e.error}</div>}
                </li>
              );
              if (e.type === "text" && !e.final) return <li key={i} className="px-4 py-3 text-sm text-muted-foreground italic enter">{e.text}</li>;
              if (e.type === "error") return <li key={i} className="px-4 py-3 text-sm text-warn">{e.message}</li>;
              return null;
            })}
          </ol>
          {final && (
            <div className="px-4 py-4 border-t bg-ok-soft/30 enter" data-tour="agent-done">
              <div className="label text-ok mb-1">Answer</div>
              <p className="text-sm leading-relaxed whitespace-pre-wrap">{accLink(final.text)}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
