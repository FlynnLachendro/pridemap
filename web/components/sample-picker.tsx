"use client";
import { useCallback, useEffect, useState } from "react";
import { API } from "@/lib/api";

type Annot = { value: string; source: string; source_kind: string };
type Row = {
  file_name: string; run: string; size_bytes: number | null; ftp: string | null;
  annotations: Record<string, Annot>; label: string; local: boolean;
};
type Field = { field: string; distinct: number; varies: boolean; identifier: boolean; values: string[] };
type Samples = {
  accession: string; runs: number; local_runs: number; raw_cache: string;
  sources: { kind: string; file_name: string; runs_annotated: number; note: string }[];
  fields: Field[]; varying_fields: string[]; rows: Row[];
  conflicts: { field: string; annotation_says: string; tag: string; tag_says: string; note: string }[];
  note: string | null;
};
type Stage = { id: string; label: string; state: string; seconds: number | null; steps: string[]; message: string | null };
type Status = { running: boolean; stages: Stage[]; log: string[]; dir?: string };

const gb = (b: number | null) => (!b ? "" : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${(b / 1e6).toFixed(0)} MB`);
const STATE_MARK: Record<string, string> = { done: "✓", running: "…", failed: "✗", pending: "·" };

export function SamplePicker({ accession }: { accession: string }) {
  const [s, setS] = useState<Samples | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let live = true;
    fetch(`${API}/dataset/${accession}/samples`).then(async (r) => { if (!r.ok) throw new Error(await r.text()); return r.json(); })
      .then((d) => { if (live) setS(d); }).catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, [accession]);

  const runsParam = picked.join(",");

  // Poll while a run is in flight. The selection is part of the key: each one has its own run directory, so
  // asking about the wrong selection would report a different run's progress.
  useEffect(() => {
    if (!status?.running || !runsParam) return;
    const id = setInterval(() => {
      fetch(`${API}/dataset/${accession}/pipeline/status?runs=${encodeURIComponent(runsParam)}`)
        .then((r) => (r.ok ? r.json() : null)).then((d) => d && setStatus(d)).catch(() => {});
    }, 4000);
    return () => clearInterval(id);
  }, [accession, runsParam, status?.running]);

  const toggle = useCallback((file: string) => {
    setPicked((p) => (p.includes(file) ? p.filter((f) => f !== file) : [...p, file]));
    setStatus(null);
  }, []);

  const start = useCallback(async () => {
    setStarting(true);
    setErr(null);
    try {
      const r = await fetch(`${API}/dataset/${accession}/pipeline/start?runs=${encodeURIComponent(runsParam)}`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      setStatus({ running: true, stages: [], log: [] });
    } catch (e) {
      setErr(String(e));
    } finally {
      setStarting(false);
    }
  }, [accession, runsParam]);

  if (err && !s) return <p className="text-warn text-sm">{err}</p>;
  if (!s) return <p className="text-sm text-muted-foreground">Reading the sample annotations PRIDE holds…</p>;
  if (!s.rows.length) return null;

  // Only the fields that tell runs apart are worth columns; the rest are the same on every row.
  const cols = s.varying_fields.slice(0, 4);
  const chosen = s.rows.filter((r) => picked.includes(r.file_name));
  const toFetch = chosen.filter((r) => !r.local);
  const bytes = toFetch.reduce((n, r) => n + (r.size_bytes || 0), 0);

  return (
    <div className="rounded-xl border bg-card overflow-hidden" data-tour="samples">
      <div className="px-4 py-3 border-b">
        <h3 className="text-base">Pick the samples to compare</h3>
        <p className="text-xs text-muted-foreground">
          {s.sources.length
            ? <>Annotations from {s.sources.map((x, i) => <span key={x.file_name}>{i > 0 && ", "}<code>{x.file_name}</code></span>)}. Tick any two runs.</>
            : s.note}
        </p>
      </div>

      {s.conflicts.length > 0 && (
        <div className="px-4 py-3 border-b text-xs grid gap-1">
          {s.conflicts.map((c) => (
            <p key={c.field} className="text-warn">
              ⚠ <code>{c.field}</code> says <b>{c.annotation_says}</b>, but this dataset&apos;s {c.tag} tag says{" "}
              <b>{c.tag_says}</b>. {c.note}
            </p>
          ))}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="text-left px-4 py-2 font-normal"> </th>
              <th className="text-left px-3 py-2 font-normal">Run</th>
              {cols.map((c) => <th key={c} className="text-left px-3 py-2 font-normal">{c}</th>)}
              <th className="text-left px-3 py-2 font-normal">Size</th>
              <th className="text-left px-3 py-2 font-normal">Local</th>
            </tr>
          </thead>
          <tbody>
            {s.rows.map((r) => (
              <tr key={r.file_name} className={`border-b last:border-0 ${picked.includes(r.file_name) ? "bg-muted/40" : ""}`}>
                <td className="px-4 py-2">
                  <input type="checkbox" aria-label={`select ${r.file_name}`}
                         checked={picked.includes(r.file_name)} onChange={() => toggle(r.file_name)} />
                </td>
                <td className="px-3 py-2"><code className="text-xs">{r.file_name}</code></td>
                {cols.map((c) => (
                  <td key={c} className="px-3 py-2 text-xs" title={r.annotations[c] ? `from ${r.annotations[c].source}` : ""}>
                    {r.annotations[c]?.value ?? "—"}
                  </td>
                ))}
                <td className="px-3 py-2 text-xs text-muted-foreground">{gb(r.size_bytes)}</td>
                <td className="px-3 py-2 text-xs">{r.local ? <span className="text-ok">cached</span> : <span className="text-faint">no</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {picked.length > 0 && (
        <div className="px-4 py-3 border-t grid gap-2 text-sm">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground">
              {picked.length} selected · {toFetch.length === 0
                ? "all already downloaded"
                : `${toFetch.length} to download (${gb(bytes)})`}
            </span>
            <span className="grow" />
            <a className="btn btn-sm" href={`${API}/dataset/${accession}/searchkit.zip?runs=${encodeURIComponent(runsParam)}`} download>
              Download kit for these runs
            </a>
            <button className="btn btn-primary btn-sm" onClick={start} disabled={starting || !!status?.running}>
              {status?.running ? "Running…" : starting ? "Starting…" : "Run it here"}
            </button>
          </div>
          {picked.length < 2 && <p className="text-xs text-muted-foreground">Pick one more to have something to compare against.</p>}
          {picked.length === 2 && (
            <p className="text-xs text-muted-foreground">
              Two samples give fold change and which proteins were detected in only one of them. With one run a
              side there is no within-group variance, so no p-value or FDR is computed — the Analyse step will say
              so rather than show significance it cannot support.
            </p>
          )}
          <p className="text-xs text-faint">
            Raw files are cached in <code>{s.raw_cache}</code>, so a run already downloaded is never fetched twice.
          </p>
          {err && <p className="text-warn text-xs">{err}</p>}
        </div>
      )}

      {status && (
        <div className="px-4 py-3 border-t grid gap-1 text-xs">
          {status.stages.map((st) => (
            <div key={st.id} className={st.state === "failed" ? "text-warn" : st.state === "done" ? "text-ok" : ""}>
              {STATE_MARK[st.state] || "·"} {st.label}
              {st.seconds != null && <span className="text-faint"> · {st.seconds}s</span>}
              {st.message && <span className="text-faint"> — {st.message}</span>}
              {st.steps.length > 0 && <div className="pl-4 text-faint">{st.steps[st.steps.length - 1]}</div>}
            </div>
          ))}
          {!status.stages.length && <span className="text-muted-foreground">Starting…</span>}
        </div>
      )}
    </div>
  );
}
