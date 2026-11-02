# BrokerDocs local API (slice 1)

Contract between `backend/` and `frontend/`. The live OpenAPI schema is at
`GET /api/openapi.json` (interactive at `/api/docs`); this file is the
human-readable source the frontend is built from, including the SSE events
and evidence rules the OpenAPI schema cannot express.

Start the server from `broker-docs-verified/`:

```
backend/.venv/Scripts/python -m backend.main
```

It listens on `http://127.0.0.1:8000` only (port: `BROKERDOCS_PORT`). Data
lives in `broker-docs-verified/data/` (`BROKERDOCS_DATA_DIR` overrides). The model
key is read from the `OPENAI_API_KEY` environment variable on the server and
never reaches the browser.

## Conventions

- Base path `/api`. JSON in and out unless stated. Timestamps are ISO 8601 UTC.
- IDs are integers. Document columns are lettered `A` to `H` by position.
- **Origin.** CORS allows only the UI dev origin (`http://localhost:5173` and
  `http://127.0.0.1:5173`; `BROKERDOCS_UI_ORIGIN` overrides, comma list).
  Requests that change state (`POST`, `PUT`, `PATCH`, `DELETE`) from any other
  `Origin` get `403 origin_not_allowed`. The `Host` header must be `localhost`
  or `127.0.0.1` (else `400`). No cookies or credentials are used.
- In development, either call `http://127.0.0.1:8000/api/...` directly from the
  Vite app, or proxy `/api` through Vite; both pass the guards.
- Every response carries `X-Content-Type-Options: nosniff`; JSON responses are
  `Cache-Control: no-store`.

## Errors

Every error has one shape:

```json
{"error": {"code": "version_conflict", "message": "The report changed (now version 4). Reload and edit again."}}
```

`message` is safe to show as is. Some errors add fields next to `code` and
`message` (listed per route): `limit`, `run_id`, `run_status`, `version`,
`retryable`, and for `422 invalid_request` a `fields` list of
`{"loc": ["body", "value"], "message": "..."}`.

| Status | Codes |
|---|---|
| 400 | `Invalid host header` (plain text from the host guard) |
| 403 | `origin_not_allowed` |
| 404 | `not_found`, `no_report`, `cell_not_found`, `version_not_found`, `file_missing` |
| 409 | `document_in_use`, `not_retryable`, `run_active`, `version_conflict`, `regenerate_in_progress`, `already_applied`, `chat_busy`, `no_report`, `budget_exhausted` |
| 413 | `request_too_large` |
| 422 | `invalid_request`, `invalid_run`, `context_overflow` |
| 500 | `internal_error`, `export_failed` |
| 502 | `answer_rejected`, chat provider codes (`refusal`, `timeout`, `unavailable`, `schema_invalid`, `truncated`, `request_error`) |
| 503 | `missing_key` |

## Health and workspace

### `GET /api/health`

```json
{"status": "ok", "app": "BrokerDocs", "model_configured": true}
```

`model_configured` is false when `OPENAI_API_KEY` is unset. The app still
works; every model call then fails with `missing_key` (runs show it per
document).

### `GET /api/workspace`

```json
{
  "id": 1, "name": "Local workspace", "app_name": "BrokerDocs", "model_configured": true,
  "limits": {"max_pages": 80, "max_bytes": 15728640, "max_megabytes": 15.0,
             "max_documents_per_run": 8, "accepts": "application/pdf", "run_token_budget": 600000},
  "workflows": [{"id": 1, "slug": "compare-quotes", "name": "Compare Quotes", "category": "Quoting"}],
  "default_workflow": "compare-quotes"
}
```

Use `limits` for the drop zone text (DG6) and `app_name` for the wordmark (DG7).

### `GET /api/workflows`

`{"workflows": [WorkflowSummary]}` (shape as in `workspace.workflows`).

### `GET /api/workflows/{id_or_slug}`

```json
{"id": 1, "slug": "compare-quotes", "name": "Compare Quotes", "category": "Quoting",
 "input_slots": [{"key": "quotes", "label": "Quotes", "accepts": "application/pdf", "min": 1, "max": 8}],
 "instructions": "...",
 "section_plan": [{"id": "executive_summary", "number": "1", "title": "Executive Summary", "guidance": "..."}],
 "fact_schema": [{"key": "carrier_name", "label": "Carrier", "description": "...", "section": "common_declarations"}]}
```

