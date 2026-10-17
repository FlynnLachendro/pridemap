import { Suspense } from "react";
import { AnalyseView } from "@/components/analyse-view";

export default function AnalysePage() {
  return (
    <Suspense>
      <AnalyseView />
    </Suspense>
  );
}
