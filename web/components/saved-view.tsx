"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { checkSaved, deleteSaved, listSaved, pretty, seenSaved, TAG_LABEL, type SavedCheck, type SavedSearch } from "@/lib/api";
import { Skeleton } from "@/components/skeleton";

export function FilterPills({ filters }: { filters: Record<string, string> }) {
  const entries = Object.entries(filters);
  if (!entries.length) return <span className="pill">everything</span>;
  return (
    <>
      {entries.map(([k, v]) => (
        <span key={k} className="pill bg-teal-soft text-teal">
          <span className="opacity-70">{k === "years" ? "year" : k === "tagged" ? "tagged" : TAG_LABEL[k] ?? k}</span> {k === "q" ? `“${v}”` : v.split(/[|,]/).map(pretty).join(", ")}
        </span>
      ))}
    </>
  );
}

export function SavedView() {
  const [rows, setRows] = useState<SavedSearch[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<string, SavedCheck | "busy">>({});

  const reload = () => listSaved().then((r) => { setRows(r); setErr(null); }).catch((e) => setErr(String(e)));
  useEffect(() => { let live = true; listSaved().then((r) => { if (live) setRows(r); }).catch((e) => { if (live) setErr(String(e)); }); return () => { live = false; }; }, []);

  const check = async (id: string) => {
    setChecks((c) => ({ ...c, [id]: "busy" }));
    try { const r = await checkSaved(id); setChecks((c) => ({ ...c, [id]: r })); reload(); } catch (e) { setErr(String(e)); setChecks((c) => { const n = { ...c }; delete n[id]; return n; }); }
  };

  return (
    <div className="grid gap-5 max-w-4xl">
      <div className="flex flex-wrap items-end gap-3">
        <h1 className="text-3xl sm:text-4xl">Saved searches</h1>
        <Link href="/" className="ml-auto btn">Browse →</Link>
      </div>

      {err && <p className="text-warn text-sm">API error: {err}</p>}
      {!rows && !err && <div className="grid gap-2"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>}
      {rows && rows.length === 0 && <p className="text-muted-foreground">Nothing saved yet. Filter or search on Browse, then use “Save search”.</p>}
      {rows && rows.length > 0 && (
        <div className="grid gap-3">
          {rows.map((s) => {
            const ck = checks[s.id];
            const n = ck && ck !== "busy" ? ck.new_count : s.new_count;
            return (
              <article key={s.id} className="card p-4 grid gap-2 text-sm enter">
                <div className="flex flex-wrap items-center gap-3">
                  <Link href={s.url} className="font-semibold text-base hover:underline">{s.name}</Link>
                  <span className={`pill tabular ${n > 0 ? "bg-peak-soft text-peak" : "bg-ok-soft text-ok"}`}>{n} new since you saved</span>
                  <span className="ml-auto text-xs text-faint tabular">{s.total_at_save.toLocaleString()} at save · {s.created_at.slice(0, 10)}</span>
                </div>
                <div className="flex flex-wrap gap-1.5"><FilterPills filters={s.filters} /></div>
                <div className="flex flex-wrap gap-2 items-center pt-1">
                  <Link href={s.url} className="btn btn-sm">Open</Link>
                  <button type="button" className="btn btn-sm" disabled={ck === "busy"} onClick={() => check(s.id)}>{ck === "busy" ? <><span className="spinner mr-1" />Checking…</> : "Check now"}</button>
                  <button type="button" className="btn btn-sm btn-ghost" disabled={n === 0} onClick={async () => { await seenSaved(s.id); setChecks((c) => { const x = { ...c }; delete x[s.id]; return x; }); reload(); }}>Mark seen</button>
                  <button type="button" className="btn btn-sm btn-ghost ml-auto" onClick={async () => { if (!confirm(`Delete "${s.name}"?`)) return; await deleteSaved(s.id); reload(); }}>Delete</button>
                  {s.last_checked && <span className="text-xs text-faint tabular basis-full">last checked {s.last_checked.replace("T", " ").slice(0, 16)} · baseline {s.baseline_max_date ?? "—"}</span>}
                </div>
                {ck && ck !== "busy" && (
                  <div className="grid gap-1 pt-1">
                    <div className="label">{ck.total.toLocaleString()} match now · {ck.new_count} new</div>
                    {ck.new.map((d) => (
                      <div key={d.accession} className="flex gap-3 items-baseline text-xs">
                        <Link href={`/dataset/${d.accession}`} className="font-mono text-teal hover:underline">{d.accession}</Link>
                        <span className="text-faint tabular">{d.publication_date}</span>
                        <span className="truncate">{d.title}</span>
                      </div>
                    ))}
                    {ck.new_count > ck.new.length && <span className="text-xs text-faint">and {ck.new_count - ck.new.length} more</span>}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
