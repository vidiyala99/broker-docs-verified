import { API_BASE, DEMO_MESSAGE, isDemo } from "../config";
import { demoFile } from "./demoKey";
import type {
  Artifact,
  CertificateIssue,
  CertificateParameters,
  CertificateQueue,
  CertificateView,
  ClaimRow,
  ClaimsPage,
  LossMoney,
  LossReview,
  LossReviewListItem,
  SpreadsheetSummary,
  ClaimStatus,
  ClientDocument,
  Holder,
  DiscrepancyItem,
  DiscrepancyList,
  DocumentInfo,
  PolicyRole,
  EditResult,
  Line,
  Message,
  ProposalParameters,
  Queue,
  QueueState,
  Rejection,
  Run,
  Task,
  TaskInput,
  Template,
  TemplateSummary,
  Workspace,
} from "./types";

export type ExportFormat = "xlsx" | "pdf" | "docx";

/** Every backend error has one shape: {"error": {"code", "message", ...}}. `message` is safe to show. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public extra: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

const NETWORK_MESSAGE = "Can't reach the local server. Check that it is running, then retry.";

async function parse<T>(res: Response): Promise<T> {
  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
  let code = "http_error";
  let message = `Request failed (${res.status}). Retry.`;
  let extra: Record<string, unknown> = {};
  try {
    const body = (await res.json()) as { error?: { code: string; message: string } & Record<string, unknown> };
    if (body.error) {
      ({ code, message, ...extra } = body.error);
    }
  } catch {
    /* plain-text error (host guard) */
  }
  throw new ApiError(res.status, code, message, extra);
}

/** Where a recorded response lives (static demo). */
export function demoUrl(path: string): string {
  return `${import.meta.env.BASE_URL}demo/${demoFile("GET", path)}`;
}

/** Demo mode: the API address of a binary (PDF, kept original) becomes its recorded file. */
const assetUrl = (path: string) => (isDemo() ? demoUrl(path) : `${API_BASE}${path}`);

const demoReadOnly = () => new ApiError(403, "demo_read_only", DEMO_MESSAGE);

/** Demo mode: GET reads the recorded response; anything that would write is refused with the read-only message. */
async function demoRequest<T>(path: string, init: RequestInit): Promise<T> {
  if ((init.method ?? "GET").toUpperCase() !== "GET") throw demoReadOnly();
  let res: Response;
  try {
    res = await fetch(demoUrl(path));
  } catch {
    throw new ApiError(0, "network", NETWORK_MESSAGE);
  }
  // A static host may answer an unknown file with the app shell (200, HTML): that is a missing snapshot.
  if (!res.ok || !(res.headers.get("Content-Type") ?? "").includes("json")) {
    return parse<T>(new Response(JSON.stringify({ error: { code: "not_found", message: "Not found." } }), { status: 404 }));
  }
  // A request the UI makes that the live API answers with an error (a run with no report yet is a 404) is recorded as
  // {"__demo_status": 404, "error": {...}}: replayed with its status, so the UI takes the same path and no request fails.
  const text = await res.text();
  try {
    const body = JSON.parse(text) as { __demo_status?: number };
    if (body && typeof body.__demo_status === "number") return parse<T>(new Response(text, { status: body.__demo_status }));
  } catch {
    /* not JSON: parse() below reports it */
  }
  return parse<T>(new Response(text, { status: 200 }));
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (isDemo()) return demoRequest<T>(path, init);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json", ...init.headers } : init.headers,
    });
  } catch {
    throw new ApiError(0, "network", NETWORK_MESSAGE);
  }
  return parse<T>(res);
}

const json = (body: unknown) => JSON.stringify(body);

/** The optional Client field of the start screens: sent only when it holds text (API.md `client_name`). */
const clientBody = (name?: string) => (name && name.trim() ? { client_name: name.trim() } : {});

