"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCompare } from "@/lib/compare-store";

export function CompareBar() {
  const { sel, clear, toggle } = useCompare();
  const path = usePathname();
  if (!sel.length || path === "/compare" || path === "/flow") return null;
  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-30 rounded-full border bg-card shadow-lg px-4 py-2 flex items-center gap-3 text-sm max-w-[95vw]">
      <span className="font-semibold whitespace-nowrap font-mono text-xs">Shortlist {sel.length}</span>
      <span className="flex gap-1 overflow-x-auto">
        {sel.map((a) => (
          <button key={a} type="button" onClick={() => toggle(a)} className="font-mono text-xs rounded-full bg-secondary px-2 py-0.5 hover:line-through" title="remove">{a}</button>
        ))}
      </span>
      <Link href="/flow" className="rounded-full bg-teal text-primary-foreground px-3 py-1 font-semibold whitespace-nowrap text-xs">Workflow →</Link>
      {sel.length >= 2 && <Link href={`/compare?a=${sel.join(",")}`} className="rounded-full border px-3 py-1 font-semibold whitespace-nowrap text-xs">Compare</Link>}
      <button type="button" onClick={clear} className="text-xs text-muted-foreground underline">clear</button>
    </div>
  );
}
