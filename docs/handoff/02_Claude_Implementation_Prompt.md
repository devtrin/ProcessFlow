# Paste this entire prompt into Claude (ideally Claude Code, with this package attached or unzipped in its working directory)

You are a **senior software architect, systems engineer, Python/TypeScript developer and independent verification engineer**. Your assignment is to turn the attached **ProcessFlow** handoff package into a **working, local-first, browser-accessible proof of concept**. Build software rather than merely drafting another design. The user is primarily a Python developer and is not asking for a formal BPMN authoring product. Follow explicit source-grounded behaviour and show failures rather than simulating features.

## A. Authoritative package and context

Read these files **before implementing** in this order:

1. `00_README.md` — package map and how to use it.
2. `01_Architecture_and_Requirements.md` — proposed architecture, product scope, functional/NFR needs and test cases. Treat it as the governing provisional baseline, not as already approved requirements.
3. `03_Requirements_Traceability.csv` — machine-readable candidate requirements and proposed verification cases.
4. `examples/process-graph.schema.json` and `examples/sample-process.json` — illustrative canonical contract and graph.
5. `examples/sample-variant.json` — distinction between immutable baseline and non-destructive tailoring.
6. `examples/example-no-di.bpmn`, `examples/example-with-di.bpmn`, and `examples/invalid-xxe.xml` — positive/negative XML fixtures.
7. `examples/example-process-narrative.pdf` and `.txt` — text PDF test fixture, including a deliberate ambiguity.
8. `mockups/layout-a-canvas-workbench.html` and `mockups/layout-b-guided-review.html` plus corresponding PNGs — two UI directions. These are **visual/interaction prototypes only**, not real file processors.
9. `source/Gemini-conversation-extract.txt` (or `.docx`) — original conversation. Use it to verify **user-established constraints**, not as reliable implementation code.

The user-established requirements in that original conversation are: **Python-centric solution; browser-based web app; modern interactive flowchart canvas; import process narratives from PDFs and exported BPMN 2.0 XML; select steps and add, modify or tailor out step inputs and outputs.** Everything else is a design proposal and can be improved with recorded rationale. Do not discard any of these five constraints.

## B. Your mission and delivery contract

Deliver a runnable implementation with real import, parsing, graph display, editing, persistent save, reload and tests. No dummy endpoints returning "success" without storing. No misleading demo behaviour shown as production import. A valid XML fixture must work with **no AI service or account**. PDF-to-flow generation must be integrated via a real, configurable inference interface, and must make it explicit when no model provider is configured. If no AI credentials or local model are available in your execution environment, implement/test against a **mock adapter only as a fixture-driven test**, never present mock inference as real user-PDF interpretation. Still implement the genuine connector boundary and document exactly how to enable it.

You may choose library/framework versions after checking compatibility. Do not claim that optional bpmn-js or any other rendering library provides perfect BPMN interoperability. The first visualisation is a **modern process flowchart**, not strict BPMN notation or workflow execution.

**Deliverables in the resulting code repository:**

- Python FastAPI backend with safe upload, PDF text extraction, deterministic BPMN XML parsing, shared validated process graph, issues, variants and persistence.
- React + TypeScript frontend with a modern interactive diagram (`@xyflow/react`), auto-layout (ELK.js or justified alternative), process selector, evidence/review UI, selected-node inspector, add/edit/exclude/restore of input/output items, baseline/variant selector, meaningful Save and Reload, JSON export.
- Clear local run commands for Windows PowerShell and Linux/macOS (`README.md`, dependency management and `.env.example` that contains no secrets); avoid requiring Docker or a cloud account to inspect XML.
- Checked-in fixture-driven unit/integration tests and at least one UI end-to-end test of import → select → tailor → save → restart/reload.
- `IMPLEMENTATION_REPORT.md` listing architecture choices, actual supported BPMN subset, evidence of tests run, exact commands, defects/unimplemented requirements, open security assumptions and next steps.
- Working front-end layout inspired primarily by **Layout A (Canvas Workbench)**; adopt the evidence and review interaction from **Layout B**. If an alternative layout makes significantly more sense, document why.

