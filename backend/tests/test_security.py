"""NFR-02/03/04/08/16 and FR-03: hostile and malformed input."""

from __future__ import annotations

import logging
import socket

import pytest

from app.ingest.bpmn_parser import BPMN_NS, parse_bpmn
from app.ingest.xml_safety import XmlRejected, parse_untrusted_xml

from .conftest import fixture_bytes, upload

LIMITS = dict(max_bytes=5 << 20, max_depth=64, max_elements=50_000)


@pytest.fixture
def no_network(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("network access attempted during XML parsing")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


@pytest.mark.parametrize("rel", ["handoff/invalid-xxe.xml", "security/billion-laughs.xml", "security/external-dtd.xml"])
def test_dtd_and_entities_rejected_without_network(rel, no_network):
    with pytest.raises(XmlRejected) as e:
        parse_untrusted_xml(fixture_bytes(rel), **LIMITS)
    assert e.value.code == "XML_DTD_FORBIDDEN"


def test_utf16_encoded_xxe_rejected(no_network):
    src = fixture_bytes("handoff/invalid-xxe.xml").decode().replace('<?xml version="1.0"?>', '<?xml version="1.0" encoding="UTF-16"?>')
    with pytest.raises(XmlRejected) as e:
        parse_untrusted_xml(src.encode("utf-16"), **LIMITS)
    assert e.value.code == "XML_DTD_FORBIDDEN"


def test_parser_layer_alone_does_not_expand_entities():
    """Second line of defence: even if the pre-scan were bypassed, lxml is locked down."""
    from lxml import etree

    from app.ingest.xml_safety import make_parser
    root = etree.fromstring(fixture_bytes("handoff/invalid-xxe.xml"), parser=make_parser())
    text = "".join(root.itertext())
    assert "root:" not in text  # /etc/passwd content was not read


def test_depth_limit():
    deep = "<a>" * 200 + "</a>" * 200
    data = f'<definitions xmlns="{BPMN_NS}">{deep}</definitions>'.encode()
    with pytest.raises(XmlRejected) as e:
        parse_untrusted_xml(data, max_bytes=1 << 20, max_depth=64, max_elements=50_000)
    assert e.value.code in ("XML_TOO_DEEP", "XML_MALFORMED")


def test_element_count_and_size_limits():
    many = "<x/>" * 2000
    data = f'<definitions xmlns="{BPMN_NS}">{many}</definitions>'.encode()
    with pytest.raises(XmlRejected) as e:
        parse_untrusted_xml(data, max_bytes=1 << 20, max_depth=64, max_elements=1000)
    assert e.value.code == "XML_TOO_MANY_ELEMENTS"
    with pytest.raises(XmlRejected) as e:
        parse_untrusted_xml(data, max_bytes=100, max_depth=64, max_elements=50_000)
    assert e.value.code == "XML_TOO_LARGE"


def test_malformed_and_non_bpmn():
    with pytest.raises(XmlRejected) as e:
        parse_bpmn(fixture_bytes("security/malformed.bpmn"), filename="m", sha256="0" * 64, document_id="d", **LIMITS)
    assert e.value.code == "XML_MALFORMED"
    with pytest.raises(XmlRejected) as e:
        parse_bpmn(b"<html><body>hi</body></html>", filename="m", sha256="0" * 64, document_id="d", **LIMITS)
    assert e.value.code == "NOT_BPMN"


@pytest.mark.parametrize("name,data,status,code", [
    ("tool.exe", b"MZ....", 415, "UNSUPPORTED_FILE_TYPE"),
    ("empty.bpmn", b"", 422, "FILE_EMPTY"),
    ("fake.pdf", b"<definitions/>", 422, "SIGNATURE_MISMATCH"),
    ("fake.bpmn", b"%PDF-1.7 not xml", 422, "SIGNATURE_MISMATCH"),
    ("xxe.xml", None, 422, "XML_DTD_FORBIDDEN"),
])
def test_t03_upload_rejections_leave_no_project(client, name, data, status, code):
    body = data if data is not None else fixture_bytes("handoff/invalid-xxe.xml")
    r = upload(client, name, body)
    assert r.status_code == status and r.json()["code"] == code
    rec = client.get(f"/api/v1/imports/{r.json()['details']['import_id']}").json()
    assert rec["status"] == "failed" and rec["process_ids"] == [] and rec["processes"] == []
    # NFR-04: server keeps working and imports a valid file afterwards.
    ok = upload(client, "handoff/example-no-di.bpmn")
    assert ok.status_code == 201


def test_upload_size_limit(make_client):
    c = make_client(max_upload_bytes=1024)
    r = upload(c, "big.bpmn", b"<" + b"a" * 4096)
    assert r.status_code == 413 and r.json()["code"] == "FILE_TOO_LARGE"


def test_failed_import_can_be_dismissed(client):
    r = upload(client, "tool.exe", b"x")
    iid = r.json()["details"]["import_id"]
    assert client.delete(f"/api/v1/imports/{iid}").status_code == 204
    assert client.get(f"/api/v1/imports/{iid}").status_code == 404


def test_t08_html_labels_stored_verbatim_as_data(client):
    """Labels are data. The API returns them verbatim inside JSON; the UI renders them as text
    (React escaping; checked in the browser e2e test). No server-side HTML is produced."""
    r = upload(client, "bpmn/xss-labels.bpmn")
    assert r.status_code == 201
    g = client.get(f"/api/v1/processes/{r.json()['process_ids'][0]}")
    assert g.headers["content-type"].startswith("application/json")
    labels = {n["id"]: n["label"] for n in g.json()["baseline"]["nodes"]}
    assert labels["X_Task"] == "<script>window.__pfXss=2</script>Task"
    assert "default-src 'self'" in g.headers["content-security-policy"]
    assert g.headers["x-content-type-options"] == "nosniff"


def test_nfr16_logs_do_not_contain_document_text(client, caplog):
    marker = "CONFIDENTIAL-MARKER-7f3a"
    caplog.set_level(logging.DEBUG)
    bad = f'<?xml version="1.0"?><definitions xmlns="{BPMN_NS}"><process id="p"><task name="{marker}"></process>'
    upload(client, "bad.bpmn", bad.encode())
    good = fixture_bytes("handoff/example-no-di.bpmn").replace(b"Review request", marker.encode())
    upload(client, "good.bpmn", good)
    upload(client, "handoff/example-process-narrative.pdf")
    assert marker not in caplog.text
    assert "purchase request" not in caplog.text.lower()


def test_source_download_is_not_rendered_inline(client):
    r = upload(client, "handoff/example-no-di.bpmn").json()
    s = client.get(f"/api/v1/imports/{r['id']}/source")
    assert s.headers["content-type"] == "application/octet-stream"
    assert s.headers["content-disposition"].startswith("attachment")
    assert s.content == fixture_bytes("handoff/example-no-di.bpmn")
