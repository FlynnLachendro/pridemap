import { Suspense } from "react";
import { SavedView } from "@/components/saved-view";

export default function SavedPage() {
  return (
    <Suspense>
      <SavedView />
    </Suspense>
  );
}
