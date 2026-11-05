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
  /** Slice 6: the uploaded file's format. Word/Excel quotes are stored as a rendered PDF plus the original
   * (GET /api/documents/{id}/original). AMS exports: "json" | "csv"; spreadsheet loss runs: "xlsx" | "csv". */
  source_format?: SourceFormat;
  parse_error: string | null;
  warning: string | null;
  created_at: string;
  /** Only on the POST /api/documents/ams response. */
  ams?: AmsSummary;
  /** Only on spreadsheet uploads (POST /api/documents/spreadsheets, slice 5a). */
  spreadsheet?: SpreadsheetSummary;
}

/** Slice 6 (D1): "pdf" | "docx" | "xlsx" for POST /api/documents. */
export type SourceFormat = "pdf" | "docx" | "xlsx" | "json" | "csv";

export interface Rejection {
  filename: string;
  /** Slice 6 adds Word/Excel refusals: legacy_format, macros, external_links, format_mismatch, encrypted,
   * unpacked_too_large, too_many_entries, unsafe_xml, unreadable, cell_too_long, too_many_columns, too_many_rows,
   * too_many_cells, too_many_sheets (see API.md). */
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
  /** Input slot key (slice 3): "quotes", or a Policy Check role. Older payloads may omit it. */
  slot?: string;
  filename: string;
  /** Slice 6: the document's uploaded format (column label). Older payloads may omit it (= "pdf"). */
  source_format?: SourceFormat;
  status: DocStatus;
  progress: string | null;
  error: string | null;
  error_code: string | null;
  option_count: number;
  option_label: string | null;
  /** Slice 4 extraction cache: the run whose read was copied (null = read live), and when it was first read. */
  reused_from_run_id?: number | null;
  read_at?: string | null;
}

/** Slice 3b: a run's book of business. Personal lines = household cover (R1); a tag only (R2). */
export type Line = "commercial" | "personal";

export interface Run {
  id: number;
  workflow_id: number;
  workflow_name: string;
  line: Line;
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
  /** Work queue (backend/API.md "Run"): broker client name, queue state, linked proposal. */
  client_name: string | null;
  queue_state: QueueState;
  proposal_run_id: number | null;
  queue_changed_at: string | null;
  /** The name the run's queue row shows (typed, else from the documents); single-run responses only (API.md). */
  client?: string | null;
  client_source?: "broker" | "extracted" | null;
}

/** Work queue (backend/API.md "Work queue"). */
export type QueueState = "open" | "done" | "dismissed";
export type QueueGroup = "in_progress" | "needs_review" | "ready" | "needs_attention" | "done_recently";

export interface QueueItem {
  run_id: number;
  workflow_slug: string;
  workflow_name: string;
  line: Line;
  /** When the work was started (the run's created_at; queue navigation, API.md). */
  created_at: string | null;
  /** One per document in column order: its verified carrier name, or null; shown when two rows share a client. */
  carriers: (string | null)[];
  client: string | null;
  client_source: "broker" | "extracted" | null;
  summary: string;
  open_counts: Record<"critical" | "high" | "medium" | "low", number> | null;
  reasons: string[] | null;
  status: RunStatus;
  retryable: boolean;
  finished_at: string | null;
  queue_state: QueueState;
  queue_changed_at: string | null;
  proposal_run_id: number | null;
  /** The linked proposal run's `retryable` ("Retry proposal"), null without a linked proposal (API.md). */
  proposal_retryable: boolean | null;
  /** The workflow's primary action label ("Review losses"; slice 5a T12). */
  action?: string | null;
  /** Loss reviews: checks needing action; null for other workflows. */
  open_checks?: number | null;
}

export type Queue = { line: Line; truncated: boolean } & Record<QueueGroup, QueueItem[]>;

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
    }
  | DiscrepancyBlock
  | ClaimBlock
  | CertificateBlock;

// ---------------------------------------------------------------- policy check (slice 3)

export type PolicyRole = "expiring" | "quote" | "renewal" | "ams";
export const POLICY_ROLES: PolicyRole[] = ["expiring", "quote", "renewal", "ams"];
export type Severity = "critical" | "high" | "medium" | "low";
export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];

