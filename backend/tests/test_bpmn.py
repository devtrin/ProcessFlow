"""BPMN XML parsing: FR-02, FR-10..FR-15, FR-24 and the 'no I/O from sequence flows' rule."""

from __future__ import annotations

import re

import pytest

from app.ingest.bpmn_parser import BPMN_NS, parse_bpmn
from app.models import EdgeKind, NodeKind, Severity

from .conftest import fixture_bytes, upload

LIMITS = dict(max_bytes=5 << 20, max_depth=64, max_elements=50_000)


def parse(data: bytes, doc="doc"):
    return parse_bpmn(data, filename="f.bpmn", sha256="0" * 64, document_id=doc, **LIMITS)


def semantics(g):
    """Layout-independent view of a graph for equality checks."""
    return (
        g.title,
        sorted((n.id, n.kind.value, n.label, n.lane_id) for n in g.nodes),
        sorted((e.id, e.source, e.target, e.kind.value, e.label, e.is_default) for e in g.edges),
        sorted((lane.id, lane.name, tuple(lane.node_ids)) for lane in g.lanes),
    )


def test_t11_expected_kinds_ids_and_labels():
    graphs, issues = parse(fixture_bytes("handoff/example-no-di.bpmn"))
    assert issues == []
    [g] = graphs
    kinds = {n.id: n.kind for n in g.nodes}
    assert kinds == {
        "Start_1": NodeKind.start_event, "Task_Review": NodeKind.task,
        "Gateway_Approved": NodeKind.exclusive_gateway, "Task_Approve": NodeKind.task,
        "Task_Reject": NodeKind.task, "End_1": NodeKind.end_event,
    }
    edges = {e.id: e for e in g.edges}
    assert edges["F3"].label == "Yes" and edges["F4"].label == "No"
    assert edges["F4"].is_default and not edges["F3"].is_default
    assert all(e.kind == EdgeKind.sequence for e in g.edges)
    # Every element keeps XML provenance with the BPMN id and a line number.
    for n in g.nodes:
        assert n.source_id == n.id and n.source_refs[0].element_id == n.id and n.source_refs[0].line
    assert g.source.sha256 == "0" * 64 and g.source.original_id == "Process_Purchase"


def test_t14_with_and_without_di_have_equal_semantics():
    [a], _ = parse(fixture_bytes("handoff/example-no-di.bpmn"))
    [b], _ = parse(fixture_bytes("handoff/example-with-di.bpmn"))
    assert semantics(a) == semantics(b)
    assert not a.has_source_layout and all(n.layout is None for n in a.nodes)
    assert b.has_source_layout and b.nodes[0].layout.x == 120 and b.nodes[0].layout.source == "bpmndi"


def test_no_io_items_fabricated_from_sequence_flows():
    [g], _ = parse(fixture_bytes("handoff/example-no-di.bpmn"))
    assert all(not n.inputs and not n.outputs for n in g.nodes)


@pytest.mark.parametrize("prefix", ["bpmn", "bpmn2", "semantic", None])
def test_t10_prefix_independent(prefix):
    src = fixture_bytes("handoff/example-no-di.bpmn").decode()
    if prefix:
        src = src.replace(f'xmlns="{BPMN_NS}"', f'xmlns:{prefix}="{BPMN_NS}"')
        src = re.sub(r"<(/?)(?!\?|bpmndi:|dc:|di:)([A-Za-z]+)", rf"<\1{prefix}:\2", src)
    [g], _ = parse(src.encode())
    [ref], _ = parse(fixture_bytes("handoff/example-no-di.bpmn"))
    assert semantics(g) == semantics(ref)


def test_t12_lanes_preserved():
    [g], _ = parse(fixture_bytes("handoff/example-no-di.bpmn"))
    lanes = {lane.name: set(lane.node_ids) for lane in g.lanes}
    assert lanes == {"Requester": {"Start_1", "End_1"},
                     "Manager": {"Task_Review", "Gateway_Approved", "Task_Approve", "Task_Reject"}}
    assert g.participants[0].name == "Operations"


def test_data_associations_become_source_explicit_items():
    graphs, _ = parse(fixture_bytes("bpmn/purchase-with-data.bpmn"))
    g = next(x for x in graphs if x.view == "process")
    review = next(n for n in g.nodes if n.id == "Task_Review")
    assert [i.name for i in review.inputs] == ["Purchase request", "Budget reference"]
    assert [o.name for o in review.outputs] == ["Review result"]
    assert all(i.origin.value == "source_explicit" for i in review.inputs)
    assert review.inputs[0].source_refs[0].element_id == "DIA_Request"
    order = next(n for n in g.nodes if n.id == "Task_Order")
    assert [o.name for o in order.outputs] == ["Purchase ledger"]  # data store reference


