"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const links = [
  { href: "/", label: "Ask" },
  { href: "/map", label: "Map" },
  { href: "/compare", label: "Compare" },
  { href: "/about", label: "About" },
];

export function Nav() {
  const path = usePathname();
  return (
    <header className="border-b bg-card/70 backdrop-blur sticky top-0 z-20">
      <div className="max-w-6xl mx-auto px-5 h-14 flex items-center gap-6">
        <Link href="/" className="font-heading font-extrabold text-lg tracking-tight flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-sm bg-teal" aria-hidden />
          PRIDE Map
        </Link>
        <nav className="flex gap-1 text-sm">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={cn(
                "px-3 py-1.5 rounded-full font-bold text-muted-foreground hover:text-foreground",
                (path === l.href || (l.href !== "/" && path.startsWith(l.href))) && "bg-teal-soft text-teal"
              )}
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <span className="ml-auto text-xs text-muted-foreground hidden sm:block">a better front door to 42,000 mass‑spec datasets</span>
      </div>
    </header>
  );
}
