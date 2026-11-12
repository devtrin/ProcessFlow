import { memo, useEffect, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { autoLayout, sourceLayout, type LayoutResult } from "../layout";
import type { ProcessGraph, ProcessNode, ReviewState } from "../types";

export type LayoutMode = "auto" | "source";

interface CardData extends Record<string, unknown> {
  node: ProcessNode;
  review: ReviewState;
  laneName?: string;
  errors: number;
  warnings: number;
  changed: number;
  isDraft: boolean;
}
interface BandData extends Record<string, unknown> { label: string }

const KIND_LABEL: Record<string, string> = {
  start_event: "Start", end_event: "End", intermediate_event: "Event", task: "Task", subprocess: "Sub-process",
  exclusive_gateway: "Decision (XOR)", parallel_gateway: "Parallel (AND)", inclusive_gateway: "Inclusive (OR)",
  event_gateway: "Event gateway", complex_gateway: "Complex gateway", unknown: "Unsupported",
};

const GATEWAY_SYMBOL: Record<string, string> = {
  exclusive_gateway: "×", parallel_gateway: "+", inclusive_gateway: "○", event_gateway: "⬠", complex_gateway: "✳",
};

export function reviewLabel(r: ReviewState): string {
  return { source_explicit: "From source", unverified: "Unverified", needs_review: "Needs review", verified: "Verified", needs_clarification: "Needs clarification" }[r];
}

function typeLine(n: ProcessNode): string {
  const t = n.attributes["bpmn_type"] as string | undefined;
  const pretty = t ? t.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase()) : KIND_LABEL[n.kind];
  const defs = n.attributes["event_definitions"] as string[] | undefined;
  const trig = defs?.length ? ` · ${defs.map((d) => d.replace("EventDefinition", "")).join(", ")}` : "";
  return pretty + trig;
}

function Badges({ d }: { d: CardData }) {
  return (
    <span className="node-badges">
      {d.errors > 0 && <span className="nb nb-error" title={`${d.errors} error(s)`}>!{d.errors}</span>}
      {d.warnings > 0 && <span className="nb nb-warn" title={`${d.warnings} warning(s)`}>{d.warnings}</span>}
      {d.isDraft && <span className={`nb nb-review nb-${d.review}`}>{reviewLabel(d.review)}</span>}
      {d.changed > 0 && <span className="nb nb-changed" title="Tailored in this variant">edited</span>}
    </span>
  );
}

