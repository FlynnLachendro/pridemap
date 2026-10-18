// What the cloud run card shows, as two pure decision tables.
//
// These lived inline in run-remote.tsx and both were wrong in ways that were invisible by reading
// them. Pulled out here so they can be exercised directly: the bugs were in the mapping, not in the
// rendering, and a table is the only honest way to check a mapping.
//
// No React, no DOM, no imports from the component — so the tests need no frontend test setup.

/** The states a job can be in, as the card models it. */
export type JobStatus = "idle" | "submitting" | "running" | "done" | "stopped" | "error";

/** The state of a single pipeline stage. */
export type StageState = "pending" | "running" | "done" | "failed";

/** A stage as the server reports it, reduced to what the card needs to draw a row. */
export type ServerStage = { id: string; label: string; state: StageState; message?: string | null };

/** One row in the stage ladder. */
export type StageRow = { id: string; label: string; state: StageState; message?: string | null };

export type StageRowsInput = {
  status: JobStatus;
  stages: ServerStage[];
  stageProgress: boolean;
  /** True when the API answered but we could not reach it — stale stages, not failed ones. */
  contactLost: boolean;
  /** Labels for the coarse ladder, used only when the server's own stages are unavailable. */
  coarseLabels: string[];
  /** Whether a job id exists; before one does, a failure is a submission failure. */
  hasJobId: boolean;
};

/**
 * The stage rows to draw.
 *
 * The bug this replaces: a single `remoteState` variable, derived from the job's OVERALL status,
 * was assigned to the download, FASTA and search rows at once. One failure therefore painted three
 * stages ✗ — the reported `✗✗✗○○` — regardless of which had actually failed, and none of them could
 * carry a reason because the coarse ladder has no message field.
 *
 * The server knows exactly which stage failed and why. When it has told us, that is what we draw.
 */
export function stageRows(input: StageRowsInput): StageRow[] {
  const { status, stages, stageProgress, contactLost, coarseLabels, hasJobId } = input;

  // The server's own stages, whenever we have them. Note "error" is included: dropping the real
  // stages on failure hid the one thing worth reading at the one moment it mattered.
  const live = stageProgress ? stages : [];
  const usable =
    live.length > 0 &&
    (status === "running" || status === "done" || status === "error" || status === "stopped") &&
    !contactLost;

  if (usable) {
    return live.map((s) => ({ id: s.id, label: s.label, state: s.state, message: s.message ?? null }));
  }

  // The coarse ladder: fixed labels driven by the overall status alone. It is honest only about
  // what the overall status can actually tell us, which is why every row below gets the SAME state
  // — that is a property of not knowing, not a claim about individual stages.
  //
  // Contact loss renders as pending, never failed: the job is usually alive and downloading, and
  // painting it ✗ claimed a failure that had not happened.
  const coarse: StageState =
    status === "submitting" ? "pending"
    : status === "running" ? "running"
    : status === "done" ? "done"
    : status === "stopped" ? "pending"
    : contactLost ? "pending"
    : status === "error" && hasJobId ? "failed"
    : "pending";

  return coarseLabels.map((label, i) => ({ id: `coarse-${i}`, label, state: coarse, message: null }));
}

/** Which console block the card shows. At most one, ever. */
export type ConsoleBlock = { kind: "live" | "full"; text: string };

export type ConsoleInput = {
  status: JobStatus;
  log: string;
  /** True when a stage is currently reporting its own steps, which say the same thing better. */
  hasLiveSteps: boolean;
  /** Text to show while a submission is in flight; the log is empty then. */
  submittingText: string;
};

/**
 * The console blocks to render.
 *
 * The bug this replaces: two blocks with overlapping conditions. One rendered for
 * `submitting | running | error`, the other for `status !== "running" && log`. Both matched on
 * "error with a log" — after Stop, after a poll timeout, after lost contact — so the card showed
 * the last ten lines AND a collapsible copy of the same log, stacked.
 *
 * Returning a list with an at-most-one invariant makes that impossible to reintroduce by accident:
 * the overlap was two independent conditions that nobody could hold in their head at once.
 */
export function consoleBlocks(input: ConsoleInput): ConsoleBlock[] {
  const { status, log, hasLiveSteps, submittingText } = input;

  // Submitting: there is no log yet, so the card narrates instead.
  if (status === "submitting") return [{ kind: "live", text: submittingText }];

  // Running: the stage rows already say what is happening, step by step. Showing the raw log as
  // well is duplication — the local card has always suppressed it; the cloud card did not.
  if (status === "running") {
    return hasLiveSteps || !log ? [] : [{ kind: "live", text: tail(log, 10) }];
  }

  // Anything terminal: one collapsible block holding the whole log.
  if (!log) return [];
  return [{ kind: "full", text: log }];
}

/** The last `n` lines of a log. */
export function tail(log: string, n: number): string {
  return log.split("\n").slice(-n).join("\n");
}
