"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";
import {
  CpuPill,
  PipelineStages,
  StagePill,
  fmtSecs,
  fmtMins,
  useStageTiming,
  type CpuReport,
  type RunStage,
  type StageState,
} from "@/components/pipeline-stages";
import { consoleBlocks, stageRows, type JobStatus } from "@/lib/run-remote-view";

// `failure` separates "the API never answered" from "the API answered with an error". Conflating
// them told the user to restart a local server that was running fine, while the real fault was a
// 422 relayed from the remote Modal server.
type RemoteJob = {
  // JobStatus is owned by run-remote-view, where the decision tables that read it live — two copies
  // of this union would let the card and its tables disagree about what states exist.
  status: JobStatus;
  job_id: string | null;
  log: string;
  err: string | null;
  failure?: "network" | "server";
  // Stage-level progress relayed from the remote run's own progress.jsonl. `stage_progress` says
  // whether the remote server reported any at all: a server deployed before stage shipping existed
  // sends none, and "we cannot see the stages" must not be drawn as "no stage has started".
  stages?: RunStage[];
  stage_progress?: boolean;
  // The search container's own CPU/memory measurement. Null until the search phase starts writing
  // it, and from a server deployed before the readout existed — both mean "nothing to draw".
  cpu?: CpuReport | null;
  // Set when results come from the transfer cache — upload already done, skip the auto-transfer.
  upload_id?: string;
};

/** What a poll of the remote job returns, once the API has reduced the relayed stage events. */
type RemoteStatus = {
  status: string; log: string; stages?: RunStage[]; stage_progress?: boolean; cpu?: CpuReport | null;
};

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