## Documents

### `POST /api/documents` (multipart)

Form field `files`, repeated once per file. Always `200` with both lists, so
the UI can keep good files and show inline rejections (state table: Upload
partial).

```json
{
  "documents": [Document],
  "rejected": [{"filename": "huge.pdf", "code": "too_large",
                "message": "This file is larger than 15 MB; the limit is 15 MB per document.",
                "limit": 15728640}]
}
```

Rejection codes: `not_pdf` (no `%PDF-` magic bytes, whatever the name or
type), `empty`, `too_large` (`limit` in bytes), `too_many_pages` (`limit` in
pages), `too_many_documents` (more than 8 files in one request; `limit` 8).
A request whose `Content-Length` exceeds every cap combined gets
`413 request_too_large` before anything is read.

A damaged or password-protected PDF is **accepted** with `parse_error` and a
`warning`; a run then fails only that document's column with the same reason
(spec section 6).

`Document`:

```json
{"id": 3, "filename": "harborline.pdf", "size_bytes": 10781, "page_count": 3,
 "sha256": "9f2c...", "kind": "quote", "parse_error": null, "warning": null,
 "created_at": "2026-11-01T21:04:05.123456+00:00"}
```

`filename` is display text only (base name, control and path characters
removed). Files are stored under generated UUID names.

### `GET /api/documents`

`{"documents": [Document]}`, newest first.

### `GET /api/documents/{id}`

`Document`. `404 not_found`.

### `GET /api/documents/{id}/file`

The PDF bytes (`application/pdf`, `Content-Disposition: inline`). Feed this
URL to pdf.js for the source viewer. `404 file_missing` if the file is gone
("Couldn't open this file" state).

### `DELETE /api/documents/{id}`

`204`. `409 document_in_use` (with `run_id`) when a run uses the document.

## Runs

### `POST /api/runs`

```json
{"document_ids": [3, 4], "workflow": "compare-quotes", "parameters": {}}
```

`workflow` (slug or id) and `parameters` are optional. Order of
`document_ids` is the column order (A, B, ...).

- `201 {"run": Run, "created": true}` for a new run (it starts at once).
- `200 {"run": Run, "created": false}` when an identical run is still active
  (double-click Run returns the same run).
- `422 invalid_run`: no documents, a duplicate, or more than 8 (`limit: 8`).
- `404 not_found`: unknown workflow or document.

`Run`:

```json
{
  "id": 12, "workflow_id": 1, "workflow_name": "Compare Quotes",
  "status": "running", "active": true, "terminal": false,
  "error": null, "retryable": false, "parameters": {},
  "tokens_in": 5120, "tokens_out": 830, "cost_usd": 0.0029,
  "created_at": "...", "started_at": "...", "finished_at": null,
  "documents": [RunDocument],
  "artifact": {"id": 7, "version": 1}
}
```

`status`: `queued`, `running` (documents in progress), `composing` (all
documents settled, "Writing report..."), `done`, `partial` (report written,
some documents failed or the budget ran out), `failed` (no report: every
document failed, or a crash; `retryable` true), `cancelled`. `terminal` is true
once the status is final and no task is running. `artifact` is null until a
report exists.

`RunDocument` (identical to the SSE `document.status` payload):

```json
{"run_document_id": 31, "document_id": 3, "position": 0, "letter": "A",
 "filename": "harborline.pdf", "status": "reading", "progress": "Reading pages 4/10",
 "error": null, "error_code": null, "option_count": 1, "option_label": null}
```

`status`: `queued`, `reading`, `extracting`, `verifying`, `done`, `failed`.
`progress` is the per-row text (DG4). On `failed`, `error` is the user-facing
reason and `error_code` is one of: `not_pdf`, `empty`, `corrupt`, `encrypted`,
`too_large`, `too_many_pages`, `budget_exhausted`, `refusal`,
`schema_invalid`, `truncated`, `timeout`, `unavailable`, `context_overflow`,
`request_error`, `missing_key`, `cancelled`, `interrupted`, `internal_error`.
`option_count > 1` means only the first option was compared (D8); the column
header shows the warning from the artifact table.

