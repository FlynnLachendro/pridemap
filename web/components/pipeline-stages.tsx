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

/* MaxQuant internal stages, in order, with what each one actually costs.
 *
 * The costs are the point. These used to be twenty equal steps and progress was `index / 20`, which
 * is wrong by a factor of ten: on a measured 2-file run (Modal call fc-01M58HFAWP9KNBCEM528M7HQPQ)
 * feature detection, deisotoping and MS/MS preparation took 1679 s of 1826 s — 92% of the run —
 * while the surrounding stages took five to eleven seconds each. Treating them as equal is what
 * produced "Testing raw files (5/20) · ETA 42 mins" fifteen minutes into a stage that had barely
 * started, and an ETA that walked backwards for the rest of the run.
 *
 * `measured: true` means the figure came off that run's stage events. The run was truncated at
 * "Joining temporary peptide files", so everything from the Andromeda search onwards is an ESTIMATE
 * and flagged as one — no Modal run has yet produced a proteinGroups.txt to measure. Replace the
 * estimates with real durations from the first run that completes; relative size is all that
 * matters here, so an estimate that is wrong in proportion is the only thing that skews the ETA. */
export type MqStage = { name: string; secs: number; measured: boolean };

export const MQ_STAGES: MqStage[] = [
  { name: "Configuring", secs: 5, measured: true },
  { name: "Assemble run info", secs: 5, measured: true },
  { name: "Finish run info", secs: 5, measured: true },
  { name: "Testing fasta files", secs: 5, measured: true },
  { name: "Testing raw files", secs: 5, measured: true },
  { name: "Feature detection", secs: 893, measured: true },
  { name: "Deisotoping", secs: 596, measured: true },
  { name: "Calculating peak properties", secs: 10, measured: true },
  { name: "MS/MS preparation", secs: 190, measured: true },
  { name: "Combining apl files for first search", secs: 11, measured: true },
  { name: "Preparing searches", secs: 60, measured: false },
  { name: "MS/MS first search", secs: 180, measured: false },
  { name: "Mass recalibration", secs: 120, measured: false },
  { name: "MS/MS search", secs: 400, measured: false },
  { name: "Applying FDR", secs: 60, measured: false },
  { name: "Matching between runs", secs: 90, measured: false },
  { name: "Assembling proteins", secs: 90, measured: false },
  { name: "Protein FDR", secs: 30, measured: false },
  { name: "Label-free quantification", secs: 150, measured: false },
  { name: "Writing tables", secs: 60, measured: false },
];

/** Stage names only. Kept as the name lookup; `MQ_STAGES` owns the ordering and the weights. */
export const MQ_STAGE_ORDER = MQ_STAGES.map((s) => s.name);

const MQ_TOTAL_SECS = MQ_STAGES.reduce((acc, s) => acc + s.secs, 0);
/** Cost of every stage BEFORE index i. cumulative[i] is how much of the run is behind stage i. */
const MQ_CUMULATIVE_SECS = MQ_STAGES.reduce<number[]>(
  (acc, s) => [...acc, acc[acc.length - 1] + s.secs], [0],
);

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

/** Index of the stage now RUNNING, given the last stage MaxQuant reported finishing.
 *
 * MaxQuant writes `<step>.finished.txt` on completion and nothing on start, so the newest marker
 * names the stage that just ENDED — the run is already one stage further on. The card used to print
 * that marker directly, which is why a fifteen-minute feature detection displayed as "Testing raw
 * files" for its entire duration: the user was watching the name of something that finished in five
 * seconds. Returns -1 when the name is unknown, so callers can tell "no idea" from "stage 0". */
export function mqRunningIndex(lastFinished: string): number {
  const finished = MQ_STAGE_ORDER.indexOf(lastFinished);
  if (finished < 0) return -1;
  return Math.min(finished + 1, MQ_STAGES.length - 1);
}

/** Fraction of the whole search behind us, weighted by measured stage cost (0…1).
 *
 * Counts the running stage as half done. Without intra-stage progress that is the midpoint of what
 * we know, and it matters: assuming the running stage had not started made the ETA jump every time
 * a stage boundary was crossed, and assuming it was finished made the bar sit at 100% for the last
 * twenty minutes of the run. */
