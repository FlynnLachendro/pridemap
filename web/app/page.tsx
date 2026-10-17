import { Suspense } from "react";
import { Browse } from "@/components/browse";
import { stats } from "@/lib/api";

export default async function Home() {
  const s = await stats().catch(() => null);
  return (
    <Suspense>
      <Browse stats={s} />
    </Suspense>
  );
}
