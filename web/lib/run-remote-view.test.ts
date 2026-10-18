// Contract tests for the cloud run card's two decision tables.
//
// Author:  PRIDE Map
// Created: 2026-10-18
// Scope:   web/lib/run-remote-view.ts stageRows / consoleBlocks — what the card draws for a
//          given job status, stage list and log.
//
// Why this file exists. Two bugs, both invisible by reading the component:
//
//   1. One `remoteState` variable, derived from the job's OVERALL status, was assigned to the
//      download, FASTA and search rows at once. A single failure painted three stages ✗ — the
//      reported `✗✗✗○○` — whichever one had actually failed, and none could carry a reason.
//   2. Two console blocks with overlapping conditions both matched on "error with a log", so the
//      card stacked a ten-line tail and a collapsible copy of the same log.
//
// The console domain is small and closed, so it is swept exhaustively rather than sampled: the
// overlap was two independent conditions nobody could hold in their head at once, and only a table
// over every state catches the next one. The fix DELETES the shared state that caused bug 1, so
// without this table the two blocks can silently re-merge on the next edit.
//
// Runs on Node's built-in test runner — no new dependency, no frontend test setup:
//   node --experimental-strip-types --test lib/run-remote-view.test.ts
//   (or: npm run test:ui)

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  consoleBlocks,
  stageRows,
  tail,
  type JobStatus,
  type ServerStage,
} from "./run-remote-view.ts";

const COARSE = [
  "Check tools and disk space",
  "Download raw files from PRIDE",
  "Fetch the protein database",
  "MaxQuant — 2 runs",
  "Hand the tables back to PRIDE Map",
  "Analyse the protein table",
];

/** The server's view of a run that died fetching the FASTA, which is the case the old card lied about. */
const FASTA_FAILED: ServerStage[] = [
  { id: "preflight", label: "Check tools and disk space", state: "done" },
  { id: "download", label: "Download raw files from PRIDE", state: "done" },
  { id: "fasta", label: "Fetch the protein database", state: "failed", message: "UniProt returned 503" },
  { id: "search_dda", label: "MaxQuant — 2 runs", state: "pending" },
  { id: "upload", label: "Hand the tables back to PRIDE Map", state: "pending" },
];

const base = {
  stages: [] as ServerStage[],
  stageProgress: false,
  contactLost: false,
  coarseLabels: COARSE,
  hasJobId: true,
};

// ----------------------------------------------------------------- stageRows

test("one failed stage paints exactly one cross", () => {
  // Given the server reporting that only the FASTA stage failed,
  // When the rows are derived,
  // Then exactly one row is failed — the old card painted three, which is the reported ✗✗✗○○.
  const rows = stageRows({ ...base, status: "error", stages: FASTA_FAILED, stageProgress: true });

  const failed = rows.filter((r) => r.state === "failed");
  assert.equal(failed.length, 1, `expected one failed row, got ${failed.map((r) => r.label).join(", ")}`);
  assert.equal(failed[0].id, "fasta");
});

test("the reason a stage failed survives into the row", () => {
  // Given a failed stage carrying the server's explanation,
  // When the rows are derived,
  // Then the message is on the row — stage_view has always returned it and the card never drew it,
  // which is why a failure showed as a bare ✗ with nothing to act on.
  const rows = stageRows({ ...base, status: "error", stages: FASTA_FAILED, stageProgress: true });

  assert.equal(rows.find((r) => r.id === "fasta")?.message, "UniProt returned 503");
});

test("stages already finished stay finished when a later one fails", () => {
  // Given preflight and download done before the FASTA stage failed,
  // When the rows are derived,
  // Then those two still read done — the shared state used to overwrite them with the overall
  // status, telling the user work that had succeeded had failed.
  const rows = stageRows({ ...base, status: "error", stages: FASTA_FAILED, stageProgress: true });

  assert.equal(rows.find((r) => r.id === "preflight")?.state, "done");
  assert.equal(rows.find((r) => r.id === "download")?.state, "done");
});

test("the real stages are kept on failure rather than replaced by the coarse ladder", () => {
  // Given a failed run whose stages the server reported,
  // When the rows are derived,
  // Then they are the server's stages, not the hard-coded labels. Dropping them on error discarded
  // the only account of what broke, at the one moment it mattered.
  const rows = stageRows({ ...base, status: "error", stages: FASTA_FAILED, stageProgress: true });

  assert.equal(rows.length, FASTA_FAILED.length);
  assert.ok(!rows.some((r) => r.id.startsWith("coarse-")));
});

