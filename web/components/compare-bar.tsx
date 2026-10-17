"use client";
// The shortlist follows you as an unsaved collection: name it in place to keep it, or carry it into Compare / Workflow.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { X, FolderPlus } from "lucide-react";
import { useCompare } from "@/lib/compare-store";
import { useCollections } from "@/lib/collections-store";

export function CompareBar() {
  const { sel, clear, toggle } = useCompare();
  const { create } = useCollections();
  const router = useRouter();
  const path = usePathname();
  const [name, setName] = useState("");
  const [naming, setNaming] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!sel.length || path === "/compare" || path === "/flow" || path.startsWith("/tour")) return null;

  const save = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    try { const c = await create(name.trim(), sel); clear(); setName(""); setNaming(false); router.push(`/collections/${c.id}`); } finally { setBusy(false); }
  };

  return (
    <div data-tour="compare-bar" className="shortlist-bar">
      <div className="flex items-center gap-2 min-w-0">
        <span className="label text-foreground whitespace-nowrap">Unsaved collection</span>
        <span className="tabular font-mono text-xs text-faint">{sel.length}</span>
        <span className="flex gap-1 overflow-x-auto min-w-0">
          {sel.map((a) => (
            <button key={a} type="button" onClick={() => toggle(a)} className="pill gap-1 shrink-0" title="remove from shortlist">{a}<X size={11} strokeWidth={2.5} aria-hidden /></button>
          ))}
        </span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {naming ? (
          <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); save(); }}>
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setNaming(false); }} className="input h-8 w-48 text-sm" placeholder="Name this collection" aria-label="collection name" />
            <button type="submit" className="btn btn-primary btn-sm" disabled={!name.trim() || busy}>{busy ? "Saving…" : "Save"}</button>
          </form>
        ) : (
          <button type="button" data-tour="save-collection" onClick={() => setNaming(true)} className="btn btn-sm gap-1.5"><FolderPlus size={13} strokeWidth={2.2} aria-hidden />Name &amp; save</button>
        )}
        {sel.length >= 2 && <Link href={`/compare?a=${sel.join(",")}`} className="btn btn-sm">Compare</Link>}
        <Link href="/flow" className="btn btn-primary btn-sm">Workflow →</Link>
        <button type="button" onClick={clear} className="btn btn-ghost btn-sm" aria-label="clear shortlist">clear</button>
      </div>
    </div>
  );
}
