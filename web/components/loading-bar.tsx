"use client";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

/** Thin teal bar at the top while an internal navigation is in flight. */
export function LoadingBar() {
  const path = usePathname();
  const [on, setOn] = useState(false);
  // hide on route change without a synchronous setState in an effect (derive from the last path seen)
  const [seen, setSeen] = useState(path);
  if (path !== seen) { setSeen(path); if (on) setOn(false); }
  useEffect(() => {
    const start = () => setOn(true);
    const onClick = (e: MouseEvent) => {
      const a = (e.target as HTMLElement | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || a.target === "_blank" || e.metaKey || e.ctrlKey) return;
      const href = a.getAttribute("href") ?? "";
      if (href.startsWith("/") && !href.startsWith("//") && href.split("?")[0] !== window.location.pathname) start();
    };
    window.addEventListener("click", onClick, true);
    window.addEventListener("pridemap-navigating", start);
    return () => { window.removeEventListener("click", onClick, true); window.removeEventListener("pridemap-navigating", start); };
  }, []);
  useEffect(() => { if (!on) return; const t = setTimeout(() => setOn(false), 10000); return () => clearTimeout(t); }, [on]);
  return <div aria-hidden className={`loading-bar ${on ? "on" : ""}`} />;
}
