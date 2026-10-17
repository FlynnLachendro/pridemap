"use client";
import { useEffect, useState } from "react";

const KEY = "pridemap.compare";
const EVT = "pridemap-compare-change";

function read(): string[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "[]"); } catch { return []; }
}
function write(v: string[]) {
  try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* private mode etc. */ }
  window.dispatchEvent(new Event(EVT));
}

export function useCompare() {
  const [sel, setSel] = useState<string[]>([]);
  useEffect(() => {
    const sync = () => setSel(read());
    sync();
    window.addEventListener(EVT, sync);
    window.addEventListener("storage", sync);
    return () => { window.removeEventListener(EVT, sync); window.removeEventListener("storage", sync); };
  }, []);
  const toggle = (acc: string) => { const cur = read(); write(cur.includes(acc) ? cur.filter((x) => x !== acc) : [...cur, acc].slice(-6)); };
  const clear = () => write([]);
  return { sel, toggle, clear, has: (acc: string) => sel.includes(acc) };
}
