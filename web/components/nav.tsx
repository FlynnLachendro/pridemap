"use client";
import { useState } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/sidebar";
import { MobileDrawer, MobileHeader } from "@/components/mobile-header";

/**
 * The app shell, laid out like Arbor's App.tsx: on desktop a fixed-height
 * column with the sidebar (w-52, border-r) beside a scrolling content area;
 * below 768px a 48px top header with a menu button that opens the same
 * sidebar in a full-width drawer.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = usePathname();
  // Close the drawer when the route changes (state adjusted during render, not in an effect).
  const [seenPath, setSeenPath] = useState(path);
  if (path !== seenPath) { setSeenPath(path); setOpen(false); }

  return (
    <div className="h-dvh flex flex-col overflow-hidden">
      <MobileHeader className="md:hidden" onOpenDrawer={() => setOpen(true)} />
      <div className="md:hidden">
        <MobileDrawer open={open} onClose={() => setOpen(false)}>
          <Sidebar className="flex w-full h-full border-r-0" />
        </MobileDrawer>
      </div>

      <div className="flex flex-1 overflow-hidden">
        <Sidebar className="hidden md:flex w-52" />
        <main className="flex-1 min-w-0 overflow-y-auto bg-background">
          <div className="w-full max-w-6xl mx-auto px-5 py-8 pb-24">{children}</div>
        </main>
      </div>
    </div>
  );
}
