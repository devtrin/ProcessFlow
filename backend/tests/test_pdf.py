"""FR-01, FR-04, FR-05, FR-03 (PDF): text extraction and honest failure modes."""

from __future__ import annotations

import pymupdf
import pytest

from app.ingest.pdf_reader import PdfRejected, extract_pdf

from .conftest import fixture_bytes, upload


def _pdf(builder) -> bytes:
    doc = pymupdf.open()
    builder(doc)
    return doc.tobytes()


def test_t04_page_and_block_anchors():
    pdf = extract_pdf(fixture_bytes("handoff/example-process-narrative.pdf"), max_pages=100)
    assert pdf.page_count == 1
    block = next(b for b in pdf.blocks if "manager reviews the purchase request" in b.text)
    assert block.page == 1 and block.id.startswith("p1b")
    x0, y0, x1, y1 = block.bbox
    assert 0 <= x0 < x1 <= 612 and 0 <= y0 < y1 <= 842
    assert pdf.blocks[0].role == "heading"
    assert "PURCHASE REQUEST HANDLING" in pdf.blocks[0].text
    expected = fixture_bytes("handoff/example-process-narrative.txt").decode()
    for line in ("1. The requester submits a purchase request", "AMBIGUITY TO TEST"):
        assert line in expected and line in pdf.page_text(1)


def test_multi_page_anchors_and_tables():
    def build(doc):
        p1 = doc.new_page()
        p1.insert_text((72, 72), "Step one happens on the first page.", fontsize=11)
        p2 = doc.new_page()
        p2.insert_text((72, 72), "Step two happens on the second page.", fontsize=11)
    pdf = extract_pdf(_pdf(build), max_pages=100)
    assert [b.page for b in pdf.blocks] == [1, 2]
    assert "second page" in pdf.page_text(2) and "second page" not in pdf.page_text(1)


def test_t05_scanned_image_only_pdf_reports_ocr_needed():
    def build(doc):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False)
        pix.set_rect(pix.irect, (200, 200, 200))
        page.insert_image(pymupdf.Rect(72, 72, 472, 272), pixmap=pix)
    with pytest.raises(PdfRejected) as e:
        extract_pdf(_pdf(build), max_pages=100)
    assert e.value.code == "PDF_NO_TEXT_OCR_REQUIRED"
    assert "scanned" in e.value.message and "OCR is not supported" in e.value.message


def test_encrypted_pdf_rejected():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret process text")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    with pytest.raises(PdfRejected) as e:
        extract_pdf(data, max_pages=100)
    assert e.value.code == "PDF_ENCRYPTED"


def test_page_limit_and_corrupt():
    data = _pdf(lambda d: [d.new_page().insert_text((72, 72), f"page {i} text here") for i in range(5)])
    with pytest.raises(PdfRejected) as e:
        extract_pdf(data, max_pages=3)
    assert e.value.code == "PDF_TOO_MANY_PAGES"
    with pytest.raises(PdfRejected) as e:
        extract_pdf(b"%PDF-1.7\n garbage", max_pages=3)
    assert e.value.code in ("PDF_UNREADABLE", "PDF_EMPTY", "PDF_NO_TEXT_OCR_REQUIRED")


def test_t01_pdf_upload_records_hash_and_extraction(client):
    r = upload(client, "handoff/example-process-narrative.pdf")
    assert r.status_code == 201, r.text
    body = r.json()
    import hashlib
    assert body["sha256"] == hashlib.sha256(fixture_bytes("handoff/example-process-narrative.pdf")).hexdigest()
    assert body["status"] == "extracted" and body["processes"] == []
    assert body["extraction_summary"]["page_count"] == 1
    assert body["inference"]["state"] == "not_run"
    ex = client.get(f"/api/v1/imports/{body['id']}/extraction").json()
    assert any("budget reference" in b["text"] for b in ex["blocks"])


def test_scanned_pdf_upload_fails_without_steps(client):
    def build(doc):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
        page.insert_image(pymupdf.Rect(72, 72, 172, 172), pixmap=pix)
    r = upload(client, "scan.pdf", _pdf(build))
    assert r.status_code == 422 and r.json()["code"] == "PDF_NO_TEXT_OCR_REQUIRED"
    rec = client.get(f"/api/v1/imports/{r.json()['details']['import_id']}").json()
    assert rec["status"] == "failed" and rec["process_ids"] == []
