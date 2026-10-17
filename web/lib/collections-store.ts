"use client";
// Client-side view of the user's collections: one shared fetch, refreshed whenever any component changes a collection.
import { useEffect, useState } from "react";
import { createCollection, deleteCollection, listCollections, patchCollection, type CollectionRow } from "@/lib/api";

const EVT = "pridemap-collections-change";
let cache: CollectionRow[] | null = null;
let inflight: Promise<CollectionRow[]> | null = null;

async function refresh() {
  inflight = inflight ?? listCollections().then((r) => { cache = r; return r; }).finally(() => { inflight = null; });
  const r = await inflight;
  window.dispatchEvent(new Event(EVT));
  return r;
}

export function useCollections() {
  // Always start empty so server and client render the same HTML; the cache is applied after mount.
  const [rows, setRows] = useState<CollectionRow[] | null>(null);
  useEffect(() => {
    const sync = () => setRows(cache);
    const id = window.setTimeout(() => { if (cache) sync(); else refresh().catch(() => {}); }, 0);
    window.addEventListener(EVT, sync);
    return () => { window.clearTimeout(id); window.removeEventListener(EVT, sync); };
  }, []);
  return {
    rows,
    reload: refresh,
    async create(name: string, accessions: string[], note?: string) {
      const c = await createCollection({ name, accessions, note });
      await refresh();
      return c;
    },
    async setMembership(id: string, accession: string, member: boolean) {
      const cur = cache?.find((c) => c.id === id)?.accessions ?? [];
      const next = member ? Array.from(new Set([...cur, accession])) : cur.filter((a) => a !== accession);
      const r = await patchCollection(id, { accessions: next });
      await refresh();
      return r;
    },
    async rename(id: string, name: string) { const r = await patchCollection(id, { name }); await refresh(); return r; },
    async remove(id: string) { await deleteCollection(id); await refresh(); },
    inAny: (accession: string) => (cache ?? []).filter((c) => c.accessions.includes(accession)),
  };
}
