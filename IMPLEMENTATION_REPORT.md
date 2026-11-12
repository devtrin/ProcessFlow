# ProcessFlow v0.1 implementation report

Date: 12 November 2026. Environment used for all evidence below: Linux container (x86_64, 4 cores), Python 3.13, Node 22.23, Chromium via Playwright 1.63 (headless). No AI credentials and no local model were available.

## 1. Summary

| Definition-of-done item | Result |
|---|---|
| 1. BPMN XML with and without DI produces the correct semantic graph and interactive auto-layout, without any API key | **Met.** Unit tests compare both handoff files for identical semantics; browser tests render both, the DI file can switch to source layout. |
| 2. Text PDF produces an AI-assisted draft with source evidence when a provider is configured, or this is documented as blocked | **Pipeline implemented; live-model result blocked.** Extraction, prompt, two real adapters, schema validation, evidence grounding and the review UI are built and tested with a test-only replay of a hand-written candidate (unit tests, and a browser test through the real Ollama adapter against a local stub server). No real model was run, so extraction quality is unproven. |
| 3. Inputs/outputs added, renamed, excluded and restored, persisting across backend restart | **Met.** API test and browser test both stop and restart the backend on the same data directory and verify the saved variant. |
| 4. Original source and baseline preserved; variant export records changes and source locations | **Met.** Source bytes and baseline hashes are compared after edits; a SQLite trigger blocks baseline updates; export validated against the JSON Schema. |
| 5. Malformed and malicious XML rejected safely; unknown constructs visible | **Met** for the tested set (XXE, billion laughs, external DTD, UTF-16 DTD, depth/count/size, malformed, wrong namespace, non-BPMN root; unsupported elements, vendor extensions, broken refs, duplicates). |
| 6. Tests executed and recorded against requirement ids | **Met.** 69 backend tests, 2 frontend unit tests, 10 browser tests, all passing; outputs in `docs/test-evidence/`; per-requirement status in `docs/requirements-status.csv` and section 6. |
| 7. UI readable at 1366×768 and 1920×1080, responsive inspector, visible errors | **Met** for the checks automated (no clipping, no page scroll, inspector ≥ 320 px, canvas size, visible upload errors). Contrast and screen-reader use were not audited. |

## 2. Architecture as built

```
Browser (React 19 + TypeScript)                 Python backend (FastAPI, 127.0.0.1:8000)
 Sidebar: import, provider status, sources ─┐    POST /api/v1/imports ── size/type/signature checks, SHA-256
 Canvas: @xyflow/react + ELK, lane bands    │      ├─ .bpmn/.xml → xml_safety → bpmn_parser → validators ─┐
 Inspector: details / evidence / changes    ├─►    └─ .pdf → pdf_reader (PyMuPDF) → stored extraction     │
 Review panel: diagnostics + review queue   │    POST /imports/{id}/infer → adapter → validate → ground ──┤
 Save / Reload / Export                     │                                                          ▼
                                            └─►  SQLite (WAL) + sources/<sha256>.<ext> (write-once)
                                                 baselines (immutable, hashed) · variants (versioned)
                                                 reviews (+history) · imports · extractions
```

- The same `ProcessGraph` (schema 0.2) feeds the canvas, inspector, variants and export for both XML and PDF sources.
- The backend serves the built UI, so one command starts the app. CSP and `nosniff` headers are set on every response.
- Full design rationale: `DECISIONS.md`.

Key files:

| Concern | File |
|---|---|
| Canonical contract | `backend/app/models.py`, generated `schema/process-graph.schema.json` |
| XML hardening | `backend/app/ingest/xml_safety.py` |
| BPMN mapping | `backend/app/ingest/bpmn_parser.py` |
| PDF extraction | `backend/app/ingest/pdf_reader.py` |
| Inference boundary and adapters | `backend/app/ai/base.py`, `prompt.py`, `ollama_adapter.py`, `anthropic_adapter.py`, `registry.py` |
| Grounding and review gating | `backend/app/ingest/pdf_inference.py` |
| Graph validation | `backend/app/domain/validators.py` |
| Variant tailoring | `backend/app/domain/tailoring.py`, mirrored for previews in `frontend/src/tailoring.ts` |
| Persistence | `backend/app/store/repository.py` |
| API | `backend/app/main.py` |
| UI | `frontend/src/App.tsx`, `components/FlowCanvas.tsx`, `Inspector.tsx`, `Sidebar.tsx`, `IssuesPanel.tsx`, `layout.ts` |

