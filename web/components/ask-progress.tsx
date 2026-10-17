"use client";
// What the search is doing while a question runs: the stages advance on a timer (one request, ~3 s) and all tick when it lands.
import { useEffect, useState } from "react";
import { Check } from "lucide-react";

const STAGES = ["Understanding the question", "Expanding expert terms", "Searching every dataset's text and tags", "Ranking by fit and evidence"];

export function AskProgress({ active }: { active: boolean }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setI((x) => Math.min(x + 1, STAGES.length - 1)), 800);
    return () => { window.clearInterval(t); window.setTimeout(() => setI(0), 0); };
  }, [active]);
  if (!active) return null;
  return (
    <ol className="ask-progress" aria-live="polite">
      {STAGES.map((s, k) => (
        <li key={s} className={k < i ? "done" : k === i ? "now" : ""}>
          <span className="dot" aria-hidden>{k < i ? <Check size={10} strokeWidth={3} /> : k === i ? <span className="spinner" /> : null}</span>
          {s}
        </li>
      ))}
    </ol>
  );
}
