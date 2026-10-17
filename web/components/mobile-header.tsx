"use client";
import { useEffect } from "react";
import { Wordmark } from "@/components/sidebar";

/** Arbor's MobileHeader: h-12, surface, 1px bottom border, menu · wordmark · theme. */
export function MobileHeader({ onOpenDrawer, className = "" }: { onOpenDrawer: () => void; className?: string }) {
  return (
    <header className={`h-12 bg-card border-b border-border flex items-center justify-between px-3 shrink-0 ${className}`}>
      <button
        type="button"
        onClick={onOpenDrawer}
        className="w-10 h-10 flex items-center justify-center rounded-md text-muted-foreground hover:text-teal transition-colors duration-[120ms] shrink-0"
        aria-label="Open menu"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d="M3 6h18M3 12h18M3 18h18" />
        </svg>
      </button>
      <Wordmark />
    </header>
  );
}

/** Arbor's MobileSidebarDrawer: full-width slide-in with a scrim; locks body scroll. */
export function MobileDrawer({ open, onClose, children }: { open: boolean; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    if (!open) return;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  if (!open) return null;
  return (
    <>
      <div className="mobile-drawer-backdrop" onClick={onClose} aria-hidden />
      <div className="mobile-drawer open bg-card" role="dialog" aria-modal="true" aria-label="Menu">
        <button
          type="button"
          onClick={onClose}
          className="absolute top-3 right-3 z-10 w-8 h-8 flex items-center justify-center rounded-full bg-card border border-border-light text-faint hover:text-foreground transition-colors duration-[120ms]"
          aria-label="Close menu"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
            <path d="M3 3l8 8M11 3l-8 8" />
          </svg>
        </button>
        <div className="h-full overflow-y-auto flex flex-col">{children}</div>
      </div>
    </>
  );
}