## 3. Supported BPMN subset (v0.1)

Matching is by namespace `http://www.omg.org/spec/BPMN/20100524/MODEL` and local name; the prefix is irrelevant (tested with `bpmn`, `bpmn2`, `semantic` and default namespace).

| BPMN construct | Result in ProcessFlow |
|---|---|
| `process` | One selectable baseline per process. |
| `startEvent`, `endEvent`, `intermediateCatchEvent`, `intermediateThrowEvent` | Event nodes (start/end/intermediate). Event definitions (timer, message, ...) listed on the node; info issue `EVENT_TRIGGER_NOT_INTERPRETED`. |
| `boundaryEvent` | Intermediate event node plus a dotted `boundary_attachment` link to its activity; `cancelActivity` kept as an attribute. |
| `task`, `userTask`, `serviceTask`, `manualTask`, `scriptTask`, `businessRuleTask`, `sendTask`, `receiveTask` | Task node; BPMN type kept in `attributes.bpmn_type` and shown on the card. |
| `subProcess`, `adHocSubProcess`, `transaction`, `callActivity` | Collapsed sub-process placeholder with child count; info issue `SUBPROCESS_COLLAPSED`. Inner flow not drawn. |
| `exclusiveGateway`, `parallelGateway`, `inclusiveGateway`, `eventBasedGateway` | Distinct gateway kinds with distinct symbols; `default` flow marked. |
| `complexGateway` | Shown as `complex_gateway`; warning `UNSUPPORTED_SEMANTICS`. |
| `sequenceFlow` | `sequence` edge with `name` label, `conditionExpression` text and default marker. |
| `collaboration`, `participant`, `messageFlow` | Participant names on nodes; separate "Collaboration view" baseline with `message` edges (dashed) and black-box pools. Never merged into sequence flow. |
| `laneSet`, `lane`, `childLaneSet`, `flowNodeRef` | Lane assignment on nodes; nested lanes flattened to "Parent / Child"; drawn as horizontal bands. |
| `dataInputAssociation`, `dataOutputAssociation` to `dataObjectReference` / `dataStoreReference` (+`dataState`), `ioSpecification` `dataInput`/`dataOutput` | Source-explicit input/output items with XML provenance. No items are created from sequence flows. |
| `documentation` | Shown in the inspector. |
| `extensionElements`, foreign-namespace children | Kept as a flag; info `VENDOR_EXTENSIONS_IGNORED`. |
| `textAnnotation`, `association`, `group` | Not drawn; info `ARTIFACTS_NOT_RENDERED` lists them. |
| `standardLoopCharacteristics`, `multiInstanceLoopCharacteristics` | Attribute only; info `LOOP_MARKER_NOT_INTERPRETED`. |
| Any other BPMN element inside a process | `unknown` node (dashed) so connected flows remain visible; warning `UNSUPPORTED_ELEMENT`. |
| `choreography`, `conversation` and unknown top-level elements | Not shown; warning `UNSUPPORTED_ELEMENT`. |
| BPMNDI `BPMNShape`/`BPMNEdge` | Optional. Bounds and waypoints stored; "Source layout" available only when every node has bounds. |
| Duplicate ids, dangling refs, cross-process sequence flows, broken lane/default/attachedTo refs | Error or warning issues; invalid edges are not drawn; internal ids stay unique. |

Not supported: execution semantics of any kind, compensation, event sub-process content, expanded sub-processes, BPMN export or round trip. The tool does not claim BPMN 2.0 compliance.

## 4. PDF pipeline as built

