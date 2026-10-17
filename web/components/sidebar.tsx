"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search, Map, GitCompareArrows, FlaskConical, Route, ShieldCheck, FolderOpen, Bell, Bot, Info, Plus, Play, ChevronRight, type LucideIcon } from "lucide-react";
import { TourButton } from "@/components/tour";
import { useCollections } from "@/lib/collections-store";
import { useCompare } from "@/lib/compare-store";
import { setRail, useProgress } from "@/lib/progress-store";
import { CollectionDialog } from "@/components/collection-dialog";
import { cn } from "@/lib/utils";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type NavItem = { href: string; label: string; icon: LucideIcon };
export const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  { title: "Find", items: [{ href: "/", label: "Browse", icon: Search }, { href: "/map", label: "Map", icon: Map }] },
  { title: "Work", items: [{ href: "/flow", label: "Workflow", icon: Route }, { href: "/compare", label: "Compare", icon: GitCompareArrows }, { href: "/analyse", label: "Analyse", icon: FlaskConical }] },
  { title: "Yours", items: [{ href: "/collections", label: "Collections", icon: FolderOpen }, { href: "/saved", label: "Saved searches", icon: Bell }] },
  { title: "Trust", items: [{ href: "/eval", label: "Eval", icon: ShieldCheck }, { href: "/mcp", label: "MCP / agents", icon: Bot }, { href: "/about", label: "About", icon: Info }] },
];
export const NAV = NAV_GROUPS.flatMap((g) => g.items);

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

function NavLink({ item, path }: { item: NavItem; path: string }) {
  const active = isActive(path, item.href);
  const Icon = item.icon;
  return (
    <Link href={item.href} aria-current={active ? "page" : undefined} className={cn("nav-link", active && "active")}>
      <Icon size={16} strokeWidth={2} aria-hidden />
      <span>{item.label}</span>
    </Link>
  );
}

/** Left rail: grouped navigation with icons, your collections listed under their entry, the workflow switch, the tour. */
export function Sidebar({ className }: { className?: string }) {
  const path = usePathname();
  const stats = useStats();
  const { rows } = useCollections();
  const { sel } = useCompare();
  const progress = useProgress();
  const [newOpen, setNewOpen] = useState(false);
  const [showCols, setShowCols] = useState(false);
  useEffect(() => { const id = window.setTimeout(() => { try { setShowCols(localStorage.getItem("pridemap.side-collections") === "open"); } catch {} }, 0); return () => window.clearTimeout(id); }, []);
  const toggleCols = () => { setShowCols((v) => { try { localStorage.setItem("pridemap.side-collections", v ? "closed" : "open"); } catch {} return !v; }); };
  return (
    <aside className={cn("flex-col shrink-0 bg-card border-r border-border", className)}>
      <div className="px-4 h-14 flex items-center border-b border-border shrink-0">
        <Wordmark />
      </div>

      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-3 py-3 flex flex-col gap-4 min-w-0" aria-label="Primary">
        {NAV_GROUPS.map((g) => (
          <div key={g.title} className="grid gap-0.5">
            <div className="nav-group">{g.title}</div>
            {g.items.map((item) => (
              <div key={item.href}>
                {item.href === "/collections" ? (
                  <div className="flex items-center">
                    <NavLink item={item} path={path} />
                    <button type="button" className="nav-fold" onClick={toggleCols} aria-expanded={showCols} aria-label={showCols ? "hide your collections" : "show your collections"}>
                      {rows && rows.length > 0 && <span className="tabular font-mono">{rows.length}</span>}
                      <ChevronRight size={14} strokeWidth={2.2} className={showCols ? "rotate-90" : ""} aria-hidden />
                    </button>
                  </div>
                ) : <NavLink item={item} path={path} />}
                {item.href === "/collections" && showCols && (
                  <ul className="nav-sub">
                    {(rows ?? []).slice(0, 6).map((c) => (
                      <li key={c.id}>
                        <Link href={`/collections/${c.id}`} aria-current={path === `/collections/${c.id}` ? "page" : undefined} className={cn("nav-sub-link", path === `/collections/${c.id}` && "active")}>
                          <span className="truncate">{c.name}</span>
                          <span className="tabular font-mono text-faint shrink-0">{c.n}</span>
                        </Link>
                      </li>
                    ))}
                    {rows && rows.length > 6 && <li><Link href="/collections" className="nav-sub-link text-faint">all {rows.length} collections…</Link></li>}
                    <li>
                      <button type="button" className="nav-sub-link w-full text-teal" onClick={() => setNewOpen(true)}>
                        <Plus size={13} strokeWidth={2.4} aria-hidden /><span className="truncate">{sel.length ? `New from shortlist (${sel.length})` : "New collection"}</span>
                      </button>
                    </li>
                  </ul>
                )}
              </div>
            ))}
          </div>
        ))}
      </nav>
      <CollectionDialog accessions={sel} open={newOpen} onOpenChange={setNewOpen} allowEmpty />

      <div className="px-3 py-2 border-t border-border shrink-0 grid gap-2">
        <label className="flex items-center justify-between gap-2 text-[13px] text-muted-foreground px-1 cursor-pointer select-none">
          <span>Workflow rail</span>
          <span className={cn("switch", progress?.rail && "on")} role="switch" aria-checked={!!progress?.rail} tabIndex={0}
            onClick={() => setRail(!progress?.rail)} onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); setRail(!progress?.rail); } }}>
            <span className="switch-knob" />
          </span>
        </label>
        <TourButton className="btn btn-primary w-full"><Play size={14} strokeWidth={2.4} aria-hidden /> Take the tour</TourButton>
      </div>
      <div className="px-4 py-2 border-t border-border text-[12px] text-faint font-mono tabular shrink-0" aria-live="polite">
        {stats ? `${stats.tagged.toLocaleString()} of ${stats.projects.toLocaleString()} tagged` : "a front door to 42,000 datasets"}
      </div>

      <div className="px-4 py-2 border-t border-border text-[12px] text-faint font-body shrink-0">
        <p className="leading-snug">
          MIT · hackathon Oct 2026 ·{" "}
          <a href={`${API}/docs`} className="hover:text-teal hover:underline underline-offset-[3px] transition-colors duration-[120ms]">API docs</a>
        </p>
      </div>
    </aside>
  );
}
