import { Suspense } from "react";
import { CompareView } from "@/components/compare-view";

export default function ComparePage() {
  return (
    <Suspense>
      <CompareView />
    </Suspense>
  );
}
