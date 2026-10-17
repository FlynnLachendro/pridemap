"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { createCollection, type Collection } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

export function CollectionDialog({ accessions, open, onOpenChange, onCreated }: { accessions: string[]; open: boolean; onOpenChange: (o: boolean) => void; onCreated?: (c: Collection) => void }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save {accessions.length} dataset{accessions.length === 1 ? "" : "s"} as a collection</DialogTitle>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!name.trim() || busy) return;
            setBusy(true);
            setErr(null);
            try {
              const c = await createCollection({ name: name.trim(), note: note.trim() || undefined, accessions });
              onOpenChange(false);
              setName("");
              setNote("");
              if (onCreated) onCreated(c); else router.push(`/collections/${c.id}`);
            } catch (ex) { setErr(String(ex)); } finally { setBusy(false); }
          }}
        >
          <div className="flex flex-wrap gap-1">{accessions.map((a) => <span key={a} className="pill font-mono">{a}</span>)}</div>
          <label className="grid gap-1 text-sm">
            <span className="font-medium">Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} className="input h-10" placeholder="e.g. mouse liver phospho candidates" autoFocus required />
          </label>
          <label className="grid gap-1 text-sm">
            <span className="font-medium">Note (optional)</span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} className="input" placeholder="what this shortlist is for" />
          </label>
          {err && <p className="text-warn text-xs">{err}</p>}
          <DialogFooter>
            <DialogClose render={<Button variant="outline" type="button" />}>Cancel</DialogClose>
            <Button type="submit" disabled={busy || !name.trim()} className="bg-teal text-primary-foreground hover:bg-accent-hover">{busy ? "Saving…" : "Save collection"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
