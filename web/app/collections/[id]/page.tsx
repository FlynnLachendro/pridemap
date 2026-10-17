import { Suspense } from "react";
import { CollectionView } from "@/components/collection-view";

export default function CollectionPage() {
  return (
    <Suspense>
      <CollectionView />
    </Suspense>
  );
}
