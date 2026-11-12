import { useCallback, useEffect, useMemo, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { api, ApiError } from "./api";
import { FlowCanvas, type LayoutMode } from "./components/FlowCanvas";
import { Inspector } from "./components/Inspector";
import { IssuesPanel } from "./components/IssuesPanel";
import { Sidebar } from "./components/Sidebar";
import { addItem, canonical, emptyChanges, nodeChangeCount, resolveGraph, setOverride, updateAdded } from "./tailoring";
import type { DataItem, Extraction, ImportView, ProcessBundle, ProcessNode, ProviderStatus, ReviewDecision, ReviewState, Variant, VariantChanges } from "./types";

type Notice = { kind: "ok" | "error" | "info"; text: string; action?: { label: string; run: () => void } } | null;

export default function App() {
  const [imports, setImports] = useState<ImportView[]>([]);
  const [provider, setProvider] = useState<ProviderStatus | null>(null);
  const [selImport, setSelImport] = useState<string | null>(null);
  const [selProcess, setSelProcess] = useState<string | null>(null);
  const [bundle, setBundle] = useState<ProcessBundle | null>(null);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [mode, setMode] = useState<"baseline" | "variant">("baseline");
  const [variant, setVariant] = useState<Variant | null>(null);
  const [draft, setDraft] = useState<VariantChanges>(emptyChanges());
  const [draftName, setDraftName] = useState("");
  const [selNode, setSelNode] = useState<string | null>(null);
  const [focusNonce, setFocusNonce] = useState(0);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("auto");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [uploadError, setUploadError] = useState<{ file: string; message: string; code: string } | null>(null);
  const [newVariantName, setNewVariantName] = useState<string | null>(null);
  const [remoteConfirm, setRemoteConfirm] = useState<ImportView | null>(null);
  const [find, setFind] = useState("");
  const [issuesOpen, setIssuesOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(true);

  const dirty = !!variant && (canonical(draft) !== canonical(variant.changes) || draftName !== variant.name);

  const fail = useCallback((e: unknown) => {
    const err = e instanceof ApiError ? e : new ApiError(0, "CLIENT_ERROR", String(e));
    setNotice({ kind: "error", text: `${err.message} [${err.code}]` });
  }, []);

  const refreshImports = useCallback(async () => {
    const list = await api.imports();
    setImports(list);
    return list;
  }, []);

  useEffect(() => {
    api.provider().then(setProvider).catch(fail);
    refreshImports().catch(fail);
  }, [refreshImports, fail]);

  useEffect(() => {
    if (!notice || notice.kind === "error") return;
    const t = setTimeout(() => setNotice(null), 8000);
    return () => clearTimeout(t);
  }, [notice]);

  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); } };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const confirmDiscard = () => !dirty || window.confirm("You have unsaved variant changes. Discard them?");

  const loadProcess = useCallback(async (pid: string, keepVariantId?: string) => {
    const b = await api.process(pid);
    setBundle(b);
    setSelProcess(pid);
    setSelNode(null);
    if (keepVariantId) {
      const v = await api.variant(keepVariantId);
      setVariant(v.variant);
      setDraft(v.variant.changes);
      setDraftName(v.variant.name);
      setMode("variant");
    } else {
      setVariant(null);
      setDraft(emptyChanges());
      setDraftName("");
      setMode("baseline");
    }
    setLayoutMode("auto");
    setIssuesOpen(b.baseline.view === "pdf_draft" || b.baseline.issues.some((i) => i.severity === "error"));
    const docId = b.baseline.source.document_id;
    if (b.baseline.source.kind === "pdf" && docId) api.extraction(docId).then(setExtraction).catch(() => setExtraction(null));
    else setExtraction(null);
  }, []);

  const selectImport = (id: string) => {
    setSelImport(id === selImport ? null : id);
    const imp = imports.find((i) => i.id === id);
    if (imp && imp.processes.length && !imp.processes.some((p) => p.process_id === selProcess)) {
      if (!confirmDiscard()) return;
      loadProcess(imp.processes[0].process_id).catch(fail);
    }
  };

  const selectProcess = (pid: string) => {
    if (pid === selProcess || !confirmDiscard()) return;
    loadProcess(pid).catch(fail);
  };

  const upload = async (f: File) => {
    setBusy("upload");
    setUploadError(null);
    try {
      const rec = await api.upload(f);
      await refreshImports();
      setSelImport(rec.id);
      if (rec.processes.length && confirmDiscard()) await loadProcess(rec.processes[0].process_id);
      setNotice({ kind: "ok", text: rec.kind === "pdf" ? `Extracted text from ${rec.filename}. No process has been drafted yet.` : `Imported ${rec.filename}: ${rec.processes.length} view(s).` });
    } catch (e) {
      const err = e as ApiError;
      setUploadError({ file: f.name, message: err.message, code: err.code });
      refreshImports().catch(() => undefined);
    } finally {
      setBusy(null);
    }
  };

  const runInference = async (imp: ImportView, ack: boolean) => {
    setRemoteConfirm(null);
    setBusy("infer");
    try {
      const rec = await api.infer(imp.id, ack);
      await refreshImports();
      const pid = rec.process_ids[rec.process_ids.length - 1];
      if (confirmDiscard()) await loadProcess(pid);
      setNotice({ kind: "info", text: "AI-assisted draft created. Every step needs human review against the source evidence." });
    } catch (e) {
      fail(e);
      refreshImports().catch(() => undefined);
    } finally {
      setBusy(null);
    }
  };

  const onInfer = (imp: ImportView) => {
    if (provider?.remote) setRemoteConfirm(imp);
    else runInference(imp, false);
  };

  const onDismiss = async (imp: ImportView) => {
    try { await api.dismissImport(imp.id); await refreshImports(); } catch (e) { fail(e); }
  };

  const openVariant = async (vid: string) => {
    if (!selProcess || !confirmDiscard()) return;
    try { await loadProcess(selProcess, vid); } catch (e) { fail(e); }
  };

  const createVariant = async () => {
    if (!selProcess || !newVariantName?.trim()) return;
    try {
      const v = await api.createVariant(selProcess, newVariantName.trim());
      setNewVariantName(null);
      await loadProcess(selProcess, v.id);
      setNotice({ kind: "ok", text: `Variant “${v.name}” created from the baseline.` });
    } catch (e) { fail(e); }
  };

  const save = async () => {
    if (!variant) return;
    setBusy("save");
    try {
      const r = await api.saveVariant(variant.id, variant.version, draftName.trim() || variant.name, draft);
      setVariant(r.variant);
      setDraft(r.variant.changes);
      setDraftName(r.variant.name);
      setBundle((b) => b && { ...b, variants: b.variants.map((x) => (x.id === r.variant.id ? { ...x, version: r.variant.version, name: r.variant.name } : x)) });
      setNotice({ kind: "ok", text: `Variant saved (version ${r.variant.version}). Saving stores your tailoring; it does not verify or approve the process.` });
    } catch (e) {
      const err = e as ApiError;
      if (err.code === "STALE_VERSION") setNotice({ kind: "error", text: err.message, action: { label: "Reload variant", run: () => reload(true) } });
      else fail(e);
    } finally {
      setBusy(null);
    }
  };

  const reload = async (force = false) => {
    if (!force && !confirmDiscard()) return;
    try {
      await refreshImports();
      api.provider().then(setProvider).catch(() => undefined);
      if (selProcess) await loadProcess(selProcess, variant?.id);
      setNotice({ kind: "info", text: "Reloaded from the backend." });
    } catch (e) { fail(e); }
  };

  const onReview = async (node: ProcessNode, state: ReviewState, note: string) => {
    if (!selProcess) return;
    setBusy("review");
    try {
      const d = await api.review(selProcess, node.id, state, note);
      setBundle((b) => b && { ...b, reviews: [...b.reviews.filter((r) => r.element_id !== d.element_id), d] });
      setNotice({ kind: "ok", text: `Review decision recorded for “${node.label}”.` });
    } catch (e) { fail(e); } finally { setBusy(null); }
  };

  // ---- tailoring handlers (draft only; persisted on Save)
  const isAdded = (item: DataItem) => item.origin === "user_added";
  const onExclude = (n: ProcessNode, it: DataItem, reason: string) =>
    setDraft((d) => (isAdded(it) ? updateAdded(d, it.id, { state: "excluded" }) : setOverride(d, n.id, it.id, { state: "excluded", reason: reason || null })));
  const onRestore = (n: ProcessNode, it: DataItem) =>
    setDraft((d) => (isAdded(it) ? updateAdded(d, it.id, { state: "included" }) : setOverride(d, n.id, it.id, null)));
  const onEdit = (n: ProcessNode, it: DataItem, name: string, description: string) =>
    setDraft((d) => {
      if (isAdded(it)) return updateAdded(d, it.id, { name, description });
      const base = bundle?.baseline.nodes.find((x) => x.id === n.id);
      const b = [...(base?.inputs ?? []), ...(base?.outputs ?? [])].find((x) => x.id === it.id);
      return setOverride(d, n.id, it.id, { name: b && b.name === name ? null : name, description: b && b.description === description ? null : description });
    });
  const onAdd = (n: ProcessNode, dir: "input" | "output", name: string, description: string) => setDraft((d) => addItem(d, n.id, dir, name, description));
  const onRemoveAdded = (_n: ProcessNode, it: DataItem) => setDraft((d) => updateAdded(d, it.id, null));

  const graph = useMemo(() => {
    if (!bundle) return null;
    return mode === "variant" && variant ? resolveGraph(bundle.baseline, draft) : bundle.baseline;
  }, [bundle, mode, variant, draft]);

  const reviewMap = useMemo(() => Object.fromEntries((bundle?.reviews ?? []).map((r) => [r.element_id, r])) as Record<string, ReviewDecision>, [bundle?.reviews]);
  const reviewStates = useMemo(() => Object.fromEntries(Object.entries(reviewMap).map(([k, v]) => [k, v.state])) as Record<string, ReviewState>, [reviewMap]);
  const changed = useMemo(() => {
    if (mode !== "variant") return {};
    const m: Record<string, number> = {};
    graph?.nodes.forEach((n) => { const c = nodeChangeCount(draft, n.id); if (c) m[n.id] = c; });
    return m;
  }, [graph, draft, mode]);

  const node = graph?.nodes.find((n) => n.id === selNode) ?? null;
  const select = (id: string | null) => { setSelNode(id); };
  const focus = (id: string) => { setSelNode(id); setFocusNonce((x) => x + 1); };
  const matches = find.trim() && graph ? graph.nodes.filter((n) => `${n.label} ${n.id} ${n.actor ?? ""}`.toLowerCase().includes(find.trim().toLowerCase())).slice(0, 8) : [];

  const pendingReview = graph?.view === "pdf_draft" ? graph.nodes.filter((n) => !["verified", "source_explicit"].includes(reviewStates[n.id] ?? n.review_state)).length : 0;
  const currentImport = imports.find((i) => i.id === bundle?.baseline.source.document_id);

  return (
    <div className={`app ${navOpen ? "" : "nav-collapsed"}`}>
      <header className="topbar">
        <div className="brand">
          <button className="icon nav-toggle" onClick={() => setNavOpen(!navOpen)} aria-label="Toggle source navigator">{"☰"}</button>
          <span className="logo" aria-hidden>{"⌘"}</span>
          <span className="brand-name">ProcessFlow</span>
          <span className="tag">Local workspace · data stays on this machine</span>
        </div>
        <div className="top-actions">
          {variant && mode === "variant" && (
            <span className={`save-state ${dirty ? "dirty" : ""}`} data-testid="save-state">
              {dirty ? "Unsaved changes" : `Saved · v${variant.version}`}
            </span>
          )}
          <button onClick={() => reload()} disabled={!!busy}>Reload</button>
          <button className="primary" onClick={save} disabled={!variant || mode !== "variant" || !dirty || !!busy} data-testid="save-variant">
            {busy === "save" ? "Saving…" : "Save variant"}
          </button>
        </div>
      </header>

      <div aria-live="polite" className="notice-slot">
        {notice && (
          <div className={`notice ${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"} data-testid="notice">
            <span>{notice.text}</span>
            {notice.action && <button onClick={notice.action.run}>{notice.action.label}</button>}
            <button className="icon" onClick={() => setNotice(null)} aria-label="Dismiss message">{"×"}</button>
          </div>
        )}
      </div>

      <Sidebar imports={imports} provider={provider} selectedImport={selImport} selectedProcess={selProcess} busy={busy} uploadError={uploadError}
        onUpload={upload} onSelectImport={selectImport} onSelectProcess={selectProcess} onInfer={onInfer} onDismiss={onDismiss} />

      <main className="center">
        {!graph ? (
          <div className="welcome">
            <h2>Import a process to begin</h2>
            <p>BPMN 2.0 XML (.bpmn/.xml) is parsed deterministically, without any AI service. Text-based PDFs are extracted locally; drafting a process from a PDF needs a configured inference provider and always ends in human review.</p>
            <p className="muted small">Sample files are in <code>fixtures/</code> in the repository.</p>
          </div>
        ) : (
          <>
            <div className="canvas-head">
              <div className="title-block">
                <div className="eyebrow">{graph.source.kind === "pdf" ? "PDF" : "BPMN XML"} / {graph.view === "collaboration" ? "Collaboration view" : graph.view === "pdf_draft" ? "AI-assisted draft" : "Process"}</div>
                <h1 data-testid="process-title">{graph.title}</h1>
                <div className="muted small">
                  {graph.source.filename} · {graph.nodes.length} nodes · {graph.edges.length} connections · {graph.lanes.length} lanes
                  {pendingReview > 0 && <> · <span className="badge r-needs_review">{pendingReview} steps need review</span></>}
                </div>
              </div>
              <div className="head-tools">
                <div className="find">
                  <input type="search" placeholder="Find step…" aria-label="Find step" value={find} onChange={(e) => setFind(e.target.value)} />
                  {matches.length > 0 && (
                    <ul className="find-results" role="listbox" aria-label="Matching steps">
                      {matches.map((n) => (
                        <li key={n.id}><button role="option" aria-selected={n.id === selNode} onClick={() => { focus(n.id); setFind(""); }}>{n.label || n.id} <span className="muted small">{n.kind}</span></button></li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="seg" role="group" aria-label="Layout">
                  <button className={layoutMode === "auto" ? "active" : ""} onClick={() => setLayoutMode("auto")} aria-pressed={layoutMode === "auto"}>Auto layout</button>
                  <button className={layoutMode === "source" ? "active" : ""} onClick={() => setLayoutMode("source")} aria-pressed={layoutMode === "source"}
                    disabled={!graph.has_source_layout} title={graph.has_source_layout ? "Use BPMNDI coordinates from the file" : "The file has no complete BPMNDI layout"}>Source layout</button>
                </div>
                <a className="btn" href={variant && mode === "variant" ? api.exportUrl("variant", variant.id) : api.exportUrl("baseline", graph.process_id)} download
                  title={dirty ? "Exports the last saved version" : "Download canonical JSON"}>Export JSON</a>
              </div>
            </div>

            <div className="view-bar">
              <div className="seg" role="group" aria-label="Baseline or variant">
                <button className={mode === "baseline" ? "active" : ""} aria-pressed={mode === "baseline"} onClick={() => { if (confirmDiscard()) { if (variant) setDraft(variant.changes); setMode("baseline"); } }}>Baseline</button>
                <button className={mode === "variant" ? "active" : ""} aria-pressed={mode === "variant"} disabled={!variant} onClick={() => setMode("variant")}>Variant</button>
              </div>
              <label className="small">Variant
                <select value={variant?.id ?? ""} onChange={(e) => e.target.value && openVariant(e.target.value)} aria-label="Select variant">
                  <option value="">{bundle!.variants.length ? "Choose…" : "None yet"}</option>
                  {bundle!.variants.map((v) => <option key={v.id} value={v.id}>{v.name} (v{v.version})</option>)}
                </select>
              </label>
              {newVariantName === null ? (
                <button onClick={() => setNewVariantName("")}>+ New variant</button>
              ) : (
                <form className="inline-form" onSubmit={(e) => { e.preventDefault(); createVariant(); }}>
                  <input value={newVariantName} onChange={(e) => setNewVariantName(e.target.value)} placeholder="Variant name" aria-label="New variant name" autoFocus maxLength={120} />
                  <button className="primary" type="submit" disabled={!newVariantName.trim()}>Create</button>
                  <button type="button" onClick={() => setNewVariantName(null)}>Cancel</button>
                </form>
              )}
              {variant && mode === "variant" && (
                <label className="small">Name<input value={draftName} onChange={(e) => setDraftName(e.target.value)} maxLength={120} aria-label="Variant name" /></label>
              )}
              <span className="muted small legend">
                <span className="lg lg-seq" /> sequence <span className="lg lg-msg" /> message <span className="lg lg-inf" /> inferred
              </span>
            </div>

            <div className="canvas-wrap" data-testid="canvas">
              <ReactFlowProvider>
                <FlowCanvas graph={graph} structureKey={graph.process_id} layoutMode={layoutMode} reviews={reviewStates} changedNodes={changed}
                  selectedId={selNode} onSelect={select} focusNonce={focusNonce} />
              </ReactFlowProvider>
            </div>
            <IssuesPanel graph={graph} reviews={reviewMap} onSelect={focus} open={issuesOpen} onToggle={() => setIssuesOpen(!issuesOpen)} />
            {currentImport && currentImport.issues.length > 0 && <span className="sr-only">{currentImport.issues.length} import diagnostics</span>}
          </>
        )}
      </main>

      {graph && bundle && (
        <Inspector graph={graph} baseline={bundle.baseline} node={node} mode={mode} variant={variant} draft={draft} reviews={reviewMap}
          extraction={extraction} busy={!!busy} onExclude={onExclude} onRestore={onRestore} onEdit={onEdit} onAdd={onAdd}
          onRemoveAdded={onRemoveAdded} onReview={onReview}
          onRequestVariant={() => (variant ? setMode("variant") : setNewVariantName(""))} />
      )}

      {remoteConfirm && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="remote-title">
          <div className="modal">
            <h3 id="remote-title">Send PDF text to a remote AI provider?</h3>
            <p>The extracted text of <strong>{remoteConfirm.filename}</strong> ({String(remoteConfirm.extraction_summary["char_count"] ?? "?")} characters) will be sent to <strong>{provider?.provider}</strong> ({provider?.model}). It leaves this machine and is subject to that provider’s data retention terms.</p>
            <p className="muted small">The result is a draft for review, not an authoritative process.</p>
            <div className="item-actions">
              <button className="primary" onClick={() => runInference(remoteConfirm, true)}>Send and draft</button>
              <button onClick={() => setRemoteConfirm(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
