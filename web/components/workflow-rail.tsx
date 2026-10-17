"use client";
// The loop, always in view: Find → Confirm → Set up → Analyse → Check. Lit by where you are, ticked by what you've done.
// Sits at the top of <main> on every page; the sidebar switch hides it (pridemap.rail).
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Check } from "lucide-react";
import { useProgress } from "@/lib/progress-store";
import { cn } from "@/lib/utils";

type Step = { key: string; label: string; href: string; routes: (p: string) => boolean };
const STEPS: Step[] = [
  { key: "find", label: "Find", href: "/", routes: (p) => p === "/" || p === "/map" || p.startsWith("/saved") },
  { key: "confirm", label: "Confirm", href: "/flow", routes: (p) => p === "/flow" || p.startsWith("/collections") },
  { key: "setup", label: "Set up", href: "/flow#setup", routes: (p) => p.startsWith("/dataset/") },
  { key: "analyse", label: "Analyse", href: "/analyse", routes: (p) => p === "/analyse" },
  { key: "check", label: "Check", href: "/eval", routes: (p) => p === "/eval" || p === "/compare" },
];

export function WorkflowRail() {
  const path = usePathname();
  const p = useProgress();
  if (!p || !p.rail || path.startsWith("/tour") || path === "/mcp" || path === "/about") return null;
  const done: Record<string, boolean> = { find: p.shortlist > 0, confirm: p.confirmed > 0, setup: p.setup > 0, analyse: p.analysed, check: p.checked };
  const detail: Record<string, string> = {
    find: p.shortlist ? `${p.shortlist} shortlisted` : "shortlist candidates",
    confirm: p.confirmed ? `${p.confirmed} confirmed` : "read the case, tick",
    setup: p.setup ? `${p.setup} sheet${p.setup === 1 ? "" : "s"} opened` : "search parameters",
    analyse: p.analysed ? "results ready" : "A vs B, volcano",
    check: p.checked ? "eval seen" : "trust the tags",
  };
  const current = STEPS.findIndex((s) => s.routes(path));
  return (
    <nav aria-label="Workflow" data-tour="rail" className="rail">
      <ol>
        {STEPS.map((s, i) => {
          const isDone = done[s.key];
          const isCur = i === current;
          return (
            <li key={s.key} className={cn(isDone && "done", isCur && "current")}>
              <Link href={s.href} aria-current={isCur ? "step" : undefined}>
                <span className="rail-dot" aria-hidden>{isDone ? <Check size={11} strokeWidth={3} /> : i + 1}</span>
                <span className="rail-label">{s.label}</span>
                <span className="rail-detail">{detail[s.key]}</span>
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
