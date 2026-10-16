"use client";
import { useState } from "react";
import { correct, TAG_LABEL } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogClose } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

export function Correct({ accession, tag, current }: { accession: string; tag: string; current: string }) {
  const [value, setValue] = useState(current);
  const [note, setNote] = useState("");
  const [done, setDone] = useState(false);
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<button type="button" className="text-xs underline text-muted-foreground hover:text-foreground" />}>
        {done ? "reported ✓" : "wrong?"}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Correct {TAG_LABEL[tag] ?? tag} for {accession}</DialogTitle>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            await correct(accession, { tag, suggested_value: value || undefined, note: note || undefined });
            setDone(true);
            setOpen(false);
          }}
        >
          <label className="grid gap-1 text-sm">
            <span className="font-bold">Should be</span>
            <input value={value} onChange={(e) => setValue(e.target.value)} className="h-10 rounded-lg border bg-background px-3" />
          </label>
          <label className="grid gap-1 text-sm">
            <span className="font-bold">Why (optional)</span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} className="rounded-lg border bg-background px-3 py-2" placeholder="e.g. the paper says TiO2 enrichment was used" />
          </label>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" type="button" />}>Cancel</DialogClose>
            <Button type="submit" className="bg-teal text-white hover:bg-teal/90">Send correction</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
