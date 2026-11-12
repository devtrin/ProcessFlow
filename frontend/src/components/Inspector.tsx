import { useEffect, useMemo, useState } from "react";
import type { DataItem, Extraction, Issue, ProcessGraph, ProcessNode, ReviewDecision, ReviewState, SourceRef, Variant, VariantChanges } from "../types";
import { reviewLabel } from "./FlowCanvas";

type Tab = "details" | "evidence" | "changes";

const ORIGIN_LABEL: Record<string, string> = { source_explicit: "From source", ai_inferred: "AI-inferred", user_added: "User-added" };

export interface InspectorProps {
  graph: ProcessGraph; // resolved (variant) or baseline
  baseline: ProcessGraph;
  node: ProcessNode | null;
  mode: "baseline" | "variant";
  variant: Variant | null;
  draft: VariantChanges;
  reviews: Record<string, ReviewDecision>;
  extraction: Extraction | null;
  busy: boolean;
  onExclude: (node: ProcessNode, item: DataItem, reason: string) => void;
  onRestore: (node: ProcessNode, item: DataItem) => void;
  onEdit: (node: ProcessNode, item: DataItem, name: string, description: string) => void;
  onAdd: (node: ProcessNode, direction: "input" | "output", name: string, description: string) => void;
  onRemoveAdded: (node: ProcessNode, item: DataItem) => void;
  onReview: (node: ProcessNode, state: ReviewState, note: string) => void;
  onRequestVariant: () => void;
}

function Grounding({ r }: { r: SourceRef }) {
  if (!r.grounding) return null;
  const text = { exact: "Quote found on page", normalised: "Quote found (whitespace/case differ)", wrong_page: "Quote is on a different page", not_found: "Quote NOT found in PDF" }[r.grounding];
  return <span className={`badge g-${r.grounding}`}>{text}</span>;
}

