"use client";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useCompare } from "@/lib/compare-store";
import { API } from "@/lib/api";
import { markAnalysed } from "@/lib/progress-store";
import { RunRemote } from "@/components/run-remote";

type Group = { stem: string; columns: string[]; fill: number };
type MzTab = {
  rows: number; runs: number; per_run_columns: number; per_run_values: number;
  intensity_based: boolean; comparable: boolean; quant_kind: string | null;
};
type Inspect = {
  upload_id: string; file: string; n_rows: number; n_cols: number;
  id_columns: string[]; numeric_columns: string[]; suggested_groups: Group[]; contaminant_columns: string[];
  odetta_compatible: boolean; odetta_prefixes: string[]; mztab?: MzTab | null;
};
type Row = { id: string; log2fc: number | null; t: number | null; p: number | null; q: number | null; mean_a: number | null; mean_b: number | null; n_a: number; n_b: number };
type Pt = { id: string; log2fc: number | null; neglog10p: number | null; sig: "up" | "down" | "ns" };
type RunOut = {
  summary: { n_proteins: number; n_dropped_contaminants: number; n_tested: number; n_sig_q05: number; n_up: number; n_down: number; log_transform: string; p99_raw: number | null; min_valid: number; sign: string; test: string };
  columns: string[]; rows: Row[]; volcano: Pt[];
};
type OdettaOut = {
  ok: boolean; error: string | null; stdout: string; stderr: string; command?: string; outdir?: string;
  outputs: { name: string; rows: number; columns: string[]; head: Record<string, unknown>[]; error?: string }[];
};
type Mode = "welch" | "odetta";
type SortKey = keyof Row;

const CUSTOM = "__custom__";
const PAGE = 300;

async function jfetch<T>(r: Promise<Response>): Promise<T> {
  const res = await r;
  if (!res.ok) {
    let msg = `${res.status}`;
    try { const j = await res.json(); msg += ` ${j.detail ?? JSON.stringify(j)}`; } catch { msg += ` ${await res.text()}`; }
    throw new Error(msg);
  }
  return res.json();
}
const fmt = (v: number | null | undefined, d = 3) => (v === null || v === undefined ? "—" : Math.abs(v) < 1e-3 && v !== 0 ? v.toExponential(2) : v.toFixed(d));
const csvCell = (v: unknown) => { const s = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };

type PrideTable = { file_name: string; category: string; size_bytes: number | null; kind: string; openable: boolean };
/** Which search engine can actually run on this dataset's raw files — decided by the API from vendor + acquisition. */
type Track = { kind: string; engine: string; engine_label: string; run_count: number; output_table: string; config: string; why: string };
type Substage = { name: string; detail: string };
type Stage = {
  id: string; label: string; detail: string; where: string;
  engine?: string; engine_label?: string; output_table?: string; substages?: Substage[];
};
type SearchPlan = {
  vendor: string | null; vendor_label: string; acquisition: string; tag_acquisition: string | null; raw_count: number;
  engine: string | null; engine_label: string | null; why: string | null; output_table: string | null;
  maxquant_offered: boolean; maxquant_reason: string | null; caveat: string | null; conflict: string | null;
  mixed: boolean; runs_dda: number; runs_dia: number; runs_unclassified: number; sidecars: number;
  tracks: Track[]; stages: Stage[];
  convert: { from: string; to: string; tool: string; url: string; why: string } | null; kit_url?: string;
};
type PrideResults = { accession: string; tables: PrideTable[]; archives: PrideTable[]; raw_files: number; has_results: boolean; note: string | null; plan: SearchPlan | null };

// The origin is client-only. Reading it through useSyncExternalStore keeps the server render ("") and the
// client render consistent, with no state written from an effect.
const NO_SUB = () => () => {};
const getOrigin = () => window.location.origin;
const getOriginServer = () => "";

type RunStage = Stage & { state: "pending" | "running" | "done" | "failed"; seconds: number | null; steps: string[]; message: string | null };
type RunStatus = {
  accession: string; can_run: boolean; exists: boolean; running: boolean; finished: boolean; failed: boolean;
  stages: RunStage[]; log: string[]; uploads: string[]; dir: string | null;
};
const STATE_MARK: Record<string, string> = { pending: "○", running: "◐", done: "✓", failed: "✗" };

