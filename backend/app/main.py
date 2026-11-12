"""ProcessFlow FastAPI application.

Run from the ``backend`` directory:  ``python -m app`` (binds 127.0.0.1:8000).
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .ai.base import InferenceError, ProcessInferenceAdapter
from .ai.registry import build_adapter, provider_status
from .config import Settings, load_dotenv, load_settings
from .domain.tailoring import ChangeRejected, resolve, summarise_diff, validate_changes
from .domain.validators import validate_graph
from .ingest.bpmn_parser import parse_bpmn
from .ingest.pdf_inference import infer_process
from .ingest.pdf_reader import ExtractedPdf, PdfRejected, extract_pdf
from .ingest.xml_safety import XmlRejected
from .models import (
    SCHEMA_VERSION,
    ImportRecord,
    ImportStatus,
    Issue,
    ReviewDecision,
    ReviewState,
    Severity,
    SourceKind,
    VariantChanges,
    utcnow,
)
from .store.repository import NotFound, Repository, StaleVersion, new_id

log = logging.getLogger("processflow")

ALLOWED_EXT = {".pdf": SourceKind.pdf, ".bpmn": SourceKind.bpmn_xml, ".xml": SourceKind.bpmn_xml}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, details: dict | None = None,
                 related: list[str] | None = None, recovery: str | None = None):
        self.status, self.code, self.message = status, code, message
        self.details, self.related, self.recovery = details or {}, related or [], recovery


def _err(status, code, message, **kw):
    return ApiError(status, code, message, **kw)


class InferRequest(BaseModel):
    acknowledge_remote: bool = False


class ReviewRequest(BaseModel):
    element_id: str = Field(min_length=1)
    state: Literal["verified", "needs_clarification", "needs_review"]
    note: str = Field(default="", max_length=2000)


class CreateVariantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class SaveVariantRequest(BaseModel):
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    changes: VariantChanges


def create_app(settings: Settings | None = None, adapter: ProcessInferenceAdapter | None | Literal["auto"] = "auto") -> FastAPI:
    settings = settings or load_settings()
    repo = Repository(settings.data_dir)
    inference = build_adapter(settings) if adapter == "auto" else adapter

    app = FastAPI(title="ProcessFlow", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings, app.state.repo, app.state.adapter = settings, repo, inference
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        return resp

    @app.exception_handler(ApiError)
    async def api_error(_req: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={
            "code": exc.code, "message": exc.message, "details": exc.details,
            "related_element_ids": exc.related, "recovery_action": exc.recovery})

    @app.exception_handler(Exception)
    async def unhandled(_req: Request, exc: Exception):
        # Never echo file content or exception text that may include it.
        log.exception("unhandled error type=%s", type(exc).__name__)
        return JSONResponse(status_code=500, content={
            "code": "INTERNAL_ERROR", "message": "Unexpected server error. The request was not completed.",
            "details": {}, "related_element_ids": [], "recovery_action": "Check the backend log; retry."})

    # ------------------------------------------------------------------ info
    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "schema_version": SCHEMA_VERSION, "data_dir": str(settings.data_dir)}

    @app.get("/api/v1/provider")
    def provider():
        return provider_status(inference, settings)

    # ------------------------------------------------------------------ imports
    @app.post("/api/v1/imports", status_code=201)
    async def create_import(file: UploadFile = File(...)):
        filename = Path(file.filename or "upload").name[:200]
        ext = Path(filename).suffix.lower()
        now = utcnow()
        rec = ImportRecord(id=new_id("imp"), filename=filename, kind=ALLOWED_EXT.get(ext, SourceKind.bpmn_xml),
                           size_bytes=0, status=ImportStatus.uploaded, created_at=now, updated_at=now)

        def fail(code: str, message: str, status: int = 422):
            rec.status = ImportStatus.failed
            rec.issues.append(Issue(code=code, severity=Severity.error, message=message))
            repo.save_import(rec)  # diagnostic record only: no source file, no baseline
            log.info("import rejected id=%s code=%s size=%d", rec.id, code, rec.size_bytes)
            raise _err(status, code, message, details={"import_id": rec.id},
                       recovery="Check the file type and content, then import again.")

        if ext not in ALLOWED_EXT:
            fail("UNSUPPORTED_FILE_TYPE", f"'{ext or '(none)'}' files are not supported. Use .pdf, .bpmn or .xml.", 415)
        limit = settings.max_upload_bytes
        data = await file.read(limit + 1)
        rec.size_bytes = len(data)
        if len(data) > limit:
            fail("FILE_TOO_LARGE", f"The file exceeds the {limit // (1024 * 1024)} MB upload limit.", 413)
        if not data:
            fail("FILE_EMPTY", "The file is empty.")
        rec.sha256 = hashlib.sha256(data).hexdigest()
        kind = ALLOWED_EXT[ext]
        if kind == SourceKind.pdf and not data.startswith(b"%PDF-"):
            fail("SIGNATURE_MISMATCH", "The file has a .pdf extension but is not a PDF.")
        if kind == SourceKind.bpmn_xml and data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] not in (b"<", b"\xff", b"\xfe", b"\x00"):
            fail("SIGNATURE_MISMATCH", "The file has an XML extension but does not contain XML.")

        if kind == SourceKind.bpmn_xml:
            try:
                graphs, doc_issues = parse_bpmn(data, filename=filename, sha256=rec.sha256, document_id=rec.id,
                                                max_bytes=settings.max_xml_bytes, max_depth=settings.max_xml_depth,
                                                max_elements=settings.max_xml_elements)
            except XmlRejected as exc:
                fail(exc.code, exc.message)
            for g in graphs:
                g.issues = g.issues + validate_graph(g)
            rec.issues = doc_issues
            if not any(g.nodes for g in graphs):
                rec.status = ImportStatus.failed
                repo.save_import(rec)
                raise _err(422, "NO_PROCESS_LOGIC", "; ".join(i.message for i in doc_issues if i.severity == Severity.error)
                           or "No process content found.", details={"import_id": rec.id})
            rec.status = ImportStatus.ready
            rec.process_ids = [g.process_id for g in graphs]
            path = repo.store_source(data, rec.sha256, ext)
            repo.import_with_baselines(rec, graphs, path)
            log.info("bpmn imported id=%s processes=%d size=%d sha=%s", rec.id, len(graphs), len(data), rec.sha256[:12])
            return _import_view(rec)

        # PDF: uploaded -> extracting -> extracted | failed
        rec.status = ImportStatus.extracting
        try:
            pdf = extract_pdf(data, max_pages=settings.max_pdf_pages, min_chars_per_page=settings.min_text_chars_per_page)
        except PdfRejected as exc:
            fail(exc.code, exc.message)
        rec.status = ImportStatus.extracted
        rec.issues = pdf.issues
        rec.extraction_summary = {"page_count": pdf.page_count, "block_count": len(pdf.blocks),
                                  "table_count": len(pdf.tables), "char_count": pdf.char_count,
                                  "pages_without_text": pdf.pages_without_text}
        rec.inference = {"state": "not_run", "provider": provider_status(inference, settings)}
        path = repo.store_source(data, rec.sha256, ext)
        repo.import_with_baselines(rec, [], path, extraction=pdf.to_dict())
        log.info("pdf extracted id=%s pages=%d blocks=%d", rec.id, pdf.page_count, len(pdf.blocks))
        return _import_view(rec)

    def _import_view(rec: ImportRecord) -> dict:
        return {**rec.model_dump(mode="json"), "processes": repo.list_baselines(rec.id)}

    @app.get("/api/v1/imports")
    def list_imports():
        return [_import_view(r) for r in repo.list_imports()]

    @app.get("/api/v1/imports/{import_id}")
    def get_import(import_id: str):
        return _import_view(_get(repo.get_import, import_id))

    @app.delete("/api/v1/imports/{import_id}", status_code=204)
    def delete_import(import_id: str):
        _get(repo.get_import, import_id)
        try:
            repo.delete_failed_import(import_id)
        except ValueError as exc:
            raise _err(409, "IMPORT_NOT_REMOVABLE", str(exc))
        return Response(status_code=204)

    @app.get("/api/v1/imports/{import_id}/extraction")
    def get_extraction(import_id: str):
        return _get(repo.get_extraction, import_id)

    @app.get("/api/v1/imports/{import_id}/source")
    def get_source(import_id: str):
        data, rec = _get(repo.source_bytes, import_id)
        return Response(content=data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{rec.sha256}{Path(rec.filename).suffix}"'})

    @app.post("/api/v1/imports/{import_id}/infer")
    def run_inference(import_id: str, body: InferRequest):
        rec = _get(repo.get_import, import_id)
        if rec.kind != SourceKind.pdf:
            raise _err(400, "NOT_A_PDF", "Inference applies to PDF imports only; BPMN XML is parsed deterministically.")
        status = provider_status(inference, settings)
        if inference is None:
            raise _err(409, "PROVIDER_NOT_CONFIGURED", status["message"],
                       recovery="Configure PF_INFERENCE_PROVIDER (see README), restart the backend, then retry.")
        if not status["ready"]:
            raise _err(409, "PROVIDER_NOT_READY", status["message"])
        if inference.is_remote and not body.acknowledge_remote:
            raise _err(409, "REMOTE_ACK_REQUIRED",
                       f"This sends the extracted PDF text to a remote provider ({inference.name}, {inference.model}). "
                       "Confirm in the UI to continue.")
        pdf = ExtractedPdf.from_dict(repo.get_extraction(import_id))
        prev = rec.status
        rec.status = ImportStatus.extracting
        rec.inference = {"state": "running", "provider": status, "started_at": utcnow()}
        repo.save_import(rec)
        n = sum(1 for p in rec.process_ids) + 1
        try:
            graph = infer_process(inference, pdf, filename=rec.filename, sha256=rec.sha256, document_id=rec.id,
                                  process_id=f"{rec.id}__ai{n}", max_chars=settings.max_inference_chars)
        except InferenceError as exc:
            rec.status = prev if prev != ImportStatus.extracting else ImportStatus.extracted
            rec.inference = {"state": "failed", "provider": status, "error_code": exc.code, "error": exc.message,
                             "finished_at": utcnow()}
            repo.save_import(rec)
            log.info("inference failed id=%s code=%s", rec.id, exc.code)
            raise _err(502 if exc.code.startswith("PROVIDER") else 422, exc.code, exc.message,
                       details={"import_id": rec.id})
        rec.status = ImportStatus.needs_review
        rec.process_ids = [*rec.process_ids, graph.process_id]
        rec.inference = {"state": "completed", "provider": status, "finished_at": utcnow(),
                         "model": graph.provenance.get("inference", {}).get("model"),
                         "attempts": graph.provenance.get("inference", {}).get("attempts")}
        repo.import_with_baselines(rec, [graph], None)
        log.info("inference completed id=%s nodes=%d issues=%d", rec.id, len(graph.nodes), len(graph.issues))
        return _import_view(rec)

    # ------------------------------------------------------------------ processes
    @app.get("/api/v1/processes/{process_id}")
    def get_process(process_id: str):
        graph, gsha = _get(repo.get_baseline, process_id)
        return {"baseline": graph.model_dump(mode="json"), "baseline_sha256": gsha,
                "reviews": [r.model_dump(mode="json") for r in repo.list_reviews(process_id)],
                "variants": [_variant_summary(v) for v in repo.list_variants(process_id)]}

    @app.post("/api/v1/processes/{process_id}/reviews")
    def set_review(process_id: str, body: ReviewRequest):
        graph, _ = _get(repo.get_baseline, process_id)
        ids = {n.id for n in graph.nodes} | {e.id for e in graph.edges}
        if body.element_id not in ids:
            raise _err(404, "ELEMENT_NOT_FOUND", f"No node or edge '{body.element_id}' in this process.")
        d = ReviewDecision(process_id=process_id, element_id=body.element_id, state=ReviewState(body.state),
                           note=body.note, at=utcnow())
        repo.set_review(d)
        return d.model_dump(mode="json")

    @app.get("/api/v1/processes/{process_id}/export")
    def export_baseline(process_id: str):
        graph, gsha = _get(repo.get_baseline, process_id)
        rec = repo.get_import(graph.source.document_id) if graph.source.document_id else None
        payload = {"export_type": "processflow.baseline", "schema_version": SCHEMA_VERSION, "exported_at": utcnow(),
                   "note": "ProcessFlow canonical JSON. This is not BPMN XML and makes no round-trip claim.",
                   "import": rec.model_dump(mode="json") if rec else None, "baseline_sha256": gsha,
                   "baseline": graph.model_dump(mode="json"),
                   "reviews": [r.model_dump(mode="json") for r in repo.list_reviews(process_id)]}
        return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="{process_id}-baseline.json"'})

    # ------------------------------------------------------------------ variants
    def _variant_summary(v):
        return {"id": v.id, "name": v.name, "version": v.version, "updated_at": v.updated_at}

    @app.post("/api/v1/processes/{process_id}/variants", status_code=201)
    def create_variant(process_id: str, body: CreateVariantRequest):
        _get(repo.get_baseline, process_id)
        return repo.create_variant(process_id, body.name.strip()).model_dump(mode="json")

    @app.get("/api/v1/variants/{variant_id}")
    def get_variant(variant_id: str):
        v = _get(repo.get_variant, variant_id)
        graph, gsha = repo.get_baseline(v.process_id)
        return {"variant": v.model_dump(mode="json"), "resolved": resolve(graph, v.changes).model_dump(mode="json"),
                "baseline_unchanged": gsha == v.baseline_sha256}

    @app.put("/api/v1/variants/{variant_id}")
    def save_variant(variant_id: str, body: SaveVariantRequest):
        v = _get(repo.get_variant, variant_id)
        graph, _ = repo.get_baseline(v.process_id)
        try:
            clean = validate_changes(graph, body.changes)
        except ChangeRejected as exc:
            raise _err(422, "INVALID_CHANGE", exc.message)
        summary = summarise_diff(v.changes, clean, graph)
        try:
            saved = repo.save_variant(variant_id, expected_version=body.expected_version, name=body.name.strip(),
                                      changes=clean, summary=summary)
        except StaleVersion as exc:
            raise _err(409, "STALE_VERSION",
                       f"This variant was saved elsewhere (now version {exc.current}); your copy is version "
                       f"{body.expected_version}. Reload to see the latest, then reapply your edits.",
                       details={"current_version": exc.current}, recovery="Reload the variant.")
        return {"variant": saved.model_dump(mode="json"), "resolved": resolve(graph, saved.changes).model_dump(mode="json")}

    @app.get("/api/v1/variants/{variant_id}/export")
    def export_variant(variant_id: str):
        v = _get(repo.get_variant, variant_id)
        graph, gsha = repo.get_baseline(v.process_id)
        rec = repo.get_import(graph.source.document_id) if graph.source.document_id else None
        payload = {"export_type": "processflow.variant", "schema_version": SCHEMA_VERSION, "exported_at": utcnow(),
                   "note": "ProcessFlow canonical JSON. This is not BPMN XML and makes no round-trip claim.",
                   "import": rec.model_dump(mode="json") if rec else None,
                   "source": graph.source.model_dump(mode="json"), "baseline_sha256": gsha,
                   "baseline_unchanged": gsha == v.baseline_sha256, "variant": v.model_dump(mode="json"),
                   "baseline": graph.model_dump(mode="json"),
                   "resolved": resolve(graph, v.changes).model_dump(mode="json"),
                   "reviews": [r.model_dump(mode="json") for r in repo.list_reviews(v.process_id)]}
        return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="{v.id}-v{v.version}.json"'})

    def _get(fn, key):
        try:
            return fn(key)
        except NotFound:
            raise _err(404, "NOT_FOUND", f"'{key}' was not found.")

    # ------------------------------------------------------------------ static frontend
    dist = Path(os.environ.get("PF_FRONTEND_DIST") or Path(__file__).resolve().parents[2] / "frontend" / "dist")
    if dist.is_dir():
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise _err(404, "NOT_FOUND", "Unknown API route.")
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


def _app_from_env() -> FastAPI:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    logging.basicConfig(level=os.environ.get("PF_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