test("lost contact is drawn as pending, never as failed", () => {
  // Given stale stages and no way to reach the API,
  // When the rows are derived,
  // Then nothing is marked failed: the job is usually alive and downloading, and painting it ✗
  // claimed a failure that had not happened — it cost two debugging sessions.
  const rows = stageRows({
    ...base,
    status: "error",
    stages: FASTA_FAILED,
    stageProgress: true,
    contactLost: true,
  });

  assert.ok(!rows.some((r) => r.state === "failed"));
});

test("the coarse ladder is used only when the server reported no stages", () => {
  // Given a running job from a server too old to report stages,
  // When the rows are derived,
  // Then the fixed labels are drawn — the honest fallback, and the only case it still covers.
  const rows = stageRows({ ...base, status: "running", stageProgress: false });

  assert.deepEqual(rows.map((r) => r.label), COARSE);
  assert.ok(rows.every((r) => r.state === "running"));
});

test("a submission that failed before a job existed blames nothing downstream", () => {
  // Given an error with no job id — the submission itself was refused,
  // When the rows are derived,
  // Then no stage is marked failed, because none of them ever ran.
  const rows = stageRows({ ...base, status: "error", hasJobId: false });

  assert.ok(!rows.some((r) => r.state === "failed"));
});

// ----------------------------------------------------------------- consoleBlocks

const ALL_STATUSES: JobStatus[] = ["idle", "submitting", "running", "done", "stopped", "error"];

test("at most one console block is ever shown, in every state", () => {
  // Given every combination of status, log presence and live steps,
  // When the console blocks are derived,
  // Then never more than one is returned. Two blocks used to overlap on "error with a log" — after
  // Stop, a poll timeout, or lost contact — and this sweep is what stops them re-merging.
  for (const status of ALL_STATUSES) {
    for (const log of ["", "line one\nline two"]) {
      for (const hasLiveSteps of [true, false]) {
        const blocks = consoleBlocks({ status, log, hasLiveSteps, submittingText: "Waking Modal…" });
        assert.ok(
          blocks.length <= 1,
          `status=${status} log=${log ? "yes" : "no"} steps=${hasLiveSteps} gave ${blocks.length} blocks`,
        );
      }
    }
  }
});

test("a terminal state with output always shows exactly one block", () => {
  // Given a finished, stopped or failed job that produced output,
  // When the console blocks are derived,
  // Then exactly one is shown — the at-most-one invariant must not be satisfied by showing none,
  // which would hide the log entirely.
  for (const status of ["done", "stopped", "error"] as JobStatus[]) {
    const blocks = consoleBlocks({
      status,
      log: "it ended",
      hasLiveSteps: false,
      submittingText: "",
    });
    assert.equal(blocks.length, 1, `status=${status} showed ${blocks.length} blocks`);
    assert.equal(blocks[0].kind, "full");
  }
});

test("the raw log is suppressed while stage steps are saying the same thing", () => {
  // Given a running job whose current stage is reporting its own steps,
  // When the console blocks are derived,
  // Then no console is shown — the stage rows already say what is happening. The local card has
  // always done this; the cloud card showed both.
  const blocks = consoleBlocks({
    status: "running",
    log: "lots of output",
    hasLiveSteps: true,
    submittingText: "",
  });

  assert.deepEqual(blocks, []);
});

test("a running job with no stage steps still shows its log tail", () => {
  // Given a running job that is not reporting steps yet,
  // When the console blocks are derived,
  // Then the tail is shown — otherwise suppressing the console would leave the card blank during
  // the opaque early minutes of a run.
  const blocks = consoleBlocks({
    status: "running",
    log: "connecting…",
    hasLiveSteps: false,
    submittingText: "",
  });

  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].kind, "live");
});

test("submitting narrates even though there is no log yet", () => {
  // Given a submission in flight,
  // When the console blocks are derived,
  // Then the narration is shown, because the log is empty at that point and an empty card looks
  // like nothing is happening during a 120 s cold start.
  const blocks = consoleBlocks({
    status: "submitting",
    log: "",
    hasLiveSteps: false,
    submittingText: "Waking Modal… 12s",
  });

  assert.deepEqual(blocks, [{ kind: "live", text: "Waking Modal… 12s" }]);
});

test("tail keeps the last lines, which are the ones that explain a failure", () => {
  const log = Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n");
  assert.equal(tail(log, 3), "line 27\nline 28\nline 29");
});