export function RefView({ r, extraction }: { r: SourceRef; extraction: Extraction | null }) {
  const [open, setOpen] = useState(false);
  if (r.type === "bpmn_xml") {
    return (
      <div className="ref">
        <div className="ref-head">BPMN XML · <code>{r.element_type}</code> id <code>{r.element_id ?? "(none)"}</code>{r.line ? ` · line ${r.line}` : ""}</div>
        {r.xpath && <div className="muted small"><code>{r.xpath}</code></div>}
      </div>
    );
  }
  const pageBlocks = extraction?.blocks.filter((b) => b.page === r.page) ?? [];
  return (
    <div className="ref">
      <div className="ref-head">PDF · page {r.page}{r.block_id ? ` · block ${r.block_id}` : ""} <Grounding r={r} /></div>
      {r.quote && <blockquote className={r.grounding === "not_found" ? "quote bad" : "quote"}>{r.quote}</blockquote>}
      {extraction && pageBlocks.length > 0 && (
        <>
          <button className="link" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide" : "Show"} page {r.page} text</button>
          {open && (
            <div className="page-text" aria-label={`Extracted text of page ${r.page}`}>
              {pageBlocks.map((b) => (
                <p key={b.id} className={b.id === r.block_id ? "hit" : b.role === "heading" ? "heading" : ""}>{b.text}</p>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ItemRow({ node, item, baseItem, editable, onExclude, onRestore, onEdit, onRemoveAdded }: {
  node: ProcessNode; item: DataItem; baseItem?: DataItem; editable: boolean;
  onExclude: InspectorProps["onExclude"]; onRestore: InspectorProps["onRestore"]; onEdit: InspectorProps["onEdit"]; onRemoveAdded: InspectorProps["onRemoveAdded"];
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(item.name);
  const [desc, setDesc] = useState(item.description);
  useEffect(() => { setName(item.name); setDesc(item.description); }, [item.name, item.description]);
  const excluded = item.state === "excluded";
  const modified = !!item.edited_from_id || (item.origin === "user_added") || excluded;
  const canRestore = item.origin !== "user_added" ? (excluded || !!item.edited_from_id) : excluded;
  return (
    <li className={`item ${excluded ? "excluded" : ""}`} data-testid={`item-${item.name}`}>
      {!editing ? (
        <>
          <div className="item-main">
            <span className="item-name">{item.name}</span>
            <span className={`badge o-${item.origin}`}>{ORIGIN_LABEL[item.origin]}</span>
            {excluded && <span className="badge b-excl">Tailored out</span>}
            {item.edited_from_id && baseItem && baseItem.name !== item.name && <span className="badge b-edit" title={`Baseline name: ${baseItem.name}`}>Renamed</span>}
          </div>
          {item.description && <div className="muted small">{item.description}</div>}
          {item.edited_from_id && baseItem && baseItem.name !== item.name && <div className="muted small">Baseline: “{baseItem.name}”</div>}
          {editable && (
            <div className="item-actions">
              <button onClick={() => setEditing(true)} aria-label={`Edit ${item.name}`}>Edit</button>
              {!excluded && <button onClick={() => onExclude(node, item, "")} aria-label={`Tailor out ${item.name}`}>Tailor out</button>}
              {canRestore && <button onClick={() => onRestore(node, item)} aria-label={`Restore ${item.name}`}>Restore</button>}
              {item.origin === "user_added" && <button className="danger" onClick={() => onRemoveAdded(node, item)} aria-label={`Remove ${item.name}`}>Remove</button>}
            </div>
          )}
          {!editable && modified && <span className="sr-only">modified in variant</span>}
        </>
      ) : (
        <form className="item-form" onSubmit={(e) => { e.preventDefault(); if (name.trim()) { onEdit(node, item, name.trim(), desc.trim()); setEditing(false); } }}>
          <label>Name<input value={name} onChange={(e) => setName(e.target.value)} autoFocus required maxLength={200} /></label>
          <label>Description<input value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={500} /></label>
          <div className="item-actions">
            <button type="submit" className="primary" disabled={!name.trim()}>Apply</button>
            <button type="button" onClick={() => { setEditing(false); setName(item.name); setDesc(item.description); }}>Cancel</button>
          </div>
        </form>
      )}
    </li>
  );
}

function AddForm({ direction, onAdd, onCancel }: { direction: "input" | "output"; onAdd: (n: string, d: string) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  return (
    <form className="item-form" onSubmit={(e) => { e.preventDefault(); if (name.trim()) onAdd(name.trim(), desc.trim()); }} aria-label={`Add ${direction}`}>
      <label>New {direction} name<input value={name} onChange={(e) => setName(e.target.value)} autoFocus required maxLength={200} /></label>
      <label>Description (optional)<input value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={500} /></label>
      <div className="item-actions">
        <button type="submit" className="primary" disabled={!name.trim()}>Add {direction}</button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function ItemList({ title, direction, node, baseNode, editable, props }: {
  title: string; direction: "input" | "output"; node: ProcessNode; baseNode?: ProcessNode; editable: boolean; props: InspectorProps;
}) {
  const [adding, setAdding] = useState(false);
  useEffect(() => setAdding(false), [node.id]);
  const items = direction === "input" ? node.inputs : node.outputs;
  const baseItems = new Map((direction === "input" ? baseNode?.inputs : baseNode?.outputs)?.map((i) => [i.id, i]) ?? []);
  return (
    <section className="items" aria-label={title}>
      <div className="items-head">
        <h4>{title} <span className="muted">({items.filter((i) => i.state === "included").length} included)</span></h4>
        {editable && !adding && <button className="link" onClick={() => setAdding(true)}>+ Add {direction}</button>}
      </div>
      {items.length === 0 && !adding && (
        <p className="muted small empty">No {direction}s {props.graph.source.kind === "bpmn_xml" ? "are declared in the XML (no data associations)" : "are stated in the source"}.{editable ? ` Use “Add ${direction}”.` : ""}</p>
      )}
      <ul>
        {items.map((it) => (
          <ItemRow key={it.id} node={node} item={it} baseItem={baseItems.get(it.id)} editable={editable}
            onExclude={props.onExclude} onRestore={props.onRestore} onEdit={props.onEdit} onRemoveAdded={props.onRemoveAdded} />
        ))}
      </ul>
      {adding && <AddForm direction={direction} onAdd={(n, d) => { props.onAdd(node, direction, n, d); setAdding(false); }} onCancel={() => setAdding(false)} />}
    </section>
  );
}

function ReviewBox({ node, decision, onReview, busy }: { node: ProcessNode; decision?: ReviewDecision; onReview: InspectorProps["onReview"]; busy: boolean }) {
  const [note, setNote] = useState(decision?.note ?? "");
  useEffect(() => setNote(decision?.note ?? ""), [node.id, decision?.note]);
  const state = decision?.state ?? node.review_state;
  return (
    <section className="review-box" aria-label="Interpretation review">
      <div className="row-between">
        <h4>Interpretation status</h4>
        <span className={`badge r-${state}`}>{reviewLabel(state)}</span>
      </div>
      <p className="muted small">AI-inferred. Compare with the source evidence before relying on it. A review decision is recorded separately; it is not a variant save and does not approve the process.</p>
      <label className="small">Reviewer note<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} placeholder="Optional" /></label>
      <div className="item-actions">
        <button className="ok" disabled={busy} onClick={() => onReview(node, "verified", note)}>Mark verified</button>
        <button disabled={busy} onClick={() => onReview(node, "needs_clarification", note)}>Needs clarification</button>
        {state !== "needs_review" && <button disabled={busy} onClick={() => onReview(node, "needs_review", note)}>Reset</button>}
      </div>
      {decision && <p className="muted small">Last decision {new Date(decision.at).toLocaleString("en-GB")}</p>}
    </section>
  );
}

export function Inspector(props: InspectorProps) {
  const { node, graph, baseline, mode, variant, draft, extraction } = props;
  const [tab, setTab] = useState<Tab>("details");
  const baseNode = useMemo(() => baseline.nodes.find((n) => n.id === node?.id), [baseline, node?.id]);
  const nodeIssues: Issue[] = useMemo(() => (node ? graph.issues.filter((i) => i.element_ids.includes(node.id)) : []), [graph.issues, node]);

  if (!node) {
    return (
      <aside className="inspector" aria-label="Step inspector">
        <div className="insp-empty">
          <h3>Step inspector</h3>
          <p className="muted">Select a step on the canvas (click, or Tab to it and press Enter) or use <strong>Find step</strong>.</p>
          <p className="muted small">Inputs and outputs can be added, renamed, tailored out and restored in a variant. The imported baseline is read-only.</p>
        </div>
      </aside>
    );
  }
  const editable = mode === "variant" && !!variant && !props.busy;
  const isDraft = graph.view === "pdf_draft";
  const lane = graph.lanes.find((l) => l.id === node.lane_id);
  const nodeOverrides = draft.overrides.filter((o) => o.node_id === node.id);
  const nodeAdded = draft.added.filter((a) => a.node_id === node.id);
  const itemRefs = [...node.inputs, ...node.outputs].filter((i) => i.source_refs.length);

  return (
    <aside className="inspector" aria-label="Step inspector">
      <header className="insp-head">
        <div className="eyebrow">Step inspector</div>
        <span className={`badge ${mode === "variant" ? "b-variant" : "b-baseline"}`}>{mode === "variant" ? `Editing variant: ${variant?.name ?? "—"}` : "Baseline (read-only)"}</span>
        <h3 data-testid="inspector-title">{node.label || node.id}</h3>
        <div className="muted small">
          {(node.attributes["bpmn_type"] as string) ?? node.kind}
          {lane ? ` · ${lane.name}` : ""}
          {isDraft ? ` · actor: ${node.actor === "not_stated" ? "not stated in source" : node.actor}` : ""}
          {node.source_id ? ` · source id ${node.source_id}` : ""}
        </div>
      </header>
      <div className="tabs" role="tablist">
        {(["details", "evidence", "changes"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t === "details" ? "Details" : t === "evidence" ? "Source evidence" : `Changes${nodeOverrides.length + nodeAdded.length ? ` (${nodeOverrides.length + nodeAdded.length})` : ""}`}
          </button>
        ))}
      </div>
      <div className="insp-body">
        {tab === "details" && (
          <>
            {isDraft ? <ReviewBox node={node} decision={props.reviews[node.id]} onReview={props.onReview} busy={props.busy} /> : (
              <div className="row-between"><span className="small">Review status</span><span className="badge r-source_explicit">Deterministic from XML</span></div>
            )}
            {nodeIssues.length > 0 && (
              <ul className="issue-mini">
                {nodeIssues.map((i, k) => <li key={k} className={`sev-${i.severity}`}><strong>{i.code}</strong> {i.message}</li>)}
              </ul>
            )}
            {mode === "baseline" && (
              <div className="hint">
                Baseline is read-only. <button className="link" onClick={props.onRequestVariant}>Switch to a variant</button> to tailor inputs and outputs.
              </div>
            )}
            <ItemList title="Inputs" direction="input" node={node} baseNode={baseNode} editable={editable} props={props} />
            <ItemList title="Outputs" direction="output" node={node} baseNode={baseNode} editable={editable} props={props} />
            {typeof node.attributes["documentation"] === "string" && (
              <section><h4>Documentation (from XML)</h4><p className="small">{node.attributes["documentation"] as string}</p></section>
            )}
            <details className="attrs">
              <summary>Source attributes</summary>
              <dl>
                {Object.entries(node.attributes).filter(([k]) => k !== "documentation").map(([k, v]) => (
                  <div key={k}><dt>{k}</dt><dd>{typeof v === "string" ? v : JSON.stringify(v)}</dd></div>
                ))}
              </dl>
            </details>
          </>
        )}
        {tab === "evidence" && (
          <>
            <h4>Step</h4>
            {node.source_refs.length === 0 ? <p className="sev-warning small">No source reference: treat as unverified.</p> : node.source_refs.map((r, i) => <RefView key={i} r={r} extraction={extraction} />)}
            {itemRefs.length > 0 && <h4>Inputs and outputs</h4>}
            {itemRefs.map((it) => (
              <div key={it.id} className="item-ev">
                <div className="small"><strong>{it.name}</strong> ({it.direction})</div>
                {it.source_refs.map((r, i) => <RefView key={i} r={r} extraction={extraction} />)}
              </div>
            ))}
            {nodeIssues.flatMap((i) => i.source_refs).length > 0 && <h4>Evidence cited by issues</h4>}
            {nodeIssues.flatMap((i, k) => i.source_refs.map((r, j) => <RefView key={`${k}-${j}`} r={r} extraction={extraction} />))}
          </>
        )}
        {tab === "changes" && (
          <>
            {mode === "baseline" || !variant ? <p className="muted">No variant selected. Changes are only recorded in variants.</p> : (
              <>
                <p className="small">Unsaved and saved tailoring for this step in <strong>{variant.name}</strong> (saved v{variant.version}).</p>
                {nodeOverrides.length === 0 && nodeAdded.length === 0 && <p className="muted small">No changes to this step.</p>}
                <ul className="changes">
                  {nodeOverrides.map((o) => {
                    const b = [...(baseNode?.inputs ?? []), ...(baseNode?.outputs ?? [])].find((i) => i.id === o.item_id);
                    return (
                      <li key={o.item_id}>
                        {o.state === "excluded" && <>Tailored out <strong>{b?.name}</strong>{o.reason ? ` (${o.reason})` : ""}. </>}
                        {o.name != null && <>Renamed <strong>{b?.name}</strong> to <strong>{o.name}</strong>. </>}
                        {o.description != null && <>Description set. </>}
                      </li>
                    );
                  })}
                  {nodeAdded.map((a) => <li key={a.item.id}>Added {a.item.direction} <strong>{a.item.name}</strong>{a.item.state === "excluded" ? " (tailored out)" : ""}</li>)}
                </ul>
                <h4>Variant history</h4>
                <ol className="history">
                  {[...variant.change_log].reverse().map((c) => (
                    <li key={c.version}><span className="muted small">v{c.version} · {new Date(c.at).toLocaleString("en-GB")}</span><ul>{c.summary.map((s, i) => <li key={i} className="small">{s}</li>)}</ul></li>
                  ))}
                </ol>
              </>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
