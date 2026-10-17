"use client";
import { useCompare } from "@/lib/compare-store";
import { cn } from "@/lib/utils";

export function CompareToggle({ accession, className }: { accession: string; className?: string }) {
  const { has, toggle } = useCompare();
  const on = has(accession);
  return (
    <button
      type="button"
      onClick={() => toggle(accession)}
      aria-pressed={on}
      className={cn("text-xs rounded-full border px-2 py-0.5 font-bold", on ? "bg-peak-soft text-peak border-peak" : "text-muted-foreground hover:bg-secondary", className)}
    >
      {on ? "✓ shortlisted" : "+ shortlist"}
    </button>
  );
}