## C. Phased execution instructions (perform, do not merely propose)

**Phase 0 — verify handoff & plan**

- Inventory files and current environment. Compare the prompt/spec to existing code if any. Identify contradictions or incomplete specifications; document as `DECISIONS.md` with selected assumptions.
- Verify critical facts against the formal BPMN XML namespace, current library docs, and security best practice. Do not copy Gemini's `http://omg.org` namespace: it is wrong.
- Produce a concise implementation plan and a traceability checklist, then proceed directly into implementation. Ask a question *only if a hard blocker prevents any meaningful progress*.

**Phase 1 — contract, deterministic XML and working UI**

- Implement data models using Pydantic v2 and JSON-compatible frontend types. Refine the illustrative schema if needed, keeping its required invariants and explaining changes.
- Implement secure XML parsing, namespace-aware queries independent of prefix; reject DTD/XXE; enforce file/depth/element limits; handle duplicate IDs, broken references, unfamiliar constructs as issues. Never fetch external resources referenced by XML.
- In v0.1 map standard events, basic tasks, XOR/parallel gateways, labelled sequence flows, pool/lane references, subprocess placeholders and collaboration/message flows where present. Keep source IDs and type distinctions. Do not conflate message and sequence flows. Do not create I/O data items from simple sequence flows.
- Import valid XML **with and without** BPMNDI. Make source coordinates optional. Prefer modern auto-layout where imported DI would impair legibility, but preserve provenance of original XML layout if present.
- Build working React flow canvas, inspector and data editor. Original imported baseline is immutable. Variant changes are stored separately, with `included/excluded` item state, source origin and change version. Give user a restore action.
- Implement backend persistence with SQLite and an app-owned local directory. `Save` must survive a full application restart. Use a write version / optimistic concurrency check rather than silent last-write-wins where feasible.
- Make the process visibly selectable in multi-process XML rather than merging independent processes silently.

**Phase 2 — real PDF extraction pipeline**

- Implement PyMuPDF text extraction (blocks/pages/locations; simple tables where useful), with detection for password-protected, scanned or no-text PDFs. Show error/warning if OCR is not supported; do not invent extracted text.
- Implement a provider-agnostic `ProcessInferenceAdapter` interface. Support **at least one real adapter** using structured JSON output with a configured model/backend, protected credentials and an explicit outbound-data warning for remote providers. Local-first/no outbound by default. Use `.env.example` and documented configuration; do not embed secrets.
- Form a constrained extraction prompt instructing the model to report only evidenced process tasks, events, decisions, actors, I/O and conditions; preserve exact PDF page/quote provenance and explicitly return `unknown`/`not_stated` where absent. LLM-generated candidate output is always subject to validation/human review; if it has non-existent evidence anchors, raise issues.
- Provide an unambiguous review gate for AI-derived logic. The sample narrative PDF deliberately says "another review" may be needed but gives no actor or trigger: **do not add a complete invented loop**. Flag uncertainty instead.
- Reuse the same canonical graph, canvas, inspector and variant persistence as for BPMN XML.

**Phase 3 — verify, harden and document**

- Tests: parse both valid XML fixtures, check equal semantics; check DTD/XXE rejection; broken refs/duplicate IDs; ensure valid XML with no DI renders; tailored items persist after restart and original baseline remains unchanged; insert HTML in node labels and prove it is escaped; PDF text extraction page anchor; provider-unconfigured behaviour; structured-output validation; fabricated evidence fails review.
- Exercise actual frontend with browser tooling if available. Test 1366×768 and 1920×1080 layouts and keyboard selection of a step.
- Implement graceful state transitions (`uploaded`, `extracting`, `needs_review`, `ready`, `failed`); source-specific diagnostics must be accessible from the UI.
- Mark each FR/NFR from the provisional baseline with `implemented`, `partial`, `not implemented`, or `blocked`, and cite test evidence; do not mark an untested requirement "passed".
- Deliver a final concise run guide and test summary. Demonstrate the two end-to-end use cases or explicitly explain what remains unproven.

## D. Non-negotiable domain constraints