1. Upload: `%PDF-` signature, size limit, SHA-256, page limit (100), encrypted PDFs rejected (`PDF_ENCRYPTED`).
2. Extraction: PyMuPDF `get_text("dict", sort=True)` → blocks with page, bbox and a heading heuristic (font size ≥ 1.2× page median, or bold short line); `find_tables()` best-effort. Text-less documents fail with `PDF_NO_TEXT_OCR_REQUIRED` (mentions "probably scanned" when page images exist); text-less pages in otherwise text PDFs produce a warning. No OCR.
3. Import status `extracted`. Drafting is a separate, explicit action.
4. Inference: provider-agnostic `ProcessInferenceAdapter`; constrained prompt with page/block-anchored text; JSON-schema structured output; Pydantic validation with one bounded re-prompt; no truncation (too-long input is refused).
5. Grounding: every quote checked on its page (`exact`, `normalised`), elsewhere (`wrong_page`) or nowhere (`not_found` → `FABRICATED_EVIDENCE` error). Missing evidence → `MISSING_EVIDENCE`. Back-edges → `INFERRED_LOOP`. Model-reported uncertainties → `AMBIGUITY` issues with quotes.
6. Review: every node and edge is `needs_review`. Mark verified / Needs clarification writes timestamped review records; the baseline stays unchanged.

For the handoff narrative, the hand-written honest candidate yields six steps, Yes/No branches, actors Requester and Manager, decision and end event in a "No lane" band (no actor stated), and one `AMBIGUITY` issue quoting "In some cases, the request may need another review." No loop or extra review step is drawn. The fabricated and invented-loop candidates trigger `FABRICATED_EVIDENCE`, `EVIDENCE_WRONG_PAGE`, `INFERRED_LOOP` and `CYCLE_PRESENT`.

## 5. Test evidence

Commands (run from the repository root unless shown):

```bash
cd backend && . .venv/bin/activate && python -m pytest -rA            # 69 passed in ~5 s
cd frontend && npx tsc -b && npx vitest run                           # types OK; 2 passed
cd frontend && npm run build && npx playwright test --trace off       # 10 passed in ~24 s
cd backend && python scripts/bench_import.py                          # NFR-11 measurement
```

A clean `python -m venv` + `pip install -r backend/requirements-dev.txt` was also done in a scratch directory; the 69 tests passed there.

Recorded outputs: `docs/test-evidence/backend-pytest.txt`, `frontend-unit.txt`, `e2e-playwright.txt`, `bench-import.txt`, `perf-300-nodes.txt` and screenshots (`workbench-1366x768.png`, `workbench-1920x1080.png`, `variant-after-restart-1366.png`, `pdf-draft-evidence-1366.png`).

Failures during development, fixed and re-run:
- E2E keyboard selection failed because React Flow's internal selection fought the app-controlled selection, causing React error #185 (update loop). Fixed by making selection app-controlled (DECISIONS D-17).
- Vitest initially collected the Playwright specs; restricted to `src/`.

Measurements:
- NFR-11: generated 1.93 MB, 300-node BPMN, 5 runs through the API: median 0.057–0.063 s, worst 0.10 s (target 3 s).
- NFR-12: 300-node graph in headless Chromium: import + layout + render 0.66–0.90 s; selection → inspector updated: median 123–136 ms, p95 148 ms in one run and 162 ms in another (target p95 < 150 ms). The figure includes Playwright polling overhead, so it is an upper bound, but the target is **not reliably met** on this machine.

## 6. Requirement status

Status vocabulary: implemented (with test evidence), partial, not implemented, blocked. "Implemented" means the behaviour exists and the cited test passed in this environment; it is not an acceptance sign-off. The same table is in `docs/requirements-status.csv`.