/** Runs the pipeline here, through the local API, and shows it happening. No terminal. */
function RunHere({ accession, remoteAvailable }: { accession: string; remoteAvailable: boolean }) {
  const router = useRouter();
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const seen = useRef<string | null>(null);

  const poll = useCallback(async () => {
    try { return await jfetch<RunStatus>(fetch(`${API}/dataset/${accession}/pipeline/status`)); } catch { return null; }
  }, [accession]);

  // One timer owns the polling: a quick beat while a run is live, a slow one otherwise so an idle page is cheap.
  const running = status?.running ?? false;
  useEffect(() => {
    let live = true;
    const tick = async () => {
      const s = await poll();
      if (live && s) setStatus(s);
    };
    const first = window.setTimeout(tick, 0);
    const id = window.setInterval(tick, running ? 2000 : 15000);
    return () => { live = false; window.clearTimeout(first); window.clearInterval(id); };
  }, [poll, running]);
  // When the run hands a table back, open it: the point of the whole exercise is the table, not the run.
  useEffect(() => {
    const last = status?.uploads?.[status.uploads.length - 1];
    if (last && seen.current !== last) { seen.current = last; router.push(`/analyse?upload=${encodeURIComponent(last)}`); }
  }, [status?.uploads, router]);

  if (status && !status.can_run) return null;  // a hosted PRIDE Map cannot run searches; the one-liner still can

  const start = async () => {
    setStarting(true); setErr(null);
    try {
      await jfetch(fetch(`${API}/dataset/${accession}/pipeline/start?web=${encodeURIComponent(window.location.origin)}`, { method: "POST" }));
      const s = await poll();
      if (s) setStatus(s);
    } catch (e) { setErr(String(e)); } finally { setStarting(false); }
  };

  const stages = status?.stages ?? [];
  const active = stages.find((s) => s.state === "running");
  const done = stages.filter((s) => s.state === "done").length;
  return (
    <div className="rounded-md border bg-card p-3 grid gap-2" data-tour="run-here">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-semibold">Run it here</span>
        {status?.running
          ? <span className="pill">running · {done}/{stages.length - 1} stages</span>
          : status?.finished ? <span className="pill bg-ok-soft text-ok">finished</span>
          : status?.failed ? <span className="pill text-warn">stopped</span> : null}
        {remoteAvailable && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-ok/40 bg-ok-soft px-2.5 py-0.5 text-xs font-medium text-ok">
            <span className="size-1.5 rounded-full bg-ok animate-pulse" />
            Remote MaxQuant connected
          </span>
        )}
        <button type="button" className="btn btn-sm btn-primary ml-auto" disabled={starting || !!status?.running} onClick={start}>
          {status?.running ? <><span className="spinner mr-2" />Running…</> : starting ? "Starting…" : status?.exists ? "Resume" : "Run the whole pipeline"}
        </button>
      </div>
      <p className="text-xs text-faint">
        PRIDE Map runs every stage for you on this machine — nothing to paste into a terminal. Safe to close this
        page; the run keeps going and resumes where it stopped.
      </p>
      {err && <p className="text-xs text-warn">{err}</p>}
      {stages.length > 0 && (status?.running || status?.finished || status?.failed) && (
        <ol className="grid gap-0.5 text-xs">
          {stages.map((s) => (
            <li key={s.id} className={s.state === "failed" ? "text-warn" : s.state === "pending" ? "text-faint" : ""}>
              <span aria-hidden className="inline-block w-4">{STATE_MARK[s.state]}</span>
              {s.label}
              {s.seconds != null && <span className="text-faint"> · {Math.floor(s.seconds / 60)}m {s.seconds % 60}s</span>}
              {s.message && <span className="text-faint"> · {s.message}</span>}
              {s.state === "running" && s.steps.length > 0 && (
                <div className="pl-6 text-faint">{s.steps[s.steps.length - 1]}</div>
              )}
            </li>
          ))}
        </ol>
      )}
      {active && status?.log && status.log.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-faint hover:text-foreground">output</summary>
          <pre className="mt-1 max-h-48 overflow-auto bg-muted/50 p-2 rounded text-[11px] leading-snug">{status.log.join("\n")}</pre>
        </details>
      )}
    </div>
  );
}

/** The whole pipeline as one command. Carries this page's origin so the script can link results straight back. */
function OneLiner({ accession }: { accession: string }) {
  const origin = useSyncExternalStore(NO_SUB, getOrigin, getOriginServer);
  const [copied, setCopied] = useState(false);
  const cmd = `curl -fsSL ${API}/dataset/${accession}/pipeline.sh${origin ? `?web=${encodeURIComponent(origin)}` : ""} | bash`;
  return (
    <div className="rounded-md border bg-card p-3 grid gap-2">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold">Paste this into a terminal</span>
        <button
          type="button"
          className="btn btn-sm btn-primary ml-auto"
          onClick={() => { navigator.clipboard?.writeText(cmd).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }).catch(() => {}); }}
        >{copied ? "Copied ✓" : "Copy command"}</button>
      </div>
      <code className="text-xs break-all select-all rounded bg-muted/50 px-2 py-1.5 block">{cmd}</code>
      <div className="text-xs text-faint">
        Nothing to download or unzip, and it checks your tools and free space <em>before</em> fetching anything.
        Re-run the same command to resume.
      </div>
    </div>
  );
}

