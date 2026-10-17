import { Suspense } from "react";
import { MapView } from "@/components/map-view";


export default function MapPage() {
  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-3xl">PRIDE in chunks</h1>
        <p className="text-muted-foreground">Click a block to drill in, a leaf to search it.</p>
      </div>
      <Suspense><MapView /></Suspense>
    </div>
  );
}
