import { useRef, useState } from "react";
import type { ImportView, ProviderStatus } from "../types";

const STATUS_LABEL: Record<string, string> = {
  uploaded: "Uploaded", extracting: "Extracting…", extracted: "Text extracted", needs_review: "Needs review", ready: "Ready", failed: "Failed",
};

const VIEW_LABEL: Record<string, string> = { process: "Process", collaboration: "Collaboration view", pdf_draft: "AI-assisted draft" };

export interface SidebarProps {
  imports: ImportView[];
  provider: ProviderStatus | null;
  selectedImport: string | null;
  selectedProcess: string | null;
  busy: string | null;
  uploadError: { file: string; message: string; code: string } | null;
  onUpload: (f: File) => void;
  onSelectImport: (id: string) => void;
  onSelectProcess: (pid: string) => void;
  onInfer: (imp: ImportView) => void;
  onDismiss: (imp: ImportView) => void;
}

export function Sidebar(p: SidebarProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  return (
    <nav className="sidebar" aria-label="Sources and processes">
      <section>
        <div className="eyebrow">Import</div>
        <div
          className={`drop ${drag ? "drag" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) p.onUpload(f); }}
        >
          <button className="primary block" onClick={() => fileRef.current?.click()} disabled={!!p.busy}>
            {p.busy === "upload" ? "Importing…" : "+ Import PDF or BPMN XML"}
          </button>
          <input ref={fileRef} type="file" accept=".pdf,.bpmn,.xml" hidden data-testid="file-input"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) p.onUpload(f); e.target.value = ""; }} />
          <div className="muted small">.bpmn / .xml (BPMN 2.0) or text-based .pdf. Files stay on this machine.</div>
        </div>
        {p.uploadError && (
          <div className="alert error" role="alert" data-testid="upload-error">
            <strong>{p.uploadError.file}</strong> was not imported: {p.uploadError.message} <span className="muted">[{p.uploadError.code}]</span>
          </div>
        )}
      </section>

      <section className="provider" aria-label="AI provider status">
        <div className="eyebrow">PDF drafting provider</div>
        {p.provider ? (
          <div className={`prov ${p.provider.ready ? "ok" : "off"}`} data-testid="provider-status">
            <strong>{p.provider.provider === "none" ? "Not configured" : `${p.provider.provider}${p.provider.model ? ` · ${p.provider.model}` : ""}`}</strong>
            {p.provider.remote && <span className="badge b-remote">remote</span>}
            <div className="small">{p.provider.message}</div>
          </div>
        ) : <div className="muted small">Checking…</div>}
      </section>

      <section className="sources">
        <div className="eyebrow">Sources ({p.imports.length})</div>
        {p.imports.length === 0 && <p className="muted small">Nothing imported yet.</p>}
        <ul>
          {p.imports.map((imp) => {
            const open = imp.id === p.selectedImport;
            return (
              <li key={imp.id} className={`source ${open ? "open" : ""}`}>
                <button className="source-btn" onClick={() => p.onSelectImport(imp.id)} aria-expanded={open}>
                  <span className="source-name">{imp.filename}</span>
                  <span className="source-meta">
                    <span className="badge">{imp.kind === "pdf" ? "PDF" : "BPMN XML"}</span>
                    <span className={`badge s-${imp.status}`}>{STATUS_LABEL[imp.status]}</span>
                  </span>
                </button>
                {open && (
                  <div className="source-body">
                    <div className="muted small">sha256 {imp.sha256 ? imp.sha256.slice(0, 12) + "…" : "—"} · {(imp.size_bytes / 1024).toFixed(1)} KB</div>
                    {imp.status === "failed" && (
                      <>
                        <ul className="issue-mini">{imp.issues.map((i, k) => <li key={k} className={`sev-${i.severity}`}><strong>{i.code}</strong> {i.message}</li>)}</ul>
                        <button onClick={() => p.onDismiss(imp)}>Dismiss</button>
                      </>
                    )}
                    {imp.kind === "pdf" && imp.status !== "failed" && (
                      <div className="pdf-box">
                        <div className="small">{String(imp.extraction_summary["page_count"] ?? "?")} page(s), {String(imp.extraction_summary["block_count"] ?? "?")} text blocks extracted.</div>
                        {imp.inference?.state === "failed" && (
                          <div className="alert error small" role="alert">Last AI drafting attempt failed: {imp.inference.error} [{imp.inference.error_code}]</div>
                        )}
                        <button className="primary block" disabled={!p.provider?.ready || !!p.busy} onClick={() => p.onInfer(imp)}
                          title={p.provider?.ready ? "" : p.provider?.message}>
                          {p.busy === "infer" ? "Drafting…" : imp.processes.length ? "Draft again with AI" : "Draft process with AI"}
                        </button>
                        {!p.provider?.ready && <div className="muted small">Unavailable: no ready provider. Text extraction is complete; no steps are invented without a model.</div>}
                      </div>
                    )}
                    {imp.processes.length > 0 && (
                      <ul className="processes" aria-label="Processes in this source">
                        {imp.processes.map((pr) => (
                          <li key={pr.process_id}>
                            <button className={`proc-btn ${pr.process_id === p.selectedProcess ? "active" : ""}`} onClick={() => p.onSelectProcess(pr.process_id)}
                              aria-current={pr.process_id === p.selectedProcess}>
                              <span>{pr.title}</span>
                              <span className="muted small">{VIEW_LABEL[pr.view]} · {pr.node_count} nodes · {pr.issue_count} issues</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    {imp.issues.length > 0 && imp.status !== "failed" && (
                      <details className="small"><summary>Import diagnostics ({imp.issues.length})</summary>
                        <ul className="issue-mini">{imp.issues.map((i, k) => <li key={k} className={`sev-${i.severity}`}><strong>{i.code}</strong> {i.message}</li>)}</ul>
                      </details>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </nav>
  );
}
