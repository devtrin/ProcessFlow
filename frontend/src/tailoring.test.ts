import { describe, expect, it } from "vitest";
import { addItem, canonical, emptyChanges, resolveGraph, setOverride, updateAdded } from "./tailoring";
import type { ProcessGraph } from "./types";

const base: ProcessGraph = {
  schema_version: "0.2", process_id: "p", title: "t", view: "process",
  source: { kind: "bpmn_xml", filename: "f", sha256: "0".repeat(64), imported_at: "" },
  participants: [], lanes: [], edges: [], issues: [], review_state: "source_explicit", has_source_layout: false, provenance: {},
  nodes: [{
    id: "T", kind: "task", label: "Review", inputs: [
      { id: "i1", name: "Request", description: "", direction: "input", state: "included", origin: "source_explicit", source_refs: [] },
    ], outputs: [], source_refs: [], review_state: "source_explicit", attributes: {},
  }],
};

describe("client-side variant resolution", () => {
  it("excludes, renames, adds and restores without touching the baseline", () => {
    const frozen = JSON.stringify(base);
    let c = setOverride(emptyChanges(), "T", "i1", { state: "excluded" });
    c = setOverride(c, "T", "i1", { name: "Request form" });
    c = addItem(c, "T", "output", "Checklist", "");
    const g = resolveGraph(base, c);
    expect(g.nodes[0].inputs[0]).toMatchObject({ state: "excluded", name: "Request form", edited_from_id: "i1" });
    expect(g.nodes[0].outputs.map((o) => [o.name, o.origin])).toEqual([["Checklist", "user_added"]]);
    expect(JSON.stringify(base)).toBe(frozen);
    const restored = setOverride(c, "T", "i1", null);
    expect(resolveGraph(base, restored).nodes[0].inputs[0]).toMatchObject({ state: "included", name: "Request" });
    const removed = updateAdded(restored, c.added[0].item.id, null);
    expect(canonical(removed)).toBe(canonical(emptyChanges()));
  });

  it("drops no-op overrides so restore leaves no residue", () => {
    const c = setOverride(setOverride(emptyChanges(), "T", "i1", { state: "excluded" }), "T", "i1", { state: null });
    expect(c.overrides).toHaveLength(0);
  });
});
