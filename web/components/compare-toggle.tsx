"use client";
import { useCompare } from "@/lib/compare-store";
import { cn } from "@/lib/utils";

export function CompareToggle({ accession, className, tourIndex }: { accession: string; className?: string; tourIndex?: number }) {
  const { has, toggle } = useCompare();
  const on = has(accession);
  return (
    <button
      type="button"
      onClick={() => toggle(accession)}
      aria-pressed={on}
      data-tour-acc={accession}
      data-tour={tourIndex !== undefined ? `shortlist-${tourIndex}` : undefined}
      className={cn("pill border bg-transparent transition-colors duration-[120ms]", on ? "bg-peak-soft text-peak border-peak" : "border-border text-muted-foreground hover:bg-secondary", className)}
    >
      {on ? "✓ shortlisted" : "+ shortlist"}
    </button>
  );
}