export const api = {
  workspace: () => request<Workspace>("/api/workspace"),
  /** Newest first. `line` filters (D9: Workflows = commercial; D5: Personal lines = personal, limit 100); omitted = all runs. */
  runs: (limit = 20, line?: Line) =>
    request<{ runs: Run[] }>(`/api/runs?limit=${limit}${line ? `&line=${line}` : ""}`).then((r) => r.runs),
  run: (id: number) => request<Run>(`/api/runs/${id}`),
  /** `additionalContext` (slice 6, Compare Quotes): optional, at most 1,000 characters; sent only when not blank. */
  startRun: (documentIds: number[], line?: Line, clientName?: string, additionalContext?: string) =>
    request<{ run: Run; created: boolean }>("/api/runs", {
      method: "POST",
      body: json({
        document_ids: documentIds,
        workflow: "compare-quotes",
        ...(line ? { line } : {}),
        ...clientBody(clientName),
        ...(additionalContext && additionalContext.trim() ? { parameters: { additional_context: additionalContext.trim() } } : {}),
      }),
    }),
  /** Re-tag a run (run header control). The body is exactly {"line": ...} (API.md). */
  setRunLine: (id: number, line: Line) => request<Run>(`/api/runs/${id}`, { method: "PATCH", body: json({ line }) }),
  /** Work queue: any subset of line / client_name ("" clears) / queue_state (API.md PATCH /api/runs/{id}). */
  patchRun: (id: number, body: { line?: Line; client_name?: string; queue_state?: QueueState }) =>
    request<Run>(`/api/runs/${id}`, { method: "PATCH", body: json(body) }),
  /** Work queue groups for one line (API.md "Work queue"). */
  queue: (line: Line) => request<Queue>(`/api/queue?line=${line}`),
  cancelRun: (id: number) => request<{ run: Run; cancelled: boolean }>(`/api/runs/${id}/cancel`, { method: "POST" }),
  retryRun: (id: number) => request<{ run: Run }>(`/api/runs/${id}/retry`, { method: "POST" }),
  deleteDocument: (id: number) => request<void>(`/api/documents/${id}`, { method: "DELETE" }),
  artifact: (runId: number) => request<Artifact>(`/api/runs/${runId}/artifact`),
  editCell: (artifactId: number, body: { block_id: string; row_key: string; column_key: string; value: string | null; base_version: number }) =>
    request<EditResult>(`/api/artifacts/${artifactId}/cells`, { method: "PATCH", body: json(body) }),
  undo: (artifactId: number, version: number, baseVersion: number) =>
    request<EditResult>(`/api/artifacts/${artifactId}/undo`, {
      method: "POST",
      body: json({ version, base_version: baseVersion }),
    }),
  regenerate: (artifactId: number, baseVersion: number) =>
    request<EditResult & { fallback: boolean; reason: string | null }>(`/api/artifacts/${artifactId}/regenerate`, {
      method: "POST",
      body: json({ base_version: baseVersion }),
    }),
  feedback: (artifactId: number, rating: number, version: number) =>
    request<{ feedback: { rating: number; comment: string | null } | null }>(`/api/artifacts/${artifactId}/feedback`, {
      method: "PUT",
      body: json({ rating, version }),
    }),
  messages: (runId: number) => request<{ messages: Message[] }>(`/api/runs/${runId}/messages`).then((r) => r.messages),
  ask: (runId: number, content: string) =>
    request<{ user_message: Message; assistant_message: Message }>(`/api/runs/${runId}/messages`, {
      method: "POST",
      body: json({ content }),
    }),
  applyEdit: (runId: number, messageId: number, editId: number) =>
    request<EditResult & { message: Message }>(`/api/runs/${runId}/messages/${messageId}/edits/${editId}/apply`, {
      method: "POST",
    }),
  suggestions: (runId: number) =>
    request<{ suggestions: string[] }>(`/api/runs/${runId}/suggestions`).then((r) => r.suggestions),
  documents: () => request<{ documents: DocumentInfo[] }>("/api/documents").then((r) => r.documents),
  templates: () => request<{ templates: TemplateSummary[] }>("/api/templates").then((r) => r.templates),
  template: (idOrSlug: string | number) => request<Template>(`/api/templates/${encodeURIComponent(String(idOrSlug))}`),
  /** `source` (work queue Build proposal): the comparison it is built from and its line (M5). */
  startProposal: (documentIds: number[], parameters: ProposalParameters, source?: { runId: number; line: Line | null }) =>
    request<{ run: Run; created: boolean }>("/api/runs", {
      method: "POST",
      body: json({
        document_ids: documentIds,
        workflow: "proposal-builder",
        parameters,
        ...(source ? { source_run_id: source.runId, ...(source.line ? { line: source.line } : {}) } : {}),
      }),
    }),
  startPolicyCheck: (slots: { documentId: number; role: PolicyRole }[], line?: Line, clientName?: string) =>
    request<{ run: Run; created: boolean }>("/api/runs", {
      method: "POST",
      body: json({
        document_ids: slots.map((s) => s.documentId),
        workflow: "policy-check",
        slots: Object.fromEntries(slots.map((s) => [String(s.documentId), s.role])),
        ...(line ? { line } : {}),
        ...clientBody(clientName),
      }),
    }),
  discrepancies: (runId: number) => request<DiscrepancyList>(`/api/runs/${runId}/discrepancies`),
  patchDiscrepancy: (runId: number, itemId: string, body: { status?: "open" | "resolved"; note?: string | null }) =>
    request<DiscrepancyItem>(`/api/runs/${runId}/discrepancies/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: json(body),
    }),
  // ---------------------------------------------------------------- certificates (slice 4, API.md "Certificates")
  startCertificate: (documentIds: number[], parameters: CertificateParameters, clientName?: string) =>
    request<{ run: Run; created: boolean }>("/api/runs", {
      method: "POST",
      body: json({ document_ids: documentIds, workflow: "certificate", parameters, ...clientBody(clientName) }),
    }),
  certificate: (runId: number) => request<CertificateView>(`/api/certificates/${runId}`),
  certificateQueue: () => request<CertificateQueue>("/api/certificates/queue"),
  holders: (client: string) =>
    request<{ client_key: string; holders: Holder[] }>(`/api/certificates/holders?client=${encodeURIComponent(client)}`).then((r) => r.holders),
  clientDocuments: (client: string) =>
    request<{ client_key: string; documents: ClientDocument[] }>(`/api/certificates/documents?client=${encodeURIComponent(client)}`).then(
      (r) => r.documents,
    ),
  addCertificateDocuments: (runId: number, documentIds: number[]) =>
    request<CertificateView>(`/api/certificates/${runId}/documents`, { method: "POST", body: json({ document_ids: documentIds }) }),
  issueCertificate: (runId: number, version: number) =>
    request<{ issue: CertificateIssue; created: boolean; certificate: CertificateView }>(`/api/certificates/${runId}/issue`, {
      method: "POST",
      body: json({ version }),
    }),
  newCertificateVersion: (runId: number) => request<CertificateView>(`/api/certificates/${runId}/new-version`, { method: "POST" }),
  certificatePdfUrl: (runId: number, version: number) => assetUrl(`/api/certificates/${runId}/issues/${version}/pdf`),
  /** D10/D15: a certificate claim decision; `resolved` needs a note (the override reason), every call sends the fingerprint. */
  patchClaim: (runId: number, itemId: string, body: { status: ClaimStatus; note?: string | null; expected_fingerprint: string }) =>
    request<unknown>(`/api/runs/${runId}/discrepancies/${encodeURIComponent(itemId)}`, { method: "PATCH", body: json(body) }),
  // ---------------------------------------------------------------- loss reviews (slice 5a, API.md "Loss reviews")
  startLossReview: (documentIds: number[], clientName?: string) =>
    request<{ run: Run; created: boolean }>("/api/runs", {
      method: "POST",
      body: json({ document_ids: documentIds, workflow: "loss-run", ...clientBody(clientName) }),
    }),
  lossReviews: () => request<{ reviews: LossReviewListItem[] }>("/api/loss-runs").then((r) => r.reviews),
  lossReview: (runId: number) => request<LossReview>(`/api/loss-runs/${runId}`),
  /** One page of the claims table; `params` are the API's query (sort, dir, quick filters, document_id, scope...). */
  lossClaims: (runId: number, params: Record<string, string | number | boolean | null | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v != null && v !== false && v !== "") q.set(k, String(v));
    return request<ClaimsPage>(`/api/loss-runs/${runId}/claims?${q}`);
  },
  lossRowDecision: (runId: number, rowId: number, body: { kind: "edit"; amounts: Partial<Record<LossMoney, number>> } | { kind: "confirm" | "leave_out"; note: string }) =>
    request<{ decision: { id: number; kind: string }; review: LossReview }>(`/api/loss-runs/${runId}/rows/${rowId}/decision`, {
      method: "POST",
      body: json(body),
    }),
  lossUndoDecision: (runId: number, decisionId: number) =>
    request<{ review: LossReview }>(`/api/loss-runs/${runId}/decisions/${decisionId}`, { method: "DELETE" }),
  lossAddClaim: (runId: number, body: Record<string, string | number | null>) =>
    request<{ row: ClaimRow; review: LossReview }>(`/api/loss-runs/${runId}/claims`, { method: "POST", body: json(body) }),
  lossRemoveAdded: (runId: number, rowId: number) =>
    request<{ review: LossReview }>(`/api/loss-runs/${runId}/claims/${rowId}`, { method: "DELETE" }),
  lossCheck: (runId: number, itemId: string, body: { action: "accept"; note: string } | { action: "pick"; document_id: number } | { action: "reopen" }, expected?: string) =>
    request<{ review?: LossReview } & Partial<LossReview>>(`/api/loss-runs/${runId}/checks/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: json({ ...body, ...(expected ? { expected_fingerprint: expected } : {}) }),
    }),
  sheetSummary: (documentId: number) => request<DocumentInfo & { spreadsheet?: SpreadsheetSummary }>(`/api/documents/${documentId}/sheets`),
  tasks: () => request<{ tasks: Task[] }>("/api/tasks").then((r) => r.tasks),
  createTask: (body: TaskInput) => request<Task>("/api/tasks", { method: "POST", body: json(body) }),
  patchTask: (id: number, body: TaskInput) => request<Task>(`/api/tasks/${id}`, { method: "PATCH", body: json(body) }),
  /** Sent only when the undo toast closes or the page unmounts (D6). */
  deleteTask: (id: number) => request<void>(`/api/tasks/${id}`, { method: "DELETE" }),
  fileUrl: (documentId: number) => assetUrl(`/api/documents/${documentId}/file`),
  /** Slice 6: the file as uploaded (a Word or Excel quote's kept original; a PDF returns itself), as an attachment. */
  originalUrl: (documentId: number) => assetUrl(`/api/documents/${documentId}/original`),
  eventsUrl: (runId: number, lastEventId?: number) =>
    `${API_BASE}/api/runs/${runId}/events${lastEventId != null ? `?last_event_id=${lastEventId}` : ""}`,
};