| ID | Status | Evidence / note |
|---|---|---|
| FR-01 | implemented | `test_pdf.py::test_t01_pdf_upload_records_hash_and_extraction`; e2e "PDF without a provider" |
| FR-02 | implemented | `test_bpmn.py::test_t02_api_import_bpmn_and_xml_extensions` |
| FR-03 | implemented | `test_security.py::test_t03_*` (type, empty, signature ×2, XXE), `test_upload_size_limit`, `test_pdf.py::test_encrypted_pdf_rejected`, `test_page_limit_and_corrupt`. Diagnostic-only record kept (DECISIONS C-06) |
| FR-04 | partial | Pages, blocks, bboxes, headings: `test_t04_page_and_block_anchors`, `test_multi_page_anchors_and_tables`. Table extraction implemented but no table fixture tested |
| FR-05 | implemented | `test_t05_scanned_image_only_pdf_reports_ocr_needed`, `test_scanned_pdf_upload_fails_without_steps` |
| FR-06 | partial (live model blocked) | Pipeline and adapters tested with replayed hand-written candidates: `test_t06_t07_*`, `test_ollama_adapter_payload_and_locality`, e2e UC-01 (stub). No real model available |
| FR-07 | partial (live model blocked) | `not_stated` actors, AI-inferred items with evidence: `test_t06_t07_*`. Same limitation as FR-06 |
| FR-08 | implemented | `test_t08_fabricated_and_wrong_page_evidence_raise_issues`, `test_ambiguity_is_flagged_not_invented` |
| FR-09 | implemented | `test_t09_broken_refs_and_duplicate_ids_reported`, `test_invented_loop_is_flagged` (cycle is info, not error). `BRANCH_UNLABELLED` implemented, not covered by a dedicated test |
| FR-10 | implemented | `test_t10_prefix_independent[bpmn,bpmn2,semantic,None]` |
| FR-11 | implemented | `test_t11_expected_kinds_ids_and_labels`, `test_t13_*` |
| FR-12 | implemented | `test_t12_lanes_preserved`; e2e checks the "Manager" lane band |
| FR-13 | implemented | `test_t13_unsupported_constructs_are_visible`, `test_t09_*` |
| FR-14 | implemented | `test_t14_with_and_without_di_have_equal_semantics`; e2e "XML without DI ... source layout" |
| FR-15 | implemented | `test_t15_reimport_is_deterministic`, provenance asserts in `test_t11` |
| FR-16 | implemented | e2e renders labelled Yes/No branches, distinct shapes, pan/zoom/fit controls, Find step. Parallel fork/join is parsed (`test_t24`) but not separately checked in the browser |
| FR-17 | implemented | e2e UC-02 and keyboard tests (inspector title, items, evidence) |
| FR-18 | implemented | `test_t18_to_t22_*`; e2e UC-02 add output |
| FR-19 | implemented | same tests: rename + description; baseline name kept |
| FR-20 | implemented | same tests: tailor out and restore, baseline unchanged |
| FR-21 | implemented | same tests: restart on same data dir; e2e restarts the backend process |
| FR-22 | implemented | baseline hash and source bytes compared; `test_baseline_table_is_immutable` |
| FR-23 | implemented | `test_d23_review_decisions_persist_and_are_separate_from_baseline`; e2e UC-01 (stub): review queue, Mark verified, persists across restart |
| FR-24 | implemented | `test_t24_multi_process_not_merged`; sidebar lists each process and the collaboration view |
| FR-25 | partial | Export with provenance and schema version, validated against JSON Schema: `test_t25_*`. Re-importing an exported snapshot is not implemented |
| FR-26 | partial | Find step by label, id or actor; diagnostics filter by severity. No filter by role, issue status or source location on the canvas |
| FR-27 | partial | Per-step Changes tab and per-version change log; no whole-graph baseline/variant diff view |
| FR-28 | implemented | Review decisions with timestamp and note, history table: `test_d23_*`, e2e UC-01 |
| FR-29 | not implemented | No undo/redo. Unsaved edits can be discarded with Reload |
| FR-30 | not implemented | No SVG/PNG export |
| FR-31 | not implemented | Future: OCR |
| FR-32 | not implemented | Future: BPMN notation view |
| FR-33 | not implemented | Future: multi-user |
| NFR-01 | implemented | Default provider `none`; `test_provider_unconfigured_is_explicit_and_makes_no_calls` (socket connect blocked); remote needs env flag + per-request ack: `test_remote_provider_needs_per_request_acknowledgement`, `test_anthropic_requires_explicit_remote_enablement`. Browser network traffic not inspected |
| NFR-02 | implemented | `test_security.py` DTD/XXE/billion-laughs/external DTD/UTF-16/depth/count/size, lxml-only layer test |
| NFR-03 | implemented | Upload, XML byte/depth/element and PDF page limits tested |
| NFR-04 | implemented (small fuzz set) | After each rejection a valid import still succeeds (`test_t03_*`); no randomised fuzzing |
| NFR-05 | implemented | Origin badges (From source / AI-inferred / User-added), review badges, Renamed/Tailored out markers; asserted in e2e |
| NFR-06 | implemented | API and e2e restart tests; baseline integrity hash checked on every read |
| NFR-07 | partial | e2e at 1366×768 and 1920×1080 (no clipping or page scroll), keyboard Enter selection and Find step. No contrast measurement or screen-reader test |
| NFR-08 | implemented | e2e "HTML/script in labels" (no execution, rendered as text); CSP header test |
| NFR-09 | implemented | No secrets in repo or bundle; readiness messages never echo keys (tested); provider status shown in UI |
| NFR-10 | implemented | Parser, PDF reader, grounding, validators, tailoring and repository are tested independently |
| NFR-11 | implemented (measured) | 300 nodes / 1.93 MB: median ≈ 0.06 s (target 3 s), `bench-import.txt` |
| NFR-12 | partial (target not reliably met) | p95 148 ms and 162 ms in two runs vs 150 ms target, headless container, `perf-300-nodes.txt` |
| NFR-13 | partial | `schema_version` on graphs and variants, `db_schema_version` in SQLite; no migration code yet (only one version exists) |
| NFR-14 | partial | `PF_DATA_DIR` configurable; backup = copy the folder (documented). Move-to-another-machine not demonstrated |
| NFR-15 | partial | Pinned Python and npm dependencies; clean pip install + tests verified on Linux only. Windows not tried |
| NFR-16 | implemented | `test_nfr16_logs_do_not_contain_document_text` |
| NFR-17 | implemented | `test_nfr17_stale_write_rejected`; single-transaction import + baselines |
| NFR-18 | partial | Ollama adapter is local by default and refuses non-local URLs without opt-in; never run against a real local model |
| NFR-19 | not implemented | No desktop packaging |