export interface DiscrepancyBlock {
  type: "discrepancy";
  id: string;
  key: string;
  title: string;
  severity: Severity;
  item_state: "discrepancy" | "needs_review";
  lob: string | null;
  prose: Segment[];
  slots: Partial<Record<PolicyRole, Cell>>;
}

/** One cited fact behind a slot's chips (GET /discrepancies `sources`). */
export interface SlotSource {
  passage_id: number;
  page_no: number | null;
  rects: [number, number, number, number][];
  /** AMS documents only: the field the fact cites ("Cov A"). */
  field: string | null;
}

export interface DiscrepancyItem extends DiscrepancyBlock {
  status: "open" | "resolved";
  note: string | null;
  changed_since_resolved: boolean;
  updated_at: string | null;
  sources?: Partial<Record<PolicyRole, SlotSource[]>>;
  /** Slice 3b: the task linked to this item (first open, else newest), or null (API.md, O4). */
  task?: { id: number; status: TaskStatus } | null;
}

// ---------------------------------------------------------------- certificates (slice 4, backend/API.md "Certificates")

export type Wording = "additional_insured" | "waiver_of_subrogation" | "primary_noncontributory";
export type ClaimKind = "wording" | "in_force" | "insured" | "description" | "edited";
export type ClaimVerdict = "supported" | "not_supported" | "needs_signoff";
export type ClaimStatus = "open" | "resolved" | "removed";

export interface CertificateParameters {
  holder_name: string;
  holder_address?: string;
  wording?: Partial<Record<Wording, boolean>>;
  /** YYYY-MM-DD; omitted = today. */
  certificate_date?: string | null;
  description?: string;
}

export interface ClaimBlock {
  type: "claim";
  id: string;
  key: string;
  kind: ClaimKind;
  title: string;
  verdict: ClaimVerdict;
  reason: string | null;
  condition: string | null;
  wording: Wording | null;
  policy_letter: string | null;
  removable: boolean;
  prose: Segment[];
  basis: string[];
}

export interface ClaimItem extends ClaimBlock {
  status: ClaimStatus;
  note: string | null;
  changed_since_resolved: boolean;
  updated_at: string | null;
  /** Send back as expected_fingerprint on PATCH (D15). */
  fingerprint: string;
  needs_action: boolean;
  task?: { id: number; status: TaskStatus } | null;
}

export interface CertificateField {
  key: string;
  label: string;
  cell: Cell;
}

export interface CertificatePolicy {
  letter: string;
  insurer: string | null;
  document_id: number | null;
  line: "general_liability" | "auto" | "umbrella" | "workers_comp" | "property" | "other";
  line_label: string;
  fields: CertificateField[];
  wording: Wording[];
}

export interface CertificateBlock {
  type: "certificate";
  id: "certificate";
  layout: string;
  certificate_date: string;
  holder_name: string;
  holder_address: string;
  producer: CertificateField | null;
  insured: CertificateField[];
  insurers: { letter: string; name: string; naic: string | null }[];
  policies: CertificatePolicy[];
  description: string | null;
}

export interface CertificateIssue {
  id: number;
  run_id: number;
  artifact_id: number;
  version: number;
  issued_at: string;
  holder: { name: string; address: string };
  claims: { key: string; title: string; kind: ClaimKind; verdict: ClaimVerdict; reason: string | null;
    condition: string | null; status: ClaimStatus; note: string | null }[];
  pdf_sha256: string;
  pdf_bytes: number;
  pdf_url: string;
}

export type CertificateBlocked = "run_active" | "claims_open" | "no_coverage" | "issued" | "no_draft";

/** GET /api/certificates/{run_id} */
export interface CertificateView {
  run: Run;
  client: string | null;
  client_source: "broker" | "extracted" | null;
  artifact_id: number | null;
  version: number | null;
  certificate: CertificateBlock | null;
  claims: ClaimItem[];
  counts: { total: number; open: number; supported: number; overridden: number; removed: number } | null;
  notices: Notice[];
  chips: Record<string, Chip>;
  issue: CertificateIssue | null;
  issues: CertificateIssue[];
  can_issue: boolean;
  blocked: CertificateBlocked[];
}

export interface Holder {
  id: number;
  name: string;
  address: string;
  last_used_at: string;
}

