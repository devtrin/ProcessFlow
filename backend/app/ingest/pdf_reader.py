"""Text-PDF extraction with PyMuPDF: pages, positioned blocks, headings, simple tables.

No OCR. Encrypted, image-only and text-less PDFs are reported as such; nothing
is invented to fill the gap (FR-05).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import pymupdf

from ..models import Issue, Severity


class PdfRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class TextBlock:
    id: str
    page: int
    bbox: list[float]
    text: str
    role: str = "body"  # body | heading | table

    def to_dict(self) -> dict:
        return {"id": self.id, "page": self.page, "bbox": self.bbox, "text": self.text, "role": self.role}


@dataclass
class ExtractedPdf:
    page_count: int
    blocks: list[TextBlock] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    pages_without_text: list[int] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def page_text(self, page: int) -> str:
        return "\n".join(b.text for b in self.blocks if b.page == page and b.role != "table")

    @property
    def char_count(self) -> int:
        return sum(len(b.text) for b in self.blocks)

    def to_dict(self) -> dict:
        return {
            "page_count": self.page_count,
            "blocks": [b.to_dict() for b in self.blocks],
            "tables": self.tables,
            "pages_without_text": self.pages_without_text,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExtractedPdf":
        return cls(
            page_count=d["page_count"],
            blocks=[TextBlock(**b) for b in d["blocks"]],
            tables=d.get("tables", []),
            pages_without_text=d.get("pages_without_text", []),
            metadata=d.get("metadata", {}),
        )


def _round(r) -> list[float]:
    return [round(float(v), 1) for v in r]


def extract_pdf(data: bytes, *, max_pages: int, min_chars_per_page: int = 20, find_tables: bool = True) -> ExtractedPdf:
    if not data.startswith(b"%PDF-"):
        raise PdfRejected("PDF_SIGNATURE", "The file does not start with a PDF signature (%PDF-).")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several types for damaged files
        raise PdfRejected("PDF_UNREADABLE", f"The PDF could not be opened: {type(exc).__name__}.") from None
    with doc:
        if doc.needs_pass or doc.is_encrypted:
            raise PdfRejected("PDF_ENCRYPTED",
                              "The PDF is password-protected. Remove the password and import it again.")
        if doc.page_count == 0:
            raise PdfRejected("PDF_EMPTY", "The PDF has no pages.")
        if doc.page_count > max_pages:
            raise PdfRejected("PDF_TOO_MANY_PAGES",
                              f"The PDF has {doc.page_count} pages; the limit is {max_pages}.")
        out = ExtractedPdf(page_count=doc.page_count,
                           metadata={k: v for k, v in (doc.metadata or {}).items() if k in ("title", "producer", "format") and v})
        image_pages: list[int] = []
        for pno, page in enumerate(doc, start=1):
            info = page.get_text("dict", sort=True)
            sizes: list[float] = []
            raw_blocks = []
            for b in info.get("blocks", []):
                if b.get("type") != 0:
                    continue
                lines = []
                block_sizes = []
                bold = True
                for ln in b.get("lines", []):
                    spans = ln.get("spans", [])
                    lines.append("".join(s.get("text", "") for s in spans))
                    for s in spans:
                        if s.get("text", "").strip():
                            block_sizes.append(s.get("size", 0))
                            bold = bold and bool(s.get("flags", 0) & 16)
                text = "\n".join(l.rstrip() for l in lines).strip()
                if not text:
                    continue
                sizes.extend(block_sizes)
                raw_blocks.append((b["bbox"], text, max(block_sizes or [0]), bold and bool(block_sizes)))
            median = statistics.median(sizes) if sizes else 0
            page_chars = 0
            for i, (bbox, text, size, bold) in enumerate(raw_blocks):
                role = "heading" if (median and size >= median * 1.2 and len(text) < 160) or (bold and len(text) < 120) else "body"
                out.blocks.append(TextBlock(id=f"p{pno}b{i}", page=pno, bbox=_round(bbox), text=text, role=role))
                page_chars += len(text)
            if page_chars < min_chars_per_page:
                out.pages_without_text.append(pno)
                if page.get_images(full=False):
                    image_pages.append(pno)
            if find_tables:
                try:
                    for ti, tab in enumerate(page.find_tables().tables):
                        rows = tab.extract()
                        if rows and len(rows) >= 2:
                            out.tables.append({"id": f"p{pno}t{ti}", "page": pno, "bbox": _round(tab.bbox),
                                               "rows": [[(c or "").strip() for c in r] for r in rows]})
                except Exception:  # table detection is best-effort
                    out.issues.append(Issue(code="PDF_TABLE_DETECTION_FAILED", severity=Severity.info,
                                            message=f"Table detection failed on page {pno}; text blocks are still available."))
        if out.char_count < min_chars_per_page:
            msg = ("The PDF has no extractable text layer" +
                   (" and contains page images, so it is probably scanned" if image_pages else "") +
                   ". OCR is not supported in this version; no process steps were generated.")
            raise PdfRejected("PDF_NO_TEXT_OCR_REQUIRED", msg)
        if out.pages_without_text:
            out.issues.append(Issue(
                code="PDF_PAGES_WITHOUT_TEXT", severity=Severity.warning,
                message=f"Pages {', '.join(map(str, out.pages_without_text))} have little or no extractable text "
                        f"({'image content present, ' if image_pages else ''}OCR not supported). Their content is not analysed."))
        out.issues.append(Issue(
            code="PDF_READING_ORDER", severity=Severity.info,
            message="Text order follows PyMuPDF's positional sort. Multi-column layouts can be read in the wrong order; "
                    "check evidence quotes against the page."))
        return out