export function mqProgressFraction(runningIdx: number): number | null {
  if (runningIdx < 0 || runningIdx >= MQ_STAGES.length) return null;
  const done = MQ_CUMULATIVE_SECS[runningIdx] + MQ_STAGES[runningIdx].secs / 2;
  return Math.max(0, Math.min(1, done / MQ_TOTAL_SECS));
}

/** Seconds remaining, from elapsed time and cost-weighted progress. Null when not yet estimable. */
export function mqEtaSecs(elapsedSecs: number, runningIdx: number): number | null {
  const fraction = mqProgressFraction(runningIdx);
  if (fraction == null || fraction <= 0 || elapsedSecs <= 0) return null;
  return Math.max(0, Math.round(elapsedSecs * (1 - fraction) / fraction));
}

export type MqEta = {
  /** Point estimate, seconds. */
  secs: number;
  /** Honest bounds around it. */
  lowSecs: number;
  highSecs: number;
  /** True when the remaining time leans mostly on stages nobody has timed yet. */
  provisional: boolean;
};

/** Seconds remaining WITH the uncertainty that estimate actually carries.
 *
 * A single number here was dishonest, and measurably so. The estimate is elapsed time scaled by the
 * cost profile in MQ_STAGES, so it is only as good as that profile's SHAPE — and the shape came from
 * one run whose scratch was on a network volume. Moving that scratch to local disk made feature
 * detection roughly six times faster (893 s → 148 s on the same file), which means the early stages
 * are no longer the 92% the profile says they are. The model therefore believes the run is about
 * half done when it is nearer a sixth, under-forecasts, and the "6 mins" never arrives — it slides
 * outward as the run proceeds, which is exactly what someone watching the card sees.
 *
 * Two things follow, and both belong in the output rather than in a comment:
 *   - the band widens with how much of the REMAINING cost comes from stages flagged unmeasured,
 *     because those numbers are guesses and should not be quoted to the minute;
 *   - `provisional` is set whenever the remainder leans on them, so the card can say so.
 *
 * The honest fix for the shape itself is recalibration from a completed post-fix run; until one
 * exists, this reports a range instead of pretending to a precision it does not have. */
export function mqEta(elapsedSecs: number, runningIdx: number): MqEta | null {
  const fraction = mqProgressFraction(runningIdx);
  if (fraction == null || fraction <= 0 || elapsedSecs <= 0) return null;

  const remaining = MQ_STAGES.slice(runningIdx + 1);
  const remainingSecs = remaining.reduce((acc, s) => acc + s.secs, 0);
  const unmeasuredSecs = remaining.reduce((acc, s) => acc + (s.measured ? 0 : s.secs), 0);
  // How much of what is left is guesswork, 0…1. With nothing left to do the question is moot and
  // the share is 0, which keeps the band tight at the end of the run rather than flaring.
  const guessShare = remainingSecs > 0 ? unmeasuredSecs / remainingSecs : 0;

  const secs = Math.max(0, Math.round(elapsedSecs * (1 - fraction) / fraction));
  // 40% either side at best, 100% when the remainder is entirely unmeasured. The one calibration
  // point available showed a 6x shape error, so a band narrower than this would be false comfort.
  const spread = 0.4 + 0.6 * guessShare;
  return {
    secs,
    lowSecs: Math.max(0, Math.round(secs / (1 + spread))),
    highSecs: Math.round(secs * (1 + spread)),
    provisional: guessShare > 0.5,
  };
}

