"""Graph-level consistency checks shared by the XML and PDF pipelines (FR-09).

Cycles are reported as information, not errors: BPMN processes may loop on purpose.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..models import EdgeKind, Issue, NodeKind, ProcessGraph, Severity

GATEWAYS_NEEDING_LABELS = {NodeKind.exclusive_gateway, NodeKind.inclusive_gateway}
CONTROL_EDGES = {EdgeKind.sequence, EdgeKind.inferred_sequence}


def validate_graph(graph: ProcessGraph) -> list[Issue]:
    issues: list[Issue] = []
    counts = Counter(n.id for n in graph.nodes)
    for nid, c in counts.items():
        if c > 1:
            issues.append(Issue(code="DUPLICATE_NODE_ID", severity=Severity.error,
                                message=f"Internal node id '{nid}' occurs {c} times.", element_ids=[nid]))
    edge_counts = Counter(e.id for e in graph.edges)
    for eid, c in edge_counts.items():
        if c > 1:
            issues.append(Issue(code="DUPLICATE_EDGE_ID", severity=Severity.error,
                                message=f"Internal edge id '{eid}' occurs {c} times.", element_ids=[eid]))
    ids = set(counts)
    for e in graph.edges:
        for end in (e.source, e.target):
            if end not in ids:
                issues.append(Issue(code="EDGE_ENDPOINT_MISSING", severity=Severity.error,
                                    message=f"Edge '{e.id}' references missing node '{end}'.", element_ids=[e.id]))
    item_ids = Counter(i.id for n in graph.nodes for i in (*n.inputs, *n.outputs))
    for iid, c in item_ids.items():
        if c > 1:
            issues.append(Issue(code="DUPLICATE_ITEM_ID", severity=Severity.error,
                                message=f"Data item id '{iid}' occurs {c} times.", element_ids=[iid]))

    out: dict[str, list] = defaultdict(list)
    inc: dict[str, list] = defaultdict(list)
    for e in graph.edges:
        if e.kind in CONTROL_EDGES:
            out[e.source].append(e)
            inc[e.target].append(e)

    for n in graph.nodes:
        if n.kind in GATEWAYS_NEEDING_LABELS and len(out[n.id]) > 1:
            unlabelled = [e.id for e in out[n.id] if not (e.label or e.condition_expression or e.is_default)]
            if unlabelled:
                issues.append(Issue(code="BRANCH_UNLABELLED", severity=Severity.warning,
                                    message=f"Decision '{n.label or n.id}' has outgoing branches without a label, "
                                            "condition or default marker.", element_ids=[n.id, *unlabelled]))
        if not n.source_refs:
            issues.append(Issue(code="NO_PROVENANCE", severity=Severity.warning,
                                message=f"Node '{n.label or n.id}' has no source reference and is unverified.",
                                element_ids=[n.id]))

    if graph.view != "collaboration" and graph.nodes:
        if not any(n.kind == NodeKind.start_event for n in graph.nodes):
            issues.append(Issue(code="NO_START_EVENT", severity=Severity.info,
                                message="No start event; the flow entry point is implicit."))
        if not any(n.kind == NodeKind.end_event for n in graph.nodes):
            issues.append(Issue(code="NO_END_EVENT", severity=Severity.info,
                                message="No end event; the flow end is implicit."))
        flow_nodes = [n for n in graph.nodes if n.attributes.get("bpmn_type") != "boundaryEvent"]
        isolated = [n.id for n in flow_nodes if not out[n.id] and not inc[n.id] and len(flow_nodes) > 1]
        if isolated:
            issues.append(Issue(code="DISCONNECTED_NODE", severity=Severity.warning,
                                message="These nodes have no incoming or outgoing control flow.",
                                element_ids=isolated))

    cycle = _find_cycle(graph.nodes, out)
    if cycle:
        issues.append(Issue(code="CYCLE_PRESENT", severity=Severity.info,
                            message="The flow contains a loop. Loops are allowed; check that it is intended.",
                            element_ids=cycle))
    return issues


def _find_cycle(nodes, out) -> list[str] | None:
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n.id: WHITE for n in nodes}
    for start in colour:
        if colour[start] != WHITE:
            continue
        stack = [(start, iter(out[start]))]
        path = [start]
        colour[start] = GREY
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = BLACK
                stack.pop()
                path.pop()
                continue
            t = nxt.target
            if colour.get(t) == GREY:
                return path[path.index(t):] + [t]
            if colour.get(t) == WHITE:
                colour[t] = GREY
                stack.append((t, iter(out[t])))
                path.append(t)
    return None