def test_t13_unsupported_constructs_are_visible():
    graphs, _ = parse(fixture_bytes("bpmn/purchase-with-data.bpmn"))
    g = next(x for x in graphs if x.view == "process")
    codes = {i.code for i in g.issues}
    assert {"UNSUPPORTED_SEMANTICS", "EVENT_TRIGGER_NOT_INTERPRETED", "SUBPROCESS_COLLAPSED",
            "VENDOR_EXTENSIONS_IGNORED", "ARTIFACTS_NOT_RENDERED"} <= codes
    nodes = {n.id: n for n in g.nodes}
    assert nodes["GW_Complex"].kind == NodeKind.complex_gateway
    assert nodes["Timer_Escalate"].attributes["event_definitions"] == ["timerEventDefinition"]
    assert nodes["Sub_Receive"].kind == NodeKind.subprocess
    assert nodes["Sub_Receive"].attributes["collapsed_child_count"] == 3
    assert "Sub_Check" not in nodes  # collapsed, not flattened into the parent flow
    att = [e for e in g.edges if e.kind == EdgeKind.boundary_attachment]
    assert [(e.source, e.target) for e in att] == [("Task_Review", "Timer_Escalate")]
    cond = next(e for e in g.edges if e.id == "SF_Yes")
    assert cond.condition_expression == "${approved == true}"


def test_message_flows_stay_distinct_from_sequence_flows():
    graphs, _ = parse(fixture_bytes("bpmn/purchase-with-data.bpmn"))
    proc = next(x for x in graphs if x.view == "process")
    collab = next(x for x in graphs if x.view == "collaboration")
    assert not any(e.kind == EdgeKind.message for e in proc.edges)
    msgs = {e.id: e for e in collab.edges if e.kind == EdgeKind.message}
    assert set(msgs) == {"MF_Order", "MF_Confirm"}
    assert msgs["MF_Order"].target == "Part_Supplier"
    pool = next(n for n in collab.nodes if n.id == "Part_Supplier")
    assert pool.attributes["black_box_pool"] is True


def test_t24_multi_process_not_merged():
    graphs, _ = parse(fixture_bytes("bpmn/multi-process.bpmn"))
    procs = [g for g in graphs if g.view == "process"]
    assert [g.title for g in procs] == ["Buyer process", "Seller process"]
    buyer_ids = {n.id for n in procs[0].nodes}
    assert buyer_ids == {"B_Start", "B_Send", "B_Receive", "B_End"}
    assert all(e.source in buyer_ids and e.target in buyer_ids for e in procs[0].edges)
    collab = next(g for g in graphs if g.view == "collaboration")
    assert {e.id for e in collab.edges if e.kind == EdgeKind.message} == {"M_Order", "M_Invoice"}
    assert {n.participant_id for n in collab.nodes} == {"P_Buyer", "P_Seller"}
    seller = procs[1]
    assert sum(n.kind == NodeKind.parallel_gateway for n in seller.nodes) == 2


def test_t09_broken_refs_and_duplicate_ids_reported():
    graphs, doc_issues = parse(fixture_bytes("bpmn/broken-refs.bpmn"))
    assert [i.code for i in doc_issues] == ["DUPLICATE_ID"]
    a = graphs[0]
    codes = [(i.code, i.severity) for i in a.issues]
    assert ("BROKEN_REFERENCE", Severity.error) in codes
    assert ("CROSS_PROCESS_SEQUENCE_FLOW", Severity.error) in codes
    assert ("UNSUPPORTED_ELEMENT", Severity.warning) in codes
    ids = [n.id for n in a.nodes]
    assert len(ids) == len(set(ids)), "internal ids must be unique"
    assert any(i.startswith("A_Task__dup") for i in ids)
    assert next(n for n in a.nodes if n.id == "A_Odd").kind == NodeKind.unknown
    node_ids = set(ids)
    assert all(e.source in node_ids and e.target in node_ids for e in a.edges)
    assert {e.id for e in a.edges} == {"A_F1", "A_F4"}


def test_t15_reimport_is_deterministic(client):
    r1 = upload(client, "handoff/example-with-di.bpmn").json()
    r2 = upload(client, "handoff/example-with-di.bpmn").json()
    assert r1["sha256"] == r2["sha256"]
    g1 = client.get(f"/api/v1/processes/{r1['process_ids'][0]}").json()["baseline"]
    g2 = client.get(f"/api/v1/processes/{r2['process_ids'][0]}").json()["baseline"]
    strip = lambda g: [(n["id"], n["source_id"], n["kind"], n["label"]) for n in g["nodes"]]
    assert strip(g1) == strip(g2)


def test_t02_api_import_bpmn_and_xml_extensions(client):
    for name in ("handoff/example-no-di.bpmn",):
        r = upload(client, name)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "ready" and body["kind"] == "bpmn_xml"
        assert len(body["sha256"]) == 64
        assert body["processes"][0]["node_count"] == 6
    r = upload(client, "copy.xml", fixture_bytes("handoff/example-no-di.bpmn"))
    assert r.status_code == 201


def test_wrong_namespace_rejected_with_explanation(client):
    r = upload(client, "bpmn/wrong-namespace.bpmn")
    assert r.status_code == 422
    assert r.json()["code"] == "BPMN_WRONG_NAMESPACE"
    assert "http://www.omg.org/spec/BPMN/20100524/MODEL" in r.json()["message"]


def test_valid_xml_without_process_logic_gives_helpful_error(client):
    data = f'<definitions xmlns="{BPMN_NS}" id="d"><process id="p"/></definitions>'.encode()
    r = upload(client, "empty.bpmn", data)
    assert r.status_code == 422 and r.json()["code"] == "NO_PROCESS_LOGIC"
