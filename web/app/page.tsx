import { Suspense } from "react";
import { Ask } from "@/components/ask";
import { stats } from "@/lib/api";


export default async function Home() {
  const s = await stats().catch(() => null);
  return (
    <Suspense>
      <Ask stats={s} />
    </Suspense>
  );
}
