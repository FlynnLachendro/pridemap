import { Suspense } from "react";
import { CollectionsView } from "@/components/collections-view";

export default function CollectionsPage() {
  return (
    <Suspense>
      <CollectionsView />
    </Suspense>
  );
}
