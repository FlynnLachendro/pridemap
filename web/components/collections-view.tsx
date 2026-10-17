"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useCollections } from "@/lib/collections-store";
import { EmptyState } from "@/components/empty-state";
import { useCompare } from "@/lib/compare-store";
import { CollectionDialog } from "@/components/collection-dialog";
import { Skeleton } from "@/components/skeleton";

export function CollectionsView() {
  const { sel } = useCompare();
  const { rows, remove, rename, reload } = useCollections();
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [find, setFind] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  useEffect(() => { reload().catch((e) => setErr(String(e))); }, [reload]);
  const shown = (rows ?? []).filter((c) => !find || c.name.toLowerCase().includes(find.toLowerCase()) || (c.note ?? "").toLowerCase().includes(find.toLowerCase()));

  return (
    <div className="grid gap-5 max-w-4xl">
      <div className="flex flex-wrap items-end gap-3">
        <h1 className="text-3xl sm:text-4xl">Collections</h1>
        <div className="ml-auto flex items-center gap-2">
          {rows && rows.length > 3 && <input value={find} onChange={(e) => setFind(e.target.value)} className="input h-9 w-48 text-sm" placeholder="Find a collection" aria-label="find a collection" />}
          <button type="button" className="btn btn-primary" onClick={() => setOpen(true)}>
            New collection{sel.length ? ` from shortlist (${sel.length})` : ""}
          </button>
        </div>
      </div>
      <CollectionDialog accessions={sel} open={open} onOpenChange={setOpen} allowEmpty />

      {err && <p className="text-warn text-sm">API error: {err}</p>}
      {!rows && !err && <div className="grid gap-2"><Skeleton className="h-14" /><Skeleton className="h-14" /></div>}
      {rows && rows.length === 0 && (
        <EmptyState title="Keep the datasets you'd use together" text="A collection is a named shortlist you can share by link, export as SDRF, or fetch with one download script. Add to one from any result card, or name your shortlist in the bar at the bottom." action="Browse datasets" href="/" />
      )}
      {rows && rows.length > 0 && shown.length === 0 && <p className="text-sm text-muted-foreground">Nothing matches “{find}”.</p>}
      {shown.length > 0 && (
        <div className="card divide-y">
          {shown.map((c) => (
            <div key={c.id} className="flex items-center gap-3 px-4 py-3 text-sm">
              {editing === c.id ? (
                <form className="flex items-center gap-1" onSubmit={async (e) => { e.preventDefault(); setEditing(null); if (draft.trim() && draft.trim() !== c.name) await rename(c.id, draft.trim()); }}>
                  <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={() => setEditing(null)} onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); }} className="input h-8 w-64 text-sm font-semibold" aria-label="collection name" />
                </form>
              ) : (
                <Link href={`/collections/${c.id}`} className="font-semibold hover:underline" onDoubleClick={(e) => { e.preventDefault(); setDraft(c.name); setEditing(c.id); }} title="double-click to rename">{c.name}</Link>
              )}
              <span className="pill font-mono tabular">{c.n} dataset{c.n === 1 ? "" : "s"}</span>
              {c.note && <span className="text-muted-foreground truncate flex-1 min-w-0 basis-40">{c.note}</span>}
              <span className="ml-auto text-xs text-faint tabular shrink-0">{c.updated_at.slice(0, 10)}</span>
              <Link href={`/collections/${c.id}`} className="btn btn-sm">Open</Link>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => { setDraft(c.name); setEditing(c.id); }}>Rename</button>
              <button type="button" className="btn btn-sm btn-ghost" onClick={async () => { if (!confirm(`Delete "${c.name}"?`)) return; await remove(c.id); }}>Delete</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
