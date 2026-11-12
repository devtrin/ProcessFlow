"""Hardened XML loading for untrusted BPMN files.

Defence in depth, following the OWASP XXE Prevention Cheat Sheet:
1. Byte-level pre-scan rejects any DOCTYPE / ENTITY declaration before parsing.
2. lxml parser with DTD loading, entity resolution and network access disabled,
   and huge_tree off (libxml2's own depth/size guards stay active).
3. Post-parse check that no DTD was recorded and no entity nodes exist.
4. Explicit limits on byte size, element depth and element count.

lxml is used rather than ``defusedxml`` because element line numbers
(``sourceline``) are needed for provenance. defusedxml's lxml shim is deprecated.
"""

from __future__ import annotations

import codecs
import re

from lxml import etree


class XmlRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_DECL_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY|ELEMENT|ATTLIST)", re.IGNORECASE)


def _decoded_for_scan(data: bytes) -> bytes:
    """Return a UTF-8 view for scanning, so UTF-16/32 encoded DTDs are caught too."""
    for bom, enc in (
        (codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ):
        if data.startswith(bom):
            return data[len(bom):].decode(enc, errors="replace").encode("utf-8")
    # BOM-less UTF-16: every other byte NUL near the start
    head = data[:200]
    if head and head.count(b"\x00") > len(head) // 3:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return data.decode(enc).encode("utf-8")
            except UnicodeDecodeError:
                continue
    return data


def make_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
        recover=False,
    )


def parse_untrusted_xml(
    data: bytes, *, max_bytes: int, max_depth: int, max_elements: int
) -> etree._Element:
    if not data or not data.strip():
        raise XmlRejected("XML_EMPTY", "The XML file is empty.")
    if len(data) > max_bytes:
        raise XmlRejected(
            "XML_TOO_LARGE", f"XML file is {len(data)} bytes; the limit is {max_bytes} bytes."
        )
    if _DECL_RE.search(_decoded_for_scan(data)):
        raise XmlRejected(
            "XML_DTD_FORBIDDEN",
            "The XML contains a DOCTYPE or entity declaration. DTDs and entities are "
            "rejected to prevent XXE and entity-expansion attacks; BPMN files do not need them.",
        )
    try:
        root = etree.fromstring(data, parser=make_parser())
    except etree.XMLSyntaxError as exc:
        # exc.msg holds the libxml2 message with line/column but no file content.
        raise XmlRejected("XML_MALFORMED", f"The file is not well-formed XML: {exc.msg}") from None

    tree = root.getroottree()
    if tree.docinfo.doctype or tree.docinfo.internalDTD is not None or tree.docinfo.externalDTD is not None:
        raise XmlRejected("XML_DTD_FORBIDDEN", "The XML declares a DTD, which is not allowed.")

    count = 0
    stack: list[tuple[etree._Element, int]] = [(root, 1)]
    while stack:
        el, depth = stack.pop()
        if isinstance(el, etree._Entity):  # pragma: no cover - blocked earlier
            raise XmlRejected("XML_ENTITY_FORBIDDEN", "Entity references are not allowed.")
        count += 1
        if depth > max_depth:
            raise XmlRejected(
                "XML_TOO_DEEP", f"XML nesting exceeds the depth limit of {max_depth}."
            )
        if count > max_elements:
            raise XmlRejected(
                "XML_TOO_MANY_ELEMENTS", f"XML has more than {max_elements} elements."
            )
        for child in el:
            stack.append((child, depth + 1))
    return root
