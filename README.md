# ProcessFlow (proof of concept v0.1)

A local, browser-based tool that turns **BPMN 2.0 XML** exports and **text-based PDF process narratives** into one canonical process graph, shows it as an interactive flowchart, and lets you **add, rename, tailor out and restore step inputs and outputs** in saved variants without touching the imported baseline.

- BPMN XML is parsed deterministically. No AI service or account is needed.
- PDFs are text-extracted locally with PyMuPDF. Drafting a process from a PDF needs a configured model (local Ollama or the Anthropic API). Without one, the app says so and draws nothing.
- Everything is stored on your machine (SQLite + an app-owned folder). The server binds to `127.0.0.1` only.

This is a proof of concept. It is not a BPMN editor, does not execute workflows and makes no claim of full BPMN 2.0 compliance. See `IMPLEMENTATION_REPORT.md` for what is and is not supported, and `DECISIONS.md` for design choices.

## Requirements

- Python 3.11 or newer (developed and tested on 3.13)
- Node.js 20 or newer with npm (tested on Node 22)
- No Docker, no cloud account

## Install and run

### Linux / macOS

```bash
# 1. Backend
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt

# 2. Frontend (build once; the backend serves it)
cd ../frontend
npm ci
npm run build

# 3. Start
cd ../backend
python -m app            # http://127.0.0.1:8000
```

### Windows (PowerShell)

```powershell
# 1. Backend
cd backend
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# 2. Frontend
cd ..\frontend
npm ci
npm run build

# 3. Start
cd ..\backend
python -m app            # http://127.0.0.1:8000
```

If PowerShell blocks `Activate.ps1`, run `Set-ExecutionPolicy -Scope Process RemoteSigned` in that window, or call `.\.venv\Scripts\python.exe -m app` directly. The Windows commands are written from the documented tool behaviour; they have **not** been run on Windows in this PoC (see the report).

Open http://127.0.0.1:8000 and use **Import PDF or BPMN XML**. Sample files are in `fixtures/` (start with `fixtures/bpmn/purchase-with-data.bpmn`).

### Frontend development mode (optional)

Run the backend as above, then in `frontend/` run `npm run dev` and open http://127.0.0.1:5173. Vite proxies `/api` to the backend.

## Configuration

Copy `backend/.env.example` to `backend/.env` and edit. It contains no secrets. Main settings:

| Variable | Default | Meaning |
|---|---|---|
| `PF_DATA_DIR` | `%LOCALAPPDATA%\ProcessFlow` (Windows), `~/Library/Application Support/ProcessFlow` (macOS), `~/.local/share/processflow` (Linux) | SQLite database and imported source files. Back up by copying this folder. |
| `PF_MAX_UPLOAD_MB`, `PF_MAX_XML_MB`, `PF_MAX_PDF_PAGES` | 20, 5, 100 | Upload limits |
| `PF_INFERENCE_PROVIDER` | `none` | `none`, `ollama` or `anthropic` |
| `PF_ALLOW_REMOTE_INFERENCE` | `false` | Must be `true` before any remote provider is used |

## Enabling PDF inference

By default no provider is configured: PDFs are extracted and you can read the text, but no process is drafted. The UI shows "Not configured" and the **Draft process with AI** button is disabled.

**Local model (no data leaves the machine), Ollama:**

1. Install Ollama and pull a model that supports structured JSON output, for example `ollama pull llama3.1:8b` (choose any model your policy allows).
2. In `backend/.env`:
   ```
   PF_INFERENCE_PROVIDER=ollama
   PF_OLLAMA_MODEL=llama3.1:8b
   ```
3. Restart the backend. The sidebar shows the provider as configured.

**Anthropic API (remote: PDF text is sent to Anthropic):**

1. `pip install -r requirements.txt` already includes the `anthropic` package.
2. Set your key in the shell that starts the backend (not in a committed file):
   - Linux/macOS: `export ANTHROPIC_API_KEY=...`
   - PowerShell: `$env:ANTHROPIC_API_KEY = "..."`
3. In `backend/.env`:
   ```
   PF_INFERENCE_PROVIDER=anthropic
   PF_ALLOW_REMOTE_INFERENCE=true
   # optional: PF_ANTHROPIC_MODEL=claude-opus-5-5
   ```
4. Restart. Each drafting request shows a confirmation dialog stating that the extracted text will leave the machine.

Whatever the provider, the result is an **AI-assisted draft**: every step is `needs review`, every quote is checked against the PDF text, fabricated or misplaced quotes are flagged, and ambiguities are listed as issues rather than drawn as steps. Neither adapter has been run against a live model during this PoC; see the report.

## Tests

```bash
# Backend unit + API tests (69 tests)
cd backend && . .venv/bin/activate && python -m pytest

# Frontend type check and unit tests
cd frontend && npm run typecheck && npm test

# Browser end-to-end tests (Chromium via Playwright; starts and restarts the real backend)
cd frontend && npm run build && npx playwright install chromium && npm run e2e
```

On Windows use `.\.venv\Scripts\Activate.ps1` instead of `. .venv/bin/activate`. `npx playwright install chromium` is only needed once per machine.

Other scripts:

- `python scripts/export_schema.py` regenerates `schema/process-graph.schema.json` from the Pydantic models.
- `python scripts/bench_import.py` measures import time of a generated 300-node, ~2 MB BPMN file.

## Repository map

```
backend/app/
  main.py               FastAPI routes, upload handling, state transitions, static UI
  models.py             Canonical contract (Pydantic v2, schema 0.2)
  ingest/xml_safety.py  Hardened XML loading (DTD/XXE rejection, limits)
  ingest/bpmn_parser.py Deterministic BPMN -> ProcessGraph
  ingest/pdf_reader.py  PyMuPDF text, blocks, headings, tables; OCR-needed detection
  ingest/pdf_inference.py  Candidate validation, evidence grounding, review gating
  ai/                   ProcessInferenceAdapter, prompt, Ollama and Anthropic adapters
  domain/               Graph validators, variant tailoring
  store/repository.py   SQLite + source files, immutable baselines, optimistic saves
backend/tests/          pytest suite (fixture-driven)
frontend/src/           React + TypeScript UI (App, FlowCanvas, Inspector, Sidebar, IssuesPanel)
frontend/e2e/           Playwright tests
fixtures/               BPMN, PDF, security and inference test fixtures (all fictional)
schema/                 Generated JSON Schema for ProcessGraph 0.2
docs/handoff/           The original design handoff (input, not implementation)
```

## Data and privacy

- Imported files are copied into `PF_DATA_DIR/sources/` under their SHA-256 and never modified. Baselines and variants live in `PF_DATA_DIR/processflow.sqlite3`. Nothing is deleted automatically; delete the folder to remove everything.
- Logs record ids, sizes, hash prefixes and error codes, not document text (tested).
- With `PF_INFERENCE_PROVIDER=none` or a local Ollama URL, no document content is sent anywhere. With Anthropic, extracted PDF text is sent to the Anthropic API when you confirm a drafting request; retention follows your Anthropic account terms.
