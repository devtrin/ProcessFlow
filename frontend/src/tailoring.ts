// Client-side mirror of backend/app/domain/tailoring.py, used to preview unsaved
// edits. The backend re-validates and resolves on save; exports use the backend.
import type { DataItem, ItemOverride, ProcessGraph, VariantChanges } from "./types";

export const emptyChanges = (): VariantChanges => ({ overrides: [], added: [] });

export function resolveGraph(baseline: ProcessGraph, changes: VariantChanges): ProcessGraph {
  const g: ProcessGraph = structuredClone(baseline);
  const over = new Map(changes.overrides.map((o) => [`${o.node_id}\u0000${o.item_id}`, o]));
  for (const n of g.nodes) {
    for (const it of [...n.inputs, ...n.outputs]) {
      const o = over.get(`${n.id}\u0000${it.id}`);
      if (!o) continue;
      if (o.state) it.state = o.state;
      if (o.name != null || o.description != null) {
        it.edited_from_id = it.id;
        if (o.name != null) it.name = o.name;
        if (o.description != null) it.description = o.description;
      }
    }
  }
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  for (const a of changes.added) {
    const n = byId.get(a.node_id);
    if (!n) continue;
    (a.item.direction === "input" ? n.inputs : n.outputs).push(structuredClone(a.item));
  }
  return g;
}

function cleanOverride(o: ItemOverride): ItemOverride | null {
  return o.state == null && o.name == null && o.description == null ? null : o;
}

export function setOverride(changes: VariantChanges, nodeId: string, itemId: string, patch: Partial<ItemOverride> | null): VariantChanges {
  const rest = changes.overrides.filter((o) => !(o.node_id === nodeId && o.item_id === itemId));
  if (patch === null) return { ...changes, overrides: rest };
  const prev = changes.overrides.find((o) => o.node_id === nodeId && o.item_id === itemId) ?? { node_id: nodeId, item_id: itemId };
  const merged = cleanOverride({ ...prev, ...patch });
  return { ...changes, overrides: merged ? [...rest, merged] : rest };
}

export function updateAdded(changes: VariantChanges, itemId: string, patch: Partial<DataItem> | null): VariantChanges {
  if (patch === null) return { ...changes, added: changes.added.filter((a) => a.item.id !== itemId) };
  return { ...changes, added: changes.added.map((a) => (a.item.id === itemId ? { ...a, item: { ...a.item, ...patch } } : a)) };
}

export function addItem(changes: VariantChanges, nodeId: string, direction: "input" | "output", name: string, description: string): VariantChanges {
  const id = `user_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
  const item: DataItem = { id, name, description, direction, state: "included", origin: "user_added", source_refs: [] };
  return { ...changes, added: [...changes.added, { node_id: nodeId, item }] };
}

export function canonical(c: VariantChanges): string {
  const ov = [...c.overrides].sort((a, b) => (a.node_id + a.item_id).localeCompare(b.node_id + b.item_id));
  return JSON.stringify({ overrides: ov.map((o) => ({ ...o, state: o.state ?? null, name: o.name ?? null, description: o.description ?? null, reason: o.reason ?? null })), added: c.added });
}

export function nodeChangeCount(c: VariantChanges, nodeId: string): number {
  return c.overrides.filter((o) => o.node_id === nodeId).length + c.added.filter((a) => a.node_id === nodeId).length;
}
