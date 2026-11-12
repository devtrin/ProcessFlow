# Decisions and assumptions

Status of this file: working record for the v0.1 proof of concept, 12 November 2026. The handoff package (`docs/handoff/`) is a provisional baseline, not approved requirements. Each decision below states what was chosen and why. Items marked **open** need a decision from the product owner.

## User-established constraints (from `docs/handoff/source/Gemini-conversation-extract.txt`)

Checked against the conversation and kept intact:

| # | Constraint | Where it is met |
|---|---|---|
| U1 | Python-centric solution | All parsing, extraction, inference, validation and storage are in the FastAPI backend (`backend/app`). The frontend only renders and edits. |
| U2 | Browser-based web app | Single local server on `127.0.0.1:8000` serves the API and the built UI. |
| U3 | Modern interactive flowchart canvas | `@xyflow/react` with ELK auto-layout, custom task/decision/event shapes, pan/zoom/fit. Not BPMN notation. |
| U4 | Import process narratives from PDFs and exported BPMN 2.0 XML | `POST /api/v1/imports` handles `.pdf`, `.bpmn`, `.xml`. |
| U5 | Select steps and add, modify or tailor out step inputs/outputs | Inspector with Add, Edit (rename/description), Tailor out, Restore, in non-destructive variants. |

## Contradictions and gaps found in the handoff

| ID | Finding | Decision |
|---|---|---|
| C-01 | The Gemini sample uses namespace `http://omg.org`. | Wrong. The parser requires `http://www.omg.org/spec/BPMN/20100524/MODEL` and gives a specific `BPMN_WRONG_NAMESPACE` message for the Gemini value (`fixtures/bpmn/wrong-namespace.bpmn`, tested). |
| C-02 | `sample-process.json` shows `source_explicit` inputs/outputs on `Task_Review` attributed to the BPMN XML, but neither `example-no-di.bpmn` nor `example-with-di.bpmn` contains data associations. | The parser does not invent them: importing the handoff XML yields steps with empty I/O lists (tested: `test_no_io_items_fabricated_from_sequence_flows`). A new fixture `fixtures/bpmn/purchase-with-data.bpmn` carries explicit `dataInputAssociation`/`dataOutputAssociation` so source-explicit items can be tested. |
| C-03 | `sample-process.json` sets `participant_id: null` although the collaboration names a participant for the process. | Nodes get the participant id when a `participant/@processRef` points at their process. |
| C-04 | `sample-variant.json` models a variant as an operation log (`op: exclude/add`). Restore then needs log rewriting and leaves residue. | Variants store state, not operations: `overrides` keyed by (node, item) plus `added` items. Restore removes the override. History is kept separately in an append-only `change_log` with a human-readable summary per saved version. |
| C-05 | The five import states (`uploaded`, `extracting`, `needs_review`, `ready`, `failed`) have no state for "PDF text extracted, no model run yet". | Added `extracted`. A PDF without a provider ends there, honestly, rather than in `failed` or a fake `needs_review`. |
| C-06 | FR-03 asks for "no partial project" on rejection, but the prompt asks for source-specific diagnostics accessible from the UI. | A rejected upload stores a diagnostic-only import record (status `failed`, issues, size, hash). No source file and no baseline are stored. The user can dismiss it. |
| C-07 | The mockups show counts ("2 items to review", "6/6 evidence anchors") and a 6-page PDF. | These are fictional. The UI computes every count from the loaded graph; nothing is copied from the mockups. |
| C-08 | Schema 0.1 lacks fields the requirements need (line numbers, grounding result, view type, layout provenance, needs-clarification state). | Schema bumped to 0.2; see D-05. |

## Design decisions

**D-01 Stack.** FastAPI + Pydantic v2 + lxml + PyMuPDF + SQLite (backend); React 19 + TypeScript + `@xyflow/react` 12 + `elkjs` + Vite (frontend). Versions are pinned in `backend/requirements.txt` and `frontend/package-lock.json`. Rationale: matches the handoff proposal and the Python-centric constraint; all are maintained and installable without Docker.

