"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";
import {
  PipelineStages,
  StagePill,
  fmtSecs,
  useStageTiming,
  type RunStage,
  type StageState,
} from "@/components/pipeline-stages";

// `failure` separates "the API never answered" from "the API answered with an error". Conflating
// them told the user to restart a local server that was running fine, while the real fault was a
// 422 relayed from the remote Modal server.
type RemoteJob = {
  status: "idle" | "submitting" | "running" | "done" | "error";
  job_id: string | null;
  log: string;
  err: string | null;
  failure?: "network" | "server";
  // Stage-level progress relayed from the remote run's own progress.jsonl. `stage_progress` says
  // whether the remote server reported any at all: a server deployed before stage shipping existed
  // sends none, and "we cannot see the stages" must not be drawn as "no stage has started".
  stages?: RunStage[];
  stage_progress?: boolean;
};

/** What a poll of the remote job returns, once the API has reduced the relayed stage events. */
type RemoteStatus = { status: string; log: string; stages?: RunStage[]; stage_progress?: boolean };

/** A polling failure that remembers WHICH layer failed, so the UI never guesses. */
class PollError extends Error {
  constructor(message: string, readonly failure: "network" | "server") {
    super(message);
  }
}

/** A one-line description of an error response, unwrapping the API's relayed upstream detail. */
async function describeHttpError(r: Response): Promise<string> {
  const text = await r.text();
  try {
    const detail = JSON.parse(text)?.detail;
    if (detail && typeof detail === "object" && detail.status) {
      const upstream = typeof detail.upstream === "string" ? detail.upstream : JSON.stringify(detail.upstream);
      return `HTTP ${r.status} — upstream ${detail.source ?? "remote server"} returned ${detail.status}: ${upstream}`;
    }
    return `HTTP ${r.status} — ${typeof detail === "string" ? detail : text.slice(0, 500)}`;
  } catch {
    return `HTTP ${r.status} — ${text.slice(0, 500)}`;
  }
}

