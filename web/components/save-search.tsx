"use client";
import { useState } from "react";
import Link from "next/link";
import { createSaved } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

const VIEW_KEYS = new Set(["page", "size", "sort", "view", "ask", "tour"]);

export function SaveSearch({ filters, total }: { filters: Record<string, string>; total: number }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const question = Object.fromEntries(Object.entries(filters).filter(([k, v]) => !VIEW_KEYS.has(k) && v));
  const url = `/?${new URLSearchParams(question).toString()}`;
  return (
    <>
      <button type="button" className="btn btn-sm" onClick={() => { setSaved(null); setOpen(true); }}>Save search</button>
      {saved && <Link href="/saved" className="text-xs text-ok enter whitespace-nowrap">Saved ✓ view saved searches →</Link>}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Save this search</DialogTitle>
          </DialogHeader>
          <form
            className="grid gap-3"
            onSubmit={async (e) => {
              e.preventDefault();
              if (busy) return;
              setBusy(true);
              setErr(null);
              try {
                const s = await createSaved({ name: name.trim() || undefined, filters: question, url, total_at_save: total });
                setSaved(s.id);
                setName("");
                setOpen(false);
              } catch (ex) { setErr(String(ex)); } finally { setBusy(false); }
            }}
          >
            <div className="flex flex-wrap gap-1 text-xs">
              {Object.entries(question).map(([k, v]) => <span key={k} className="pill bg-teal-soft text-teal"><span className="opacity-70">{k}</span> {v}</span>)}
              <span className="pill tabular">{total.toLocaleString()} now</span>
            </div>
            <label className="grid gap-1 text-sm">
              <span className="font-medium">Name (optional)</span>
              <input value={name} onChange={(e) => setName(e.target.value)} className="input h-10" placeholder="e.g. mouse liver phospho" autoFocus />
            </label>
            {err && <p className="text-warn text-xs">{err}</p>}
            <DialogFooter>
              <DialogClose render={<Button variant="outline" type="button" />}>Cancel</DialogClose>
              <Button type="submit" disabled={busy} className="bg-teal text-primary-foreground hover:bg-accent-hover">{busy ? "Saving…" : "Save"}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