/** Download an export. Returns the saved file name and the X-Export-Note header (XLSX holds tables only). */
export async function downloadExport(artifactId: number, format: ExportFormat, version: number) {
  if (isDemo()) throw demoReadOnly();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/artifacts/${artifactId}/export?format=${format}&version=${version}`);
  } catch {
    throw new ApiError(0, "network", NETWORK_MESSAGE);
  }
  if (!res.ok) await parse(res);
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const filename = match?.[1] ?? `compare-quotes.${format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return { filename, note: res.headers.get("X-Export-Note") };
}

/** One file per request so each row gets its own progress bar (state table: Upload loading). */
export function uploadFile(
  file: File,
  onProgress: (fraction: number) => void,
  path: "/api/documents" | "/api/documents/ams" | "/api/documents/spreadsheets" = "/api/documents",
): Promise<{ documents: DocumentInfo[]; rejected: Rejection[] }> {
  if (isDemo()) return Promise.reject(demoReadOnly());
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}${path}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* fall through */
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as { documents: DocumentInfo[]; rejected: Rejection[] });
      } else {
        const err = (body as { error?: { code: string; message: string } } | null)?.error;
        reject(new ApiError(xhr.status, err?.code ?? "http_error", err?.message ?? "Upload failed. Retry."));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "network", NETWORK_MESSAGE));
    const form = new FormData();
    form.append("files", file, file.name);
    xhr.send(form);
  });
}
