"""PDF text -> (adapter) -> validated candidate -> grounded, review-gated ProcessGraph.

Every quote the model supplies is checked against the extracted page text.
Nothing produced here is marked verified: all nodes and edges start as
``needs_review`` and only a human review decision changes that.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict

from pydantic import ValidationError

from ..ai.base import CandidateProcess, Evidence, InferenceError, InferencePrompt, ProcessInferenceAdapter
from ..ai.prompt import build_prompt
from ..domain.validators import validate_graph
from ..models import (
    DataItem,
    Direction,
    EdgeKind,
    GraphSource,
    Issue,
    ItemOrigin,
    Lane,
    NodeKind,
    ProcessEdge,
    ProcessGraph,
    ProcessNode,
    ReviewState,
    Severity,
    SourceKind,
    SourceRef,
    utcnow,
)
from .pdf_reader import ExtractedPdf

NOT_STATED = {"not_stated", "not stated", "unknown", ""}
MAX_ATTEMPTS = 2  # first try + one bounded re-prompt on schema-invalid output

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         " ": " ", "­": ""})


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    t = re.sub(r"-\s*\n\s*", "", t)  # de-hyphenate line breaks
    return re.sub(r"\s+", " ", t).strip().lower()


class Grounder:
    def __init__(self, pdf: ExtractedPdf):
        self.pdf = pdf
        self.raw = {p: pdf.page_text(p) for p in range(1, pdf.page_count + 1)}
        self.norm = {p: normalise(t) for p, t in self.raw.items()}

    def _block_for(self, page: int, quote_n: str):
        for b in self.pdf.blocks:
            if b.page == page and quote_n[:40] in normalise(b.text):
                return b
        return None

    def ground(self, ev: Evidence) -> SourceRef:
        q = ev.quote.strip()
        qn = normalise(q)
        page = ev.page
        if page in self.raw and q and q in self.raw[page]:
            status = "exact"
        elif page in self.norm and qn and qn in self.norm[page]:
            status = "normalised"
        else:
            other = next((p for p, t in self.norm.items() if qn and qn in t), None)
            if other is not None:
                status, page = "wrong_page", ev.page
                block = self._block_for(other, qn)
                return SourceRef(type=SourceKind.pdf, page=ev.page if ev.page >= 1 else 1, quote=q,
                                 grounding=status, block_id=block.id if block else None,
                                 bbox=block.bbox if block else None)
            status = "not_found"
        block = self._block_for(page, qn) if status in ("exact", "normalised") else None
        return SourceRef(type=SourceKind.pdf, page=max(page, 1), quote=q, grounding=status,
                         block_id=block.id if block else None, bbox=block.bbox if block else None)


def _validate_candidate(raw_text: str) -> CandidateProcess:
    data = json.loads(raw_text)
    return CandidateProcess.model_validate(data)


def request_candidate(adapter: ProcessInferenceAdapter, prompt: InferencePrompt) -> tuple[CandidateProcess, dict]:
    """Call the adapter, validate, re-prompt once with the validation error if needed."""
    attempts = []
    last_err = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = adapter.infer(prompt)
        try:
            cand = _validate_candidate(result.raw_text)
            attempts.append({"attempt": attempt, "valid": True})
            return cand, {"provider": result.provider, "model": result.model, "usage": result.usage,
                          "attempts": attempts}
        except (json.JSONDecodeError, ValidationError) as exc:
            last_err = (f"invalid JSON: {exc.msg}" if isinstance(exc, json.JSONDecodeError)
                        else "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:20]))
            attempts.append({"attempt": attempt, "valid": False, "error": last_err[:2000]})
            prompt = InferencePrompt(system=prompt.system, user=prompt.user, schema=prompt.schema,
                                     previous_output=result.raw_text[:20000],
                                     retry_feedback="Your previous output failed schema validation: "
                                                    f"{last_err[:2000]}. Return corrected JSON only.")
    raise InferenceError("MODEL_OUTPUT_INVALID",
                         f"The model output failed schema validation after {MAX_ATTEMPTS} attempts: {last_err[:500]}")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "actor"


def candidate_to_graph(cand: CandidateProcess, pdf: ExtractedPdf, *, filename: str, sha256: str,
                       document_id: str, process_id: str, inference_meta: dict) -> ProcessGraph:
    g = Grounder(pdf)
    issues: list[Issue] = []

    def refs_for(evs: list[Evidence], element_id: str, what: str) -> list[SourceRef]:
        refs = [g.ground(e) for e in evs]
        if not refs:
            issues.append(Issue(code="MISSING_EVIDENCE", severity=Severity.warning,
                                message=f"{what} has no source quote. Treat it as unverified.", element_ids=[element_id]))
        for r in refs:
            if r.grounding == "not_found":
                issues.append(Issue(code="FABRICATED_EVIDENCE", severity=Severity.error,
                                    message=f"{what}: quoted evidence was not found anywhere in the PDF text.",
                                    element_ids=[element_id], source_refs=[r]))
            elif r.grounding == "wrong_page":
                issues.append(Issue(code="EVIDENCE_WRONG_PAGE", severity=Severity.warning,
                                    message=f"{what}: the quote exists in the PDF but not on page {r.page}.",
                                    element_ids=[element_id], source_refs=[r]))
        return refs

    # ---- steps -> nodes
    id_map: dict[str, str] = {}
    nodes: list[ProcessNode] = []
    seen: set[str] = set()
    for s in cand.steps:
        nid = s.id
        if nid in seen:
            n = 2
            while f"{s.id}_{n}" in seen:
                n += 1
            nid = f"{s.id}_{n}"
            issues.append(Issue(code="DUPLICATE_ID", severity=Severity.error,
                                message=f"The model returned step id '{s.id}' more than once; renamed to '{nid}'. "
                                        "Flows referencing it may be attached to the wrong step.", element_ids=[nid]))
        seen.add(nid)
        id_map.setdefault(s.id, nid)
        kind = NodeKind.exclusive_gateway if s.kind == "decision" else NodeKind(s.kind)
        actor = None if s.actor.strip().lower() in NOT_STATED else s.actor.strip()
        node_refs = refs_for(s.evidence, nid, f"Step '{s.label}'")
        items: dict[Direction, list[DataItem]] = {Direction.input: [], Direction.output: []}
        for direction, src in ((Direction.input, s.inputs), (Direction.output, s.outputs)):
            for i, it in enumerate(src):
                iid = f"{nid}::{'in' if direction == Direction.input else 'out'}{i + 1}"
                items[direction].append(DataItem(
                    id=iid, name=it.name, direction=direction, origin=ItemOrigin.ai_inferred,
                    source_refs=refs_for(it.evidence, nid, f"{direction.value.title()} '{it.name}' of '{s.label}'")))
        attrs: dict = {"candidate_kind": s.kind, "actor_stated": actor is not None}
        if s.kind == "decision":
            attrs["gateway_type_assumed"] = "exclusive"
        nodes.append(ProcessNode(
            id=nid, source_id=s.id, kind=kind, label=s.label, actor=actor or "not_stated",
            inputs=items[Direction.input], outputs=items[Direction.output], source_refs=node_refs,
            review_state=ReviewState.needs_review, attributes=attrs,
        ))
    no_actor = [n.id for n in nodes if n.kind == NodeKind.task and n.actor == "not_stated"]
    if no_actor:
        issues.append(Issue(code="ACTOR_NOT_STATED", severity=Severity.info,
                            message="The document names no actor for these steps.", element_ids=no_actor))

    # ---- flows -> inferred edges
    node_ids = {n.id for n in nodes}
    edges: list[ProcessEdge] = []
    seen_e: set[str] = set()
    for f in cand.flows:
        s, t = id_map.get(f.source, f.source), id_map.get(f.target, f.target)
        if s not in node_ids or t not in node_ids:
            issues.append(Issue(code="INVALID_FLOW_REFERENCE", severity=Severity.error,
                                message=f"Model flow '{f.id}' connects '{f.source}' -> '{f.target}', but at least one "
                                        "end is not a known step. The flow was not drawn.", element_ids=[f.id]))
            continue
        eid = f.id if f.id not in seen_e else f"{f.id}_{len(seen_e)}"
        seen_e.add(eid)
        cond = f.condition.strip()
        label = None if cond.lower() in NOT_STATED | {"none"} else cond
        edges.append(ProcessEdge(
            id=eid, source_id=f.id, source=s, target=t, kind=EdgeKind.inferred_sequence, label=label,
            source_refs=refs_for(f.evidence, eid, f"Flow {s} -> {t}"), review_state=ReviewState.needs_review,
        ))
        if cond.lower() in ("not_stated", "not stated"):
            issues.append(Issue(code="CONDITION_NOT_STATED", severity=Severity.warning,
                                message=f"Branch {s} -> {t} exists but its condition is not stated in the text.",
                                element_ids=[eid]))

    # ---- uncertainties -> issues (never steps)
    for u in cand.uncertainties:
        related = [id_map.get(r, r) for r in u.related_step_ids if id_map.get(r, r) in node_ids]
        refs = refs_for(u.evidence, related[0] if related else "process", "Uncertainty")
        issues.append(Issue(code="AMBIGUITY", severity=Severity.warning,
                            message=f"Unresolved in source: {u.description}", element_ids=related, source_refs=refs))

    # ---- inferred loops need explicit human confirmation
    out = defaultdict(list)
    for e in edges:
        out[e.source].append(e.target)
    order = {n.id: i for i, n in enumerate(nodes)}
    back = [e.id for e in edges if order.get(e.target, 0) <= order.get(e.source, 0)]
    if back:
        issues.append(Issue(code="INFERRED_LOOP", severity=Severity.warning,
                            message="The draft contains flow(s) back to an earlier step. Confirm the source states "
                                    "who triggers the loop and when before verifying.", element_ids=back))

    # ---- lanes from stated actors (inferred grouping)
    lanes: dict[str, Lane] = {}
    for n in nodes:
        if n.actor and n.actor != "not_stated":
            lid = f"lane_{_slug(n.actor)}"
            lanes.setdefault(lid, Lane(id=lid, name=n.actor, node_ids=[]))
            lanes[lid].node_ids.append(n.id)
            n.lane_id = lid

    title = cand.process_title if cand.process_title.strip().lower() not in NOT_STATED else filename
    graph = ProcessGraph(
        process_id=process_id, title=title, view="pdf_draft",
        source=GraphSource(kind=SourceKind.pdf, filename=filename, sha256=sha256, imported_at=utcnow(),
                           original_id=None, document_id=document_id),
        lanes=list(lanes.values()), nodes=nodes, edges=edges, issues=issues, review_state="needs_review",
        provenance={"pipeline": "pdf_inference", "inference": inference_meta,
                    "note": "AI-assisted draft. Lanes are grouped by the actor named in the text."},
    )
    graph.issues = issues + validate_graph(graph)
    return graph


def infer_process(adapter: ProcessInferenceAdapter, pdf: ExtractedPdf, *, filename: str, sha256: str,
                  document_id: str, process_id: str, max_chars: int) -> ProcessGraph:
    prompt = build_prompt(pdf, filename)
    if len(prompt.user) > max_chars:
        raise InferenceError("INFERENCE_INPUT_TOO_LARGE",
                             f"The extracted text is {len(prompt.user)} characters; the configured limit is "
                             f"{max_chars}. It was not truncated or sent.")
    cand, meta = request_candidate(adapter, prompt)
    meta["remote"] = adapter.is_remote
    meta["prompt_chars"] = len(prompt.user)
    if not cand.steps:
        raise InferenceError("NO_PROCESS_FOUND",
                             "The model found no process steps described in this PDF. Nothing was drawn.")
    return candidate_to_graph(cand, pdf, filename=filename, sha256=sha256, document_id=document_id,
                              process_id=process_id, inference_meta=meta)
