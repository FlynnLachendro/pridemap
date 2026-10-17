import { Suspense } from "react";
import { Browse } from "@/components/browse";

export default function Home() {
  return (
    <Suspense>
      <Browse />
    </Suspense>
  );
}