**D-02 One process, one port.** In normal use the backend serves the built frontend (`frontend/dist`), so the app is one command on `127.0.0.1:8000`. Vite dev server with proxy is available for frontend work. Rationale: simplest local run on Windows and Linux; no CORS in normal use.

**D-03 lxml, locked down, instead of defusedxml.** Line numbers (`sourceline`) are needed for provenance, and `defusedxml.lxml` is deprecated. Defence in depth: (1) byte pre-scan rejects any `<!DOCTYPE`/`<!ENTITY`/`<!ELEMENT`/`<!ATTLIST`, including UTF-16 encoded files; (2) parser with `resolve_entities=False`, `no_network=True`, `load_dtd=False`, `huge_tree=False`; (3) post-parse DTD check; (4) byte, depth and element-count limits. Each layer has a regression test, including one that bypasses the pre-scan and checks lxml alone does not read `/etc/passwd`.

**D-04 Deterministic XML mapping only.** BPMN XML never goes through a model. Elements are matched by namespace URI and local name, so any prefix works. Supported subset and the handling of everything else are in `IMPLEMENTATION_REPORT.md`.

**D-05 Schema 0.2 changes from the illustrative 0.1** (all additive except where noted):
- `NodeKind` gains `complex_gateway` (shown, semantics flagged as not interpreted).
- `EdgeKind` gains `boundary_attachment` (boundary event attached to an activity). It is not a control-flow edge.
- `ReviewState` gains `needs_clarification` (from Layout B's review actions).
- `SourceRef` gains `line`, `element_type`, `block_id` and `grounding` (`exact`/`normalised`/`wrong_page`/`not_found`) for AI quotes.
- `ProcessGraph` gains `view` (`process`, `collaboration`, `pdf_draft`), `has_source_layout`, `provenance`; `source` gains `document_id`; `review_state` adds `source_explicit`.
- `Layout` gains width/height and `source: "bpmndi"`; `ProcessEdge` gains `source_id` and optional DI `waypoints`; `ProcessNode` gains `actor`.
- `schema_version` is `"0.2"`. The JSON Schema is generated from the Pydantic models into `schema/process-graph.schema.json`; a test fails if the file drifts from the models. **Changed:** variants (see C-04).

**D-06 Internal ids.** Node and edge ids are the BPMN ids, which keeps the mapping stable and readable (FR-15). A duplicate XML id keeps its first occurrence; later ones become `<id>__dup_line<N>` and a `DUPLICATE_ID` error is raised. PDF step ids come from the model (`S1`...) and are de-duplicated the same way. Graph `process_id` is `<import id>__<n>` so the same XML imported twice gives two independent baselines with the same internal node ids.

**D-07 Multiple processes and collaborations.** Each `<process>` becomes its own selectable baseline; independent processes are never merged. When a collaboration has message flows or more than one participant with a process, an extra, clearly labelled "Collaboration view" baseline is created that puts the pools side by side and adds message flows as a distinct edge kind. Black-box pools (participants without a process) appear as `unknown` nodes with `black_box_pool: true`. Variants are per baseline, so tailoring in the collaboration view does not propagate to the per-process view. **Open:** whether variants should be shared across views.

**D-08 Sub-processes are collapsed placeholders** with child count and child ids in attributes, plus a `SUBPROCESS_COLLAPSED` info issue. Inner flows are not drawn. This avoids silently flattening a sub-process into the parent flow.

**D-09 PDF drafting is an explicit user action.** After upload the PDF is only extracted. The user clicks "Draft process with AI". Rationale: inference can be slow and, for remote providers, sends data off the machine. Remote use needs `PF_ALLOW_REMOTE_INFERENCE=true` in the backend environment **and** a per-request confirmation in the UI (`acknowledge_remote`), enforced by the API.

**D-10 Inference adapters.** `ProcessInferenceAdapter` (in `backend/app/ai/base.py`) with two real adapters:
- `ollama` (local-first, default URL 127.0.0.1): Ollama's native `/api/chat` with a JSON-schema `format`.
- `anthropic` (remote): official `anthropic` Python SDK, structured outputs (`output_config.format` with JSON schema), adaptive thinking, model `claude-opus-5-5` by default, server-side refusal fallbacks enabled by default (`PF_ANTHROPIC_FALLBACKS=off` disables them).
Neither has been run against a live model in this environment (no credentials, no local model). They are tested with injected transports/clients and hand-written fixtures. No mock adapter ships in the application; the replay adapter lives in `backend/tests/conftest.py` only.

**D-11 The model returns a candidate, not a graph.** The candidate contract (`CandidateProcess`) is small and closed: steps, flows, uncertainties, with page+quote evidence and `not_stated` sentinels. The backend validates it with Pydantic, re-prompts once with the validation error, and otherwise fails with `MODEL_OUTPUT_INVALID`. It then grounds every quote against the extracted page text. Fabricated quotes raise `FABRICATED_EVIDENCE` (error), quotes on another page raise `EVIDENCE_WRONG_PAGE`, flows back to earlier steps raise `INFERRED_LOOP`, and model-reported uncertainties become `AMBIGUITY` issues, never steps. Every AI node and edge starts as `needs_review`; there is no confidence score.

**D-12 PDF-specific inference assumptions.** A "decision" is mapped to an exclusive gateway with `gateway_type_assumed: exclusive` in attributes. Lanes for PDF drafts are grouped by the actor the text names; steps without a stated actor go to a "No lane" band. Both are marked as inferred in provenance.

**D-13 Review decisions are a separate overlay.** Baselines are immutable (enforced by a SQLite trigger and an integrity hash), so "Mark verified" writes a review record (with timestamp and note, plus append-only history) rather than editing the baseline. Review is distinct from saving a variant, and neither implies approval of the process; the UI says so on save.

**D-14 Optimistic concurrency.** Saves send `expected_version`; a mismatch returns HTTP 409 `STALE_VERSION` and the UI offers a reload. No last-write-wins.

**D-15 Layout.** ELK layered, left to right. Lanes and pools are drawn as horizontal bands after layout: ELK fixes each node's column; within a band nodes are packed into rows in ELK's vertical order. This is a readable approximation, not ELK partitioning, and edges can cross band borders. When every node has BPMNDI bounds the user can switch to "Source layout", which uses the file's coordinates (scaled, because the cards are larger than BPMN shapes). Auto layout is the default for readability.

**D-16 Layout A with Layout B's review features.** Three columns (sources, canvas, inspector) from Layout A. From Layout B: interpretation status with Mark verified / Needs clarification, a review queue, and a source-evidence panel that shows the quote and the highlighted block in the extracted page text. The step-by-step focus mode of Layout B was not built.

**D-17 Selection is app-controlled.** React Flow's internal selection is disabled; click and Enter/Space on a focused node select it. This avoided an update loop between React Flow's selection and the inspector.

**D-18 Export is ProcessFlow JSON**, labelled as not BPMN XML. It includes import record, source hash, baseline, baseline hash, variant changes and change log, resolved graph and review decisions.

**D-19 Security headers.** CSP `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'` (React Flow uses inline styles), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`. Source downloads are `application/octet-stream` attachments. The UI uses no `dangerouslySetInnerHTML`.

## Open decisions for the product owner

1. Which model provider (if any) is permitted for confidential PDFs, and under what data-retention terms.
2. Real sample documents and acceptance criteria for PDF drafting quality (the single fixture is not an evaluation).
3. Whether variants should span the per-process and collaboration views (D-07).
4. Whether expanded sub-process views are needed in the next release (D-08).
5. Whether undo/redo (FR-29), visual diff (FR-27) and image export (FR-30) are needed for the next milestone.
6. Windows packaging (NFR-19) and supported Windows/Python versions.
