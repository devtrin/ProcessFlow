# ProcessFlow — Claude-ready concept and prototype handoff

**Prepared:** 12 November 2026  
**Purpose:** Design and initiate a Python-based web app to turn PDF narratives and BPMN 2.0 XML exports into interactive, editable process diagrams.  
**Status:** Architecture + initial requirements + test methods + **two browser mockups** + **implementation prompt**, not the implemented application.

## Fast start

1. **For reviewing the concept:** open `01_Architecture_and_Requirements.md`.
2. **For viewing the two designs:** open `mockups/layout-a-canvas-workbench.html` and `mockups/layout-b-guided-review.html` in a web browser; PNG screenshots are under `assets/`.
3. **For developing with Claude:** attach this **entire unzipped directory** (or provide the ZIP if Claude supports ZIP ingestion). Paste the full contents of `02_Claude_Implementation_Prompt.md` as your first implementation instruction. In Claude Code, place the folder in the workspace and ask Claude to follow that prompt.
4. **For testing against known data:** use BPMN XML, PDF and JSON fixtures under `examples/`. They are fictional, not confidential.

## What is included

| File/folder | Why it exists |
|---|---|
| `01_Architecture_and_Requirements.md` | Proposed architecture; source critique; 9 stakeholder needs, 33 functional and 19 non-functional requirements with corresponding test methods, two use cases, decisions and staged implementation |
| `03_Requirements_Traceability.csv` | Machine-readable matrix of 33 functional + 19 non-functional candidate requirements with verification methods, ready for status tracking |
| `02_Claude_Implementation_Prompt.md` | Comprehensive paste-ready agent prompt with explicit deliverables, phases, non-negotiable behaviour and definition of done |
| `mockups/layout-a-canvas-workbench.html` | Self-contained illustrative UI, canvas-first with editable inspector (recommended foundation) |
| `mockups/layout-b-guided-review.html` | Self-contained illustrative UI, source-evidence/review-first alternative |
| `assets/layout-a.png`, `assets/layout-b.png` | Static screenshots for preview and easy attachment to Claude |
| `examples/process-graph.schema.json` | Proposed canonical JSON graph contract (illustrative v0.1) |
| `examples/sample-process.json` | Fictional graph with tasks, decision, flow types and data items |
| `examples/sample-variant.json` | Fictional example of non-destructive tailoring |
| `examples/example-with-di.bpmn` | Valid BPMN XML illustrating source BPMN DI coordinates |
| `examples/example-no-di.bpmn` | The same valid XML process without BPMN DI (tests auto-layout) |
| `examples/invalid-xxe.xml` | Deliberately malicious fixture to test safe XML rejection — **do not parse with unsafe XML parser** |
| `examples/example-process-narrative.pdf` and `.txt` | Fictional narrative PDF, with deliberate ambiguity that an AI model must not invent into a full flow |
| `source/Gemini-conversation-extract.docx` and `.txt` | User-provided source and text mirror for simple AI ingestion |

**Mockup note:** The HTML designs support a few simulated selection/edit interactions in browser memory. They do not import documents, call AI or save to a backend; popup messages say so. The PNGs are screenshots of these designs. Use them as visual targets, not as evidence of completed requirements.

**Fixtures note:** `sample-process.json` uses an all-zero SHA-256 placeholder to illustrate the schema; a real import must calculate the actual source hash. The sample PDF contains one page, though the mockups illustrate a fictional, larger source PDF; source locations displayed inside mockups are **illustrative**.

## Recommended architecture in one line

`Text PDF → PyMuPDF → configured AI extraction + evidence → graph validation → ProcessGraph`  
`BPMN XML → secure namespace-aware parser → graph validation → ProcessGraph`  
`ProcessGraph → SQLite baseline/variants → FastAPI → React Flow canvas + inspector + review UI`

BPMN XML can run without external AI. Automatic process inference from natural-language PDF **requires a configured inference service or local model**; no provider is bundled in this design package.

## Design decisions to validate during implementation

The initial stack, test thresholds and supported BPMN subset are proposed, not approved. Validate confidential-file handling, model provider policy, realistic sample processes, deployment model and performance targets before production. The current work is a **requirements and concept handoff**, not a certification or a finished product.
