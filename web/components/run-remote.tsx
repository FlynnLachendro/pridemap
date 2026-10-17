"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";

type RemoteJob = { status: "idle" | "submitting" | "running" | "done" | "error"; job_id: string | null; log: string; err: string | null };

export function RunRemote({ accession, runs }: { accession: string; runs?: string[] }) {
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
          setJob((j) => ({ ...j, status: s.status as "done" | "error", log: s.log }));
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
  const runsLabel = runs?.length ? `MaxQuant — ${runs.length} DDA run${runs.length !== 1 ? "s" : ""}` : "MaxQuant search";
  // Mirror the local RunHere pipeline stages; map coarse remote job status onto them
  const stages: { label: string; state: StageState }[] = active ? [
    {
      label: "Check tools and disk space",
      state: job.status === "submitting" ? "running"
        : job.status === "error" && !job.job_id ? "failed"
        : "done",
    },
    {
      label: "Download raw files from PRIDE",
      state: job.status === "submitting" ? "pending"
        : job.status === "running" ? "running"
        : job.status === "done" ? "done"
        : job.job_id ? "failed" : "pending",
    },
    {
      label: "Fetch the protein database",
      state: job.status === "submitting" ? "pending"
        : job.status === "running" ? "running"
        : job.status === "done" ? "done"
        : job.job_id ? "failed" : "pending",
    },
    {
      label: runsLabel,
      state: job.status === "submitting" ? "pending"
        : job.status === "running" ? "running"
        : job.status === "done" ? "done"
        : job.job_id ? "failed" : "pending",
    },
    {
      label: "Hand the tables back to PRIDE Map",
      state: job.status === "done" ? "done"
        : job.status === "running" ? "pending"
        : job.status === "submitting" ? "pending"
        : job.job_id ? "failed" : "pending",
    },
    {
      label: "Analyse the protein table",
      state: job.status === "done" ? "done" : "pending",
    },
  ] : [];

  return (
    <div className="rounded-md border bg-card p-3 grid gap-2 mt-2">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-semibold">Run on cloud (MaxQuant via Devin)</span>
        {/* Fix 7: show connection badge only when idle; job status badge replaces it when active */}
        {!active && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-ok/40 bg-ok-soft px-2.5 py-0.5 text-xs font-medium text-ok">
            <span className="size-1.5 rounded-full bg-ok animate-pulse" />
            Remote MaxQuant connected
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
        Submits the search kit to the remote MaxQuant server — nothing to install locally. Safe to close this
        page; the job keeps running and results download automatically when the search finishes.
      </p>
      {job.err && <p className="text-xs text-warn">{job.err}</p>}
      {/* Fix 5: pseudo-stage list mirrors the local RunHere card */}
      {stages.length > 0 && (
        <ol className="grid gap-0.5 text-xs">
          {stages.map((s) => (
            <li key={s.label} className={s.state === "failed" ? "text-warn" : s.state === "pending" ? "text-faint" : ""}>
              <span aria-hidden className="inline-block w-4">{STAGE_MARK[s.state]}</span>
              {s.label}
            </li>
          ))}
        </ol>
      )}
      {job.log && (() => {
        const lines = job.log.split("\n");
        return job.status === "running"
          ? <pre className="text-xs bg-muted/40 rounded p-2 max-h-40 overflow-y-auto whitespace-pre-wrap">{lines.slice(-10).join("\n")}</pre>
          : (
            <details className="text-xs">
              <summary className="cursor-pointer text-faint hover:text-foreground">output ({lines.length} lines)</summary>
              <pre className="mt-1 max-h-48 overflow-auto bg-muted/50 p-2 rounded text-[11px] leading-snug">{job.log}</pre>
            </details>
          );
      })()}
      {job.status === "done" && job.job_id && (
        <a className="btn btn-sm btn-primary self-start" href={`${API}/dataset/${accession}/pipeline/remote/${job.job_id}/results`} download={`${accession}_proteinGroups.txt`}>
          Download proteinGroups.txt
        </a>
      )}
    </div>
  );
}
