"use client";
import { useEffect, useRef } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { driver, type Driver } from "driver.js";
import "driver.js/dist/driver.css";
import { STEPS, TOUR_A, TOUR_B, TOUR_Q } from "@/lib/tour";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
async function waitFor(sel: string, ms = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const el = document.querySelector(sel);
    if (el) return el as HTMLElement;
    await sleep(150);
  }
  return null;
}
function setValue(el: HTMLInputElement | HTMLSelectElement, v: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, v);
  el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
}

// Actions the tour performs on the page before a step is shown. Each waits for its effect.
const ACTIONS: Record<string, () => Promise<void>> = {
  "type-question": async () => {
    const input = (await waitFor('[data-tour="search"] input')) as HTMLInputElement | null;
    if (!input) return;
    for (let i = 1; i <= TOUR_Q.length; i++) { setValue(input, TOUR_Q.slice(0, i)); await sleep(18); }
    await sleep(300);
    input.closest("form")?.requestSubmit();
    await waitFor('[data-tour="ask"]', 20000);
    await sleep(600);
  },
  "tick-dia": async () => {
    const box = (await waitFor('[data-tour="facet-acquisition-DIA"]')) as HTMLInputElement | null;
    if (box && !box.checked) { box.click(); await sleep(900); }
  },
  "shortlist-two": async () => {
    for (const acc of [TOUR_A, TOUR_B]) {
      const b = document.querySelector(`[data-tour-acc="${acc}"]`) as HTMLButtonElement | null;
      if (b && b.getAttribute("aria-pressed") !== "true") { b.click(); await sleep(350); }
    }
    await waitFor('[data-tour="compare-bar"]');
  },
  "tick-candidate": async () => {
    const box = (await waitFor(`[data-tour="confirm-${TOUR_B}"]`)) as HTMLInputElement | null;
    await waitFor('[data-tour="why-ready"]', 20000);
    if (box && !box.checked) { box.click(); await sleep(400); }
  },
  "load-example": async () => {
    const b = (await waitFor('[data-tour="example"]')) as HTMLButtonElement | null;
    b?.click();
    await waitFor('[data-tour="groups"]', 30000);
    await sleep(500);
  },
  "set-groups-run": async () => {
    const sel = (await waitFor('[data-tour="group-b"]')) as HTMLSelectElement | null;
    if (sel) {
      const opt = [...sel.options].find((o) => o.textContent?.includes("Set1_Brain_IP_WTKI"));
      if (opt) { setValue(sel, opt.value); await sleep(300); }
    }
    (document.querySelector('[data-tour="run"]') as HTMLButtonElement | null)?.click();
    await waitFor('[data-tour="results"]', 60000);
    await sleep(600);
  },
};

export function Tour() {
  const path = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const drv = useRef<Driver | null>(null);

  useEffect(() => {
    const raw = params.get("tour");
    if (raw === null) return;
    const start = Number(raw) || 0;
    const steps = STEPS.map((s, i) => ({ ...s, i })).filter((s) => s.i >= start && s.route === path);
    if (!steps.length) return;
    let cancelled = false;

    const d = driver({
      showProgress: true,
      progressText: "{{current}} of " + STEPS.length,
      allowClose: true,
      overlayOpacity: 0.45,
      stagePadding: 8,
      popoverClass: "pridemap-tour",
      nextBtnText: "Next →",
      prevBtnText: "← Back",
      doneBtnText: "Finish",
      onDestroyStarted: () => { if (!cancelled) { cancelled = true; d.destroy(); router.replace(path); } },
      onNextClick: async () => {
        const idx = d.getActiveIndex() ?? 0;
        const step = steps[idx];
        if (step.next) { cancelled = true; d.destroy(); router.push(step.next); return; }
        const nxt = steps[idx + 1];
        if (!nxt) { cancelled = true; d.destroy(); router.push("/about"); return; }
        if (nxt.before && ACTIONS[nxt.before]) await ACTIONS[nxt.before]();
        d.moveNext();
      },
      steps: steps.map((s) => ({
        element: s.el,
        popover: { title: s.title, description: s.text, side: s.side ?? "bottom", align: "start", progressText: `${s.i + 1} of ${STEPS.length}` },
      })),
    });
    drv.current = d;

    (async () => {
      const first = steps[0];
      if (first.before && ACTIONS[first.before]) await ACTIONS[first.before]();
      await waitFor(first.el, 20000);
      if (!cancelled) d.drive(0);
    })();

    return () => { cancelled = true; d.destroy(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, params]);

  return null;
}

export function TourButton({ className }: { className?: string }) {
  const router = useRouter();
  return (
    <button type="button" className={className} onClick={() => { try { localStorage.removeItem("pridemap.compare"); localStorage.removeItem("pridemap.confirmed"); } catch {} router.push("/?tour=0"); }}>
      ▶ Take the tour
    </button>
  );
}
