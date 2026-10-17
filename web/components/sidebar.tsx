"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const NAV = [
  { href: "/", label: "Browse" },
  { href: "/map", label: "Map" },
  { href: "/compare", label: "Compare" },
  { href: "/analyse", label: "Analyse" },
  { href: "/flow", label: "Workflow" },
  { href: "/eval", label: "Eval" },
  { href: "/about", label: "About" },
];

export function isActive(path: string, href: string) {
  return path === href || (href !== "/" && path.startsWith(href));
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <Link href="/" className={cn("flex items-center gap-2 font-heading font-semibold text-xl tracking-tight text-foreground", className)}>
      <span className="inline-block w-2.5 h-2.5 rounded-full bg-teal shrink-0" aria-hidden />
      PRIDE Map
    </Link>
  );
}

function useStats() {
  const [s, setS] = useState<{ tagged: number; projects: number } | null>(null);
  useEffect(() => {
    let live = true;
    fetch(`${API}/stats`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (live && d && typeof d.tagged === "number") setS({ tagged: d.tagged, projects: d.projects }); })
      .catch(() => {});
    return () => { live = false; };
  }, []);
  return s;
}

/** Arbor's sidebar: w-52, surface, 1px right border; rows 13px with a
 *  12%-accent wash for the active one; 12px footer with the theme toggle. */
export function Sidebar({ className }: { className?: string }) {
  const path = usePathname();
  const stats = useStats();
  return (
    <aside className={cn("flex-col shrink-0 bg-card border-r border-border", className)}>
      <div className="px-4 h-14 flex items-center border-b border-border shrink-0">
        <Wordmark />
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-0.5" aria-label="Primary">
        {NAV.map((l) => {
          const active = isActive(path, l.href);
          return (
            <Link
              key={l.href}
              href={l.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "block px-3 py-2 rounded-md text-[15px] font-sans font-bold transition-colors duration-[120ms]",
                active
                  ? "bg-[color-mix(in_srgb,var(--teal)_12%,transparent)] text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-surface-2"
              )}
            >
              {l.label}
            </Link>
          );
        })}
      </nav>

      <div className="px-4 py-2 border-t border-border text-[12px] text-faint font-mono tabular shrink-0" aria-live="polite">
        {stats ? `${stats.tagged.toLocaleString()} of ${stats.projects.toLocaleString()} tagged` : "a front door to 42,000 datasets"}
      </div>

      <div className="px-4 py-2 border-t border-border text-[12px] text-faint font-body shrink-0">
        <div className="flex items-center justify-between gap-2">
          <p className="leading-snug">
            MIT · hackathon Oct 2026 ·{" "}
            <a href={`${API}/docs`} className="hover:text-teal hover:underline underline-offset-[3px] transition-colors duration-[120ms]">API docs</a>
          </p>
        </div>
      </div>
    </aside>
  );
}