/** Raw-only dataset: name the engine that can actually run, say why, and hand over a kit for it. */
function SearchKit({ plan, accession, remoteAvailable }: { plan: SearchPlan; accession: string; remoteAvailable: boolean }) {
  const tracks = plan.tracks ?? [];
  const stages = plan.stages ?? [];
  const label = tracks.length > 1 ? "search kit" : `${plan.engine_label} kit`;
  const auto = stages.filter((s) => s.where !== "you decide").length;
  return (
    <div className="rounded-lg border bg-muted/30 p-4 grid gap-3" data-tour="searchkit">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-semibold text-sm">Run the search</span>
        {tracks.map((t) => <span key={t.kind} className="pill">{t.kind} → {t.engine_label}</span>)}
        <span className="text-xs text-faint">{plan.raw_count} × {plan.vendor_label}</span>
      </div>
      {plan.conflict && <p className="text-warn text-xs">⚠ {plan.conflict}</p>}

      <RunHere accession={accession} remoteAvailable={remoteAvailable} />
      {remoteAvailable && plan.maxquant_offered && <RunRemote accession={accession} />}

      <p className="text-xs text-muted-foreground">
        All {auto} stages — tool and disk check, download, {plan.convert ? "conversion, " : ""}
        {tracks.map((t) => t.engine_label).join(" and ")}, then the finished
        table{tracks.length > 1 ? "s are" : " is"} loaded straight into Analyse. The only thing left for you is
        picking group A and group B.
      </p>

      <details className="text-xs">
        <summary className="cursor-pointer text-faint hover:text-foreground">
          Rather drive it yourself? A one-line command, the kit as a zip, and every step spelled out
        </summary>
        <div className="mt-2"><OneLiner accession={accession} /></div>
        <div className="grid gap-3 mt-2">
          <div className="flex flex-wrap items-center gap-3">
            <a className="btn btn-sm" href={`${API}${plan.kit_url ?? `/dataset/${accession}/searchkit.zip`}`} download>
              Download {label} (zip)
            </a>
            <span className="text-faint">then <code>bash run_all.sh</code>, or run each script yourself</span>
          </div>
          {plan.caveat && <p className="text-muted-foreground">{plan.caveat}</p>}
          <ol className="grid gap-1.5">
            {stages.map((s, i) => (
              <li key={s.id}>
                <div className="flex items-baseline gap-2">
                  <span className="tabular text-faint shrink-0">{i + 1}.</span>
                  <span className="font-semibold">{s.label}</span>
                  <span className={`pill shrink-0 ${s.where === "automatic" ? "" : "text-faint"}`}>{s.where}</span>
                </div>
                <div className="text-muted-foreground pl-6">{s.detail}</div>
                {s.substages && s.substages.length > 0 && (
                  <details className="pl-6 mt-0.5">
                    <summary className="cursor-pointer text-faint hover:text-foreground">
                      what {s.engine_label} does inside — {s.substages.length} steps
                    </summary>
                    <ol className="grid gap-0.5 mt-1 border-l pl-3">
                      {s.substages.map((sub) => (
                        <li key={sub.name} className="text-muted-foreground">
                          <span className="font-medium">{sub.name}</span> — {sub.detail}
                        </li>
                      ))}
                    </ol>
                    <p className="text-faint mt-1">
                      {s.engine === "maxquant"
                        ? "Reported live: the run tails combined/proc/ and echoes each as MaxQuant finishes it."
                        : "Reported as DIA-NN progresses."}
                    </p>
                  </details>
                )}
              </li>
            ))}
          </ol>
          {!plan.maxquant_offered && plan.maxquant_reason && (
            <p className="text-faint"><span className="font-semibold">Why not MaxQuant:</span> {plan.maxquant_reason}</p>
          )}
        </div>
      </details>
    </div>
  );
}

/** Open a submitter result table straight from a confirmed PRIDE dataset: no download, no file picker. */
function FromPride({ initial, busy, onOpen }: { initial: string; busy: boolean; onOpen: (acc: string, file: string) => void }) {
  const { sel } = useCompare();
  const [acc, setAcc] = useState(initial);
  const [res, setRes] = useState<PrideResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [remoteAvailable, setRemoteAvailable] = useState(false);
  useEffect(() => {
    fetch(`${API}/remote-mq`).then((r) => r.ok ? r.json() : null).then((d) => { if (d) setRemoteAvailable(d.available); }).catch(() => {});
  }, []);
  const look = async (a: string) => {
    const x = a.trim().toUpperCase();
    if (!x) return;
    setLoading(true); setErr(null); setRes(null);
    try { setRes(await jfetch<PrideResults>(fetch(`${API}/dataset/${encodeURIComponent(x)}/results`))); } catch (e) { setErr(String(e)); } finally { setLoading(false); }
  };
  useEffect(() => { if (initial) { const id = window.setTimeout(() => { setAcc(initial); look(initial); }, 0); return () => window.clearTimeout(id); } }, [initial]);
  const all = res ? [...res.tables, ...res.archives] : [];
  return (
    <div className="rounded-xl border bg-card p-5 grid gap-3" data-tour="from-pride">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-semibold">Start from a PRIDE dataset</span>
        <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); look(acc); }}>
          <input value={acc} onChange={(e) => setAcc(e.target.value)} className="input h-9 w-40 font-mono text-sm" placeholder="PXD…" aria-label="accession" />
          <button type="submit" className="btn h-9" disabled={loading || !acc.trim()}>{loading ? <><span className="spinner mr-2" />Looking…</> : "Find its result tables"}</button>
        </form>
        {sel.length > 0 && <span className="flex flex-wrap gap-1 text-xs items-center"><span className="text-faint font-mono">shortlist:</span>{sel.map((a) => <button key={a} type="button" className="pill font-mono" onClick={() => { setAcc(a); look(a); }}>{a}</button>)}</span>}
      </div>
      {err && <p className="text-warn text-sm">{err}</p>}
      {res && res.note && <p className="note text-sm"><span aria-hidden>ⓘ</span><span>{res.note} {res.raw_files > 0 && <Link href={`/dataset/${res.accession}#reanalyse`} className="underline">Open the set‑up sheet →</Link>}</span></p>}
      {res && res.plan && res.plan.engine && <SearchKit plan={res.plan} accession={res.accession} remoteAvailable={remoteAvailable} />}
      {res && all.length > 0 && (
        <ul className="grid gap-1">
          {all.slice(0, 8).map((t) => (
            <li key={t.file_name} className="flex flex-wrap items-center gap-3 text-sm rounded-md border px-3 py-2">
              <span className="font-mono text-xs truncate max-w-md" title={t.file_name}>{t.file_name}</span>
              <span className="pill">{t.kind}</span>
              {t.size_bytes ? <span className="text-faint tabular text-xs">{(t.size_bytes / 1e6).toFixed(1)} MB</span> : null}
              <button type="button" className="btn btn-sm btn-primary ml-auto" disabled={busy || !t.openable} onClick={() => onOpen(res.accession, t.file_name)} title={t.openable ? "" : "too large to fetch here; download from PRIDE and upload"}>Open in Analyse</button>
            </li>
          ))}
          {all.length > 8 && <li className="text-xs text-faint">…and {all.length - 8} more on PRIDE</li>}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">Uses the submitter&apos;s own search output on PRIDE (MaxQuant proteinGroups, DIA‑NN report, FragPipe tables, any CSV/TSV/XLSX). PRIDE Map can&apos;t run MaxQuant or DIA‑NN itself; if a dataset only has raw files, it works out which engine fits and hands you a ready‑made kit to run on your own machine.</p>
    </div>
  );
}