/** GET /api/certificates/documents: the client's documents, newest upload per sha256 (C3). */
export interface ClientDocument extends DocumentInfo {
  already_read: boolean;
  read_at: string | null;
}

/** GET /api/certificates/documents: the client's documents, newest upload per sha256 (C3). */
export interface ClientDocument extends DocumentInfo {
  already_read: boolean;
  read_at: string | null;
}

export type CertificateQueueGroup = "in_progress" | "needs_attention" | "ready" | "done_recently";

export interface CertificateQueueItem {
  run_id: number;
  workflow_slug: "certificate";
  workflow_name: string;
  line: Line;
  created_at: string | null;
  client: string | null;
  client_source: "broker" | "extracted" | null;
  summary: string;
  reasons: string[] | null;
  open_claims: number | null;
  status: RunStatus;
  retryable: boolean;
  finished_at: string | null;
  queue_state: QueueState;
  queue_changed_at: string | null;
  issued_at: string | null;
}

export type CertificateQueue = { truncated: boolean } & Record<CertificateQueueGroup, CertificateQueueItem[]>;

export type TaskStatus = "open" | "done";

/** GET /api/tasks item (slice 3b, API.md "Tasks"). The read-only fields are computed by the server. */
export interface Task {
  id: number;
  title: string;
  due_date: string | null;
  status: TaskStatus;
  run_id: number | null;
  discrepancy_item_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  run_label: string | null;
  run_missing: boolean;
  discrepancy: { item_id: string; title: string; status: "open" | "resolved"; in_report: boolean } | null;
}

/** POST takes title, due_date, run_id, discrepancy_item_id; PATCH also status. null clears. */
export interface TaskInput {
  title?: string;
  due_date?: string | null;
  status?: TaskStatus;
  run_id?: number | null;
  discrepancy_item_id?: string | null;
}

export interface DiscrepancyList {
  run_id: number;
  artifact_id: number;
  version: number;
  total: number;
  resolved: number;
  counts: Record<Severity, number>;
  open_counts: Record<Severity, number>;
  items: DiscrepancyItem[];
}

export interface AmsSummary {
  format: "json" | "csv";
  field_count: number;
  unmapped: string[];
  notices: string[];
}

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

// --------------------------------------------------------------------- loss reviews (slice 5a, backend/API.md)
// Money is integer cents. The claims are paged from GET /api/loss-runs/{id}/claims, never part of the artifact.

export type LossRole = "counted" | "superseded" | "conflicting" | "left_out";
// Named LossClaimStatus: ClaimStatus is the certificate claim status (above).
export type LossClaimStatus = "open" | "closed" | "reopened" | "unknown";
export type LossMoney = "paid" | "reserve" | "incurred" | "recovery" | "deductible";
export type LossAmounts = Record<LossMoney, number | null>;
export type LossQuickFilter = "over_100k" | "open_only" | "last_3_months" | "open_reserve";

export interface SheetMapping {
  index: number;
  header: string;
  column: string;
  label: string;
  by: "synonym" | "model";
}

export interface SheetSummary {
  name: string;
  rows: number;
  data_rows: number;
  header_row: number | null;
  has_claims_table: boolean;
  mapped: SheetMapping[];
  unmapped: { index: number; header: string }[];
}

/** POST /api/documents/spreadsheets: each accepted document's `spreadsheet`. */
export interface SpreadsheetSummary {
  format: "xlsx" | "csv";
  data_rows: number;
  page_count: number;
  sheets: SheetSummary[];
}

export interface LossChip {
  document_id: number;
  letter: string;
  filename: string;
  kind: "page" | "sheet";
  /** "Travelers p.14" or "Sheet 2024, row 17". */
  label: string;
  /** PDF page, or the sheet page to load from `url`. */
  page_no: number | null;
  rects: number[][];
  page_width: number | null;
  page_height: number | null;
  url: string | null;
}

export interface LossMetricTotal {
  cents: number;
  from_claims: number;
}

export interface LossDocumentSummary {
  document_id: number;
  letter: string;
  filename: string;
  kind: string;
  carrier: string | null;
  valuation_date: string | null;
  status: string;
  rows: number;
  counted_rows: number;
  unverified_rows: number;
  printed_totals: number;
  unread_pages: number[];
}

