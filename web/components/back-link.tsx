"use client";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

/** Back to wherever you came from inside the app (results, workflow, a collection); falls back to Browse. */
export function BackLink({ fallback = "/" }: { fallback?: string }) {
  const router = useRouter();
  return (
    <button type="button" className="btn btn-ghost btn-sm -ml-2" onClick={() => { if (typeof window !== "undefined" && window.history.length > 1 && document.referrer.startsWith(window.location.origin)) router.back(); else router.push(fallback); }}>
      <ArrowLeft size={14} strokeWidth={2.2} aria-hidden /> Back
    </button>
  );
}
