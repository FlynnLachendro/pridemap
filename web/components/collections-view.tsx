"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { deleteCollection, listCollections, type CollectionRow } from "@/lib/api";
import { useCompare } from "@/lib/compare-store";
import { CollectionDialog } from "@/components/collection-dialog";
import { Skeleton } from "@/components/skeleton";

export function CollectionsView() {
  const { sel } = useCompare();
  const [rows, setRows] = useState<CollectionRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const reload = () => listCollections().then((r) => { setRows(r); setErr(null); }).catch((e) => setErr(String(e)));
  useEffect(() => { let live = true; listCollections().then((r) => { if (live) setRows(r); }).catch((e) => { if (live) setErr(String(e)); }); return () => { live = false; }; }, []);

  return (
    <div className="grid gap-5 max-w-4xl">
      <div className="flex flex-wrap items-end gap-3">
        <h1 className="text-3xl sm:text-4xl">Collections</h1>
        <div className="ml-auto flex items-center gap-2">
          <button type="button" className="btn btn-primary" disabled={!sel.length} onClick={() => setOpen(true)} title={sel.length ? "" : "shortlist some datasets first"}>
            New from shortlist{sel.length ? ` (${sel.length})` : ""}
          </button>
        </div>
      </div>
      <CollectionDialog accessions={sel} open={open} onOpenChange={setOpen} />

      {err && <p className="text-warn text-sm">API error: {err}</p>}
      {!rows && !err && <div className="grid gap-2"><Skeleton className="h-14" /><Skeleton className="h-14" /></div>}
      {rows && rows.length === 0 && <p className="text-muted-foreground">No collections yet. Shortlist datasets from Browse, then save them here.</p>}
      {rows && rows.length > 0 && (
        <div className="card divide-y">
          {rows.map((c) => (
            <div key={c.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
              <Link href={`/collections/${c.id}`} className="font-semibold hover:underline">{c.name}</Link>
              <span className="pill font-mono tabular">{c.n} dataset{c.n === 1 ? "" : "s"}</span>
              {c.note && <span className="text-muted-foreground truncate max-w-md">{c.note}</span>}
              <span className="ml-auto text-xs text-faint tabular">{c.updated_at.slice(0, 10)}</span>
              <Link href={`/collections/${c.id}`} className="btn btn-sm">Open</Link>
              <button type="button" className="btn btn-sm btn-ghost" onClick={async () => { if (!confirm(`Delete "${c.name}"?`)) return; await deleteCollection(c.id); reload(); }}>Delete</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