### `GET /api/runs?limit=20`

`{"runs": [Run]}`, newest first (Recent runs list, DG1). `limit` 1 to 100.

### `GET /api/runs/{id}`

`Run`.

### `POST /api/runs/{id}/retry`

`202 {"run": Run}`. Re-runs only the documents that did not finish, then
recomposes (a new artifact version). Cell edits are carried into the new
version and the sections they affect are flagged stale.
`409 not_retryable` (with `run_status`) unless the run is `failed`,
`partial` or `cancelled` and idle.

### `POST /api/runs/{id}/cancel`

`200 {"run": Run, "cancelled": true}`. `cancelled` is false when nothing was
running. A cancel writes no new artifact version (a report from an earlier
execution stays); unfinished documents fail with `cancelled`, and the run can
be retried.

### `GET /api/runs/{id}/events` (Server-Sent Events)

`text/event-stream`. Use `EventSource`; on reconnect the browser sends
`Last-Event-ID` and the stream resumes after it (or pass
`?last_event_id=N`). The stream first sends `retry: 3000`, then replays the
run's events, then live ones, and **closes after `run.end`**. Lines starting
with `:` are keepalive comments (every 15 s).

Each frame:

```
id: 41
event: document.status
data: {"id":41,"run_id":12,"type":"document.status","data":{...}}
```

The `data` line is the whole event; the payload is its `data` field.

| `type` | payload `data` |
|---|---|
| `run.status` | `{"status": "running", "error": null, "retryable": false}` |
| `document.status` | `RunDocument` |
| `run.usage` | `{"tokens_in": 5120, "tokens_out": 830, "cost_usd": 0.0029}` |
| `artifact.ready` | `{"artifact_id": 7, "version": 1, "fallback": false}` (`fallback` true: template report, see notices) |
| `run.end` | `{"status": "done"}` (final status) |
| `run.snapshot` | `{"run": {"id", "status", "error", "retryable", "tokens_in", "tokens_out", "cost_usd"}, "documents": [RunDocument], "artifact": {"id": 7, "version": 1} or null}` |

`run.snapshot` has no `id` and is sent alone when the server has no history
for the run (for example after a restart) and the run is idle; render from it
and close. For a run that already ended, the replay ends with its `run.end`.
After `run.end` (or a snapshot), fetch `GET /api/runs/{id}` and the artifact.

## Artifacts

### `GET /api/runs/{run_id}/artifact?version=N` and `GET /api/artifacts/{id}?version=N`

`version` is optional (default: current). `404 no_report` when the run has no
report yet; `404 version_not_found`.

```json
{
  "id": 7, "run_id": 12, "title": "Compare Quotes", "kind": "report",
  "version": 3, "current_version": 3, "is_current": true,
  "note": "edit premium-table/total_premium/A", "created_at": "...",
  "content": ArtifactContent,
  "chips": {"88": Chip},
  "counts": {"verified": 31, "scan_read": 2, "not_found": 1, "absent": 6, "failed": 0, "edited": 1},
  "stale_sections": ["executive_summary"],
  "feedback": {"rating": 1, "comment": null}
}
```

`counts` is over table cells, for the trust summary in the outline header
(DG2). `feedback` is for the viewed version, or null.

`Chip` (keyed by passage id as a string):

```json
{"passage_id": 88, "label": "B2", "letter": "B", "document_id": 4,
 "filename": "juniper.pdf", "page_no": 3, "verified": true,
 "rects": [[72.0, 140.2, 310.5, 152.9]], "page_width": 612.0, "page_height": 792.0}
```

A chip is a (document, page) passage, so one label repeats across the cells
of a column. `label` is the document letter plus the page rank (DG11).
`rects` are PDF points from the top-left of the page (PyMuPDF space; scale by
the rendered width over `page_width`); draw them as the dashed highlight.
`verified: false` means the page was read from a scan: draw a page-level
highlight, not rects. `chips` includes the original-source chips of edited
cells, so labels never shift after an edit.

`ArtifactContent`:

