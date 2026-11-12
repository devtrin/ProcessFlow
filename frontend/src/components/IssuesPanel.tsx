import { useState } from "react";
import type { Issue, ProcessGraph, ReviewDecision, ReviewState } from "../types";
import { reviewLabel } from "./FlowCanvas";

export function IssuesPanel({ graph, reviews, onSelect, open, onToggle }: {
  graph: ProcessGraph; reviews: Record<string, ReviewDecision>; onSelect: (id: string) => void; open: boolean; onToggle: () => void;
}) {
  const isDraft = graph.view === "pdf_draft";
  const [tab, setTab] = useState<"issues" | "queue">(isDraft ? "queue" : "issues");
  const [sev, setSev] = useState<"all" | "error" | "warning" | "info">("all");
  const counts = { error: 0, warning: 0, info: 0 } as Record<string, number>;
  graph.issues.forEach((i) => counts[i.severity]++);
  const nodeIds = new Set(graph.nodes.map((n) => n.id));
  const shown = graph.issues.filter((i) => sev === "all" || i.severity === sev);
  const state = (id: string, fallback: ReviewState) => reviews[id]?.state ?? fallback;
  const pending = graph.nodes.filter((n) => !["verified", "source_explicit"].includes(state(n.id, n.review_state)));

  return (
    <section className={`issues ${open ? "open" : ""}`} aria-label="Review issues">
      <div className="issues-bar">
        <button className="link" onClick={onToggle} aria-expanded={open}>{open ? "▾" : "▸"} Review panel</button>
        <span className="badge sev-error">{counts.error} errors</span>
        <span className="badge sev-warning">{counts.warning} warnings</span>
        <span className="badge sev-info">{counts.info} info</span>
        {isDraft && <span className="badge r-needs_review">{pending.length} of {graph.nodes.length} steps not verified</span>}
      </div>
      {open && (
        <div className="issues-body">
          <div className="tabs" role="tablist">
            {isDraft && <button role="tab" aria-selected={tab === "queue"} className={tab === "queue" ? "active" : ""} onClick={() => setTab("queue")}>Review queue</button>}
            <button role="tab" aria-selected={tab === "issues"} className={tab === "issues" ? "active" : ""} onClick={() => setTab("issues")}>Diagnostics ({graph.issues.length})</button>
            {tab === "issues" && (
              <select aria-label="Filter by severity" value={sev} onChange={(e) => setSev(e.target.value as typeof sev)}>
                <option value="all">All severities</option><option value="error">Errors</option><option value="warning">Warnings</option><option value="info">Info</option>
              </select>
            )}
          </div>
          {tab === "issues" && (
            <ul className="issue-list" data-testid="issue-list">
              {shown.length === 0 && <li className="muted small">No diagnostics{sev !== "all" ? " at this severity" : ""}.</li>}
              {shown.map((i: Issue, k) => (
                <li key={k} className={`sev-${i.severity}`}>
                  <span className={`badge sev-${i.severity}`}>{i.severity}</span> <code>{i.code}</code> {i.message}
                  {i.element_ids.filter((id) => nodeIds.has(id)).map((id) => (
                    <button key={id} className="link small" onClick={() => onSelect(id)}>{id}</button>
                  ))}
                  {i.source_refs.filter((r) => r.type === "pdf").map((r, j) => <span key={j} className="muted small"> · p.{r.page} “{r.quote?.slice(0, 80)}”</span>)}
                </li>
              ))}
            </ul>
          )}
          {tab === "queue" && isDraft && (
            <ol className="queue">
              {graph.nodes.map((n) => {
                const s = state(n.id, n.review_state);
                return (
                  <li key={n.id}>
                    <button className="link" onClick={() => onSelect(n.id)}>{n.label}</button>
                    <span className={`badge r-${s}`}>{reviewLabel(s)}</span>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}
