"use client";
import { useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { driver, type Driver } from "driver.js";
import "driver.js/dist/driver.css";
import { STEPS, TOUR_A, TOUR_B, TOUR_COLLECTION, TOUR_Q, TOUR_SAVED } from "@/lib/tour";
import { createCollection, createSaved, deleteCollection, deleteSaved, listCollections, listSaved } from "@/lib/api";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// A status pill the tour shows while it is doing something (typing, clicking, changing page).
function status(msg: string | null) {
  let el = document.getElementById("tour-status");
  if (!msg) { el?.remove(); return; }
  if (!el) { el = document.createElement("div"); el.id = "tour-status"; el.className = "tour-status"; el.setAttribute("role", "status"); document.body.appendChild(el); }
  el.innerHTML = `<span class="spinner"></span><span>${msg}</span>`;
}
const MSG: Record<string, string> = {
  "save-search": "Tour: saving this search…",
  "save-collection": "Tour: saving the shortlist as a collection…",
  "type-question": "Tour: typing Charlotte’s question…",
  "tick-dia": "Tour: narrowing to DIA…",
  "shortlist-two": "Tour: shortlisting two datasets…",
  "tick-candidate": "Tour: Claude is writing the case for each candidate…",
  "load-example": "Tour: loading Odetta’s protein table…",
  "set-groups-run": "Tour: running lyso‑IP vs mock beads…",
  "run-agent": "Tour: asking Claude…",
  "wait-agent": "Tour: Claude is using the tools (searching, reading, comparing)…",
};
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
    for (let i = 3; i <= TOUR_Q.length + 2; i += 3) { setValue(input, TOUR_Q.slice(0, i)); await sleep(40); }
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
  "save-search": async () => {
    // create via the API (robust) so the Saved searches page has a real row; replace a previous tour row of the same name
    try {
      for (const r of await listSaved()) if (r.name === TOUR_SAVED) await deleteSaved(r.id);
      const sp = new URLSearchParams(window.location.search);
      const filters: Record<string, string> = {};
      sp.forEach((v, k) => { if (!["tour", "ask", "page", "size"].includes(k) && v) filters[k] = v; });
      const total = Number(document.querySelector("h2.tabular")?.textContent?.replace(/[^0-9]/g, "") ?? 0);
      await createSaved({ name: TOUR_SAVED, filters, url: `/?${new URLSearchParams(filters).toString()}`, total_at_save: total });
    } catch (e) { console.warn("tour save-search", e); }
    await waitFor('[data-tour="save-search"]');
  },
  "save-collection": async () => {
    try {
      for (const c of await listCollections()) if (c.name === TOUR_COLLECTION) await deleteCollection(c.id);
      const c = await createCollection({ name: TOUR_COLLECTION, accessions: [TOUR_A, TOUR_B], note: "Made by the tour: the two shortlisted mouse liver phospho datasets." });
      sessionStorage.setItem("tour-collection", c.id);
    } catch (e) { console.warn("tour save-collection", e); }
  },
  "run-agent": async () => {
    const b = (await waitFor('[data-tour="agent-run"]')) as HTMLButtonElement | null;
    b?.click();
    await waitFor('[data-tour="agent-log"] li', 60000);   // first tool call has appeared
    await sleep(400);
  },
  "wait-agent": async () => {
    await waitFor('[data-tour="agent-done"]', 120000);
    await sleep(400);
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

// Centre the element when it fits the viewport; otherwise put its top just below the top edge. Scrolls the nearest
// scrollable ancestor (the app's <main>, not the window). `onlyIfOff` re-scrolls only when the element has drifted
// out of view (used while entrance animations settle).
function scroller(el: HTMLElement): HTMLElement | null {
  let n = el.parentElement;
  while (n) { const o = getComputedStyle(n).overflowY; if ((o === "auto" || o === "scroll") && n.scrollHeight > n.clientHeight) return n; n = n.parentElement; }
  return null;
}
function scrollTo(el: HTMLElement | undefined, want?: "center" | "start", onlyIfOff = false) {
  if (!el) return;
  const sc = scroller(el);
  const box = sc ? sc.getBoundingClientRect() : { top: 0, height: window.innerHeight };
  const r = el.getBoundingClientRect();
  const vh = box.height;
  const fits = r.height < vh * 0.72;
  const top = r.top - box.top;                          // element top relative to the scroller's viewport
  if (onlyIfOff) {
    const visible = top >= 0 && (fits ? top + r.height <= vh : top <= vh * 0.3);
    if (visible) return;
  }
  const delta = top - (fits && want !== "start" ? (vh - r.height) / 2 : 24);
  if (sc) sc.scrollTop = Math.max(0, sc.scrollTop + delta);
  else window.scrollTo({ top: Math.max(0, window.scrollY + delta), behavior: "instant" as ScrollBehavior });
}

function label(href: string) {
  const p = href.split("?")[0];
  if (p.startsWith("/collections/")) return "your new collection";
  return p.startsWith("/dataset/") ? "the dataset’s set‑up sheet" : { "/compare": "Compare", "/flow": "Workflow", "/analyse": "Analyse", "/eval": "Eval", "/map": "the Map", "/mcp": "the agent page", "/tour/done": "the finish" }[p] ?? p;
}

export function Tour() {
  const path = usePathname();
  const router = useRouter();
  const drv = useRef<Driver | null>(null);

  // Re-run only when the route changes: same-page URL updates (filters, the question) must not restart the tour.
  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get("tour");
    if (raw === null) return;
    const start = Number(raw) || 0;
    const routeOf = (r: string) => (r === "collection" ? path.startsWith("/collections/") : r === path);
    const steps = STEPS.map((s, i) => ({ ...s, i })).filter((s) => s.i >= start && routeOf(s.route));
    if (!steps.length) return;
    let cancelled = false;

    const d = driver({
      showProgress: true,
      progressText: "{{current}} of " + STEPS.length,
      allowClose: true,
      overlayOpacity: 0.45,
      stagePadding: 8,
      popoverClass: "pridemap-tour",
      smoothScroll: false,
      onHighlightStarted: (el) => {
        const idx = d.getActiveIndex() ?? 0;
        scrollTo(el as HTMLElement | undefined, steps[idx]?.scroll);
      },
      onHighlighted: (el) => {
        const idx = d.getActiveIndex() ?? 0;
        // entrance animations (route-in, .enter) shift boxes for ~300 ms; re-measure until they settle
        for (const t of [60, 160, 320, 520, 800, 1200]) setTimeout(() => { if (cancelled) return; scrollTo(el as HTMLElement | undefined, steps[idx]?.scroll, true); d.refresh(); }, t);
      },
      nextBtnText: "Next →",
      prevBtnText: "← Back",
      doneBtnText: "Finish",
      onDestroyStarted: () => { if (!cancelled) { cancelled = true; d.destroy(); router.replace(path); } },
      onNextClick: async () => {
        const idx = d.getActiveIndex() ?? 0;
        const step = steps[idx];
        if (step.next) {
          let href = step.next;
          if (href === "@collection") { const id = sessionStorage.getItem("tour-collection"); href = id ? `/collections/${id}?tour=${step.i + 1}` : `/flow?q=${encodeURIComponent(TOUR_Q)}&tour=${step.i + 2}`; }
          cancelled = true; d.destroy(); status(`Tour: opening ${label(href)}…`); try { sessionStorage.setItem("tour-status", `Tour: opening ${label(href)}…`); } catch {} window.dispatchEvent(new Event("pridemap-navigating")); router.push(href); return;
        }
        const nxt = steps[idx + 1];
        if (!nxt) { cancelled = true; d.destroy(); status(null); router.push("/tour/done"); return; }
        if (nxt.before && ACTIONS[nxt.before]) {
          document.body.classList.add("tour-busy"); status(MSG[nxt.before] ?? "Tour: working…");
          try { await ACTIONS[nxt.before](); } finally { status(null); document.body.classList.remove("tour-busy"); }
        }
        d.moveNext();
      },
      steps: steps.map((s) => ({
        element: s.el,
        popover: { title: s.title, description: s.text, side: s.side ?? "bottom", align: s.align ?? "start", progressText: `${s.i + 1} of ${STEPS.length}`, doneBtnText: s.next ? "Next →" : "Finish" },
      })),
    });
    drv.current = d;
    const onResize = () => d.isActive() && d.refresh();
    window.addEventListener("resize", onResize);

    (async () => {
      const first = steps[0];
      let carried: string | null = null;
      try { carried = sessionStorage.getItem("tour-status"); sessionStorage.removeItem("tour-status"); } catch {}
      if (first.before && ACTIONS[first.before]) { status(MSG[first.before] ?? carried ?? "Tour: working…"); await ACTIONS[first.before](); }
      else if (carried) status(carried);
      await waitFor(first.el, 20000);
      status(null);
      if (!cancelled) d.drive(0);
    })();

    return () => { cancelled = true; window.removeEventListener("resize", onResize); d.destroy(); status(null); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  return null;
}

export function TourButton({ className }: { className?: string }) {
  // Full page load on purpose: the tour starts from a clean Browse page whatever page you're on.
  return (
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full reload is the point: the tour must start clean from any page
    <button type="button" className={className} onClick={() => { try { localStorage.removeItem("pridemap.compare"); localStorage.removeItem("pridemap.confirmed"); } catch {} window.location.assign("/?tour=0"); }}>
      ▶ Take the tour
    </button>
  );
}
