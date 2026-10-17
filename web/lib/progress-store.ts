"use client";
// What the researcher has done so far in the loop, read by the workflow rail on every page.
import { useEffect, useState } from "react";

const EVT = "pridemap-progress";
const KEYS = { confirmed: "pridemap.confirmed", setup: "pridemap.setup-seen", analysed: "pridemap.analysed", checked: "pridemap.checked", rail: "pridemap.rail" } as const;

export type Progress = { shortlist: number; confirmed: number; setup: number; analysed: boolean; checked: boolean; rail: boolean };

function read(): Progress {
  const j = <T,>(k: string, d: T): T => { try { return JSON.parse(localStorage.getItem(k) ?? "null") ?? d; } catch { return d; } };
  const confirmed = j<Record<string, boolean>>(KEYS.confirmed, {});
  return {
    shortlist: j<string[]>("pridemap.compare", []).length,
    confirmed: Object.values(confirmed).filter(Boolean).length,
    setup: j<string[]>(KEYS.setup, []).length,
    analysed: j<boolean>(KEYS.analysed, false),
    checked: j<boolean>(KEYS.checked, false),
    rail: localStorage.getItem(KEYS.rail) !== "off",
  };
}

export function markSetupSeen(accession: string) {
  try { const cur: string[] = JSON.parse(localStorage.getItem(KEYS.setup) ?? "[]"); if (!cur.includes(accession)) localStorage.setItem(KEYS.setup, JSON.stringify([...cur, accession])); } catch {}
  window.dispatchEvent(new Event(EVT));
}
export function markAnalysed() { try { localStorage.setItem(KEYS.analysed, "true"); } catch {} window.dispatchEvent(new Event(EVT)); }
export function markChecked() { try { localStorage.setItem(KEYS.checked, "true"); } catch {} window.dispatchEvent(new Event(EVT)); }
export function setRail(on: boolean) { try { localStorage.setItem(KEYS.rail, on ? "on" : "off"); } catch {} window.dispatchEvent(new Event(EVT)); }
export function resetProgress() {
  try { [KEYS.confirmed, KEYS.setup, KEYS.analysed, KEYS.checked, "pridemap.compare"].forEach((k) => localStorage.removeItem(k)); } catch {}
  window.dispatchEvent(new Event(EVT)); window.dispatchEvent(new Event("pridemap-compare-change"));
}

export function useProgress(): Progress | null {
  const [p, setP] = useState<Progress | null>(null);
  useEffect(() => {
    const sync = () => setP(read());
    const id = window.setTimeout(sync, 0);
    for (const e of [EVT, "storage", "pridemap-compare-change"]) window.addEventListener(e, sync);
    return () => { window.clearTimeout(id); for (const e of [EVT, "storage", "pridemap-compare-change"]) window.removeEventListener(e, sync); };
  }, []);
  return p;
}