// outputTable is gone: it named the file the user used to be told to upload by hand, and the
// handoff now collects that file itself, so nothing read the prop any more.
export function RunRemote({ accession, runs, engineLabel = "search engine", onDone }: { accession: string; runs?: string[]; engineLabel?: string; onDone?: (jobId: string) => void }) {
  const [job, setJob] = useState<RemoteJob>({ status: "idle", job_id: null, log: "", err: null });
  const [stopping, setStopping] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const seen = useRef<string | null>(null);
  const submitStart = useRef<number | null>(null);

  // The run selection decides which stages the plan contains, so every status call carries it —
  // without it the relayed events are matched against the wrong stage list.
  const runsQS = runs?.length ? `?runs=${encodeURIComponent(runs.join(","))}` : "";

  // Resume an in-progress job after UI close/reopen, or restore from transfer cache on reload.
  useEffect(() => {
    let live = true;
    fetch(`${API}/dataset/${accession}/pipeline/remote/active${runsQS}`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        if (!live) return;
        if (d?.upload_id && d?.status === "done") {
          // Transfer cache hit: results already in Analyse — jump straight to the comparison page.
          setJob({ status: "done", job_id: null, log: "", err: null,
                   stages: [], stage_progress: false, upload_id: d.upload_id });
          onDone?.(d.upload_id);
          return;
        }
        if (!d?.job_id) return;
        const s = (d.status === "done" || d.status === "error") ? d.status : "running";
        setJob({ status: s as RemoteJob["status"], job_id: d.job_id, log: d.log ?? "", err: null,
                 stages: d.stages ?? [], stage_progress: d.stage_progress ?? false,
                 cpu: d.cpu ?? null });
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
        const progress = {
          stages: s.stages ?? [], stage_progress: s.stage_progress ?? false, cpu: s.cpu ?? null,
        };
        if (s.status === "done" || s.status === "error") {
          setJob((j) => ({ ...j, status: s.status as "done" | "error", log: s.log, ...progress }));
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

  // When the remote job finishes, download its results and upload to PRIDE Map's Analyse endpoint.
  // This replaces the manual download-then-upload step the user would otherwise have to perform.
  const uploadSeen = useRef<string | null>(null);
  useEffect(() => {
    // job.upload_id means results came from the transfer cache — already handled in mount/start.
    // Deliberately NOT conditional on onDone: the transfer is what writes the cache entry, so
    // skipping it when no callback is mounted would make every later run re-pay the whole search.
    if (job.status !== "done" || !job.job_id || job.upload_id || uploadSeen.current === job.job_id) return;
    uploadSeen.current = job.job_id;
    const id = job.job_id;
    setJob((j) => ({ ...j, status: "transferring" }));
    (async () => {
      try {
        const r = await fetch(`${API}/dataset/${accession}/pipeline/remote/${id}/results`);
        if (!r.ok) throw new Error(await describeHttpError(r));
        const blob = await r.blob();
        const fd = new FormData();
        fd.append("file", blob, `${accession}_proteinGroups.txt`);
        const u = await fetch(`${API}/analyse/inspect`, { method: "POST", body: fd });
        if (!u.ok) throw new Error(await describeHttpError(u));
        const { upload_id } = await u.json();
        setJob((j) => ({ ...j, status: "done", upload_id }));
        onDone?.(upload_id);
        // Record in the server-side transfer cache so a repeat of this same selection skips the
        // search entirely. Built with URLSearchParams because hand-joining produced "...complete&
        // upload_id=" whenever runs was empty — no "?", so the id never arrived, the endpoint
        // rejected the call, and nothing was ever cached.
        const q = new URLSearchParams({ upload_id });
        if (runs?.length) q.set("runs", runs.join(","));
        fetch(`${API}/dataset/${accession}/pipeline/remote/complete?${q}`, { method: "POST" }).catch(() => {});
      } catch (e) {
        setJob((j) => ({ ...j, status: "error", err: `Could not load results into Analyse: ${String(e)}`, failure: "server" }));
      }
    })();
  }, [job.status, job.job_id, job.upload_id, accession, runs, onDone]);

  // Clean up on unmount only after job finishes
  useEffect(() => {
    return () => {
      if (job.job_id && (job.status === "done" || job.status === "transferring") && seen.current !== job.job_id) {
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
      const data = await r.json();
      if (data.upload_id && data.status === "done") {
        // Transfer cache hit: skip MaxQuant entirely and go straight to the Analyse page.
        setJob({ status: "done", job_id: null, log: "", err: null, upload_id: data.upload_id });
        onDone?.(data.upload_id);
        return;
      }
      setJob({ status: "running", job_id: data.job_id, log: "", err: null });
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
      // "stopped", not "error". Reusing the error state made a deliberate stop render as a failure
      // — same red stage marks, same wording — so the card could not tell the user apart from a
      // broken run.
      setJob((j) => ({ ...j, status: "stopped", err: "Stopped by user" }));
    } catch (e) { setJob((j) => ({ ...j, err: String(e) })); }
    finally { setStopping(false); }
  };

  const busy = job.status === "submitting" || job.status === "running" || job.status === "transferring";
  const active = busy || job.status === "done" || job.status === "error" || job.status === "stopped";

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
  // What the remote run itself reported, when it could be read: the same stage records a local run
  // produces, so they render through the same component with the same elapsed time and ETA.
  const liveStages: RunStage[] = job.stage_progress ? (job.stages ?? []) : [];
  const timing = useStageTiming(liveStages, st === "running");
  // "error" belongs here. Excluding it meant the real stage list vanished at the exact moment it
  // mattered most — the server knows which stage failed and why, and the card replaced that with a
  // hard-coded ladder that cannot say either. Contact loss still falls back, because then we have no
  // current stages, only stale ones.
  const showLiveStages =
    liveStages.length > 0 && (st === "running" || st === "done" || st === "error") && !contactLost;
  // The coarse view: fixed labels driven by the job's overall status alone. It is what the card can
  // honestly show before the first stage event arrives, after contact is lost, or against a remote
  // server too old to report stages — and nothing else. It used to be shown on failure too, where
  // it overwrote the server's per-stage truth with one state repeated across three rows.
  //
  // INVARIANT: these labels mirror the stages pipeline_stages() emits for a DDA + MaxQuant plan
  // (api/omics.py). A stage added there must be added here, or the ladder miscounts in the window
  // before live stages arrive.
  const coarseLabels = [
    "Check tools and disk space",
    "Download raw files from PRIDE",
    "Fetch the protein database",
    runsLabel,
    "Hand the tables back to PRIDE Map",
    "Analyse the protein table",
  ];
  const coarseStages = stageRows({
    status: st,
    stages: [],
    stageProgress: false,
    contactLost,
    coarseLabels,
    hasJobId: Boolean(job.job_id),
  });

  // Whether the stage currently running is narrating its own steps. When it is, the raw log repeats
  // what the rows above already say — the local card has always suppressed it here.
  const hasLiveSteps = showLiveStages && liveStages.some((s) => s.state === "running" && s.steps.length > 0);
  const submittingText = [
    `Waking Modal server… ${elapsed}s`,
    elapsed < 10 ? "" : elapsed < 60
      ? "Cold-start in progress (20–60 s is normal)"
      : `Still waiting — cold-start can take up to 120 s (${elapsed}s elapsed)`,
  ].filter(Boolean).join("\n");
  const blocks = consoleBlocks({
    status: st,
    log: job.status === "error" && !job.job_id
      ? (job.failure === "server"
          ? "Submission failed — the API answered with an error.\nThis is not a connectivity problem; the detail below names the layer that failed."
          : `Submission failed — could not reach the API at ${API}.\nCheck that the local API server is running (uv run api/main.py).`)
      : job.log,
    hasLiveSteps,
    submittingText,
  });

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
          // Show StagePill only once stage timing is actually ticking (overallSecs > 0 means at
          // least one stage has a started timestamp). Before that, fall back to wall-clock elapsed
          // so the card never regresses to "0s elapsed" when stages arrive but are still all-pending.
          showLiveStages && timing.overallSecs > 0
            ? <StagePill timing={timing} />
            : (() => {
                const displaySecs = timing.overallSecs > 0 ? timing.overallSecs : (elapsed > 0 ? elapsed : null);
                return (
                  <span className="pill">
                    running
                    {displaySecs != null ? ` · elapsed ${fmtSecs(displaySecs)}` : "…"}
                    {timing.overallEtaSecs != null ? ` · ETA ${fmtMins(timing.overallEtaSecs)}` : ""}
                  </span>
                );
              })()
        )}
        {job.status === "done" && <span className="pill bg-ok-soft text-ok">finished</span>}
        {job.status === "transferring" && <span className="pill"><span className="spinner mr-1.5" />Loading results into Analyse…</span>}
        {/* "failed" and "stopped" are different events and now read differently. They shared the
            error state before, so a run the user stopped on purpose was reported as a failure. */}
        {job.status === "error" && (
          <span className="pill text-warn">{contactLost ? "lost contact" : "failed"}</span>
        )}
        {job.status === "stopped" && <span className="pill text-faint">stopped</span>}
        {job.status === "running" && (
          <button type="button" className="btn btn-sm btn-warn ml-auto" disabled={stopping} onClick={stop}>
            {stopping ? "Stopping…" : "Stop"}
          </button>
        )}
        {job.status !== "running" && (
          <button type="button" className="btn btn-sm btn-primary ml-auto" disabled={busy} onClick={start}>
            {job.status === "submitting"
              ? <><span className="spinner mr-2" />Submitting…</>
              : active ? "Run again" : "Run on cloud"}
          </button>
        )}
      </div>
      {/* Fix 6: description matches the depth of the local card */}
      <p className="text-xs text-faint">
        Submits the search kit to the remote {engineLabel} server — nothing to install locally. Safe to close this
        page; the job keeps running and results are loaded into Analyse automatically when the search finishes.
      </p>
      {job.err && <p className="text-xs text-warn">{job.err}</p>}
      {/* Shown while running and after it finishes: the final sample carries the peak, which is what
          the next cpu/memory choice should be made from. */}
      <CpuPill cpu={job.cpu} />
      {showLiveStages
        ? <PipelineStages stages={liveStages} timing={timing} runLabels={runs ?? []} />
        : (
          <ol className="grid gap-0.5 text-xs">
            {coarseStages.map((s) => (
              <li key={s.id} className={s.state === "failed" ? "text-warn" : s.state === "pending" ? "text-faint" : ""}>
                <span aria-hidden className="inline-block w-4">{STAGE_MARK[s.state]}</span>
                {s.label}
              </li>
            ))}
          </ol>
        )}
      {/* Exactly one console, decided in one place. Two blocks with independent conditions both
          matched on "error with a log" — after Stop, a poll timeout, or lost contact — and stacked
          a ten-line tail on top of a collapsible copy of the same log. consoleBlocks owns the
          choice now, and its test sweeps every state to keep it that way. */}
      {blocks.map((b) =>
        b.kind === "live" ? (
          <pre key="live" className="text-xs bg-muted/40 rounded p-2 max-h-40 overflow-y-auto whitespace-pre-wrap">
            {[
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
              b.text,
              job.err ? `\nError   : ${job.err}` : "",
            ].filter(Boolean).join("\n")}
          </pre>
        ) : (
          <details key="full" className="text-xs" open>
            <summary className="cursor-pointer text-faint hover:text-foreground">
              output ({b.text.split("\n").length} lines)
            </summary>
            <pre className="mt-1 max-h-48 overflow-auto bg-muted/50 p-2 rounded text-[11px] leading-snug">
              {[
                `Dataset : ${accession}`,
                runs?.length ? `Runs    : ${runs.join(", ")}` : "Runs    : all",
                job.job_id ? `Job ID  : ${job.job_id}` : "",
                "",
                b.text,
              ].filter(Boolean).join("\n")}
            </pre>
          </details>
        ),
      )}
    </div>
  );
}
