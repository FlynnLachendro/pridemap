"use client";
import Link from "next/link";
import { useCompare } from "@/lib/compare-store";

/** On a dataset page: shortlist it, and compare it with anything else already shortlisted. */
export function CompareFromHere({ accession }: { accession: string }) {
  const { sel, has, toggle } = useCompare();
  const others = sel.filter((a) => a !== accession);
  return (
    <div className="card p-4 text-sm grid gap-2">
      <h2 className="text-lg">Compare</h2>
      <p className="text-muted-foreground text-xs">Shortlist datasets as you browse, then compare any of them side by side.</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => toggle(accession)} className={`btn btn-sm ${has(accession) ? "bg-peak-soft text-peak border-peak" : ""}`}>{has(accession) ? "✓ shortlisted" : "+ shortlist this"}</button>
        {others.length > 0 && (
          <Link href={`/compare?a=${[accession, ...others].join(",")}`} className="btn btn-sm btn-primary">Compare with {others.length === 1 ? others[0] : `${others.length} shortlisted`} →</Link>
        )}
      </div>
      {others.length === 0 && <p className="text-xs text-faint">Nothing else shortlisted yet. Shortlist another dataset and the compare button appears here.</p>}
    </div>
  );
}
