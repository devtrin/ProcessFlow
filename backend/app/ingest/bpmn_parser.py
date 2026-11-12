"""Deterministic BPMN 2.0 XML -> ProcessGraph mapping (no AI involved).

Elements are matched by namespace URI + local name, so any prefix (``bpmn:``,
``bpmn2:``, default namespace, ...) works. Every graph element keeps its BPMN
``id`` and a source reference with element type and line number. Constructs
outside the supported subset are reported as issues, never dropped silently.
See IMPLEMENTATION_REPORT.md "Supported BPMN subset".
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from lxml import etree

from ..models import (
    DataItem,
    Direction,
    EdgeKind,
    GraphSource,
    Issue,
    ItemOrigin,
    Lane,
    Layout,
    NodeKind,
    Participant,
    ProcessEdge,
    ProcessGraph,
    ProcessNode,
    ReviewState,
    Severity,
    SourceKind,
    SourceRef,
    utcnow,
)
from .xml_safety import XmlRejected, parse_untrusted_xml

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"

KNOWN_WRONG_NAMESPACES = {"http://omg.org", "http://www.omg.org", "http://omg.org/spec/BPMN/20100524/MODEL"}

NODE_KINDS: dict[str, NodeKind] = {
    "startEvent": NodeKind.start_event,
    "endEvent": NodeKind.end_event,
    "intermediateCatchEvent": NodeKind.intermediate_event,
    "intermediateThrowEvent": NodeKind.intermediate_event,
    "boundaryEvent": NodeKind.intermediate_event,
    "task": NodeKind.task,
    "userTask": NodeKind.task,
    "serviceTask": NodeKind.task,
    "manualTask": NodeKind.task,
    "scriptTask": NodeKind.task,
    "businessRuleTask": NodeKind.task,
    "sendTask": NodeKind.task,
    "receiveTask": NodeKind.task,
    "subProcess": NodeKind.subprocess,
    "adHocSubProcess": NodeKind.subprocess,
    "transaction": NodeKind.subprocess,
    "callActivity": NodeKind.subprocess,
    "exclusiveGateway": NodeKind.exclusive_gateway,
    "parallelGateway": NodeKind.parallel_gateway,
    "inclusiveGateway": NodeKind.inclusive_gateway,
    "eventBasedGateway": NodeKind.event_gateway,
    "complexGateway": NodeKind.complex_gateway,
}

# Process children understood but not rendered as nodes.
NON_NODE_CHILDREN = {
    "sequenceFlow", "laneSet", "dataObject", "dataObjectReference", "dataStoreReference",
    "ioSpecification", "extensionElements", "documentation", "property", "textAnnotation",
    "association", "group", "auditing", "monitoring", "ioBinding", "correlationSubscription",
}
ARTIFACTS = {"textAnnotation", "association", "group"}
ACTIVITY_KINDS = {NodeKind.task, NodeKind.subprocess}


class NotBpmn(XmlRejected):
    pass


def q(local: str, ns: str = BPMN_NS) -> str:
    return f"{{{ns}}}{local}"


def local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def ns_of(el: etree._Element) -> str | None:
    return etree.QName(el).namespace if isinstance(el.tag, str) else None


def xml_ref(el: etree._Element) -> SourceRef:
    eid = el.get("id")
    return SourceRef(
        type=SourceKind.bpmn_xml,
        element_id=eid,
        element_type=local(el),
        xpath=f"//*[@id='{eid}']" if eid else None,
        line=el.sourceline,
    )


def _text(el: etree._Element | None) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


@dataclass
class _Ctx:
    root: etree._Element
    filename: str
    sha256: str
    document_id: str
    issues: list[Issue] = field(default_factory=list)  # document-level
    by_id: dict[str, etree._Element] = field(default_factory=dict)
    duplicate_ids: set[str] = field(default_factory=set)
    owner_process: dict[str, str] = field(default_factory=dict)  # element id -> process XML id
    shapes: dict[str, Layout] = field(default_factory=dict)
    waypoints: dict[str, list[tuple[float, float]]] = field(default_factory=dict)

    def issue(self, sink: list[Issue], code: str, sev: Severity, msg: str, ids=(), refs=()):
        sink.append(Issue(code=code, severity=sev, message=msg, element_ids=list(ids), source_refs=list(refs)))


def _index_ids(ctx: _Ctx) -> None:
    counts: Counter[str] = Counter()
    for el in ctx.root.iter():
        if not isinstance(el.tag, str):
            continue
        eid = el.get("id")
        if eid is None:
            continue
        counts[eid] += 1
        if counts[eid] == 1:
            ctx.by_id[eid] = el
        else:
            ctx.duplicate_ids.add(eid)
    for eid in sorted(ctx.duplicate_ids):
        ctx.issue(
            ctx.issues, "DUPLICATE_ID", Severity.error,
            f"XML id '{eid}' is used by {counts[eid]} elements. References to it are ambiguous; "
            "the first occurrence is used and later ones receive a suffixed internal id.",
            ids=[eid],
        )


def _index_di(ctx: _Ctx) -> None:
    for shape in ctx.root.iter(q("BPMNShape", BPMNDI_NS)):
        ref = shape.get("bpmnElement")
        bounds = shape.find(q("Bounds", DC_NS))
        if ref and bounds is not None:
            try:
                ctx.shapes[ref] = Layout(
                    x=float(bounds.get("x", "0")), y=float(bounds.get("y", "0")),
                    width=float(bounds.get("width", "0")), height=float(bounds.get("height", "0")),
                )
            except ValueError:
                ctx.issue(ctx.issues, "DI_INVALID_BOUNDS", Severity.warning,
                          f"BPMNShape for '{ref}' has non-numeric bounds; ignored.", ids=[ref])
    for edge in ctx.root.iter(q("BPMNEdge", BPMNDI_NS)):
        ref = edge.get("bpmnElement")
        pts = []
        for wp in edge.findall(q("waypoint", DI_NS)):
            try:
                pts.append((float(wp.get("x", "0")), float(wp.get("y", "0"))))
            except ValueError:
                pass
        if ref and pts:
            ctx.waypoints[ref] = pts


def _lanes(ctx: _Ctx, proc: etree._Element, participant_id: str | None, issues: list[Issue]) -> list[Lane]:
    lanes: list[Lane] = []

    def walk(lane_set: etree._Element, prefix: str) -> None:
        for lane in lane_set.findall(q("lane")):
            name = lane.get("name") or lane.get("id") or "Unnamed lane"
            full = f"{prefix} / {name}" if prefix else name
            child_set = lane.find(q("childLaneSet"))
            if child_set is not None and len(child_set):
                walk(child_set, full)  # leaf lanes carry the node refs that matter
                continue
            refs = [r.text.strip() for r in lane.findall(q("flowNodeRef")) if r.text and r.text.strip()]
            lanes.append(Lane(id=lane.get("id") or f"lane_{len(lanes)}", name=full,
                              participant_id=participant_id, source_id=lane.get("id"), node_ids=refs))

    for ls in proc.findall(q("laneSet")):
        walk(ls, "")
    return lanes


def _data_ref_name(ctx: _Ctx, ref_id: str) -> tuple[str, etree._Element | None]:
    el = ctx.by_id.get(ref_id)
    if el is None:
        return ref_id, None
    name = el.get("name")
    if not name and local(el) == "dataObjectReference":
        obj = ctx.by_id.get(el.get("dataObjectRef") or "")
        name = obj.get("name") if obj is not None else None
    if not name and local(el) == "dataStoreReference":
        store = ctx.by_id.get(el.get("dataStoreRef") or "")
        name = store.get("name") if store is not None else None
    state = el.find(q("dataState"))
    if state is not None and state.get("name"):
        name = f"{name or ref_id} [{state.get('name')}]"
    return name or ref_id, el


def _data_items(ctx: _Ctx, act: etree._Element, node_id: str, issues: list[Issue]) -> tuple[list[DataItem], list[DataItem]]:
    """Inputs/outputs only from explicit BPMN data associations / ioSpecification."""
    inputs: list[DataItem] = []
    outputs: list[DataItem] = []
    associated: set[str] = set()
    for direction, tag, ref_tag, sink in (
        (Direction.input, "dataInputAssociation", "sourceRef", inputs),
        (Direction.output, "dataOutputAssociation", "targetRef", outputs),
    ):
        for i, assoc in enumerate(act.findall(q(tag))):
            other = "targetRef" if ref_tag == "sourceRef" else "sourceRef"
            for o in assoc.findall(q(other)):
                if o.text:
                    associated.add(o.text.strip())
            refs = [r.text.strip() for r in assoc.findall(q(ref_tag)) if r.text and r.text.strip()]
            if not refs:
                ctx.issue(issues, "DATA_ASSOCIATION_UNRESOLVED", Severity.warning,
                          f"{tag} on '{node_id}' has no {ref_tag}; no data item created.",
                          ids=[node_id], refs=[xml_ref(assoc)])
                continue
            for ref in refs:
                name, ref_el = _data_ref_name(ctx, ref)
                src = [xml_ref(assoc)] if assoc.get("id") else [xml_ref(act)]
                if ref_el is None:
                    ctx.issue(issues, "BROKEN_REFERENCE", Severity.error,
                              f"{tag} on '{node_id}' references unknown element '{ref}'.",
                              ids=[node_id], refs=src)
                else:
                    src.append(xml_ref(ref_el))
                sink.append(DataItem(
                    id=f"{node_id}::{assoc.get('id') or f'{tag}{i}'}::{ref}",
                    name=name, direction=direction, origin=ItemOrigin.source_explicit, source_refs=src,
                ))
    io = act.find(q("ioSpecification"))
    if io is not None:
        for tag, direction, sink in (("dataInput", Direction.input, inputs), ("dataOutput", Direction.output, outputs)):
            for d in io.findall(q(tag)):
                did = d.get("id") or ""
                if did in associated:
                    continue
                sink.append(DataItem(
                    id=f"{node_id}::io::{did}", name=d.get("name") or did, direction=direction,
                    origin=ItemOrigin.source_explicit, source_refs=[xml_ref(d)],
                ))
    return inputs, outputs


def _node_from(ctx: _Ctx, el: etree._Element, internal_id: str, kind: NodeKind, participant_id: str | None,
               issues: list[Issue], notes: dict[str, list[str]]) -> ProcessNode:
    lname = local(el)
    attrs: dict = {"bpmn_type": lname}
    eid = el.get("id") or internal_id
    defs = [local(c) for c in el if local(c).endswith("EventDefinition")]
    defs += [f"ref:{r.text.strip()}" for r in el.findall(q("eventDefinitionRef")) if r.text]
    if defs:
        attrs["event_definitions"] = defs
        notes["event_triggers"].append(eid)
    for flag in ("isInterrupting", "cancelActivity", "parallelMultiple", "triggeredByEvent", "isForCompensation",
                 "instantiate", "eventGatewayType", "gatewayDirection", "calledElement", "implementation"):
        if el.get(flag) is not None:
            attrs[flag] = el.get(flag)
    for loop in ("standardLoopCharacteristics", "multiInstanceLoopCharacteristics"):
        lc = el.find(q(loop))
        if lc is not None:
            attrs["loop"] = loop if loop.startswith("standard") else (
                "multiInstance(sequential)" if lc.get("isSequential") == "true" else "multiInstance(parallel)")
            notes["loops"].append(eid)
    doc = el.find(q("documentation"))
    if doc is not None and _text(doc):
        attrs["documentation"] = _text(doc)[:2000]
    if el.find(q("extensionElements")) is not None or any(
        isinstance(c.tag, str) and ns_of(c) != BPMN_NS for c in el
    ):
        attrs["has_vendor_extensions"] = True
        notes["extensions"].append(eid)
    if kind == NodeKind.subprocess and lname != "callActivity":
        children = [c for c in el if local(c) in NODE_KINDS]
        attrs["collapsed_child_count"] = len(children)
        attrs["collapsed_child_ids"] = [c.get("id") for c in children if c.get("id")][:200]
        notes["subprocesses"].append(eid)
    if kind == NodeKind.complex_gateway:
        ctx.issue(issues, "UNSUPPORTED_SEMANTICS", Severity.warning,
                  f"Complex gateway '{eid}' is shown, but its activation condition is not interpreted.",
                  ids=[internal_id], refs=[xml_ref(el)])
    if lname == "boundaryEvent":
        attrs["attached_to"] = el.get("attachedToRef")
    if lname in ("exclusiveGateway", "inclusiveGateway", "complexGateway") or kind in ACTIVITY_KINDS:
        if el.get("default"):
            attrs["default_flow"] = el.get("default")

    inputs, outputs = ([], [])
    if kind in ACTIVITY_KINDS:
        inputs, outputs = _data_items(ctx, el, internal_id, issues)
    elif el.find(q("dataInputAssociation")) is not None or el.find(q("dataOutputAssociation")) is not None:
        # Events can carry data associations too.
        inputs, outputs = _data_items(ctx, el, internal_id, issues)

    label = el.get("name") or ""
    return ProcessNode(
        id=internal_id, source_id=el.get("id"), kind=kind, label=label,
        participant_id=participant_id, inputs=inputs, outputs=outputs,
        source_refs=[xml_ref(el)], review_state=ReviewState.source_explicit,
        layout=ctx.shapes.get(el.get("id") or ""), attributes=attrs,
    )


def _parse_process(ctx: _Ctx, proc: etree._Element, index: int, participant: Participant | None) -> ProcessGraph:
    pid = proc.get("id") or f"process_{index}"
    issues: list[Issue] = []
    notes: dict[str, list[str]] = defaultdict(list)
    participant_id = participant.id if participant else None
    nodes: list[ProcessNode] = []
    node_ids: set[str] = set()

    def internal(el: etree._Element) -> str:
        eid = el.get("id") or f"{local(el)}_line{el.sourceline}"
        if eid in ctx.duplicate_ids and ctx.by_id.get(eid) is not el:
            return f"{eid}__dup_line{el.sourceline}"
        return eid

    flows: list[etree._Element] = []
    artifacts: list[str] = []
    for child in proc:
        if not isinstance(child.tag, str):
            continue
        lname = local(child)
        if ns_of(child) != BPMN_NS:
            ctx.issue(issues, "UNKNOWN_NAMESPACE_ELEMENT", Severity.warning,
                      f"Element '{child.tag}' (line {child.sourceline}) is outside the BPMN model namespace "
                      "and was not interpreted.", refs=[xml_ref(child)])
            continue
        if lname == "sequenceFlow":
            flows.append(child)
            continue
        if lname in NON_NODE_CHILDREN:
            if lname in ARTIFACTS:
                artifacts.append(child.get("id") or lname)
            continue
        kind = NODE_KINDS.get(lname)
        iid = internal(child)
        if kind is None:
            ctx.issue(issues, "UNSUPPORTED_ELEMENT", Severity.warning,
                      f"BPMN element <{lname}> '{child.get('id') or '(no id)'}' is not in the supported subset. "
                      "It is shown as an 'unknown' node so connected flows stay visible.",
                      ids=[iid], refs=[xml_ref(child)])
            kind = NodeKind.unknown
        node = _node_from(ctx, child, iid, kind, participant_id, issues, notes)
        nodes.append(node)
        node_ids.add(node.id)

    lanes = _lanes(ctx, proc, participant_id, issues)
    lane_of: dict[str, str] = {}
    for lane in lanes:
        kept = []
        for ref in lane.node_ids:
            if ref in node_ids:
                if ref in lane_of:
                    ctx.issue(issues, "LANE_CONFLICT", Severity.warning,
                              f"Node '{ref}' is referenced by more than one lane; first lane kept.", ids=[ref])
                    continue
                lane_of[ref] = lane.id
                kept.append(ref)
            else:
                ctx.issue(issues, "BROKEN_REFERENCE", Severity.warning,
                          f"Lane '{lane.name}' references '{ref}', which is not a flow node of this process.",
                          ids=[lane.id])
        lane.node_ids = kept
    for n in nodes:
        n.lane_id = lane_of.get(n.id)

    edges: list[ProcessEdge] = []
    defaults = {n.attributes.get("default_flow") for n in nodes if n.attributes.get("default_flow")}
    for f in flows:
        fid = internal(f)
        s, t = (f.get("sourceRef") or "").strip(), (f.get("targetRef") or "").strip()
        bad = []
        for end, ref in (("sourceRef", s), ("targetRef", t)):
            if ref in node_ids:
                continue
            if ref in ctx.by_id and ctx.owner_process.get(ref, pid) != pid:
                bad.append(("CROSS_PROCESS_SEQUENCE_FLOW",
                            f"Sequence flow '{fid}' {end} '{ref}' belongs to another process; "
                            "sequence flows must stay inside one process. Edge not drawn."))
            else:
                bad.append(("BROKEN_REFERENCE",
                            f"Sequence flow '{fid}' {end} '{ref or '(missing)'}' does not resolve to a flow node "
                            "in this process. Edge not drawn."))
        if bad:
            for code, msg in bad:
                ctx.issue(issues, code, Severity.error, msg, ids=[fid], refs=[xml_ref(f)])
            continue
        cond = f.find(q("conditionExpression"))
        edges.append(ProcessEdge(
            id=fid, source_id=f.get("id"), source=s, target=t, kind=EdgeKind.sequence,
            label=f.get("name") or None, condition_expression=_text(cond) or None,
            is_default=(f.get("id") in defaults), source_refs=[xml_ref(f)],
            review_state=ReviewState.source_explicit, waypoints=ctx.waypoints.get(f.get("id") or ""),
        ))
    edge_ids = {e.id for e in edges}
    for n in nodes:
        d = n.attributes.get("default_flow")
        if d and d not in edge_ids:
            ctx.issue(issues, "BROKEN_REFERENCE", Severity.warning,
                      f"'{n.id}' names default flow '{d}', which is not an outgoing sequence flow here.", ids=[n.id])
        att = n.attributes.get("attached_to")
        if att:
            if att in node_ids:
                edges.append(ProcessEdge(
                    id=f"{n.id}__attached", source=att, target=n.id, kind=EdgeKind.boundary_attachment,
                    label=None, source_refs=n.source_refs, review_state=ReviewState.source_explicit,
                ))
            else:
                ctx.issue(issues, "BROKEN_REFERENCE", Severity.error,
                          f"Boundary event '{n.id}' is attached to unknown activity '{att}'.", ids=[n.id])

    if notes["event_triggers"]:
        ctx.issue(issues, "EVENT_TRIGGER_NOT_INTERPRETED", Severity.info,
                  "These events have trigger definitions (timer, message, error, ...). The trigger type is shown "
                  "on the node but its execution semantics are not modelled.", ids=notes["event_triggers"])
    if notes["subprocesses"]:
        ctx.issue(issues, "SUBPROCESS_COLLAPSED", Severity.info,
                  "Sub-processes are shown as collapsed placeholders; their inner flow is not expanded in v0.1.",
                  ids=notes["subprocesses"])
    if notes["loops"]:
        ctx.issue(issues, "LOOP_MARKER_NOT_INTERPRETED", Severity.info,
                  "These activities carry loop/multi-instance markers, shown as attributes only.", ids=notes["loops"])
    if notes["extensions"]:
        ctx.issue(issues, "VENDOR_EXTENSIONS_IGNORED", Severity.info,
                  "Vendor extension elements are present and were not interpreted.", ids=notes["extensions"])
    if artifacts:
        ctx.issue(issues, "ARTIFACTS_NOT_RENDERED", Severity.info,
                  f"{len(artifacts)} artefact(s) (text annotations, associations, groups) are not drawn in v0.1.",
                  ids=artifacts)

    has_layout = bool(nodes) and all(n.layout is not None for n in nodes)
    if nodes and not has_layout and any(n.layout for n in nodes):
        ctx.issue(issues, "DI_PARTIAL", Severity.info,
                  "BPMNDI shapes exist for only some nodes; source layout view is disabled for this process.")

    title = proc.get("name") or (participant.name if participant else None) or pid
    return ProcessGraph(
        process_id=f"{ctx.document_id}__{index}", title=title, view="process",
        source=GraphSource(kind=SourceKind.bpmn_xml, filename=ctx.filename, sha256=ctx.sha256,
                           imported_at=utcnow(), original_id=pid, document_id=ctx.document_id),
        participants=[participant] if participant else [], lanes=lanes, nodes=nodes, edges=edges,
        issues=issues, review_state="source_explicit", has_source_layout=has_layout,
        provenance={"parser": "processflow.bpmn_parser", "namespace": BPMN_NS, "source_process_id": pid,
                    "source_line": proc.sourceline},
    )


def _collaboration_view(ctx: _Ctx, graphs: list[ProcessGraph], participants: list[Participant],
                        collab: etree._Element, index: int) -> ProcessGraph:
    issues: list[Issue] = []
    nodes: list[ProcessNode] = []
    edges: list[ProcessEdge] = []
    lanes: list[Lane] = []
    by_proc = {g.provenance["source_process_id"]: g for g in graphs}
    node_ids: set[str] = set()
    for p in participants:
        g = by_proc.get(p.process_ref or "")
        if g is None:
            # Black-box pool: no process content; message flows may still target it.
            el = ctx.by_id.get(p.id)
            nodes.append(ProcessNode(
                id=p.id, source_id=p.id, kind=NodeKind.unknown, label=p.name, participant_id=p.id,
                source_refs=[xml_ref(el)] if el is not None else [], review_state=ReviewState.source_explicit,
                layout=ctx.shapes.get(p.id), attributes={"bpmn_type": "participant", "black_box_pool": True},
            ))
            node_ids.add(p.id)
            continue
        for n in g.nodes:
            nodes.append(n.model_copy(update={"participant_id": p.id}, deep=True))
            node_ids.add(n.id)
        edges.extend(e.model_copy(deep=True) for e in g.edges)
        lanes.extend(lane.model_copy(update={"participant_id": p.id}, deep=True) for lane in g.lanes)
    for mf in collab.findall(q("messageFlow")):
        mid = mf.get("id") or f"messageFlow_line{mf.sourceline}"
        s, t = (mf.get("sourceRef") or "").strip(), (mf.get("targetRef") or "").strip()
        missing = [r for r in (s, t) if r not in node_ids]
        if missing:
            ctx.issue(issues, "BROKEN_REFERENCE", Severity.error,
                      f"Message flow '{mid}' references {', '.join(repr(m) for m in missing)}, "
                      "which is not a node or pool in this collaboration. Edge not drawn.",
                      ids=[mid], refs=[xml_ref(mf)])
            continue
        edges.append(ProcessEdge(
            id=mid, source_id=mf.get("id"), source=s, target=t, kind=EdgeKind.message,
            label=mf.get("name") or None, source_refs=[xml_ref(mf)],
            review_state=ReviewState.source_explicit, waypoints=ctx.waypoints.get(mid),
        ))
    has_layout = bool(nodes) and all(n.layout is not None for n in nodes)
    cid = collab.get("id") or "collaboration"
    return ProcessGraph(
        process_id=f"{ctx.document_id}__c{index}",
        title=f"Collaboration: {collab.get('name') or ', '.join(p.name for p in participants)}",
        view="collaboration",
        source=GraphSource(kind=SourceKind.bpmn_xml, filename=ctx.filename, sha256=ctx.sha256,
                           imported_at=utcnow(), original_id=cid, document_id=ctx.document_id),
        participants=participants, lanes=lanes, nodes=nodes, edges=edges, issues=issues,
        review_state="source_explicit", has_source_layout=has_layout,
        provenance={"parser": "processflow.bpmn_parser", "namespace": BPMN_NS, "source_collaboration_id": cid,
                    "note": "Read-only combined view of the pools. Message flows connect participants; "
                            "they are never merged into sequence flows."},
    )


def parse_bpmn(data: bytes, *, filename: str, sha256: str, document_id: str,
               max_bytes: int, max_depth: int, max_elements: int) -> tuple[list[ProcessGraph], list[Issue]]:
    root = parse_untrusted_xml(data, max_bytes=max_bytes, max_depth=max_depth, max_elements=max_elements)
    root_ns = ns_of(root)
    if local(root) != "definitions" or root_ns != BPMN_NS:
        if root_ns in KNOWN_WRONG_NAMESPACES:
            raise NotBpmn("BPMN_WRONG_NAMESPACE",
                          f"Root namespace is '{root_ns}'. BPMN 2.0 model files must use '{BPMN_NS}'.")
        raise NotBpmn("NOT_BPMN",
                      f"Root element is '{local(root)}' in namespace '{root_ns}'. Expected BPMN 2.0 "
                      f"<definitions> in namespace '{BPMN_NS}'.")

    ctx = _Ctx(root=root, filename=filename, sha256=sha256, document_id=document_id)
    _index_ids(ctx)
    _index_di(ctx)

    collaborations = root.findall(q("collaboration"))
    participant_for_process: dict[str, Participant] = {}
    collab_participants: list[list[Participant]] = []
    for collab in collaborations:
        plist = []
        for p in collab.findall(q("participant")):
            part = Participant(id=p.get("id") or f"participant_line{p.sourceline}",
                               name=p.get("name") or p.get("id") or "Participant",
                               source_id=p.get("id"), process_ref=p.get("processRef"))
            plist.append(part)
            if part.process_ref:
                participant_for_process.setdefault(part.process_ref, part)
        collab_participants.append(plist)

    known_top = {"process", "collaboration", "BPMNDiagram", "message", "dataStore", "itemDefinition",
                 "signal", "error", "escalation", "category", "import", "extension", "documentation",
                 "extensionElements", "relationship", "interface", "resource", "correlationProperty",
                 "globalTask", "choreography", "conversation", "partnerRole", "partnerEntity",
                 "eventDefinition", "timerEventDefinition", "messageEventDefinition"}
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if local(child) not in known_top:
            ctx.issue(ctx.issues, "UNSUPPORTED_ELEMENT", Severity.warning,
                      f"Top-level element <{local(child)}> (line {child.sourceline}) is not interpreted.",
                      refs=[xml_ref(child)])
        elif local(child) in ("choreography", "conversation"):
            ctx.issue(ctx.issues, "UNSUPPORTED_ELEMENT", Severity.warning,
                      f"<{local(child)}> diagrams are outside the supported subset and are not shown.",
                      refs=[xml_ref(child)])

    processes = root.findall(q("process"))
    for p in processes:
        for child in p:
            if isinstance(child.tag, str) and child.get("id"):
                ctx.owner_process.setdefault(child.get("id"), p.get("id") or "")
    graphs = [_parse_process(ctx, p, i, participant_for_process.get(p.get("id") or "")) for i, p in enumerate(processes)]
    for i, (collab, plist) in enumerate(zip(collaborations, collab_participants)):
        has_messages = collab.find(q("messageFlow")) is not None
        if has_messages or len([p for p in plist if p.process_ref]) > 1:
            graphs.append(_collaboration_view(ctx, graphs, plist, collab, i))
        for p in plist:
            if p.process_ref and p.process_ref not in {g.provenance.get("source_process_id") for g in graphs}:
                ctx.issue(ctx.issues, "BROKEN_REFERENCE", Severity.error,
                          f"Participant '{p.name}' references process '{p.process_ref}', which does not exist.",
                          ids=[p.id])
    if not processes:
        ctx.issue(ctx.issues, "NO_PROCESS", Severity.error,
                  "The file is valid BPMN XML but contains no <process> element, so there is no flow to show.")
    elif all(not g.nodes for g in graphs):
        ctx.issue(ctx.issues, "EMPTY_PROCESS", Severity.error,
                  "The BPMN processes contain no flow nodes (events, tasks or gateways).")
    # Duplicate-id issues are relevant to every view.
    for g in graphs:
        g.issues = [i.model_copy() for i in ctx.issues if i.code == "DUPLICATE_ID"] + g.issues
    return graphs, ctx.issues
