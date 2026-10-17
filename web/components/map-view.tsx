"use client";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { map, pretty, TAG_LABEL, type MapNode } from "@/lib/api";

const PRESETS: { label: string; by: string }[] = [
  { label: "organism → sample type → enrichment → acquisition", by: "organism,sample_type,enrichment,acquisition" },
  { label: "enrichment → organism → tissue", by: "enrichment,organism,tissue" },
  { label: "acquisition → labelling → instrument", by: "acquisition,labelling,instrument_family" },
  { label: "organism → tissue → disease", by: "organism,tissue,disease" },
];

const PALETTE = ["#8b5e3c", "#4a7c8f", "#5a7d4c", "#6b5b8a", "#b08d57", "#a84032", "#7a5030", "#8b5e6b", "#5a8da0", "#6e6354"]; // Arbor node colours

type Box = { node: MapNode; x: number; y: number; w: number; h: number };

// Bruls et al. squarified treemap
function squarify(items: { node: MapNode; w: number }[], x: number, y: number, w: number, h: number): Box[] {
  const out: Box[] = [];
  const total = items.reduce((s, i) => s + i.w, 0);
  if (!total || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let rows = items.filter((i) => i.w > 0).map((i) => ({ node: i.node, a: i.w * scale }));
  let cx = x, cy = y, cw = w, ch = h;
  const worst = (row: number[], side: number) => {
    const s = row.reduce((a, b) => a + b, 0);
    const mx = Math.max(...row), mn = Math.min(...row);
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };
  while (rows.length) {
    const side = Math.min(cw, ch);
    let row: typeof rows = [rows[0]];
    let i = 1;
    while (i < rows.length) {
      const cand = [...row, rows[i]];
      if (worst(cand.map((r) => r.a), side) <= worst(row.map((r) => r.a), side)) { row = cand; i++; } else break;
    }
    const area = row.reduce((a, r) => a + r.a, 0);
    if (cw >= ch) { // lay the row as a vertical strip on the left
      const sw = area / ch;
      let oy = cy;
      for (const r of row) { const rh = r.a / sw; out.push({ node: r.node, x: cx, y: oy, w: sw, h: rh }); oy += rh; }
      cx += sw; cw -= sw;
    } else { // horizontal strip on top
      const sh = area / cw;
      let ox = cx;
      for (const r of row) { const rw = r.a / sh; out.push({ node: r.node, x: ox, y: cy, w: rw, h: sh }); ox += rw; }
      cy += sh; ch -= sh;
    }
    rows = rows.slice(row.length);
  }
  return out;
}

function groupTail(kids: MapNode[], max = 14): MapNode[] {
  const sorted = [...kids].sort((a, b) => b.value - a.value);
  if (sorted.length <= max) return sorted;
  const head = sorted.slice(0, max - 1);
  const tail = sorted.slice(max - 1);
  return [...head, { name: `other (${tail.length})`, value: tail.reduce((s, k) => s + k.value, 0), children: tail }];
}

function layout(node: MapNode, x: number, y: number, w: number, h: number, depth: number, maxDepth: number, acc: (Box & { depth: number; parentIdx: number })[], parentIdx: number) {
  const kids = groupTail((node.children ?? []).filter((c) => c.value > 0));
  if (!kids.length || depth >= maxDepth) return;
  const pad = depth === 0 ? 2 : 4;
  const top = depth === 0 ? 0 : 16;
  const boxes = squarify(kids.map((k) => ({ node: k, w: k.value })), x + pad, y + pad + top, Math.max(0, w - 2 * pad), Math.max(0, h - 2 * pad - top));
  boxes.forEach((b, i) => {
    const idx = depth === 0 ? i : parentIdx;
    acc.push({ ...b, depth: depth + 1, parentIdx: idx });
    layout(b.node, b.x, b.y, b.w, b.h, depth + 1, maxDepth, acc, idx);
  });
}

export function MapView() {
  const router = useRouter();
  const [by, setBy] = useState(PRESETS[0].by);
  const [tree, setTree] = useState<MapNode | null>(null);
  const [path, setPath] = useState<MapNode[]>([]);
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => { map(by).then((r) => setTree(r.tree)).catch(() => setTree(null)); }, [by]);
  const choosePreset = (b: string) => { setBy(b); setPath([]); };
  const levels = by.split(",");
  const root = path.length ? path[path.length - 1] : tree;
  const W = 1000, H = 560;
  const grouped = useMemo(() => groupTail((root?.children ?? []).filter((c) => c.value > 0)), [root]);
  const boxes = useMemo(() => {
    const acc: (Box & { depth: number; parentIdx: number })[] = [];
    if (root) layout(root, 0, 0, W, H, 0, 2, acc, 0);
    return acc;
  }, [root]);

  const isGroup = (n: MapNode) => n.name.startsWith("other (");
  // level index for the i-th path entry, ignoring "other (n)" grouping nodes which don't consume a level
  const levelAt = (i: number) => levels[path.slice(0, i).filter((x) => !isGroup(x)).length];
  const realDepth = path.filter((x) => !isGroup(x)).length;

  function click(n: MapNode) {
    if (isGroup(n)) { setPath([...path, n]); return; }
    if (n.children && n.children.length && realDepth + 1 < levels.length) { setPath([...path, n]); return; }
    const p = new URLSearchParams();
    [...path, n].filter((x) => !isGroup(x)).forEach((x, i) => { if (x.name !== "unknown") p.set(levels[i], x.name); });
    router.push(`/?${p}`);
  }

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap gap-1.5 text-xs">
        {PRESETS.map((p) => (
          <button key={p.by} type="button" onClick={() => choosePreset(p.by)} className={`rounded-full border px-2.5 py-1 ${p.by === by ? "bg-teal-soft text-teal border-teal font-medium" : "hover:bg-secondary"}`}>{p.label}</button>
        ))}
      </div>
      <div className="flex items-center gap-1 text-sm flex-wrap">
        <button type="button" className="font-semibold hover:underline" onClick={() => setPath([])}>All tagged{tree ? ` (${tree.value})` : ""}</button>
        {path.map((n, i) => (
          <span key={i} className="flex items-center gap-1">
            <span className="text-muted-foreground">›</span>
            <button type="button" className="hover:underline" onClick={() => setPath(path.slice(0, i + 1))}>
              <span className="text-muted-foreground text-xs">{isGroup(n) ? "" : TAG_LABEL[levelAt(i)]} </span>{pretty(n.name)} <span className="tabular text-muted-foreground">({n.value})</span>
            </button>
          </span>
        ))}
      </div>
      {!tree && <p className="text-muted-foreground text-sm">Loading… (needs the API on {process.env.NEXT_PUBLIC_API_URL ?? "localhost:8000"})</p>}
      {tree && (
        <div className="rounded-xl border bg-card overflow-hidden">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto block" role="img" aria-label="Treemap of tagged PRIDE datasets">
            {boxes.map((b, i) => {
              const col = PALETTE[b.parentIdx % PALETTE.length];
              const isLeafLevel = b.depth === 2;
              const label = pretty(b.node.name);
              const show = b.w > 46 && b.h > 18;
              return (
                <g key={i} onClick={() => click(b.depth === 1 ? b.node : grouped[b.parentIdx])} onMouseEnter={() => setHover(`${TAG_LABEL[levels[realDepth + b.depth - 1]] ?? ""}: ${label} · ${b.node.value}`)} onMouseLeave={() => setHover(null)} className="cursor-pointer">
                  <rect x={b.x} y={b.y} width={b.w} height={b.h} fill={col} fillOpacity={isLeafLevel ? 0.28 : 0.12} stroke={col} strokeWidth={isLeafLevel ? 1 : 2} rx={3} />
                  {show && (
                    <text x={b.x + 6} y={b.y + 13} fontSize={isLeafLevel ? 11 : 13} fontWeight={isLeafLevel ? 400 : 600} fill="currentColor" className="pointer-events-none">
                      {label.length * (isLeafLevel ? 6.2 : 7.5) > b.w - 10 ? label.slice(0, Math.max(2, Math.floor((b.w - 14) / (isLeafLevel ? 6.2 : 7.5)))) + "…" : label}
                      {!isLeafLevel && b.w > 120 ? ` · ${b.node.value}` : ""}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
          <div className="px-3 py-2 text-xs text-muted-foreground border-t h-8 tabular">{hover ?? "Hover a block for its count; click to drill in."}</div>
        </div>
      )}
      {grouped.length > 0 && (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {grouped.map((c, i) => (
            <button key={c.name} type="button" onClick={() => click(c)} className="text-left rounded-md border bg-card px-3 py-2 hover:bg-secondary transition-colors duration-[120ms] flex items-center gap-2">
              <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: PALETTE[i % PALETTE.length] }} />
              <span className="font-semibold truncate">{pretty(c.name)}</span>
              <span className="ml-auto tabular text-muted-foreground text-sm">{c.value}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
