// An empty state that teaches: what this place is for, the one action that fills it, and a small drawn motif.
import Link from "next/link";

const ART = {
  stack: (
    <svg width="72" height="56" viewBox="0 0 72 56" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden>
      <rect x="14" y="20" width="44" height="30" rx="4" />
      <rect x="10" y="12" width="44" height="30" rx="4" className="fill-[var(--surface)]" />
      <rect x="6" y="4" width="44" height="30" rx="4" className="fill-[var(--surface)]" />
      <path d="M14 14h20M14 20h28M14 26h16" strokeLinecap="round" />
      <circle cx="58" cy="44" r="9" className="fill-[var(--teal-soft)]" />
      <path d="M58 40v8M54 44h8" strokeLinecap="round" />
    </svg>
  ),
  bell: (
    <svg width="72" height="56" viewBox="0 0 72 56" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden>
      <path d="M24 38h24l-3-5V22a9 9 0 0 0-18 0v11l-3 5Z" className="fill-[var(--surface)]" />
      <path d="M32 42a4 4 0 0 0 8 0" strokeLinecap="round" />
      <path d="M12 16c0-4 2-8 6-10M60 16c0-4-2-8-6-10" strokeLinecap="round" />
      <circle cx="54" cy="18" r="6" className="fill-[var(--peak-soft)]" />
    </svg>
  ),
  list: (
    <svg width="72" height="56" viewBox="0 0 72 56" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden>
      <rect x="8" y="8" width="56" height="12" rx="3" className="fill-[var(--surface)]" />
      <rect x="8" y="24" width="56" height="12" rx="3" className="fill-[var(--surface)]" />
      <rect x="8" y="40" width="56" height="12" rx="3" className="fill-[var(--surface)]" />
      <path d="M14 14h6M14 30h6M14 46h6" strokeLinecap="round" />
      <circle cx="56" cy="14" r="3" className="fill-[var(--teal-soft)]" />
    </svg>
  ),
};

export function EmptyState({ art = "stack", title, text, action, href }: { art?: keyof typeof ART; title: string; text: string; action?: string; href?: string }) {
  return (
    <div className="empty">
      <span className="text-teal">{ART[art]}</span>
      <div className="grid gap-1">
        <h3 className="text-lg">{title}</h3>
        <p className="text-sm text-muted-foreground max-w-prose">{text}</p>
      </div>
      {action && href && <Link href={href} className="btn btn-primary w-fit">{action}</Link>}
    </div>
  );
}