/** An ETA written the way its confidence warrants: a range, and a word when it is guesswork. */
export function fmtEta(eta: MqEta | null): string {
  if (eta == null || eta.secs <= 0) return "";
  const low = Math.round(eta.lowSecs / 60);
  const high = Math.round(eta.highSecs / 60);
  const range = low >= high ? fmtMins(eta.secs) : `${Math.max(low, 1)}–${high} min`;
  return eta.provisional ? `ETA ${range} (rough)` : `ETA ${range}`;
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

/* What the search container measured about its own CPU and memory use, relayed by /status.
 * Every field but the sizing is optional: the container reports what it can read, and a host
 * without cgroup v2 reports none of it. */
export type CpuReport = {
  /** Cores RESERVED — what is paid for, and what busy_pct is measured against. */
  cores: number;
  /** Cores os.cpu_count() saw. Larger than `cores` when Modal lands us on a bigger host. */
  host_cores?: number;
  allocated: number;
  memory_gb: number;
  busy_cores?: number;
  busy_pct?: number | null;
  peak_busy_cores?: number;
  rss_gb?: number;
  peak_rss_gb?: number;
};

/** One line of "is MaxQuant actually using the cores we paid for".
 *
 * Worth a permanent place on the card because the honest answer is mostly no, and nothing else on
 * screen shows it. MaxQuant's numThreads is patched to the core count and logged at startup, which
 * only proves what was REQUESTED; its feature detection, deisotoping and MS/MS preparation phases —
 * 92% of a measured 2-file run — parallelise one thread per raw file, so a 16-core container sits at
 * two busy cores for almost the whole search. This is the number that says so, and the same number
 * right-sizes the container: `peak` is what the next cpu/memory choice should be made from. */
export function CpuPill({ cpu }: { cpu?: CpuReport | null }) {
  if (!cpu) return null;
  const busy = cpu.busy_cores;
  // "Idle" vs "nothing measured" are different claims, so an absent busy figure says so outright
  // rather than drawing a zero the container never reported.
  const cpuText = busy == null
    ? `CPU — / ${cpu.cores} cores (not measured)`
    : `CPU ${busy.toFixed(1)} / ${cpu.cores} cores (${cpu.busy_pct ?? 0}%)`;
  const underused = busy != null && cpu.cores > 2 && busy < cpu.cores * 0.5;
  return (
    <span
      className={`text-xs ${underused ? "text-warn" : "text-faint"}`}
      title={
        underused
          ? `MaxQuant's early phases run one thread per raw file, not one per core, so most of a ` +
            `${cpu.cores}-core container is idle for the long middle of the search. Peak use is what ` +
            `to size the next run from.`
          : "Measured inside the search container, not requested — cgroup CPU and memory accounting."
      }
    >
      {cpuText}
      {cpu.host_cores != null && cpu.host_cores !== cpu.cores && ` (host has ${cpu.host_cores})`}
      {cpu.peak_busy_cores != null && ` · peak ${cpu.peak_busy_cores.toFixed(1)}`}
      {cpu.rss_gb != null && ` · RAM ${cpu.rss_gb.toFixed(1)} / ${cpu.memory_gb} GB`}
      {cpu.peak_rss_gb != null && ` (peak ${cpu.peak_rss_gb.toFixed(1)})`}
    </span>
  );
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
              // The slowest sample decides when the stage ends — MaxQuant's early phases run one
              // thread per raw file, concurrently, so the run finishes with the largest file, not
              // with the average of them. Averaging the indices reported an ETA no sample would hit.
              const indices = Array.from(perRun.values()).map(mqRunningIndex).filter((i) => i >= 0);
              if (indices.length === 0) {
                return overallEtaSecs != null && overallEtaSecs > 0
                  ? <span className="text-faint"> · ETA: {fmtMins(overallEtaSecs)}</span>
                  : null;
              }
              const text = fmtEta(mqEta(elapsed, Math.min(...indices)));
              return text ? <span className="text-faint"> · {text}</span> : null;
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
                          const finished = perRun.get(runIdx) ?? lastGlobal ?? "";
                          const letter = i === 0 ? "A" : "B";
                          // The stage RUNNING, not the one that just finished — see mqRunningIndex.
                          const idx = mqRunningIndex(finished);
                          const total = MQ_STAGES.length;
                          const stageName = idx >= 0 ? MQ_STAGE_ORDER[idx] : finished;
                          const etaText = elapsed ? fmtEta(mqEta(elapsed, idx)) : "";
                          const pct = Math.round((mqProgressFraction(idx) ?? 0) * 100);
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
                              {etaText && <span className="text-[10px] shrink-0">· {etaText}</span>}
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