```json
{
  "notices": [{"kind": "compose_fallback", "message": "...", "retryable": true, "dismissible": false}],
  "sections": [{
    "id": "premium", "number": "3.2", "title": "Premium", "level": 2, "stale": false,
    "blocks": [
      {"type": "paragraph", "id": "premium-p1",
       "segments": [{"text": "A (Harborline) quotes a total premium of $26,391.14.", "passage_ids": [88]}]},
      {"type": "table", "id": "premium-table", "title": "Premium",
       "columns": [{"key": "A", "label": "Harborline Specialty", "document_id": 3, "warning": null}],
       "rows": [{"key": "total_premium", "label": "Total Premium", "cells": {"A": Cell}}]}
    ]
  }]
}
```

Block types: `heading` (`id`, `level`, `text`), `paragraph` (`id`,
`segments`), `list` (`id`, `title` or null, `items`: list of segment lists),
`table` (`id`, `title`, `columns`, `rows`), `issue` (`id`, `code`, `title`,
`severity`, `category`, `impact` segments, `affected_layers`, `follow_form`).
A segment is `{"text", "passage_ids"}`: render chips for its passage ids
after the text.

Notice `kind`: `compose_fallback` (prose failed validation twice: banner plus
**Retry prose**, which is `POST /regenerate`), `budget_exhausted`,
`documents_failed` (`retryable`: offer run Retry), `info`. Show at most two
rows at the top (DG5). Column `warning` is the column-level row (failed
document reason, or `Compared "Option 1", option 1 of 3 in this document`).

`Cell`:

```json
{"value": "$26,391.14", "passage_ids": [88], "fact_ids": [301], "state": "verified",
 "edited": false, "original_value": null, "original_passage_ids": []}
```

Evidence states (M1, DG3); the text to show is already in `value`:

| `state` | `value` | Chip |
|---|---|---|
| `verified` | the value | solid chip per passage |
| `scan_read` | the value | dashed chip, tooltip "Read from scan, unverified" |
| `not_found` | `Couldn't verify` | none; triangle glyph |
| `absent` | `Not specified` | none |
| `failed` | `Extraction failed` | none; refresh glyph plus Retry (run retry) |

When `edited` is true, show the user's `value` with the pencil glyph; the
extracted value and its chips are in `original_value` and
`original_passage_ids` ("original source"); `passage_ids` is empty.

### `GET /api/artifacts/{id}/versions`

```json
{"artifact_id": 7, "current_version": 3,
 "versions": [{"version": 3, "note": "edit ...", "created_at": "...", "current": true}]}
```

Newest first. Notes: `generated`, `template fallback`, `retry, generated`,
`edit <block>/<row>/<column>`, `chat edit ...`, `restore vN`,
`regenerate prose`, `regenerate prose, template fallback`.

### `PATCH /api/artifacts/{id}/cells` (cell edit, DG12)

```json
{"block_id": "premium-table", "row_key": "total_premium", "column_key": "A",
 "value": "$30,000.00", "base_version": 2}
```

`value` is required (null clears the cell, shown as Not specified, still
marked edited); trimmed, at most 2000 characters. `base_version` is optional:
send the version the user was looking at to get `409 version_conflict` instead
of editing a newer version.

```json
{"artifact": Artifact, "version": 3, "previous_version": 2, "stale_sections": ["executive_summary"]}
```

`stale_sections` (D9): sections whose prose cites the edited cell's original
passage now have `stale: true`; show "May be out of date" with **Regenerate
prose** on their headings. A passage is a whole page, so a section that cites
the same page for another field is flagged too (safe side). For the
"Edited. Undo" toast, keep `previous_version` and send it to `/undo`.
Errors: `404 cell_not_found`, `409 run_active` (a retry is rebuilding the
report), `409 version_conflict`; on any error keep the input open with
"Couldn't save. Retry".

### `POST /api/artifacts/{id}/undo`

```json
{"version": 2, "base_version": 3}
```

Copies `version` forward as a new version (history is never rewritten).
Response as for the cell edit; `stale_sections` lists the stale sections of
the restored content. `404 version_not_found`, `409 run_active`,
`409 version_conflict`.

### `POST /api/artifacts/{id}/regenerate` (Regenerate prose and Retry prose)

