"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";

type RemoteJob = { status: "idle" | "submitting" | "running" | "done" | "error"; job_id: string | null; log: string; err: string | null };

export function RunRemote({ accession }: { accession: string }) {
  const [job, setJob] = useState<RemoteJob>({ status: "idle", job_id: null, log: "", err: null });
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
      const r = await fetch(`${API}/dataset/${accession}/pipeline/remote`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      const { job_id } = await r.json();
      setJob({ status: "running", job_id, log: "", err: null });
    } catch (e) { setJob({ status: "error", job_id: null, log: "", err: String(e) }); }
  };

  const busy = job.status === "submitting" || job.status === "running";
  return (
    <div className="rounded-md border bg-card p-3 grid gap-2 mt-2">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-semibold">Run on cloud (MaxQuant via Devin)</span>
        {job.status === "running" && <span className="pill">running…</span>}
        {job.status === "done" && <span className="pill bg-ok-soft text-ok">done</span>}
        {job.status === "error" && <span className="pill text-warn">error</span>}
        <button type="button" className="btn btn-sm btn-primary ml-auto" disabled={busy} onClick={start}>
          {busy ? <><span className="spinner mr-2" />{job.status === "submitting" ? "Submitting…" : "Running…"}</> : "Run on cloud"}
        </button>
      </div>
      <p className="text-xs text-faint">Submits the kit to the remote MaxQuant server; results download automatically when the search finishes.</p>
      {job.err && <p className="text-xs text-warn">{job.err}</p>}
      {job.log && (
        <pre className="text-xs bg-muted/40 rounded p-2 max-h-40 overflow-y-auto whitespace-pre-wrap">{job.log}</pre>
      )}
      {job.status === "done" && job.job_id && (
        <a className="btn btn-sm btn-primary self-start" href={`${API}/dataset/${accession}/pipeline/remote/${job.job_id}/results`} download={`${accession}_proteinGroups.txt`}>
          Download proteinGroups.txt
        </a>
      )}
    </div>
  );
}