## 7. Known defects and limitations

- No real model has been run. Prompt quality, schema compliance of real models (especially small local ones) and grounding hit rates are unknown. The first real run may show that quotes need fuzzier matching (for example across line-break hyphenation in other PDFs).
- Structured-output support differs by Ollama model; a model that ignores `format` will fail validation twice and return `MODEL_OUTPUT_INVALID`.
- Inference runs inside the HTTP request. A slow model blocks that request (default timeout 300 s); there is no background job or cancel.
- Lane bands are a post-layout approximation (DECISIONS D-15): edges can cross bands untidily in large models, and source layout ignores DI lane/pool shapes.
- Large graphs fit to a small zoom on 1366×768; labels are legible only after zooming in.
- The JS bundle is about 1.9 MB because ELK is bundled unminified; acceptable locally, worth lazy-loading later.
- Variants on the collaboration view are independent of variants on the per-process view.
- The handoff mockup's Layout B step-by-step focus mode was not built.
- Exported JSON cannot be imported back.

## 8. Security assumptions (open)

- Single local user on a trusted machine. No authentication; the server binds to 127.0.0.1. Exposing it on a network requires authentication and CSRF protection first.
- Uploaded files are trusted to the extent of the tested parsers. PyMuPDF (MuPDF) parses untrusted PDFs in-process; a MuPDF vulnerability would affect the backend. Sandboxing the extractor in a subprocess is a reasonable next step.
- XML hardening is tested against the listed attack classes, not proven complete.
- The Anthropic adapter relies on the official SDK and HTTPS; data retention at the provider is outside this application's control.
- SQLite and source files are not encrypted at rest; protection relies on OS account permissions.

## 9. Next steps

1. Run both adapters against real models with the narrative fixture plus at least two representative, approved PDFs; build a small evaluation set (expected steps, branches, quotes) and tune the prompt and grounding tolerance from the results.
2. Move inference to a background job with progress, cancel and retry.
3. Add undo/redo (FR-29) and a baseline-vs-variant diff view (FR-27).
4. Add import of exported snapshots (FR-25) and a migration path for schema 0.2 → next (NFR-13).
5. Test on Windows 11 with PowerShell and record the result (NFR-15).
6. Improve lane layout (ELK partitioning or layered layout per lane) and test with real models of 100+ nodes.
7. Accessibility pass: contrast measurement, screen reader labels for edges, focus order audit.
8. Isolate PDF parsing in a subprocess with resource limits.