export interface LossTotals {
  type: "loss_totals";
  id: string;
  claims: number;
  open_claims: number;
  paid: LossMetricTotal;
  reserve: LossMetricTotal;
  incurred: LossMetricTotal;
  recovery: LossMetricTotal;
  deductible: LossMetricTotal;
  unreconciled: boolean;
  as_of: string | null;
  counts: Record<string, number>;
  documents: LossDocumentSummary[];
}

export interface LossCheckOption extends Partial<LossAmounts> {
  document_id: number;
  label: string;
  valuation_date: string | null;
  claim_row_ids: number[];
}

export interface LossCheck {
  type: "loss_check";
  id: string;
  kind: "total" | "conflict" | "unverified";
  verdict: "matches" | "off" | "needs_review" | "conflicting" | "unverified" | "handled";
  decided_by: "broker" | "rows";
  title: string;
  message: string;
  /** A printed total's label as printed ("Total, all policies"); null when none or not a total. */
  label?: string | null;
  document_id: number | null;
  metric: LossMoney | "claim_count" | null;
  scope_kind: "all" | "policy_year" | "policy_number" | "carrier" | "page_subtotal" | "sheet" | null;
  scope_value: string | null;
  printed: number | null;
  computed: number | null;
  difference: number | null;
  claim_key: string | null;
  rows: number | null;
  /** Query parameters for GET .../claims that open the rows in scope. */
  filter: Record<string, unknown>;
  options: LossCheckOption[];
  picked_document_id: number | null;
  fingerprint: string;
  status: "open" | "resolved" | "match";
  needs_action: boolean;
  changed_since_resolved: boolean;
  note: string | null;
  updated_at: string | null;
  chips: LossChip[];
}

export interface LossReview {
  run: Run;
  client: string | null;
  client_source: "broker" | "extracted" | null;
  synthetic: boolean;
  artifact_id: number | null;
  version: number | null;
  totals: LossTotals | null;
  checks: LossCheck[];
  check_counts?: { to_resolve: number; handled: number; matches: number };
  /** Claims each quick filter opens (distinct claims, effective values, D10); null before the review is built. */
  filter_counts?: Record<"all" | LossQuickFilter, number> | null;
  notices: Notice[];
  counts: Record<string, number> | null;
  /** document id -> per sheet: header row and mapped / unmapped columns (after the read). */
  sheets: Record<string, { sheet: string; index: number; header_row: number | null; mapped: SheetMapping[]; unmapped: { index: number; header: string }[] }[]>;
}

export interface ClaimRow {
  id: number;
  document_id: number | null;
  letter: string | null;
  filename: string | null;
  source: "extracted" | "added";
  location: string;
  claim_number: string;
  claim_key: string;
  claimant: string | null;
  date_of_loss: string | null;
  status: LossClaimStatus;
  status_raw: string | null;
  policy_number: string | null;
  policy_year: number | null;
  line: string | null;
  carrier: string | null;
  extracted: LossAmounts;
  effective: LossAmounts;
  claim: { paid: number | null; reserve: number | null; incurred: number | null; open: boolean; date_of_loss: string | null };
  role: LossRole;
  role_reason: string | null;
  counted: boolean;
  group_key: string;
  verify_state: "verified" | "unverified";
  verify_reason: string | null;
  decision: { id: number; kind: "edited" | "confirmed" | "left_out" } | null;
  changed_since_resolved: boolean;
  flags: string[];
  note: string | null;
  chip: LossChip | null;
  history?: ClaimRow[];
}

export interface ClaimsPage {
  run_id: number;
  total: number;
  offset: number;
  limit: number;
  sort: string;
  dir: "asc" | "desc";
  as_of: string | null;
  rows: ClaimRow[];
}

export interface LossReviewListItem {
  run_id: number;
  client: string | null;
  client_source: "broker" | "extracted" | null;
  status: RunStatus;
  active: boolean;
  queue_state: QueueState;
  files: number;
  created_at: string | null;
  finished_at: string | null;
  latest_valuation_date: string | null;
  claims: number | null;
  incurred: number | null;
  open_checks: number | null;
}
