"use client";
import { useEffect, useState } from "react";

/* Single owner of "how a pipeline run is drawn": the stage list, elapsed time, ETA and the
 * per-sample progress rows. Both run cards render through this — the one that runs the pipeline on
 * this machine and the one that runs it on Modal — because the two were drifting apart: the cloud
 * card showed a bare "running…" for hours while the local card showed stages, elapsed and ETA for
 * the identical run_all.sh. Timing belongs here, not in either card. */

export type Substage = { name: string; detail: string };
export type Stage = {
  id: string; label: string; detail: string; where: string;
  engine?: string; engine_label?: string; output_table?: string; substages?: Substage[];
};
export type StageState = "pending" | "running" | "done" | "failed";
export type RunStage = Stage & {
  state: StageState; seconds: number | null; steps: string[]; message: string | null;
  /** ISO instant the stage began, recorded by the run itself; null until it starts. */
  started?: string | null;
};

export const STATE_MARK: Record<string, string> = { pending: "○", running: "◐", done: "✓", failed: "✗" };

// MaxQuant internal stage order (proc file names, spaces not underscores).
// Used to estimate progress and ETA per sample.
export const MQ_STAGE_ORDER = [
  "Configuring", "Assemble run info", "Finish run info",
  "Testing fasta files", "Testing raw files",
  "Feature detection", "Deisotoping", "Calculating peak properties",
  "MS/MS preparation", "Combining apl files for first search",
  "Preparing searches", "MS/MS first search", "Mass recalibration",
  "MS/MS search", "Applying FDR", "Matching between runs",
  "Assembling proteins", "Protein FDR", "Label-free quantification",
  "Writing tables",
];

export const fmtSecs = (s: number) => s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
export const fmtMins = (s: number) => { const m = Math.round(s / 60); return m <= 0 ? "<1 min" : `${m} min${m !== 1 ? "s" : ""}`; };

/** Parse MaxQuant proc-file step names and group by sample.
 *  Proc files: "Feature_detection 12.finished.txt" → suffix first digit = 1-based run index.
 *  Returns map of runIndex → latest stage label (underscores → spaces, suffix stripped). */
export function mqLatestPerRun(steps: string[]): Map<number, string> {
  const latest = new Map<number, string>();
  for (const step of steps) {
    const m = step.match(/^(.*?)\s+(\d+)$/);
    if (!m) continue;
    const label = m[1].replace(/_/g, " ");
    const runIdx = parseInt(m[2][0], 10);
    latest.set(runIdx, label);
  }
  return latest;
}

export type StageTiming = {
  stageElapsed: (s: RunStage) => number | null;
  overallSecs: number;
  overallEtaSecs: number | null;
  done: number;
  totalStages: number;
};

/** Elapsed time and ETA for a run, from the stage records the server reports.
 *
 * A finished stage carries its own duration and the running one carries the instant it started, so
 * every figure here is derived from the run itself. That is deliberate: timing it from when this
 * component mounted meant a page reload restarted the clock at zero halfway through a stage, and a
 * job picked up in a new tab showed no elapsed time at all. */
export function useStageTiming(stages: RunStage[], running: boolean): StageTiming {
  const [now, setNow] = useState(() => Date.now());

  // Live clock while running — drives elapsed time display.
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);

  // Elapsed seconds for a stage: the server's recorded duration once it is done, otherwise the time
  // since it started. A start in the future (a small clock difference between the machine running
  // the search and this browser) reads as 0 rather than as a negative age.
  const stageElapsed = (s: RunStage): number | null => {
    if (s.seconds != null) return s.seconds;
    if (s.state === "running" && s.started) {
      const start = Date.parse(s.started);
      if (!Number.isNaN(start)) return Math.max(0, Math.floor((now - start) / 1000));
    }
    return null;
  };

  const done = stages.filter((s) => s.state === "done").length;
  // Overall elapsed = sum of all stage durations we know about.
  const overallSecs = stages.reduce((acc, s) => acc + (stageElapsed(s) ?? 0), 0);
  const totalStages = stages.length - 1; // last stage is upload/finalise
  const overallEtaSecs = done > 0 && totalStages > done
    ? Math.round(overallSecs * (totalStages - done) / done)
    : null;

  return { stageElapsed, overallSecs, overallEtaSecs, done, totalStages };
}

