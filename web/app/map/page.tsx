import { Suspense } from "react";
import { MapView } from "@/components/map-view";


export default function MapPage() {
  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-3xl">PRIDE in chunks</h1>
        <p className="text-muted-foreground max-w-2xl">Every tagged dataset, split by what it is and how it was run. Click a block to drill in; click a leaf to search it.</p>
      </div>
      <Suspense><MapView /></Suspense>
    </div>
  );
}
