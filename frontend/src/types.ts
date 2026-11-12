// JSON-compatible mirrors of backend/app/models.py (schema 0.2).

export type NodeKind =
  | "start_event" | "end_event" | "intermediate_event" | "task" | "subprocess"
  | "exclusive_gateway" | "parallel_gateway" | "inclusive_gateway" | "event_gateway"
  | "complex_gateway" | "unknown";
export type EdgeKind = "sequence" | "message" | "association" | "inferred_sequence" | "boundary_attachment";
export type ReviewState = "source_explicit" | "unverified" | "needs_review" | "verified" | "needs_clarification";
export type ItemOrigin = "source_explicit" | "ai_inferred" | "user_added";
export type ItemState = "included" | "excluded";
export type Direction = "input" | "output";
export type Severity = "error" | "warning" | "info";
export type ImportStatus = "uploaded" | "extracting" | "extracted" | "needs_review" | "ready" | "failed";

export interface SourceRef {
  type: "pdf" | "bpmn_xml";
  page?: number | null;
  quote?: string | null;
  bbox?: number[] | null;
  block_id?: string | null;
  grounding?: "exact" | "normalised" | "wrong_page" | "not_found" | null;
  element_id?: string | null;
  element_type?: string | null;
  xpath?: string | null;
  line?: number | null;
}

export interface Issue {
  code: string;
  severity: Severity;
  message: string;
  element_ids: string[];
  source_refs: SourceRef[];
  resolved: boolean;
}

export interface DataItem {
  id: string;
  name: string;
  description: string;
  direction: Direction;
  state: ItemState;
  origin: ItemOrigin;
  source_refs: SourceRef[];
  edited_from_id?: string | null;
}

export interface ProcessNode {
  id: string;
  source_id?: string | null;
  kind: NodeKind;
  label: string;
  participant_id?: string | null;
  lane_id?: string | null;
  actor?: string | null;
  inputs: DataItem[];
  outputs: DataItem[];
  source_refs: SourceRef[];
  review_state: ReviewState;
  layout?: { x: number; y: number; width?: number | null; height?: number | null } | null;
  attributes: Record<string, unknown>;
}

export interface ProcessEdge {
  id: string;
  source_id?: string | null;
  source: string;
  target: string;
  kind: EdgeKind;
  label?: string | null;
  condition_expression?: string | null;
  is_default: boolean;
  source_refs: SourceRef[];
  review_state: ReviewState;
}

export interface Lane { id: string; name: string; participant_id?: string | null; node_ids: string[] }
export interface Participant { id: string; name: string; process_ref?: string | null }

export interface ProcessGraph {
  schema_version: string;
  process_id: string;
  title: string;
  view: "process" | "collaboration" | "pdf_draft";
  source: { kind: "pdf" | "bpmn_xml"; filename: string; sha256: string; imported_at: string; original_id?: string | null; document_id?: string | null };
  participants: Participant[];
  lanes: Lane[];
  nodes: ProcessNode[];
  edges: ProcessEdge[];
  issues: Issue[];
  review_state: string;
  has_source_layout: boolean;
  provenance: Record<string, unknown>;
}

export interface ItemOverride {
  node_id: string;
  item_id: string;
  state?: ItemState | null;
  name?: string | null;
  description?: string | null;
  reason?: string | null;
}
export interface AddedItem { node_id: string; item: DataItem }
export interface VariantChanges { overrides: ItemOverride[]; added: AddedItem[] }

export interface Variant {
  id: string;
  process_id: string;
  name: string;
  version: number;
  baseline_sha256: string;
  source_sha256: string;
  changes: VariantChanges;
  change_log: { version: number; at: string; summary: string[] }[];
  created_at: string;
  updated_at: string;
}

export interface ReviewDecision { process_id: string; element_id: string; state: ReviewState; note: string; at: string }

export interface ProcessSummary {
  process_id: string;
  title: string;
  view: ProcessGraph["view"];
  node_count: number;
  edge_count: number;
  issue_count: number;
}

export interface ProviderStatus {
  provider: string;
  configured: boolean;
  ready: boolean;
  remote: boolean;
  model: string | null;
  message: string;
}

export interface ImportView {
  id: string;
  filename: string;
  kind: "pdf" | "bpmn_xml";
  sha256: string | null;
  size_bytes: number;
  status: ImportStatus;
  created_at: string;
  updated_at: string;
  issues: Issue[];
  process_ids: string[];
  processes: ProcessSummary[];
  extraction_summary: Record<string, unknown>;
  inference: { state?: string; error?: string; error_code?: string; model?: string; provider?: ProviderStatus };
}

export interface ProcessBundle {
  baseline: ProcessGraph;
  baseline_sha256: string;
  reviews: ReviewDecision[];
  variants: { id: string; name: string; version: number; updated_at: string }[];
}

export interface Extraction {
  page_count: number;
  blocks: { id: string; page: number; bbox: number[]; text: string; role: string }[];
  tables: { id: string; page: number; rows: string[][] }[];
}