/** The one-line summary pill: how far in, how long it has taken, how long is left. */
export function StagePill({ timing }: { timing: StageTiming }) {
  const { done, totalStages, overallSecs, overallEtaSecs } = timing;
  return (
    <span className="pill">
      running · {done}/{totalStages} stages · elapsed {fmtSecs(overallSecs)}
      {overallEtaSecs != null ? ` · ETA ${fmtMins(overallEtaSecs)}` : ""}
    </span>
  );
}

/** Every stage of the run, with elapsed time, ETA, and a progress row per selected sample. */
export function PipelineStages(
  { stages, timing, runLabels = [] }: { stages: RunStage[]; timing: StageTiming; runLabels?: string[] },
) {
  const { stageElapsed, overallEtaSecs } = timing;
  return (
    <ol className="grid gap-0.5 text-xs">
      {stages.map((s) => {
        const elapsed = stageElapsed(s);
        return (
          <li key={s.id} className={s.state === "failed" ? "text-warn" : s.state === "pending" ? "text-faint" : ""}>
            <span aria-hidden className="inline-block w-4">{STATE_MARK[s.state]}</span>
            {s.label}
            {elapsed != null && <span className="text-faint"> · elapsed: {fmtSecs(elapsed)}</span>}
            {(() => {
              if (s.state !== "running" || !elapsed || s.steps.length === 0) return null;
              const perRun = mqLatestPerRun(s.steps);
              const total = MQ_STAGE_ORDER.length;
              const avgIdx = perRun.size > 0
                ? Array.from(perRun.values()).reduce((sum, name) => sum + Math.max(0, MQ_STAGE_ORDER.indexOf(name)), 0) / perRun.size
                : -1;
              // Use step-position ETA when available; fall back to pipeline estimate during global steps.
              const eta = avgIdx > 0
                ? Math.round(elapsed * (total - avgIdx) / avgIdx)
                : (overallEtaSecs ?? null);
              return eta != null && eta > 0 ? <span className="text-faint"> · ETA: {fmtMins(eta)}</span> : null;
            })()}
            {s.message && <span className="text-faint"> · {s.message}</span>}
            {(s.state === "running" || s.state === "failed") && s.steps.length > 0 && (
              <div className="pl-6 grid gap-0.5">
                {s.state === "failed"
                  ? s.steps.map((t, i) => <div key={i} className="text-faint">{t}</div>)
                  : (() => {
                      const perRun = mqLatestPerRun(s.steps);
                      // Last global step (fallback when per-run steps not yet available)
                      const lastGlobal = s.steps[s.steps.length - 1]
                        ?.replace(/^MaxQuant:\s*/, "").replace(/_/g, " ").replace(/\s+\d+$/, "");
                      if (runLabels.length > 1) {
                        // Always show A/B rows when 2 runs are selected.
                        // Use per-run step when available, fall back to the current global step.
                        return runLabels.map((label, i) => {
                          const runIdx = i + 1;
                          const stageName = perRun.get(runIdx) ?? lastGlobal ?? "";
                          const letter = i === 0 ? "A" : "B";
                          const idx = MQ_STAGE_ORDER.indexOf(stageName);
                          const total = MQ_STAGE_ORDER.length;
                          const etaSecs = elapsed && idx > 0
                            ? Math.round(elapsed * (total - idx) / idx)
                            : (elapsed ? overallEtaSecs : null);
                          const pct = idx >= 0 ? Math.round(((idx + 1) / total) * 100) : 0;
                          return (
                            <div key={runIdx} className="flex items-center gap-1.5 text-faint flex-wrap">
                              <span className="text-[10px] font-bold shrink-0 w-3">{letter}</span>
                              <span className="truncate max-w-[140px] shrink-0" title={label}>{label}</span>
                              <span className="text-[10px] shrink-0">·</span>
                              <span className="shrink-0">{stageName}</span>
                              <span className="spinner shrink-0" aria-hidden />
                              {idx >= 0 && <span className="text-[10px] shrink-0">({idx + 1}/{total})</span>}
                              <div className="h-1.5 w-20 rounded-full bg-muted shrink-0 overflow-hidden" title={`${pct}%`}>
                                <div className="h-full rounded-full bg-teal transition-all duration-500 animate-pulse" style={{ width: `${pct}%` }} />
                              </div>
                              {elapsed != null && <span className="text-[10px] shrink-0">· elapsed: {fmtSecs(elapsed)}</span>}
                              {etaSecs != null && etaSecs > 0 && <span className="text-[10px] shrink-0">· ETA: {fmtMins(etaSecs)}</span>}
                            </div>
                          );
                        });
                      }
                      // Single run or global step: show last step
                      return <div className="text-faint">{lastGlobal}</div>;
                    })()
                }
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