Body optional: `{"base_version": 3}`. Rewrites the report prose from the
verified facts **plus the user's edits** (edited values override extracted
ones in the prose; edits are kept in the tables). Clears the stale flags of
the edits the prose was written with. Counts against the run's token budget.

```json
{"artifact": Artifact, "version": 4, "previous_version": 3, "fallback": false, "reason": null}
```

`fallback: true` means the prose failed again and the template report is
shown (`reason`: `budget_exhausted`, another provider code, or
`validation_failed`). `409 run_active`, `409 regenerate_in_progress`,
`409 version_conflict`.

### `PUT /api/artifacts/{id}/feedback`

```json
{"rating": 1, "comment": "optional", "version": 3}
```

`rating`: `1` thumbs up, `-1` thumbs down, `0` clears. `version` defaults to
current. Response: `{"artifact_id": 7, "version": 3, "feedback": {"rating": 1, "comment": null}}`
(`feedback` null after a clear). `404 version_not_found`.

### `GET /api/artifacts/{id}/export?format=xlsx|pdf&version=N`

File download, `Content-Disposition: attachment; filename="compare-quotes-run-12-v3.xlsx"`.

- `xlsx`: **tables only**, one sheet per table plus a `Sources` sheet (chip,
  document, file, page, evidence). Cell comments carry each cell's source
  chips and state (edited with the original value, read from scan, couldn't
  verify). Header `X-Export-Note: XLSX holds the report tables only.` (show it
  in the toast). Values are always text, never formulas.
- `pdf`: the full report (notices, sections with the stale tag, prose with chip
  labels, tables, Sources appendix).

Header `X-Export-Tables` gives the table count. `422 invalid_request` for
another format; `500 export_failed` ("Export failed. Retry").

## Chat

### `GET /api/runs/{id}/messages`

`{"messages": [Message]}`, oldest first.

`Message`:

```json
{"id": 9, "run_id": 12, "role": "assistant",
 "content": "A (Harborline) quotes $26,391.14.",
 "segments": [{"text": "A (Harborline) quotes $26,391.14.", "passage_ids": [88]}],
 "passage_ids": [88],
 "proposed_edits": [ProposedEdit],
 "created_at": "..."}
```

Render `segments` with chips (look chips up in the artifact's `chips`; for an
answer that cites a scan-read passage use the dashed chip).

### `POST /api/runs/{id}/messages`

`{"content": "Which quote has the lowest premium?"}` (1 to 4000 characters).

`201 {"user_message": Message, "assistant_message": Message}`.

Chat answers from the **latest artifact version** (edits override extracted
values; the original is given for reference) and the page text of the run's
documents. Every chip in an answer is a validated passage of this run, and
every money, percent, date or limit figure comes from the report, the
question, or a page the same segment cites; otherwise the answer is retried
once, then refused. Nothing is stored on failure, so Retry resends the same
question.

Errors: `409 no_report` ("Run a comparison to ask questions", DG6),
`409 chat_busy` (an answer is in progress), `409 budget_exhausted`,
`503 missing_key`, `422 context_overflow`, `502 answer_rejected` and other
`502` provider codes ("Couldn't answer" with Retry; `retryable: true`).

`ProposedEdit` (chat never applies edits itself):

```json
{"id": 0, "block_id": "coverage-table", "row_key": "retention", "column_key": "A",
 "value": "$5,000", "reason": "You asked to set A's deductible.",
 "current_value": "$2,500", "proposed_on_version": 3, "status": "proposed"}
```

After apply: `"status": "applied", "applied_version": 5`.

### `POST /api/runs/{id}/messages/{message_id}/edits/{edit_id}/apply`

Applies the proposal to the **latest** version (D9), exactly like a cell edit.

```json
{"message": Message, "artifact": Artifact, "version": 5, "previous_version": 4,
 "stale_sections": ["coverage"]}
```

`409 already_applied` (with `version`), `404 not_found`, `404 cell_not_found`,
`409 run_active`.

### `GET /api/runs/{id}/suggestions`

`{"suggestions": ["Which quote has the lowest total premium?", "..."]}`

Up to 4 follow-up prompts from one cheap model call per artifact (cached in
server memory; a fixed list is returned if the call fails). Empty list when
the run has no report (hide Suggestions, DG6).
