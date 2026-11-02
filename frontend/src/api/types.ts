// Shapes from backend/API.md. Keep in step with that file.

export interface Limits {
  max_pages: number;
  max_bytes: number;
  max_megabytes: number;
  max_documents_per_run: number;
  accepts: string;
  run_token_budget: number;
}

export interface Workspace {
  id: number;
  name: string;
  app_name: string;
  model_configured: boolean;
  limits: Limits;
  workflows: { id: number; slug: string; name: string; category: string }[];
  default_workflow: string;
}

export interface DocumentInfo {
  id: number;
  filename: string;
  size_bytes: number;
  page_count: number | null;
  sha256: string;
  kind: string;
  parse_error: string | null;
  warning: string | null;
  created_at: string;
}

export interface Rejection {
  filename: string;
  code: "not_pdf" | "empty" | "too_large" | "too_many_pages" | "too_many_documents" | string;
  message: string;
  limit?: number;
}

export type RunStatus = "queued" | "running" | "composing" | "done" | "partial" | "failed" | "cancelled";
export type DocStatus = "queued" | "reading" | "extracting" | "verifying" | "done" | "failed";

export interface RunDocument {
  run_document_id: number;
  document_id: number;
  position: number;
  letter: string;
  filename: string;
  status: DocStatus;
  progress: string | null;
  error: string | null;
  error_code: string | null;
  option_count: number;
  option_label: string | null;
}

export interface Run {
  id: number;
  workflow_id: number;
  workflow_name: string;
  status: RunStatus;
  active: boolean;
  terminal: boolean;
  error: string | null;
  retryable: boolean;
  parameters: Record<string, unknown>;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  documents: RunDocument[];
  artifact: { id: number; version: number } | null;
}

export interface Segment {
  text: string;
  passage_ids: number[];
}

export interface Chip {
  passage_id: number;
  label: string;
  letter: string;
  document_id: number;
  filename: string;
  page_no: number;
  verified: boolean;
  rects: [number, number, number, number][];
  page_width: number;
  page_height: number;
}

export type EvidenceState = "verified" | "scan_read" | "not_found" | "absent" | "failed" | "conflicting";

export interface Notice {
  kind: "compose_fallback" | "budget_exhausted" | "documents_failed" | "info" | "conflicting_values";
  message: string;
  retryable: boolean;
  dismissible: boolean;
}

export interface ConflictValue {
  value: string | null;
  page_no: number | null;
  passage_id: number | null;
}

export interface Cell {
  value: string | null;
  passage_ids: number[];
  fact_ids: number[];
  state: EvidenceState | null;
  edited: boolean;
  original_value: string | null;
  original_passage_ids: number[];
  notice?: Notice | null;
  conflicts?: ConflictValue[];
}

export interface Column {
  key: string;
  label: string;
  document_id: number | null;
  warning: string | null;
}

export interface Row {
  key: string;
  label: string;
  cells: Record<string, Cell>;
}

export type Block =
  | { type: "heading"; id: string; level: number; text: string }
  | { type: "paragraph"; id: string; segments: Segment[] }
  | { type: "list"; id: string; title: string | null; items: Segment[][] }
  | { type: "table"; id: string; title: string | null; columns: Column[]; rows: Row[] }
  | {
      type: "issue";
      id: string;
      code: string;
      title: string;
      severity: string;
      category: string;
      impact: Segment[];
      affected_layers: string;
      follow_form: string;
    };

export interface Section {
  id: string;
  number: string;
  title: string;
  level: number;
  stale: boolean;
  blocks: Block[];
}

export interface ArtifactContent {
  notices: Notice[];
  sections: Section[];
}

export interface Counts {
  verified: number;
  scan_read: number;
  not_found: number;
  absent: number;
  failed: number;
  conflicting?: number;
  edited: number;
}

export interface Artifact {
  id: number;
  run_id: number;
  title: string;
  kind: string;
  version: number;
  current_version: number;
  is_current: boolean;
  note: string | null;
  created_at: string;
  content: ArtifactContent;
  chips: Record<string, Chip>;
  counts: Counts;
  stale_sections: string[];
  feedback: { rating: number; comment: string | null } | null;
}

export interface EditResult {
  artifact: Artifact;
  version: number;
  previous_version: number;
  stale_sections: string[];
}

export interface ProposedEdit {
  id: number;
  block_id: string;
  row_key: string;
  column_key: string;
  value: string | null;
  reason: string;
  current_value: string | null;
  proposed_on_version: number;
  status: "proposed" | "applied";
  applied_version?: number;
}

export interface Message {
  id: number;
  run_id: number;
  role: "user" | "assistant";
  content: string;
  segments: Segment[];
  passage_ids: number[];
  proposed_edits: ProposedEdit[];
  created_at: string;
}

// ---------------------------------------------------------------- proposal templates (slice 2)

export type BlockKind =
  | "cover_page"
  | "table_of_contents"
  | "client_details"
  | "line_comparison"
  | "premium_comparison"
  | "subjectivities"
  | "narrative";

export interface OutlineBlock {
  id: string;
  kind: BlockKind;
  title: string;
  description: string;
  ai_instruction: string;
  fact_sections: string[];
  locked: boolean;
}

export interface TemplateSummary {
  id: number;
  slug: string;
  name: string;
  description: string;
  block_count: number;
}

export interface TemplateKind {
  kind: BlockKind;
  title: string;
  table: boolean;
  description: string;
  fact_sections: string[];
}

export interface FactSection {
  id: string;
  title: string;
  fields: { key: string; label: string }[];
}

export interface TemplateLimits {
  max_blocks: number;
  max_title_chars: number;
  max_description_chars: number;
  max_instruction_chars: number;
  max_fact_sections_per_block: number;
  max_context_chars: number;
  max_name_chars: number;
}

export interface Template extends TemplateSummary {
  workflow: string;
  blocks: OutlineBlock[];
  theme: {
    font_family: string;
    body_size_pt: number;
    heading_color: string;
    heading_sizes_pt: Record<string, number>;
    accent_color: string;
  };
  variables: string[];
  kinds: TemplateKind[];
  fact_sections: FactSection[];
  limits: TemplateLimits;
}

export interface ProposalParameters {
  template_id: string;
  broker_name: string;
  brokerage_name: string;
  client_name: string;
  additional_context: string;
  no_ai_prose: boolean;
  outline: Omit<OutlineBlock, "locked">[];
}

/** One `fields` entry of a 422 invalid_request. `loc` list indexes come as strings. */
export interface FieldError {
  loc: (string | number)[];
  message: string;
}

export interface RunEvent {
  id?: number;
  run_id: number;
  type: "run.status" | "document.status" | "run.usage" | "artifact.ready" | "run.end" | "run.snapshot";
  data: Record<string, unknown>;
}