1. **Correct XML namespace:** `http://www.omg.org/spec/BPMN/20100524/MODEL`. Prefixes can vary. Preserve XML element IDs and process ownership.
2. **Security:** No DTD/entity expansion or network fetching. Upload limits, XML limits, escaped UI text, no unsafe HTML injection, no raw confidential text in logs. If you choose lxml over defusedxml, explicitly disable entity/network resolution and provide an XXE regression test.
3. **Semantics:** BPMN XML is a structured source and should not go through an LLM. Events, gateways, message flow and subprocesses must not be flattened so severely that the graph silently changes meaning. Unknown semantics → diagnostics.
4. **Provenance:** Every imported graph item has source locator or visible unverified state. AI confidence scores, if any, are not evidence or guarantees; source references and review actions are what matter.
5. **Tailoring:** `exclude` in a variant ≠ delete from original. Editing a data item ≠ editing a control-flow edge. Baseline data never changes as side effect of editing.
6. **Data handling:** Default to localhost and local storage. Do not send proprietary process PDFs to an external LLM without configuration, explicit enablement and visible notice; document data retention/privacy assumptions.
7. **Honesty:** Avoid mock success and claims of "full BPMN 2.0 compliance", "accurate AI extraction", or "secure" without scoped tests. Do not silently skip unsupported construct types.
8. **Robustness:** IDs unique within internal graph, all edge references resolve or yield errors, disabled/failed provider visible, saves atomic, source import hash recorded, invalid files do not crash server.

## E. Interaction and design specifics

- The default Canvas Workbench should show: source navigator/selector, central scrollable diagram, selected step and evidence inspector, status badges, baseline/variant toggle, deliberate Save, review issues and export.
- Clicking a step opens live editable inputs/outputs with `Add`, `Rename/Edit`, `Tailor out`, `Restore` (and source origin labels). Handle empty lists and negative cases.
- Retain the difference between "step selected", "inferred step verified", and "variant saved". Do not imply Save = process approval.
- Use consistent visual language: tasks = rectangles, decisions = diamonds, events = circles/compact round nodes; branch conditions on edges. Swimlane/pool grouping should be apparent when present.
- Avoid fabricated output counts or process status in real imports; the packaged HTML mockups display intentionally fictional examples.
- Provide helpful errors when XML or PDF file lacks extractable process logic or relevant fields.

## F. Definition of done and evidence

Consider the first PoC complete only if **all** of the following are observed:

1. A real sample BPMN XML import (with and without DI) generates the correct semantic graph and an interactive auto-layout; it works without any API key.
2. A real sample text PDF extraction produces an **AI-assisted draft** with source evidence **when a configured inference provider is available**, or clearly documents this as blocked if provider isn't available. Never fake a successful extraction.
3. A selected step can have data inputs/outputs added, renamed, excluded and restored, and the changes persist after restarting the backend.
4. Original source and baseline are preserved; variant export records changes and source locations.
5. Malformed and malicious XML are rejected safely; unknown BPMN constructs remain visible in diagnostics.
6. Tests are executed and results recorded, rather than asserted, and documented against the requirement IDs.
7. The UI is readable at the requested desktop sizes, with responsive inspector and visible errors.

Your final response should include **what works**, **how to launch**, **which tests passed/failed**, **what remains incomplete** and links/paths to the actual implementation files. Do not present the original handoff as the implementation. If time is insufficient, deliver a working vertical slice and an evidence-based gap list instead of a set of empty scaffolds.

## G. Authoritative technical references to consult, not sources of user requirements

- OMG BPMN 2.0.2 specification: https://www.omg.org/spec/BPMN/2.0.2
- bpmn-js: https://bpmn.io/toolkit/bpmn-js/walkthrough/
- React Flow: https://reactflow.dev/
- PyMuPDF: https://pymupdf.readthedocs.io/
- OWASP XXE prevention: https://cheatsheetseries.owasp.org/cheatsheets/XML_External_Entity_Prevention_Cheat_Sheet.html

Begin by reading the package in section A, then build the working PoC according to sections B–F. Preserve the trace between user needs, design choices and test evidence.
