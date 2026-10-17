"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useCompare } from "@/lib/compare-store";
import { CollectionDialog } from "@/components/collection-dialog";

export function CompareBar() {
  const { sel, clear, toggle } = useCompare();
  const path = usePathname();
  const [saving, setSaving] = useState(false);
  if (!sel.length || path === "/compare" || path === "/flow") return null;
  return (
    <div data-tour="compare-bar" className="fixed bottom-4 left-1/2 md:left-[calc(50%+104px)] -translate-x-1/2 z-30 rounded-full border bg-card shadow-lg px-4 py-2 flex items-center gap-3 text-sm max-w-[95vw] md:max-w-[calc(100vw-13rem-2rem)]">
      <span className="font-semibold whitespace-nowrap font-mono text-xs">Shortlist {sel.length}</span>
      <span className="flex gap-1 overflow-x-auto">
        {sel.map((a) => (
          <button key={a} type="button" onClick={() => toggle(a)} className="pill hover:line-through" title="remove">{a}</button>
        ))}
      </span>
      <Link href="/flow" className="rounded-full bg-teal text-primary-foreground px-3 py-1 font-semibold whitespace-nowrap text-xs">Workflow →</Link>
      {sel.length >= 2 && <Link href={`/compare?a=${sel.join(",")}`} className="rounded-full border border-border px-3 py-1 font-semibold whitespace-nowrap text-xs hover:border-teal transition-colors duration-[120ms]">Compare</Link>}
      <button type="button" data-tour="save-collection" onClick={() => setSaving(true)} className="rounded-full border border-border px-3 py-1 font-semibold whitespace-nowrap text-xs hover:border-teal transition-colors duration-[120ms]">Save as collection</button>
      <button type="button" onClick={clear} className="text-xs text-muted-foreground underline">clear</button>
      <CollectionDialog accessions={sel} open={saving} onOpenChange={setSaving} />
    </div>
  );
}
