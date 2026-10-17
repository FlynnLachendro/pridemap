"use client";
// "Add to collection" on any dataset: tick the collections it belongs in, or type a name to start a new one.
import { useState } from "react";
import { FolderPlus, Check } from "lucide-react";
import { useCollections } from "@/lib/collections-store";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export function AddToCollection({ accession, className, compact }: { accession: string; className?: string; compact?: boolean }) {
  const { rows, setMembership, create } = useCollections();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const member = (rows ?? []).filter((c) => c.accessions.includes(accession));
  const n = member.length;

  const toggle = async (id: string, on: boolean) => {
    setBusy(id); setErr(null);
    try { await setMembership(id, accession, on); } catch (e) { setErr(String(e)); } finally { setBusy(null); }
  };
  const make = async () => {
    if (!name.trim()) return;
    setBusy("new"); setErr(null);
    try { await create(name.trim(), [accession]); setName(""); } catch (e) { setErr(String(e)); } finally { setBusy(null); }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn("pill border bg-transparent gap-1.5", n ? "border-teal text-teal bg-teal-soft" : "border-border text-muted-foreground hover:bg-secondary", className)}
        aria-haspopup="dialog"
        title={n ? member.map((c) => c.name).join(", ") : "Add to a collection"}
      >
        <FolderPlus size={13} strokeWidth={2.2} aria-hidden />
        {compact ? (n ? n : null) : n ? `in ${n} collection${n === 1 ? "" : "s"}` : "collection"}
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>Collections for <span className="font-mono text-teal">{accession}</span></DialogTitle></DialogHeader>
          <div className="grid gap-3">
            {rows === null && <p className="text-sm text-muted-foreground">Loading…</p>}
            {rows && rows.length === 0 && <p className="text-sm text-muted-foreground">No collections yet. Name one below to start it with this dataset.</p>}
            {rows && rows.length > 0 && (
              <ul className="grid gap-1 max-h-72 overflow-y-auto -mx-1 px-1">
                {rows.map((c) => {
                  const on = c.accessions.includes(accession);
                  return (
                    <li key={c.id}>
                      <button
                        type="button"
                        disabled={busy === c.id}
                        onClick={() => toggle(c.id, !on)}
                        className={cn("w-full flex items-center gap-3 rounded-md border px-3 py-2 text-left text-sm transition-colors", on ? "border-teal bg-teal-soft/50" : "border-border hover:bg-surface-2")}
                        aria-pressed={on}
                      >
                        <span className={cn("w-4 h-4 rounded border flex items-center justify-center shrink-0", on ? "bg-teal border-teal text-on-accent" : "border-border bg-card")} aria-hidden>{on && <Check size={12} strokeWidth={3} />}</span>
                        <span className="font-semibold truncate">{c.name}</span>
                        <span className="ml-auto text-xs text-faint tabular font-mono shrink-0">{c.n}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); make(); }}>
              <input value={name} onChange={(e) => setName(e.target.value)} className="input h-9 text-sm" placeholder="New collection name" aria-label="new collection name" />
              <button type="submit" className="btn btn-primary h-9" disabled={!name.trim() || busy === "new"}>{busy === "new" ? "Saving…" : "Create"}</button>
            </form>
            {err && <p className="text-warn text-xs">{err}</p>}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