export function AnalyseView() {
  const params = useSearchParams();
  const router = useRouter();
  const uploadParam = params.get("upload");
  const accParam = params.get("acc");

  const [inspect, setInspect] = useState<Inspect | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [idCol, setIdCol] = useState("");
  const [stemA, setStemA] = useState("");
  const [stemB, setStemB] = useState("");
  const [colsA, setColsA] = useState<string[]>([]);
  const [colsB, setColsB] = useState<string[]>([]);
  const [logT, setLogT] = useState<"auto" | "log2" | "none">("auto");
  const [minValid, setMinValid] = useState(2);
  const [contam, setContam] = useState("");
  const [mode, setMode] = useState<Mode>("welch");
  const [prefix, setPrefix] = useState("Set1_Brain");
  const [name, setName] = useState("batch");
  const [result, setResult] = useState<RunOut | null>(null);
  const [lastBody, setLastBody] = useState<Record<string, unknown> | null>(null);
  const [odetta, setOdetta] = useState<OdettaOut | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const loadedId = inspect?.upload_id ?? null;

  const applyInspect = (d: Inspect) => {
    setInspect(d);
    setIdCol(d.id_columns[0] ?? "");
    const g = d.suggested_groups;
    setStemA(g[0]?.stem ?? CUSTOM); setColsA(g[0]?.columns ?? []);
    setStemB(g[1]?.stem ?? CUSTOM); setColsB(g[1]?.columns ?? []);
    setContam("");
    setMode("welch");
    setPrefix(d.odetta_prefixes[0] ?? "Set1_Brain");
    setResult(null); setOdetta(null); setErr(null);
  };

  // ?upload=<id> re-opens an existing upload
  useEffect(() => {
    if (!uploadParam || uploadParam === loadedId) return;
    let live = true;
    jfetch<Inspect>(fetch(`${API}/analyse/${encodeURIComponent(uploadParam)}`))
      .then((d) => { if (live) applyInspect(d); })
      .catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, [uploadParam, loadedId]);
  const reopening = !!uploadParam && uploadParam !== loadedId && !err && !busy;

  const open = (d: Inspect) => { applyInspect(d); router.replace(`/analyse?upload=${encodeURIComponent(d.upload_id)}`); };

  const upload = async (file: File) => {
    setErr(null); setBusy(`Uploading ${file.name} (${(file.size / 1e6).toFixed(1)} MB) and reading the columns…`);
    try {
      const fd = new FormData(); fd.append("file", file);
      open(await jfetch<Inspect>(fetch(`${API}/analyse/inspect`, { method: "POST", body: fd })));
    } catch (e) { setErr(String(e)); } finally { setBusy(null); }
  };
  const fromPride = async (accession: string, file_name: string) => {
    setErr(null); setBusy(`Fetching ${file_name} from PRIDE for ${accession} and reading the columns…`);
    try {
      open(await jfetch<Inspect>(fetch(`${API}/analyse/from_pride`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ accession, file_name }) })));
    } catch (e) { setErr(String(e)); } finally { setBusy(null); }
  };
  const useExample = async () => {
    setErr(null); setBusy("Loading Odetta's lyso-IP batch 2 (9k proteins × 1.9k columns)…");
    try {
      const { upload_id } = await jfetch<{ upload_id: string }>(fetch(`${API}/analyse/example`));
      open(await jfetch<Inspect>(fetch(`${API}/analyse/${upload_id}`)));
    } catch (e) { setErr(String(e)); } finally { setBusy(null); }
  };

  const pickGroup = (which: "a" | "b", stem: string) => {
    const g = inspect?.suggested_groups.find((x) => x.stem === stem);
    if (which === "a") { setStemA(stem); if (g) setColsA(g.columns); } else { setStemB(stem); if (g) setColsB(g.columns); }
  };

  const run = async () => {
    if (!inspect) return;
    setErr(null); setResult(null); setOdetta(null);
    try {
      if (mode === "odetta") {
        setBusy("Running Odetta's TESE pipeline (tag effect, input-normalised enrichment, mock-controlled dod)…");
        setOdetta(await jfetch<OdettaOut>(fetch(`${API}/analyse/odetta`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ upload_id: inspect.upload_id, prefix, name }) })));
      } else {
        setBusy("Welch t-test per protein + Benjamini–Hochberg…");
        const body = { upload_id: inspect.upload_id, id_column: idCol, group_a: colsA, group_b: colsB, log_transform: logT, min_valid: minValid, contaminant_column: contam || null };
        setLastBody({ ...body, label_a: stemA || "A", label_b: stemB || "B" });
        setResult(await jfetch<RunOut>(fetch(`${API}/analyse/run`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })));
        markAnalysed();
      }
    } catch (e) { setErr(String(e)); } finally { setBusy(null); }
  };

  const canRun = !!inspect && !busy && (mode === "odetta" ? !!prefix && !!name : !!idCol && colsA.length >= 2 && colsB.length >= 2);

  return (
    <div className="grid gap-5">
      <div>
        <h1 className="text-3xl">Analyse a protein table</h1>
      </div>

      {/* (a0) straight from a PRIDE dataset */}
      <FromPride initial={accParam ?? ""} busy={!!busy} onOpen={fromPride} />

      {/* (a) upload */}
      <div
        className={`rounded-xl border-2 border-dashed bg-card p-5 grid gap-3 ${dragging ? "border-teal bg-teal-soft/40" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files?.[0]; if (f) upload(f); }}
      >
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => fileRef.current?.click()} disabled={!!busy} className="btn btn-primary btn-lg">Choose a file</button>
          <span className="text-sm text-muted-foreground">or drop a .csv / .tsv / .txt / .xlsx here (up to 200 MB)</span>
          <input ref={fileRef} type="file" accept=".csv,.tsv,.txt,.xlsx" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
          <button type="button" onClick={useExample} disabled={!!busy} className="btn btn-lg" data-tour="example">Use the example table (Odetta&apos;s lyso‑IP batch 2)</button>
        </div>
        {inspect && (
          <p className="text-sm" data-tour="loaded">
            <span className="font-semibold">{inspect.file}</span> · <span className="tabular">{inspect.n_rows.toLocaleString()}</span> rows × <span className="tabular">{inspect.n_cols.toLocaleString()}</span> columns · {inspect.suggested_groups.length} sample groups detected
            {inspect.odetta_compatible && <span className="ml-2 pill bg-ok-soft text-ok">lyso‑IP layout: Odetta&apos;s pipeline available</span>}
          </p>
        )}
        {inspect?.mztab && !inspect.mztab.comparable && (
          <p className="note text-sm text-warn" data-tour="mztab-warning">
            <span aria-hidden>⚠</span>
            <span>
              This mzTab identifies {inspect.mztab.rows.toLocaleString()} proteins, but every one of its{" "}
              {inspect.mztab.per_run_columns} per‑run columns is empty — a flat identification list for the whole
              study, with no per‑sample numbers. There is nothing here to compare, so A vs B and a volcano plot are
              not possible from this file. Getting them means running the search over the raw files.
            </span>
          </p>
        )}
        {inspect?.mztab?.comparable && (
          <p className="note text-sm">
            <span aria-hidden>ⓘ</span>
            <span>
              mzTab: {inspect.mztab.rows.toLocaleString()} proteins across {inspect.mztab.runs} runs, quantified by{" "}
              {inspect.mztab.quant_kind}.
              {!inspect.mztab.intensity_based && " Spectral counts are semi-quantitative — noisier than LFQ intensities, and a t-test on counts is approximate."}
            </span>
          </p>
        )}
        {busy && <p className="text-sm text-teal">{busy}</p>}
        {reopening && <p className="text-sm text-teal">Re-opening upload {uploadParam}…</p>}
        {err && <p className="text-sm text-warn">{err}</p>}
      </div>

      {/* (b) choose groups */}
      {inspect && (
        <div className="rounded-xl border bg-card p-5 grid gap-4">
          <div className="flex flex-wrap gap-2">
            <ModeButton active={mode === "welch"} onClick={() => setMode("welch")}>Group A vs group B</ModeButton>
            <ModeButton active={mode === "odetta"} onClick={() => setMode("odetta")} disabled={!inspect.odetta_compatible} title={inspect.odetta_compatible ? "" : "needs <prefix>_IP_LTKI.N and <prefix>_IP_LTWT.N columns"}>Odetta&apos;s TESE pipeline</ModeButton>
          </div>

          {mode === "welch" ? (
            <>
              <div className="grid sm:grid-cols-2 gap-4" data-tour="groups">
                <GroupPicker label="Group A" tourId="group-a" hint="the baseline (e.g. WT, control)" stem={stemA} cols={colsA} groups={inspect.suggested_groups} numeric={inspect.numeric_columns} onStem={(s) => pickGroup("a", s)} onCols={(c) => { setColsA(c); setStemA(CUSTOM); }} />
                <GroupPicker label="Group B" tourId="group-b" hint="the condition (e.g. KO, treated)" stem={stemB} cols={colsB} groups={inspect.suggested_groups} numeric={inspect.numeric_columns} onStem={(s) => pickGroup("b", s)} onCols={(c) => { setColsB(c); setStemB(CUSTOM); }} />
              </div>
              <div className="grid sm:grid-cols-4 gap-3 text-sm">
                <label className="grid gap-1">
                  <span className="label">protein id column</span>
                  <select value={idCol} onChange={(e) => setIdCol(e.target.value)} className="input h-10">
                    {inspect.id_columns.map((c) => <option key={c} value={c}>{c}</option>)}
                    {!inspect.id_columns.length && <option value="">(no text id column found)</option>}
                  </select>
                </label>
                <label className="grid gap-1">
                  <span className="label">intensities are</span>
                  <select value={logT} onChange={(e) => setLogT(e.target.value as typeof logT)} className="input h-10">
                    <option value="auto">auto-detect (log2 if raw)</option>
                    <option value="log2">raw → take log2</option>
                    <option value="none">already log-scale</option>
                  </select>
                </label>
                <label className="grid gap-1">
                  <span className="label">min values per group</span>
                  <input type="number" min={2} max={50} value={minValid} onChange={(e) => setMinValid(Math.max(2, Number(e.target.value) || 2))} className="input h-10 tabular" />
                </label>
                <label className="grid gap-1">
                  <span className="label">drop contaminants by</span>
                  <select value={contam} onChange={(e) => setContam(e.target.value)} className="input h-10">
                    <option value="">(don&apos;t filter)</option>
                    {inspect.contaminant_columns.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
              </div>
            </>
          ) : (
            <div className="grid sm:grid-cols-[1fr_1fr_2fr] gap-3 text-sm">
              <label className="grid gap-1">
                <span className="label">column prefix</span>
                <select value={prefix} onChange={(e) => setPrefix(e.target.value)} className="input h-10">
                  {inspect.odetta_prefixes.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </label>
              <label className="grid gap-1">
                <span className="label">batch name</span>
                <input value={name} onChange={(e) => setName(e.target.value.replace(/[^A-Za-z0-9_-]/g, "_"))} className="input h-10 font-mono" />
              </label>
              <p className="text-muted-foreground self-end">
                Runs <code>analysis/tese_lysoip_analysis.py</code>: tag‑vs‑tag TESE effect, input‑normalised enrichment and the mock‑controlled dod, using the <code>{prefix}_IP_*</code> and <code>{prefix}_WC_*</code> columns. Positive = TESE‑high.
              </p>
            </div>
          )}

          <div className="flex items-center gap-3">
            <button type="button" onClick={run} disabled={!canRun} className="btn btn-primary btn-lg px-6" data-tour="run">{busy ? <><span className="spinner mr-2" />Running…</> : "Run"}</button>
            {mode === "welch" && <span className="text-xs text-muted-foreground">{colsA.length} vs {colsB.length} samples · each group needs ≥ 2 replicates</span>}
          </div>
        </div>
      )}

      {/* (d) results */}
      {result && <div data-tour="results"><WelchResults out={result} uploadId={inspect?.upload_id ?? "table"} reportBody={lastBody} /></div>}
      {odetta && <OdettaResults out={odetta} />}
    </div>
  );
}

function ModeButton({ active, disabled, title, onClick, children }: { active: boolean; disabled?: boolean; title?: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} className={`btn rounded-full ${active ? "btn-primary" : ""}`}>{children}</button>
  );
}

function GroupPicker({ label, hint, stem, cols, groups, numeric, onStem, onCols, tourId }: {
  tourId?: string;
  label: string; hint: string; stem: string; cols: string[]; groups: Group[]; numeric: string[]; onStem: (s: string) => void; onCols: (c: string[]) => void;
}) {
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState(false);
  const shown = useMemo(() => {
    const f = filter.toLowerCase();
    return numeric.filter((c) => !f || c.toLowerCase().includes(f)).slice(0, 400);
  }, [numeric, filter]);
  const toggle = (c: string) => onCols(cols.includes(c) ? cols.filter((x) => x !== c) : [...cols, c]);
  return (
    <div className="grid gap-2 text-sm">
      <div>
        <div className="label">{label} <span className="normal-case font-normal">· {hint}</span></div>
        <select value={stem} onChange={(e) => onStem(e.target.value)} className="input mt-1 h-10" data-tour={tourId}>
          {groups.map((g) => <option key={g.stem} value={g.stem}>{g.stem} ({g.columns.length} samples, {Math.round(g.fill * 100)}% filled)</option>)}
          <option value={CUSTOM}>custom selection…</option>
        </select>
      </div>
      <div className="flex flex-wrap gap-1">
        {cols.map((c) => <span key={c} className="pill">{c}</span>)}
        {!cols.length && <span className="text-xs text-muted-foreground">no columns yet</span>}
      </div>
      <button type="button" onClick={() => setEditing((v) => !v)} className="justify-self-start text-xs underline text-muted-foreground">{editing ? "done" : "edit columns"}</button>
      {editing && (
        <div className="rounded-md border p-2 grid gap-1">
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="filter columns" className="input h-8 px-2 text-xs" />
          <div className="max-h-48 overflow-y-auto grid gap-0.5">
            {shown.map((c) => (
              <label key={c} className="flex items-center gap-2 text-xs font-mono">
                <input type="checkbox" checked={cols.includes(c)} onChange={() => toggle(c)} />{c}
              </label>
            ))}
            {shown.length === 400 && <div className="text-xs text-muted-foreground">showing first 400; narrow the filter</div>}
          </div>
        </div>
      )}
    </div>
  );
}

function Tile({ label, value, cls }: { label: string; value: number; cls?: string }) {
  return (
    <div className={`rounded-xl border p-4 ${cls ?? "bg-card"}`}>
      <div className="text-3xl font-semibold tabular">{value.toLocaleString()}</div>
      <div className="label">{label}</div>
    </div>
  );
}

function WelchResults({ out, uploadId, reportBody }: { out: RunOut; uploadId: string; reportBody: Record<string, unknown> | null }) {
  const s = out.summary;
  const [reporting, setReporting] = useState(false);
  const downloadReport = async () => {
    if (!reportBody) return;
    setReporting(true);
    try {
      const r = await fetch(`${API}/analyse/report`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(reportBody) });
      if (!r.ok) throw new Error(await r.text());
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (r.headers.get("content-disposition")?.match(/filename="([^"]+)"/)?.[1]) ?? "pridemap_analysis.zip";
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (e) { alert(String(e)); } finally { setReporting(false); }
  };
  return (
    <div className="grid gap-4">
      <div className="card p-3 flex flex-wrap items-center gap-3 text-sm">
        <div><b>Report bundle for publication.</b> <span className="text-muted-foreground">A zip with a Methods paragraph written from these exact parameters, every tested protein as CSV, the parameters, a standalone <code>reproduce.py</code> that regenerates the result, and software versions. Upload it next to the paper.</span></div>
        <button type="button" onClick={downloadReport} disabled={!reportBody || reporting} className="btn btn-primary ml-auto" data-tour="report">{reporting ? <><span className="spinner mr-2" />Building…</> : "Download report bundle"}</button>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile label="proteins tested" value={s.n_tested} />
        <Tile label="significant (q < 0.05)" value={s.n_sig_q05} />
        <Tile label="up: higher in B" value={s.n_up} cls="bg-peak-soft text-peak" />
        <Tile label="down: higher in A" value={s.n_down} cls="bg-teal-soft text-teal" />
      </div>
      <div className="text-xs text-muted-foreground grid gap-0.5">
        <div><b>log2FC</b> = log2(mean B ÷ mean A): +1 means twice as much in B, −1 means half. {s.sign}.</div>
        <div><b>q</b> = the t-test p-value corrected for testing {s.n_tested.toLocaleString()} proteins at once (Benjamini–Hochberg); q &lt; 0.05 means about 5% of the proteins called significant are expected to be false positives.</div>
        <div>{s.n_proteins.toLocaleString()} rows in the table{s.n_dropped_contaminants ? `, ${s.n_dropped_contaminants} dropped as contaminants` : ""}; rows with fewer than {s.min_valid} values in either group were skipped. Intensities {s.log_transform === "log2" ? `were log2-transformed (99th percentile of raw values ≈ ${s.p99_raw?.toLocaleString()})` : "were used as given (already log-scale)"}. {s.test}.</div>
      </div>
      <Volcano points={out.volcano} />
      <ResultsTable rows={out.rows} columns={out.columns} uploadId={uploadId} />
    </div>
  );
}

function niceTicks(min: number, max: number, n = 6): number[] {
  const span = max - min || 1;
  const raw = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

function Volcano({ points }: { points: Pt[] }) {
  const [hover, setHover] = useState<Pt | null>(null);
  const W = 760, H = 440, m = { l: 56, r: 16, t: 20, b: 48 };
  const { pts, xmax, ymax, sx, sy } = useMemo(() => {
    const pts = points.filter((p) => p.log2fc !== null && p.neglog10p !== null) as (Pt & { log2fc: number; neglog10p: number })[];
    const xmax = Math.max(1, ...pts.map((p) => Math.abs(p.log2fc))) * 1.05;
    const ymax = Math.max(1, ...pts.map((p) => p.neglog10p)) * 1.05;
    const sx = (x: number) => m.l + ((x + xmax) / (2 * xmax)) * (W - m.l - m.r);
    const sy = (y: number) => H - m.b - (y / ymax) * (H - m.t - m.b);
    return { pts, xmax, ymax, sx, sy };
  }, [points, m.l, m.r, m.t, m.b]);
  const xt = niceTicks(-xmax, xmax), yt = niceTicks(0, ymax);
  const r = pts.length > 5000 ? 1.8 : 2.6;
  const order = [...pts.filter((p) => p.sig === "ns"), ...pts.filter((p) => p.sig !== "ns")];
  return (
    <div className="rounded-xl border bg-card p-3 grid gap-1">
      <div className="flex flex-wrap items-center gap-3 text-xs px-1">
        <span className="font-semibold">Volcano</span>
        <span className="flex items-center gap-1"><i className="inline-block w-2.5 h-2.5 rounded-full bg-peak" /> up in B (q &lt; 0.05)</span>
        <span className="flex items-center gap-1"><i className="inline-block w-2.5 h-2.5 rounded-full bg-teal" /> down in B (q &lt; 0.05)</span>
        <span className="flex items-center gap-1"><i className="inline-block w-2.5 h-2.5 rounded-full bg-muted-foreground/40" /> not significant</span>
        <span className="ml-auto font-mono text-muted-foreground min-h-4">{hover ? `${hover.id} · log2FC ${fmt(hover.log2fc, 2)} · p ${hover.neglog10p === null ? "—" : Math.pow(10, -hover.neglog10p).toExponential(2)}` : "hover a point"}</span>
      </div>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[760px] mx-auto block" role="img" aria-label="volcano plot: log2 fold change against -log10 p">
          {yt.map((v) => <line key={`gy${v}`} x1={m.l} x2={W - m.r} y1={sy(v)} y2={sy(v)} className="text-border" stroke="currentColor" strokeWidth={0.5} />)}
          <line x1={sx(0)} x2={sx(0)} y1={m.t} y2={H - m.b} className="text-muted-foreground" stroke="currentColor" strokeWidth={0.8} strokeDasharray="3 3" />
          <line x1={m.l} x2={W - m.r} y1={H - m.b} y2={H - m.b} className="text-foreground" stroke="currentColor" />
          <line x1={m.l} x2={m.l} y1={m.t} y2={H - m.b} className="text-foreground" stroke="currentColor" />
          {xt.map((v) => (
            <g key={`x${v}`} className="text-muted-foreground" fill="currentColor" stroke="currentColor">
              <line x1={sx(v)} x2={sx(v)} y1={H - m.b} y2={H - m.b + 5} />
              <text x={sx(v)} y={H - m.b + 18} textAnchor="middle" fontSize={11} stroke="none">{v}</text>
            </g>
          ))}
          {yt.map((v) => (
            <g key={`y${v}`} className="text-muted-foreground" fill="currentColor" stroke="currentColor">
              <line x1={m.l - 5} x2={m.l} y1={sy(v)} y2={sy(v)} />
              <text x={m.l - 8} y={sy(v) + 4} textAnchor="end" fontSize={11} stroke="none">{v}</text>
            </g>
          ))}
          <text x={(m.l + W - m.r) / 2} y={H - 10} textAnchor="middle" fontSize={12} className="text-foreground" fill="currentColor">log2 fold change (B − A)</text>
          <text x={14} y={(m.t + H - m.b) / 2} textAnchor="middle" fontSize={12} transform={`rotate(-90 14 ${(m.t + H - m.b) / 2})`} className="text-foreground" fill="currentColor">−log10 p</text>
          {order.map((p, i) => (
            <circle
              key={`${p.id}-${i}`} cx={sx(p.log2fc)} cy={sy(p.neglog10p)} r={r}
              className={p.sig === "up" ? "text-peak" : p.sig === "down" ? "text-teal" : "text-muted-foreground"}
              fill="currentColor" opacity={p.sig === "ns" ? 0.35 : 0.9}
              onMouseEnter={() => setHover(p)} onMouseLeave={() => setHover(null)}
            />
          ))}
          {hover && hover.log2fc !== null && hover.neglog10p !== null && (
            <circle cx={sx(hover.log2fc)} cy={sy(hover.neglog10p)} r={r + 3} fill="none" className="text-foreground" stroke="currentColor" strokeWidth={1.2} pointerEvents="none" />
          )}
        </svg>
      </div>
    </div>
  );
}

const COL_LABEL: Record<string, string> = { id: "protein", log2fc: "log2FC", p: "p", q: "q", t: "t", mean_a: "mean A", mean_b: "mean B", n_a: "n A", n_b: "n B" };

function ResultsTable({ rows, columns, uploadId }: { rows: Row[]; columns: string[]; uploadId: string }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "p", dir: 1 });
  const [all, setAll] = useState(false);
  const [q, setQ] = useState("");
  const sorted = useMemo(() => {
    const f = q.toLowerCase();
    const base = f ? rows.filter((r) => r.id.toLowerCase().includes(f)) : rows;
    return [...base].sort((a, b) => {
      const va = a[sort.key], vb = b[sort.key];
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir;
    });
  }, [rows, sort, q]);
  const shown = all ? sorted : sorted.slice(0, PAGE);
  const clickSort = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: key === "id" ? 1 : key === "p" || key === "q" ? 1 : -1 }));
  const download = () => {
    const csv = [columns.join(","), ...rows.map((r) => columns.map((c) => csvCell(r[c as SortKey])).join(","))].join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a"); a.href = url; a.download = `analyse_${uploadId}.csv`; a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="rounded-xl border bg-card grid gap-0">
      <div className="flex flex-wrap items-center gap-3 p-3 border-b text-sm">
        <span className="font-semibold">Results</span>
        <span className="text-xs text-muted-foreground">{rows.length.toLocaleString()} rows returned (every q &lt; 0.05 hit plus the top 2,000 by p). Click a header to sort.</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="find a protein" className="input h-8 w-auto px-2 text-xs ml-auto" />
        <button type="button" onClick={download} className="btn btn-sm">download CSV</button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b">
              {columns.map((c) => (
                <th key={c} onClick={() => clickSort(c as SortKey)} className={`p-2 label cursor-pointer select-none whitespace-nowrap ${c === "id" ? "text-left" : "text-right"}`}>
                  {COL_LABEL[c] ?? c}{sort.key === c ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => {
              const sig = r.q !== null && r.q < 0.05;
              const cls = sig ? (r.log2fc ?? 0) > 0 ? "text-peak" : "text-teal" : "";
              return (
                <tr key={`${r.id}-${i}`} className="border-b last:border-0">
                  {columns.map((c) => {
                    const v = r[c as SortKey];
                    return (
                      <td key={c} className={`p-2 tabular whitespace-nowrap ${c === "id" ? "text-left font-mono" : "text-right"} ${c === "log2fc" || c === "q" ? `font-semibold ${cls}` : ""}`}>
                        {c === "id" ? String(v) : typeof v === "number" && (c === "n_a" || c === "n_b") ? v : fmt(v as number | null)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {sorted.length > PAGE && (
        <button type="button" onClick={() => setAll((v) => !v)} className="p-3 text-xs underline text-muted-foreground text-left">{all ? `show first ${PAGE}` : `show all ${sorted.length.toLocaleString()}`}</button>
      )}
    </div>
  );
}

const cell = (v: unknown) => (v === null || v === undefined ? "—" : typeof v === "number" ? (Number.isInteger(v) ? v : fmt(v)) : typeof v === "boolean" ? String(v) : String(v));

function OdettaResults({ out }: { out: OdettaOut }) {
  return (
    <div className="grid gap-4">
      <div className={`rounded-xl border p-4 ${out.ok ? "border-ok bg-ok-soft/40" : "border-warn bg-warn-soft/40"}`}>
        <div className="font-heading font-semibold text-lg">{out.ok ? "Odetta's pipeline finished" : "Odetta's pipeline failed"}</div>
        {out.error && <p className="mt-1 text-sm text-warn">{out.error}</p>}
        {out.stdout && <pre className="mt-2 text-xs whitespace-pre-wrap">{out.stdout}</pre>}
        {!out.ok && out.stderr && <pre className="mt-2 text-xs whitespace-pre-wrap text-warn">{out.stderr}</pre>}
        {out.outdir && <p className="mt-2 text-xs text-muted-foreground">Full CSVs are in <code>{out.outdir}/</code>. Sign convention: positive = TESE‑high.</p>}
      </div>
      {out.outputs.map((o) => (
        <div key={o.name} className="rounded-xl border bg-card">
          <div className="p-3 border-b text-sm"><span className="font-mono font-semibold">{o.name}</span> <span className="text-muted-foreground">· {o.rows.toLocaleString()} rows · showing first {o.head.length}</span>{o.error && <span className="text-warn"> · {o.error}</span>}</div>
          <div className="overflow-x-auto">
            <table className="text-xs">
              <thead><tr className="border-b">{o.columns.map((c) => <th key={c} className="p-2 text-left whitespace-nowrap label">{c}</th>)}</tr></thead>
              <tbody>
                {o.head.map((r, i) => (
                  <tr key={i} className="border-b last:border-0">{o.columns.map((c) => <td key={c} className="p-2 tabular whitespace-nowrap">{cell(r[c])}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
