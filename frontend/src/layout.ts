// Auto-layout with ELK (layered, left-to-right), then lanes/pools are drawn as
// horizontal bands: ELK decides the column of each node; each lane band keeps
// ELK's relative vertical order of its own nodes. Source BPMNDI coordinates can
// be used instead when every node has them.
import ELK from "elkjs/lib/elk.bundled.js";
import type { ProcessGraph, ProcessNode } from "./types";

const elk = new ELK();

export interface Box { x: number; y: number; w: number; h: number }
export interface Band { id: string; label: string; x: number; y: number; w: number; h: number }
export interface LayoutResult { boxes: Record<string, Box>; bands: Band[] }

export function nodeSize(n: ProcessNode): { w: number; h: number } {
  switch (n.kind) {
    case "start_event":
    case "end_event":
    case "intermediate_event":
      return { w: 120, h: 86 };
    case "exclusive_gateway":
    case "parallel_gateway":
    case "inclusive_gateway":
    case "event_gateway":
    case "complex_gateway":
      return { w: 140, h: 110 };
    default:
      return { w: 186, h: 92 };
  }
}

const LANE_PAD = 28;
const LANE_LABEL_W = 36;

function bandKey(g: ProcessGraph, n: ProcessNode): string | null {
  if (n.lane_id) return n.lane_id;
  if (g.view === "collaboration" && n.participant_id) return `pool:${n.participant_id}`;
  return null;
}

function bandLabel(g: ProcessGraph, key: string): string {
  if (key.startsWith("pool:")) {
    const p = g.participants.find((x) => x.id === key.slice(5));
    return p ? p.name : key.slice(5);
  }
  const lane = g.lanes.find((l) => l.id === key);
  if (!lane) return key;
  if (g.view === "collaboration" && lane.participant_id) {
    const p = g.participants.find((x) => x.id === lane.participant_id);
    return p ? `${p.name} / ${lane.name}` : lane.name;
  }
  return lane.name;
}

export async function autoLayout(g: ProcessGraph): Promise<LayoutResult> {
  const graph = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "36",
      "elk.layered.spacing.nodeNodeBetweenLayers": "56",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.edgeRouting": "ORTHOGONAL",
    },
    children: g.nodes.map((n) => ({ id: n.id, width: nodeSize(n).w, height: nodeSize(n).h })),
    edges: g.edges
      .filter((e) => e.kind !== "boundary_attachment")
      .map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  };
  const res = await elk.layout(graph);
  const boxes: Record<string, Box> = {};
  for (const c of res.children ?? []) boxes[c.id] = { x: c.x ?? 0, y: c.y ?? 0, w: c.width ?? 0, h: c.height ?? 0 };

  const keyed = g.nodes.map((n) => [n, bandKey(g, n)] as const);
  if (!keyed.some(([, k]) => k)) return { boxes, bands: [] };

  // Band order: declared lanes first (document order), then pools, then unassigned.
  const order: string[] = [];
  for (const l of g.lanes) if (!order.includes(l.id)) order.push(l.id);
  for (const [, k] of keyed) if (k && !order.includes(k)) order.push(k);
  const NONE = "__unassigned";
  if (keyed.some(([, k]) => !k)) order.push(NONE);

  const minX = Math.min(...Object.values(boxes).map((b) => b.x));
  const maxX = Math.max(...Object.values(boxes).map((b) => b.x + b.w));
  const bands: Band[] = [];
  let cursor = 0;
  for (const key of order) {
    const members = keyed.filter(([, k]) => (k ?? NONE) === key).map(([n]) => n.id);
    if (!members.length) continue;
    // Pack the lane's nodes into rows: keep ELK's x (column) and vertical order,
    // but put a node in the first row where it does not overlap horizontally.
    const rows: { spans: [number, number][]; h: number }[] = [];
    const rowOf: Record<string, number> = {};
    for (const id of [...members].sort((a, b) => boxes[a].y - boxes[b].y || boxes[a].x - boxes[b].x)) {
      const b = boxes[id];
      const span: [number, number] = [b.x - 24, b.x + b.w + 24];
      let r = rows.findIndex((row) => row.spans.every(([s, e]) => span[1] <= s || span[0] >= e));
      if (r < 0) { rows.push({ spans: [], h: 0 }); r = rows.length - 1; }
      rows[r].spans.push(span);
      rows[r].h = Math.max(rows[r].h, b.h);
      rowOf[id] = r;
    }
    const rowY: number[] = [];
    rows.reduce((acc, row, i) => { rowY[i] = acc; return acc + row.h + 28; }, 0);
    for (const id of members) {
      const r = rowOf[id];
      const b = boxes[id];
      boxes[id] = { ...b, y: cursor + LANE_PAD + rowY[r] + (rows[r].h - b.h) / 2, x: b.x + LANE_LABEL_W + LANE_PAD };
    }
    const h = rowY[rows.length - 1] + rows[rows.length - 1].h + LANE_PAD * 2;
    bands.push({ id: key, label: key === NONE ? "No lane" : bandLabel(g, key), x: minX, y: cursor, w: maxX - minX + LANE_LABEL_W + LANE_PAD * 2, h });
    cursor += h;
  }
  return { boxes, bands };
}

export function sourceLayout(g: ProcessGraph): LayoutResult | null {
  if (!g.has_source_layout) return null;
  const boxes: Record<string, Box> = {};
  for (const n of g.nodes) {
    if (!n.layout) return null;
    const s = nodeSize(n);
    // BPMN shapes are smaller than these cards; spread positions so cards do not overlap.
    const cx = (n.layout.x + (n.layout.width ?? 0) / 2) * 1.7;
    const cy = (n.layout.y + (n.layout.height ?? 0) / 2) * 1.35;
    boxes[n.id] = { x: cx - s.w / 2, y: cy - s.h / 2, w: s.w, h: s.h };
  }
  return { boxes, bands: [] };
}