export function RunRemote({ accession, runs, engineLabel = "search engine", outputTable = "results.txt", onDone }: { accession: string; runs?: string[]; engineLabel?: string; outputTable?: string; onDone?: (jobId: string) => void }) {
  const [job, setJob] = useState<RemoteJob>({ status: "idle", job_id: null, log: "", err: null });
  const [stopping, setStopping] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const seen = useRef<string | null>(null);
  const submitStart = useRef<number | null>(null);

  // The run selection decides which stages the plan contains, so every status call carries it —
  // without it the relayed events are matched against the wrong stage list.
  const runsQS = runs?.length ? `?runs=${encodeURIComponent(runs.join(","))}` : "";

  // Resume an in-progress job after UI close/reopen
  useEffect(() => {
    let live = true;
    fetch(`${API}/dataset/${accession}/pipeline/remote/active${runsQS}`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        if (!live || !d?.job_id) return;
        const s = (d.status === "done" || d.status === "error") ? d.status : "running";
        setJob({ status: s as RemoteJob["status"], job_id: d.job_id, log: d.log ?? "", err: null,
                 stages: d.stages ?? [], stage_progress: d.stage_progress ?? false });
      })
      .catch(() => {});
    return () => { live = false; };
  }, [accession, runsQS]);

  const poll = useCallback(async (id: string) => {
    let r: Response;
    try {
      r = await fetch(`${API}/dataset/${accession}/pipeline/remote/${id}/status${runsQS}`);
    } catch (e) {
      // Transport failure: API down, or NEXT_PUBLIC_API_URL baked at build time now points at a
      // dead port. Naming the URL matters — the port moves whenever dev.sh restarts.
      throw new PollError(`lost contact with the API at ${API} — ${String(e)}`, "network");
    }
    if (!r.ok) throw new PollError(await describeHttpError(r), "server");
    return r.json() as Promise<RemoteStatus>;
  }, [accession, runsQS]);

  useEffect(() => {
    if (!job.job_id || job.status !== "running") return;
    let live = true;
    const tick = async () => {
      try {
        const s = await poll(job.job_id!);
        if (!live) return;
        const progress = { stages: s.stages ?? [], stage_progress: s.stage_progress ?? false };
        if (s.status === "done" || s.status === "error") {
          setJob((j) => {
            const next = { ...j, status: s.status as "done" | "error", log: s.log, ...progress };
            if (s.status === "done" && j.job_id && onDone) onDone(j.job_id);
            return next;
          });
        } else {
          setJob((j) => ({ ...j, log: s.log, ...progress }));
        }
      } catch (e) {
        if (!live) return;
        const failure = e instanceof PollError ? e.failure : "network";
        const err = e instanceof PollError ? e.message : String(e);
        setJob((j) => ({ ...j, status: "error", err, failure }));
      }
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

  // Tick elapsed seconds from submission onwards: during cold-start it is the only progress there
  // is, and once the job is running it keeps counting so the card always answers "how long has this
  // been going?". It survives the submitting → running transition because submitStart is only reset
  // when a new submission begins. A job resumed after a page reload has no known start time, so
  // nothing is ticked and the elapsed figure comes from the stages instead — a guessed start time
  // would read as fact.
  useEffect(() => {
    if (job.status !== "submitting" && job.status !== "running") return;
    if (job.status === "submitting") submitStart.current = Date.now();
    if (submitStart.current == null) return;
    const tick = () => setElapsed(Math.floor((Date.now() - submitStart.current!) / 1000));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [job.status]);

  const start = async () => {
    setElapsed(0);
    setJob({ status: "submitting", job_id: null, log: "", err: null });
    try {
      const runsParam = runs?.length ? `?runs=${encodeURIComponent(runs.join(","))}` : "";
      const r = await fetch(`${API}/dataset/${accession}/pipeline/remote${runsParam}`, { method: "POST" });
      if (!r.ok) {
        setJob({ status: "error", job_id: null, log: "", err: await describeHttpError(r), failure: "server" });
        return;
      }
      const { job_id } = await r.json();
      setJob({ status: "running", job_id, log: "", err: null });
    } catch (e) {
      // fetch() rejects only on transport failure — server down, DNS, CORS, abort.
      setJob({ status: "error", job_id: null, log: "", err: String(e), failure: "network" });
    }
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

  const STAGE_MARK: Record<StageState, string> = { pending: "○", running: "◐", done: "✓", failed: "✗" };
  const runsLabel = runs?.length ? `${engineLabel} — ${runs.length} run${runs.length !== 1 ? "s" : ""}` : `${engineLabel} search`;
  // Mirror the local RunHere pipeline stages; map coarse remote job status onto them.
  // Always rendered (idle = all pending) so the user sees what will run before clicking.
  const st = job.status;
  // A failed poll says NOTHING about the remote stages: the job keeps running on Modal whether or
  // not this page can reach the API. Painting them ✗ claimed the search had failed when contact was
  // merely lost — a lie that cost two debugging sessions, both times with a healthy job downloading
  // raw files. Unknown is shown as pending, and the banner below names what actually broke.
  const contactLost = st === "error" && job.failure === "network";
  // One state for every stage that runs remotely, so they cannot disagree about the same job.
  const remoteState: StageState = st === "submitting" ? "pending"
    : st === "running" ? "running"
    : st === "done" ? "done"
    : contactLost ? "pending"
    : st === "error" && job.job_id ? "failed" : "pending";
  // What the remote run itself reported, when it could be read: the same stage records a local run
  // produces, so they render through the same component with the same elapsed time and ETA.
  const liveStages: RunStage[] = job.stage_progress ? (job.stages ?? []) : [];
  const timing = useStageTiming(liveStages, st === "running");
  const showLiveStages = liveStages.length > 0 && (st === "running" || st === "done") && !contactLost;
  // Wall-clock time since this page submitted the job. Only known for a job submitted in this
  // session, which is why it is null rather than 0 for a resumed one.
  const wallElapsed = submitStart.current != null && elapsed > 0 ? elapsed : null;
  // The coarse view: six fixed labels driven by the job's overall status alone. It is what the card
  // can honestly show before the first stage event arrives, after contact is lost, or against a
  // remote server too old to report stages.
  const coarseStages: { label: string; state: StageState }[] = [
    {
      label: "Check tools and disk space",
      state: st === "submitting" ? "running"
        : st === "error" && !job.job_id ? "failed"
        : active ? "done" : "pending",
    },
    { label: "Download raw files from PRIDE", state: remoteState },
    { label: "Fetch the protein database", state: remoteState },
    { label: runsLabel, state: remoteState },
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
        {job.status === "running" && (
          showLiveStages
            ? <StagePill timing={timing} />
            : <span className="pill">running{wallElapsed != null ? ` · elapsed ${fmtSecs(wallElapsed)}` : "…"}</span>
        )}
        {job.status === "done" && <span className="pill bg-ok-soft text-ok">finished</span>}
        {job.status === "error" && (
          <span className="pill text-warn">{contactLost ? "lost contact" : "stopped"}</span>
        )}
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
      {showLiveStages
        ? <PipelineStages stages={liveStages} timing={timing} runLabels={runs ?? []} />
        : (
          <ol className="grid gap-0.5 text-xs">
            {coarseStages.map((s) => (
              <li key={s.label} className={s.state === "failed" ? "text-warn" : s.state === "pending" ? "text-faint" : ""}>
                <span aria-hidden className="inline-block w-4">{STAGE_MARK[s.state]}</span>
                {s.label}
              </li>
            ))}
          </ol>
        )}
      {(busy || job.status === "error") && (
        <pre className="text-xs bg-muted/40 rounded p-2 max-h-40 overflow-y-auto whitespace-pre-wrap">
          {job.status === "submitting"
            ? [
                `Dataset : ${accession}`,
                runs?.length ? `Runs    : ${runs.join(", ")}` : "Runs    : all",
                "",
                `Waking Modal server… ${elapsed}s`,
                elapsed < 10 ? "" : elapsed < 60
                  ? "Cold-start in progress (20–60 s is normal)"
                  : `Still waiting — cold-start can take up to 120 s (${elapsed}s elapsed)`,
              ].filter(l => l !== "").join("\n")
            : job.status === "error" && !job.job_id
            ? [
                `Dataset : ${accession}`,
                runs?.length ? `Runs    : ${runs.join(", ")}` : "Runs    : all",
                "",
                ...(job.failure === "server"
                  ? [
                      "Submission failed — the API answered with an error.",
                      "This is not a connectivity problem; the detail below names the layer that failed.",
                    ]
                  : [
                      `Submission failed — could not reach the API at ${API}.`,
                      "Check that the local API server is running (uv run api/main.py).",
                    ]),
                job.err ? `Error   : ${job.err}` : "",
              ].filter(Boolean).join("\n")
            : [
                `Dataset : ${accession}`,
                runs?.length ? `Runs    : ${runs.join(", ")}` : "Runs    : all",
                job.job_id ? `Job ID  : ${job.job_id}` : "",
                "",
                ...(contactLost
                  ? [
                      "This page can no longer reach the API — the job itself is unaffected and is",
                      "most likely still running on Modal. The stages above are unknown, not failed.",
                      `Check the API is up and that the UI points at it (currently ${API});`,
                      "restarting the API on a new port leaves this page calling the old one.",
                      "",
                    ]
                  : []),
                job.log ? job.log.split("\n").slice(-10).join("\n") : "Waiting for output…",
                job.status === "error" && job.err ? `\nError   : ${job.err}` : "",
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
