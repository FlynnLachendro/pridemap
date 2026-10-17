import { Suspense } from "react";
import { FlowView } from "@/components/flow-view";

export default function FlowPage() {
  return (
    <Suspense>
      <FlowView />
    </Suspense>
  );
}
