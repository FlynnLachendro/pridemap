"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";

type RemoteJob = { status: "idle" | "submitting" | "running" | "done" | "error"; job_id: string | null; log: string; err: string | null };

export function RunRemote({ accession, runs, engineLabel = "search engine", outputTable = "results.txt", onDone }: { accession: string; runs?: string[]; engineLabel?: string; outputTable?: string; onDone?: (jobId: string) => void }) {
  const [job, setJob] = useState<RemoteJob>({ status: "idle", job_id: null, log: "", err: null });
  const [stopping, setStopping] = useState(false);
  const seen = useRef<string | null>(null);

  // Resume an in-progress job after UI close/reopen
  useEffect(() => {
    let live = true;
    fetch(`${API}/dataset/${accession}/pipeline/remote/active`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        if (!live || !d?.job_id) return;
        const s = (d.status === "done" || d.status === "error") ? d.status : "running";
        setJob({ status: s as RemoteJob["status"], job_id: d.job_id, log: d.log ?? "", err: null });
      })
      .catch(() => {});
    return () => { live = false; };
  }, [accession]);

  const poll = useCallback(async (id: string) => {
    const r = await fetch(`${API}/dataset/${accession}/pipeline/remote/${id}/status`);
    if (!r.ok) throw new Error(await r.text());
    return r.json() as Promise<{ status: string; log: string }>;
  }, [accession]);

  useEffect(() => {
    if (!job.job_id || job.status !== "running") return;
    let live = true;
    const tick = async () => {
      try {
        const s = await poll(job.job_id!);
        if (!live) return;
        if (s.status === "done" || s.status === "error") {
          setJob((j) => {
            const next = { ...j, status: s.status as "done" | "error", log: s.log };
            if (s.status === "done" && j.job_id && onDone) onDone(j.job_id);
            return next;
          });
        } else {
          setJob((j) => ({ ...j, log: s.log }));
        }
      } catch (e) { if (live) setJob((j) => ({ ...j, status: "error", err: String(e) })); }
    };
    const id = window.setInterval(tick, 3000);
    tick();
    return () => { live = false; window.clearInterval(id); };
  }, [job.job_id, job.status, poll]);

  // Clean up on unmount only after job finishes
  useEffect(() => {
    return () => {
      if (job.job_id && job.status === "done" && seen.current !== job.job_id) {
        seen.current = job.job_id;
        fetch(`${API}/dataset/${accession}/pipeline/remote/${job.job_id}`, { method: "DELETE" }).catch(() => {});
      }
    };
  }, [accession, job.job_id, job.status]);

  const start = async () => {
    setJob({ status: "submitting", job_id: null, log: "", err: null });
    try {
      const runsParam = runs?.length ? `?runs=${encodeURIComponent(runs.join(","))}` : "";
      const r = await fetch(`${API}/dataset/${accession}/pipeline/remote${runsParam}`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      const { job_id } = await r.json();
      setJob({ status: "running", job_id, log: "", err: null });
    } catch (e) { setJob({ status: "error", job_id: null, log: "", err: String(e) }); }
  };

  const stop = async () => {
    if (!job.job_id) return;
    setStopping(true);
    try {
      await fetch(`${API}/dataset/${accession}/pipeline/remote/${job.job_id}`, { method: "DELETE" });
      setJob((j) => ({ ...j, status: "error", err: "Stopped by user" }));
    } catch (e) { setJob((j) => ({ ...j, err: String(e) })); }
    finally { setStopping(false); }
  };

  const busy = job.status === "submitting" || job.status === "running";
  const active = busy || job.status === "done" || job.status === "error";

  type StageState = "pending" | "running" | "done" | "failed";
  const STAGE_MARK: Record<StageState, string> = { pending: "○", running: "◐", done: "✓", failed: "✗" };
  const runsLabel = runs?.length ? `${engineLabel} — ${runs.length} run${runs.length !== 1 ? "s" : ""}` : `${engineLabel} search`;
  // Mirror the local RunHere pipeline stages; map coarse remote job status onto them.
  // Always rendered (idle = all pending) so the user sees what will run before clicking.
  const st = job.status;
  const stages: { label: string; state: StageState }[] = [
    {
      label: "Check tools and disk space",
      state: st === "submitting" ? "running"
        : st === "error" && !job.job_id ? "failed"
        : active ? "done" : "pending",
    },
    {
      label: "Download raw files from PRIDE",
      state: st === "submitting" ? "pending"
        : st === "running" ? "running"
        : st === "done" ? "done"
        : st === "error" && job.job_id ? "failed" : "pending",
    },
    {
      label: "Fetch the protein database",
      state: st === "submitting" ? "pending"
        : st === "running" ? "running"
        : st === "done" ? "done"
        : st === "error" && job.job_id ? "failed" : "pending",
    },
    {
      label: runsLabel,
      state: st === "submitting" ? "pending"
        : st === "running" ? "running"
        : st === "done" ? "done"
        : st === "error" && job.job_id ? "failed" : "pending",
    },
    {
      label: "Hand the tables back to PRIDE Map",
      state: st === "done" ? "done" : "pending",
    },
    {
      label: "Analyse the protein table",
      state: st === "done" ? "done" : "pending",
    },
  ];

  return (
    <div className="rounded-md border bg-card p-3 grid gap-2 mt-2">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-semibold">Run on cloud ({engineLabel} via Modal)</span>
        {/* Fix 7: show connection badge only when idle; job status badge replaces it when active */}
        {!active && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-ok/40 bg-ok-soft px-2.5 py-0.5 text-xs font-medium text-ok">
            <span className="size-1.5 rounded-full bg-ok animate-pulse" />
            Remote {engineLabel} connected
          </span>
        )}
        {job.status === "running" && <span className="pill">running…</span>}
        {job.status === "done" && <span className="pill bg-ok-soft text-ok">finished</span>}
        {job.status === "error" && <span className="pill text-warn">stopped</span>}
        {job.status === "running" && (
          <button type="button" className="btn btn-sm btn-warn ml-auto" disabled={stopping} onClick={stop}>
            {stopping ? "Stopping…" : "Stop"}
          </button>
        )}
        {job.status !== "running" && (
          <button type="button" className="btn btn-sm btn-primary ml-auto" disabled={busy} onClick={start}>
            {job.status === "submitting"
              ? <><span className="spinner mr-2" />Submitting…</>
              : job.status === "error" || job.status === "done" ? "Run again" : "Run on cloud"}
          </button>
        )}
      </div>
      {/* Fix 6: description matches the depth of the local card */}
      <p className="text-xs text-faint">
        Submits the search kit to the remote {engineLabel} server — nothing to install locally. Safe to close this
        page; the job keeps running and results are loaded into Analyse automatically when the search finishes.
      </p>
      {job.err && <p className="text-xs text-warn">{job.err}</p>}
      <ol className="grid gap-0.5 text-xs">
          {stages.map((s) => (
            <li key={s.label} className={s.state === "failed" ? "text-warn" : s.state === "pending" ? "text-faint" : ""}>
              <span aria-hidden className="inline-block w-4">{STAGE_MARK[s.state]}</span>
              {s.label}
            </li>
          ))}
        </ol>
      {busy && (
        <pre className="text-xs bg-muted/40 rounded p-2 max-h-40 overflow-y-auto whitespace-pre-wrap">
          {job.status === "submitting"
            ? [
                `Dataset : ${accession}`,
                runs?.length ? `Runs    : ${runs.join(", ")}` : "Runs    : all",
                "",
                "Submitting job…",
              ].join("\n")
            : [
                `Dataset : ${accession}`,
                runs?.length ? `Runs    : ${runs.join(", ")}` : "Runs    : all",
                job.job_id ? `Job ID  : ${job.job_id}` : "",
                "",
                job.log ? job.log.split("\n").slice(-10).join("\n") : "Waiting for output…",
              ].filter(Boolean).join("\n")}
        </pre>
      )}
      {job.status !== "running" && job.log && (() => {
        const lines = job.log.split("\n");
        return (
          <details className="text-xs">
            <summary className="cursor-pointer text-faint hover:text-foreground">output ({lines.length} lines)</summary>
            <pre className="mt-1 max-h-48 overflow-auto bg-muted/50 p-2 rounded text-[11px] leading-snug">{job.log}</pre>
          </details>
        );
      })()}
      {job.status === "done" && job.job_id && (
        <a className="btn btn-sm self-start" href={`${API}/dataset/${accession}/pipeline/remote/${job.job_id}/results`} download={`${accession}_${outputTable}`}>
          Download {outputTable}
        </a>
      )}
    </div>
  );
}
