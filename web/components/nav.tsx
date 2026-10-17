"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const links = [
  { href: "/", label: "Ask" },
  { href: "/map", label: "Map" },
  { href: "/compare", label: "Compare" },
  { href: "/analyse", label: "Analyse" },
  { href: "/flow", label: "Workflow" },
  { href: "/eval", label: "Eval" },
  { href: "/about", label: "About" },
];

export function Nav() {
  const path = usePathname();
  return (
    <header className="border-b bg-card sticky top-0 z-20">
      <div className="max-w-6xl mx-auto px-5 h-14 flex items-center gap-6">
        <Link href="/" className="font-heading font-semibold text-xl tracking-tight flex items-center gap-2">
          <span className="inline-block w-2.5 h-2.5 rounded-full bg-teal" aria-hidden />
          PRIDE Map
        </Link>
        <nav className="flex gap-0.5 text-sm font-mono">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={cn(
                "px-2.5 py-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors duration-150",
                (path === l.href || (l.href !== "/" && path.startsWith(l.href))) && "bg-secondary text-foreground"
              )}
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <span className="ml-auto text-xs text-faint hidden md:block font-mono">a front door to 42,000 mass‑spec datasets</span>
      </div>
    </header>
  );
}