const CardNode = memo(function CardNode({ data, selected }: NodeProps<Node<CardData>>) {
  const n = data.node;
  const inc = n.inputs.filter((i) => i.state === "included").length;
  const outc = n.outputs.filter((i) => i.state === "included").length;
  const excluded = [...n.inputs, ...n.outputs].filter((i) => i.state === "excluded").length;
  const who = data.laneName ?? (n.actor && n.actor !== "not_stated" ? n.actor : undefined);
  return (
    <div className={`card kind-${n.kind} ${selected ? "is-selected" : ""}`} data-testid={`node-${n.id}`}>
      <Handle type="target" position={Position.Left} />
      <div className="card-type">{typeLine(n)}{who ? ` · ${who}` : ""}</div>
      <div className="card-label">{n.label || <em className="muted">(unnamed)</em>}</div>
      <div className="card-foot">
        <span className="chip">{"↓"} {inc} in</span>
        <span className="chip">{"↑"} {outc} out</span>
        {excluded > 0 && <span className="chip chip-excl">{excluded} tailored out</span>}
        {n.kind === "subprocess" && <span className="chip">[+] collapsed</span>}
        <Badges d={data} />
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

const RoundNode = memo(function RoundNode({ data, selected }: NodeProps<Node<CardData>>) {
  const n = data.node;
  const isGateway = n.kind.endsWith("gateway");
  return (
    <div className={`round kind-${n.kind} ${selected ? "is-selected" : ""}`} data-testid={`node-${n.id}`}>
      <Handle type="target" position={Position.Left} />
      {isGateway ? (
        <div className="diamond"><span>{GATEWAY_SYMBOL[n.kind]}</span></div>
      ) : (
        <div className="circle">{(n.attributes["event_definitions"] as string[] | undefined)?.length ? <span className="ev">{"◉"}</span> : null}</div>
      )}
      <div className="round-label">{n.label || KIND_LABEL[n.kind]}</div>
      <Badges d={data} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

function BandNode({ data }: NodeProps<Node<BandData>>) {
  return (
    <div className="band">
      <div className="band-label">{data.label}</div>
    </div>
  );
}

const nodeTypes = { card: CardNode, round: RoundNode, band: BandNode };

function FitOnChange({ token }: { token: string }) {
  const rf = useReactFlow();
  useEffect(() => {
    const t = setTimeout(() => rf.fitView({ padding: 0.12, duration: 0 }), 30);
    return () => clearTimeout(t);
  }, [token, rf]);
  return null;
}

export function FocusNode({ nodeId, nonce }: { nodeId: string | null; nonce: number }) {
  const rf = useReactFlow();
  useEffect(() => {
    if (!nodeId || !nonce) return;
    const n = rf.getNode(nodeId);
    if (n) rf.setCenter(n.position.x + (n.measured?.width ?? 200) / 2, n.position.y + (n.measured?.height ?? 80) / 2, { zoom: Math.max(rf.getZoom(), 0.9), duration: 300 });
  }, [nodeId, nonce, rf]);
  return null;
}

export interface FlowCanvasProps {
  graph: ProcessGraph;
  structureKey: string;
  layoutMode: LayoutMode;
  reviews: Record<string, ReviewState>;
  changedNodes: Record<string, number>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  focusNonce: number;
}

export function FlowCanvas({ graph, structureKey, layoutMode, reviews, changedNodes, selectedId, onSelect, focusNonce }: FlowCanvasProps) {
  const [layout, setLayout] = useState<LayoutResult | null>(null);
  const [layoutError, setLayoutError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLayoutError(null);
    const src = layoutMode === "source" ? sourceLayout(graph) : null;
    if (src) {
      setLayout(src);
      return;
    }
    autoLayout(graph)
      .then((r) => alive && setLayout(r))
      .catch((e) => alive && setLayoutError(String(e)));
    return () => {
      alive = false;
    };
    // Layout depends on structure only, not on data-item edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structureKey, layoutMode]);

  const issueCounts = useMemo(() => {
    const m: Record<string, { e: number; w: number }> = {};
    for (const i of graph.issues) for (const id of i.element_ids) {
      m[id] ??= { e: 0, w: 0 };
      if (i.severity === "error") m[id].e++;
      if (i.severity === "warning") m[id].w++;
    }
    return m;
  }, [graph.issues]);

  const laneNames = useMemo(() => Object.fromEntries(graph.lanes.map((l) => [l.id, l.name])), [graph.lanes]);
  const isDraft = graph.view === "pdf_draft";

  const nodes: Node[] = useMemo(() => {
    if (!layout) return [];
    const bands: Node[] = layout.bands.map((b) => ({
      id: `band:${b.id}`, type: "band", position: { x: b.x, y: b.y }, data: { label: b.label },
      style: { width: b.w, height: b.h }, selectable: false, draggable: false, focusable: false, zIndex: -1,
    }));
    const cards: Node[] = graph.nodes.map((n) => {
      const box = layout.boxes[n.id] ?? { x: 0, y: 0, w: 200, h: 80 };
      const round = n.kind.endsWith("event") || n.kind.endsWith("gateway");
      const data: CardData = {
        node: n, review: reviews[n.id] ?? n.review_state, laneName: n.lane_id ? laneNames[n.lane_id] : undefined,
        errors: issueCounts[n.id]?.e ?? 0, warnings: issueCounts[n.id]?.w ?? 0, changed: changedNodes[n.id] ?? 0, isDraft,
      };
      return {
        id: n.id, type: round ? "round" : "card", position: { x: box.x, y: box.y }, data,
        style: { width: box.w, height: box.h }, selected: n.id === selectedId,
        ariaLabel: `${KIND_LABEL[n.kind]}: ${n.label || n.id}`,
      };
    });
    return [...bands, ...cards];
  }, [layout, graph.nodes, reviews, laneNames, issueCounts, changedNodes, selectedId, isDraft]);

  const edges: Edge[] = useMemo(
    () =>
      graph.edges.map((e) => {
        const label = e.label || (e.condition_expression ? `[${e.condition_expression.slice(0, 40)}]` : "") || (e.is_default ? "default" : "");
        const cls = `edge-${e.kind}`;
        const base: Edge = {
          id: e.id, source: e.source, target: e.target, type: "smoothstep", className: cls,
          label: label ? (e.is_default && e.label ? `${label} (default)` : label) : undefined,
          labelBgPadding: [6, 3], labelBgBorderRadius: 4,
          markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
          selectable: false, focusable: false,
        };
        if (e.kind === "message") return { ...base, markerEnd: { type: MarkerType.Arrow, width: 18, height: 18 } };
        if (e.kind === "boundary_attachment") return { ...base, markerEnd: undefined, type: "straight" };
        return base;
      }),
    [graph.edges],
  );

  if (layoutError) return <div className="canvas-error" role="alert">Layout failed: {layoutError}</div>;

  // Keyboard selection: Tab moves focus between steps; Enter or Space selects the focused step.
  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const el = (e.target as HTMLElement).closest(".react-flow__node") as HTMLElement | null;
    const id = el?.dataset.id;
    if (id && !id.startsWith("band:")) {
      e.preventDefault();
      onSelect(id);
    }
  };

  return (
    <div className="flow-host" onKeyDown={onKeyDown}>
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      nodesDraggable={false}
      nodesConnectable={false}
      edgesFocusable={false}
      elementsSelectable={false}
      nodesFocusable
      onNodeClick={(_, n) => n.type !== "band" && onSelect(n.id)}
      onPaneClick={() => onSelect(null)}
      minZoom={0.15}
      maxZoom={2}
      proOptions={{ hideAttribution: false }}
      fitView
    >
      <Background gap={20} size={1} />
      <Controls showInteractive={false} position="bottom-left" />
      <FitOnChange token={`${structureKey}|${layoutMode}|${layout ? 1 : 0}`} />
      <FocusNode nodeId={selectedId} nonce={focusNonce} />
    </ReactFlow>
    </div>
  );
}
