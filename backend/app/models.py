"""Canonical ProcessFlow contract (schema 0.2).

These Pydantic v2 models are the single source of truth for the graph that both
the BPMN XML pipeline and the PDF/AI pipeline produce. The JSON Schema in
``schema/process-graph.schema.json`` is generated from them (see
``scripts/export_schema.py``). Changes from the illustrative 0.1 handoff schema
are listed in DECISIONS.md (D-05).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.2"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class SourceKind(str, Enum):
    pdf = "pdf"
    bpmn_xml = "bpmn_xml"


class NodeKind(str, Enum):
    start_event = "start_event"
    end_event = "end_event"
    intermediate_event = "intermediate_event"
    task = "task"
    subprocess = "subprocess"
    exclusive_gateway = "exclusive_gateway"
    parallel_gateway = "parallel_gateway"
    inclusive_gateway = "inclusive_gateway"
    event_gateway = "event_gateway"
    complex_gateway = "complex_gateway"
    unknown = "unknown"


class EdgeKind(str, Enum):
    sequence = "sequence"
    message = "message"
    association = "association"
    inferred_sequence = "inferred_sequence"
    boundary_attachment = "boundary_attachment"


class ReviewState(str, Enum):
    source_explicit = "source_explicit"  # deterministic structured source (XML)
    unverified = "unverified"
    needs_review = "needs_review"  # AI-derived, awaiting a human decision
    verified = "verified"
    needs_clarification = "needs_clarification"


class ItemOrigin(str, Enum):
    source_explicit = "source_explicit"
    ai_inferred = "ai_inferred"
    user_added = "user_added"


class ItemState(str, Enum):
    included = "included"
    excluded = "excluded"


class Direction(str, Enum):
    input = "input"
    output = "output"


class Severity(str, Enum):
    error = "error"
    warning = "warning"
    info = "info"


class ImportStatus(str, Enum):
    uploaded = "uploaded"
    extracting = "extracting"
    extracted = "extracted"  # PDF text ready, no inference run yet (see D-09)
    needs_review = "needs_review"
    ready = "ready"
    failed = "failed"


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


class SourceRef(Strict):
    type: SourceKind
    # PDF anchors
    page: int | None = Field(default=None, ge=1)
    quote: str | None = None
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    block_id: str | None = None
    # Grounding result for AI-supplied quotes; None for deterministic sources.
    grounding: Literal["exact", "normalised", "wrong_page", "not_found"] | None = None
    # BPMN XML anchors
    element_id: str | None = None
    element_type: str | None = None
    xpath: str | None = None
    line: int | None = None


class Issue(Strict):
    code: str
    severity: Severity
    message: str
    element_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    resolved: bool = False


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------


class DataItem(Strict):
    id: str
    name: str
    description: str = ""
    direction: Direction
    state: ItemState = ItemState.included
    origin: ItemOrigin
    source_refs: list[SourceRef] = Field(default_factory=list)
    edited_from_id: str | None = None


class Layout(Strict):
    x: float
    y: float
    width: float | None = None
    height: float | None = None
    source: Literal["bpmndi"] = "bpmndi"


class ProcessNode(Strict):
    id: str
    source_id: str | None = None
    kind: NodeKind
    label: str
    participant_id: str | None = None
    lane_id: str | None = None
    actor: str | None = None  # PDF: stated role, or "not_stated"
    inputs: list[DataItem] = Field(default_factory=list)
    outputs: list[DataItem] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    review_state: ReviewState
    layout: Layout | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProcessEdge(Strict):
    id: str
    source_id: str | None = None
    source: str
    target: str
    kind: EdgeKind
    label: str | None = None
    condition_expression: str | None = None
    is_default: bool = False
    source_refs: list[SourceRef] = Field(default_factory=list)
    review_state: ReviewState
    waypoints: list[tuple[float, float]] | None = None


class Participant(Strict):
    id: str
    name: str
    source_id: str | None = None
    process_ref: str | None = None


class Lane(Strict):
    id: str
    name: str
    participant_id: str | None = None
    source_id: str | None = None
    node_ids: list[str] = Field(default_factory=list)


class GraphSource(Strict):
    kind: SourceKind
    filename: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    imported_at: str
    original_id: str | None = None
    document_id: str | None = None


class ProcessGraph(Strict):
    schema_version: Literal["0.2"] = SCHEMA_VERSION
    process_id: str
    title: str
    view: Literal["process", "collaboration", "pdf_draft"] = "process"
    source: GraphSource
    participants: list[Participant] = Field(default_factory=list)
    lanes: list[Lane] = Field(default_factory=list)
    nodes: list[ProcessNode] = Field(default_factory=list)
    edges: list[ProcessEdge] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    review_state: Literal["draft", "needs_review", "reviewed", "source_explicit"]
    has_source_layout: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Variants (non-destructive tailoring)
# --------------------------------------------------------------------------


class ItemOverride(Strict):
    """Override of an inherited baseline item. Absent fields inherit."""

    node_id: str
    item_id: str
    state: ItemState | None = None
    name: str | None = None
    description: str | None = None
    reason: str | None = None


class AddedItem(Strict):
    node_id: str
    item: DataItem


class VariantChanges(Strict):
    overrides: list[ItemOverride] = Field(default_factory=list)
    added: list[AddedItem] = Field(default_factory=list)


class ChangeLogEntry(Strict):
    version: int
    at: str
    summary: list[str]


class Variant(Strict):
    schema_version: Literal["0.2"] = SCHEMA_VERSION
    id: str
    process_id: str
    name: str
    version: int
    baseline_sha256: str
    source_sha256: str
    changes: VariantChanges
    change_log: list[ChangeLogEntry] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ReviewDecision(Strict):
    process_id: str
    element_id: str
    state: ReviewState
    note: str = ""
    at: str


class ImportRecord(Strict):
    id: str
    filename: str
    kind: SourceKind
    sha256: str | None = None
    size_bytes: int
    status: ImportStatus
    created_at: str
    updated_at: str
    issues: list[Issue] = Field(default_factory=list)
    process_ids: list[str] = Field(default_factory=list)
    extraction_summary: dict[str, Any] = Field(default_factory=dict)
    inference: dict[str, Any] = Field(default_factory=dict)
