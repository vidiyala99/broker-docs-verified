# BrokerDocs local API (slices 1 to 3b, work queue)

Covers Compare Quotes (slice 1), Proposal Builder (slice 2), Policy Check (slice 3), the
rail completion of slice 3b (run `line` for Personal lines, Tasks) and the broker work queue
(`GET /api/queue`, run client names and queue state).

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

There are no migrations. A database created before the slice 3b schema change
(2026-11-02: `run.line` and the `task` table) or before the work queue
(`run.client_name`, `run.queue_state` with its check constraint
`ck_run_queue_state`, `run.proposal_run_id`, `run.queue_changed_at` and the
index `ix_run_line_queue_state` on `run (line, queue_state)`) stops the server at
startup with a message naming the file, every missing piece, and the reseed command
(`backend/.venv/Scripts/python -m backend.dev_seed seed`, with
`BROKERDOCS_DATA_DIR` set to a scratch folder).

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
`retryable`, `field` (the body field a `400 invalid_task` is about), and for
`422 invalid_request` a `fields` list of
`{"loc": ["body", "value"], "message": "..."}`.

| Status | Codes |
|---|---|
| 400 | `Invalid host header` (plain text from the host guard), `invalid_run` (slot assignment, `line`, `client_name`, `source_run_id` or `queue_state`, see `POST /api/runs` and `PATCH /api/runs/{id}`), `invalid_task` (see Tasks) |
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

## Proposal templates (slice 2)

### `GET /api/templates`

```json
{"templates": [{"id": 1, "slug": "standard", "name": "Standard", "description": "...", "block_count": 8},
               {"id": 2, "slug": "premier", "name": "Premier", "description": "...", "block_count": 11}]}
```

### `GET /api/templates/{id_or_slug}`

```json
{"id": 1, "slug": "standard", "name": "Standard", "description": "...", "block_count": 8,
 "workflow": "proposal-builder",
 "blocks": [OutlineBlock],
 "theme": {"font_family": "Calibri", "body_size_pt": 10.5, "heading_color": "#1F3A5F",
           "heading_sizes_pt": {"1": 20, "2": 15, "3": 12}, "accent_color": "#2F6FB0"},
 "variables": ["agency_name", "broker_name", "client_name"],
 "kinds": [{"kind": "premium_comparison", "title": "Premium Comparison", "table": true,
            "description": "...", "fact_sections": ["rate", "premium"]}],
 "fact_sections": [{"id": "premium", "title": "Premium",
                    "fields": [{"key": "total_premium", "label": "Total Premium"}]}],
 "limits": {"max_blocks": 20, "max_title_chars": 120, "max_description_chars": 400,
            "max_instruction_chars": 1000, "max_fact_sections_per_block": 10,
            "max_context_chars": 4000, "max_name_chars": 200}}
```

`404 not_found`. `kinds` is the library for "add a block" (locked kinds are
not in it; `fact_sections` is the default binding for a new block of that
kind). `fact_sections` is the catalogue a block can bind to. `theme` is
applied at export only.

`OutlineBlock`:

```json
{"id": "premium", "kind": "premium_comparison", "title": "Premium Comparison",
 "description": "...", "ai_instruction": "...", "fact_sections": ["rate", "premium"], "locked": false}
```

- `kind`: `cover_page`, `table_of_contents` (both locked), `client_details`,
  `line_comparison`, `premium_comparison`, `subjectivities` (these four show
  tables), `narrative` (prose only).
- `id`: `^[a-z][a-z0-9_]{0,39}$`, unique. `cover` and `toc` are reserved for
  the locked blocks, which must be blocks 1 and 2.
- `fact_sections` (D3): the fact sections whose facts the block shows. A
  table block names at least one and gets one table per fact section; a fact
  section has **at most one table** in an outline (D4), while narrative blocks
  may name any. Facts of a fact section no block names are left out of the
  proposal. Locked blocks name none.
- `locked` is derived from `kind`; a value sent by the client is ignored.

## Proposal runs (slice 2)

`POST /api/runs` with `"workflow": "proposal-builder"` takes these
`parameters` (no other keys):

```json
{"template_id": "standard", "broker_name": "Dana Ruiz", "brokerage_name": "Ruiz and Partners",
 "client_name": "Cedar Hollow LLC", "additional_context": "...", "no_ai_prose": false,
 "outline": [OutlineBlock]}
```

`template_id` is an id or slug (required). `outline` is optional: omit it to
use the template's blocks as they are. Text caps: names 200, additional
context 4000, title 120 (one line), instruction 1000, description 400; at most
20 blocks. At least one document is still required (`422 invalid_run`).

A bad parameter returns `422 invalid_request` with one `fields` entry per
problem; `loc` is the path from the request body, with list indexes as
strings (as for every `invalid_request`):

```json
{"error": {"code": "invalid_request", "message": "The proposal settings are not valid.",
  "fields": [{"loc": ["body", "parameters", "outline", "2", "kind"], "message": "Input should be ..."},
             {"loc": ["body", "parameters", "outline", "8", "fact_sections", "0"],
              "message": "Fact section 'premium' already has a table in block 'premium'; a fact section can have only one table."}]}}
```

Paths: `outline` (missing locked block, too many blocks, no section besides
the locked ones), `outline.N.kind`, `outline.N.id` (pattern, duplicate,
reserved, locked id), `outline.N.title`, `outline.N.ai_instruction`,
`outline.N.fact_sections` (empty on a table block, set on a locked block),
`outline.N.fact_sections.M` (unknown, repeated, second table), `template_id`
(unknown template), or the parameter name.

The stored `Run.parameters` is the validated input plus the resolved template
(D6). Export and Regenerate prose read this snapshot, never the live template:

```json
{"template_id": 1, "broker_name": "...", "brokerage_name": "...", "client_name": "...",
 "additional_context": "...", "no_ai_prose": false, "outline": [OutlineBlock],
 "template": {"id": 1, "slug": "standard", "name": "Standard", "blocks": [OutlineBlock],
              "theme": {...}, "variables": [...]}}
```

The proposal artifact uses the slice 1 blocks:

- Title `Insurance Proposal for <client_name>` (or `Insurance Proposal`).
- One section per outline block, in order, with the block's `id` and `title`.
  The cover and TOC sections have `number` `""`; the others are numbered `1`, `2`, ...
- Section `cover`: heading `cover-title` (level 1, the title), then list
  `cover-details` ("Prepared for ...", "Prepared by <broker>, <brokerage>") when
  those fields are set. Section `toc`: list `toc-entries`, one item per
  numbered section (`"1 Executive Summary"`). Both are built in code: no chips,
  no model call.
- Tables: id `<fact section>-table` (for example `premium-table`), so cell
  edits survive a reorder or retitle. A block with one fact section titles its
  table with the block title; with several, each table takes the catalogue title.
- Prose: the model writes only the other sections; each section may cite and
  state only the facts of its own block. Text the user typed (titles,
  instructions, additional context, names) reaches the model in a separate
  delimited brief and is never a source of facts; the checker still gates
  every number and chip.
- `no_ai_prose: true`: no compose call. Each section gets plain text from the
  verified facts (with chips), deterministic, no `compose_fallback` notice.
  Retry and Regenerate prose keep the mode.

**Build proposal from a comparison (work queue).** `POST /api/runs` with
`"workflow": "proposal-builder"` may also take a top-level `source_run_id`: the
Compare Quotes run the proposal is built from. It must name a Compare Quotes run
whose `status` is `done`; anything else (unknown id, a Policy Check or proposal
run, a comparison that is not done, a value that is not an integer, or
`source_run_id` with another workflow) is `400 invalid_run` and creates nothing.
The id is stored as `parameters.source_run_id` (after the validated parameters
and the template snapshot), so it is part of the duplicate-run identity (M3):
the same documents built from two comparisons are two runs, and a double click
on one comparison returns the active run. The source comparison's
`proposal_run_id` is set to the new (or reused) run. Without `line`, the
proposal takes the comparison's `line` (M5); a `line` sent with the request wins.

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
removed). Files are stored under generated UUID names. `kind` is `quote` for a
PDF uploaded here and `ams` for an AMS export (next route).

### `POST /api/documents/ams` (multipart, slice 3)

Form field `files`, repeated once per file; same `200` envelope as
`POST /api/documents` (`documents` and `rejected`, so good files stay and bad
ones get an inline rejection). Use it for the AMS export of a Policy Check run;
`POST /api/documents` stays PDF-only.

- **Formats.** Only `.json` and `.csv`, and the content must agree with the
  extension (JSON opens with `{` or `[`). JSON is one flat object of field names
  and values (nested values are not accepted, `invalid_ams`). CSV is either a
  header row plus exactly one record row, or a two-column `field,value` table
  (header `field`/`name`/`key`/`field_name`/`code`/`field_code` and
  `value`/`field_value`). Text must be UTF-8. A repeated field name is rejected.
- **Caps.** 512 KB per file (also bounded by the PDF byte cap), 400 fields,
  100 characters per field name, 1000 characters per field value, and at most 8
  files per request (the same per-run document limit as PDFs). The rendered
  PDF also has to pass the normal PDF page cap. Field names and values are
  screened like document text (control characters removed, unsupported
  characters shown as `?`, counted in `ams.notices`).
- **Rejection codes** (`rejected[]`, with `limit` where a limit applies):
  `too_large` (`limit` in bytes), `empty`, `not_ams` (wrong extension, a PDF, or
  content that does not match the extension), `invalid_ams` (unreadable JSON or
  CSV, wrong shape, no fields, duplicate field), `too_many_fields` (`limit` 400),
  `field_too_long` (`limit` is the name or value cap), `too_many_documents`
  (`limit` 8), `too_many_pages` (`limit` in pages).
- **What is stored.** The export is rendered to a PDF (one line per field,
  `field_name: value`) that is served, parsed and verified like any PDF
  (`GET /api/documents/{id}/file` returns that PDF, so a citation chip opens
  the rendered page); the original file is kept beside it. The document has
  `kind: "ams"` and can be used only in a slot that accepts JSON or CSV.

Each accepted item is a `Document` plus an `ams` summary:

```json
{"documents": [{"id": 9, "filename": "ams-export.json", "size_bytes": 812, "page_count": 1,
                "sha256": "...", "kind": "ams", "parse_error": null, "warning": null,
                "created_at": "...",
                "ams": {"format": "json", "field_count": 14,
                        "unmapped": ["Effective Date", "Carrier"],
                        "notices": []}}],
 "rejected": []}
```

`ams.format` is `json` or `csv`; `field_count` is the fields read (including
empty ones); `unmapped` is the names of fields that have a value but match no
Policy Check field, so they are not compared (the effective date and expiration
date on their own, carrier and similar); `notices` is the list of record-level
notices (replaced characters). In a Policy Check report, run `notices` of kind
`info` carry the record notices, "AMS fields not compared (...)" and any
"AMS fields ... give different values for <field label>" conflict (for example
"Coverage E (Personal Liability)").

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
`document_ids` is the column order (A, B, ...). For `compare-quotes`,
`parameters` is stored as sent and not used. For `proposal-builder`, see
"Proposal runs" below: the parameters are validated and stored with the
template snapshot.

**`slots` (slice 3, optional).** A map of document id to input slot key, one
entry per document in the run, for example
`{"document_ids": [3, 4, 9], "workflow": "policy-check", "slots": {"3": "expiring", "4": "renewal", "9": "ams"}}`
(JSON keys are strings; the server reads them as integers). Each key is 1 to 50
characters and `slots` holds at most 64 entries. The valid keys are the
workflow's `input_slots` (`GET /api/workflows/{id_or_slug}`):

| Workflow | Slot key | Accepts | Min | Max |
|---|---|---|---|---|
| `compare-quotes`, `proposal-builder` | `quotes` | `application/pdf` | 1 | 8 |
| `policy-check` | `expiring` | `application/pdf` | 1 | 1 |
| `policy-check` | `renewal` | `application/pdf` | 1 | 1 |
| `policy-check` | `quote` | `application/pdf` | 0 | 1 |
| `policy-check` | `ams` | `application/json,text/csv` | 0 | 1 |

Rules, checked in this order after the duplicate and count checks and after the
workflow and documents are found:

- Without `slots`, a one-slot workflow puts every document in its slot
  (`quotes`), exactly as before slice 3. A workflow with several slots
  (`policy-check`) needs `slots`.
- With `slots`: every document in `document_ids` has exactly one entry, no entry
  names a document outside the run, every key is one of the workflow's slots,
  and each slot's document count is within its min and max.
- `accepts` is enforced. A document whose `kind` is `ams` (uploaded through
  `POST /api/documents/ams`) can only go in a slot that accepts `text/csv` or
  `application/json`; any other document only in a slot that accepts
  `application/pdf`. An AMS export therefore cannot enter a Compare Quotes run.
- A slot a run leaves empty is simply absent (`min` 0 slots only).

**`line` (slice 3b, optional).** `"commercial"` or `"personal"` (household
cover: homeowners, auto, renters, umbrella). Omitted or `null` means
`commercial` for a new run. It is a tag only: it does not change how the run
reads or compares documents. Any other value (another string, a number, a
list) is `400 invalid_run` with the message "line must be commercial or
personal." and creates nothing.

**`client_name` (work queue, optional).** The broker's name for the client
(the start screens' Client field). Control characters are dropped (the same
pattern as discrepancy notes), whitespace runs become one space and the ends are
trimmed; the result is 1 to 120 characters. Omitted, `null` or empty after
cleaning means no name. A value that is not a string, or longer than 120
characters after cleaning, is `400 invalid_run` and creates nothing. Like
`line`, it is outside the duplicate-run identity: a reused active run takes the
name when one is sent and keeps its own otherwise.

**`source_run_id` (work queue, proposal-builder only).** See "Build proposal
from a comparison" under Proposal runs.

Responses:

- `201 {"run": Run, "created": true}` for a new run (it starts at once).
- `200 {"run": Run, "created": false}` when an identical run is still active
  (double-click Run returns the same run). Identical means the same workflow,
  the same documents in the same order with the same slots, and the same
  `parameters`. `line` is **not** part of that identity: a resubmit with a
  different `line` returns the active run, and that run takes the `line` that
  was sent (D4). A resubmit without `line` leaves the run's line as it is.
- `400 invalid_run`: the slot assignment does not fit the workflow (missing,
  unknown or extra slot entry, a slot below its min or above its max, a PDF in
  the AMS slot, an AMS export in a PDF slot, or `slots` omitted for a
  multi-slot workflow), `line` is not `commercial` or `personal`, `client_name`
  is not text or too long, or `source_run_id` breaks its rules. `message` is
  safe to show.
- `422 invalid_run`: no documents, a duplicate, or more than 8 (`limit: 8`).
  These are the older cases and stay 422; slot errors are 400.
- `422 invalid_request`: a malformed body (for example `slots` with more than
  64 entries, or a key over 50 characters).
- `404 not_found`: unknown workflow or document.

A `policy-check` run additionally fails (`status: failed`, `retryable: true`)
when a required slot (min 1: `expiring`, `renewal`) has no readable document
after the documents settle. `error` then reads "The expiring policy could not be
read, so this check cannot run. Retry to try again." followed by the document
reasons. An optional slot (`quote`, `ams`) whose document failed does not fail
the run; its cells read Not read in the report (see "Policy Check discrepancies").

`Run`:

```json
{
  "id": 12, "workflow_id": 1, "workflow_name": "Compare Quotes", "line": "commercial",
  "client_name": "Cedar Hollow LLC", "queue_state": "open", "proposal_run_id": null,
  "queue_changed_at": null,
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
document failed, or a crash), `cancelled`. `terminal` is true
once the status is final and no task is running. `artifact` is null until a
report exists.

`line` (slice 3b) is `commercial` or `personal`; every run made before slice 3b
reads `commercial`. It is in every REST `Run` (create, list, get, patch, retry,
cancel). The SSE `run.snapshot` payload does not carry it (unchanged, O9).

Work queue fields, in every REST `Run` like `line` (not in SSE payloads):
`client_name` (the broker's client name, or `null`), `queue_state` (`open`,
`done` or `dismissed`; `open` for every new run and every run made before the
work queue), `proposal_run_id` (Compare Quotes: the Proposal Builder run last
started from it with `source_run_id`, else `null`; a plain id, it may name a run
that no longer exists) and `queue_changed_at` (when the item last left or was put
back in the queue: `PATCH queue_state`, its proposal finishing done or partial,
or its last open discrepancy resolved; `null` until then and again after a
Retry). See `GET /api/queue`.

The UI's label for a run is `"<workflow_name> · Run <id>"` (for example
`"Policy Check · Run 7"`); `GET /api/tasks` returns it as `run_label`.

`retryable` is true exactly when `POST /api/runs/{id}/retry` would be
accepted: the status is `failed`, `partial` or `cancelled` and no task is
running (`active` false). It is false for `done` and for any active run. Use
it to show the Retry button.

`RunDocument` (identical to the SSE `document.status` payload):

```json
{"run_document_id": 31, "document_id": 3, "position": 0, "letter": "A", "slot": "quotes",
 "filename": "harborline.pdf", "status": "reading", "progress": "Reading pages 4/10",
 "error": null, "error_code": null, "option_count": 1, "option_label": null}
```

`slot` (slice 3) is the input slot key the document was assigned to in
`POST /api/runs`: `quotes` for Compare Quotes and Proposal Builder runs, and
for any run created before slice 3; `expiring`, `renewal`, `quote` or `ams`
for Policy Check. It is in the REST `Run.documents` list and in the SSE
`document.status` and `run.snapshot` payloads alike.

`status`: `queued`, `reading`, `extracting`, `verifying`, `done`, `failed`.
`progress` is the per-row text (DG4). On `failed`, `error` is the user-facing
reason and `error_code` is one of: `not_pdf`, `empty`, `corrupt`, `encrypted`,
`too_large`, `too_many_pages`, `budget_exhausted`, `refusal`,
`schema_invalid`, `truncated`, `timeout`, `unavailable`, `context_overflow`,
`request_error`, `missing_key`, `cancelled`, `interrupted`, `internal_error`.
`option_count > 1` means only the first option was compared (D8); the column
header shows the warning from the artifact table.

### `GET /api/runs?limit=20&line=personal`

`{"runs": [Run]}`, newest first (Recent runs list, DG1). `limit` 1 to 100
(default 20; there is no paging, so a list sees at most the newest 100 runs,
D5). `line` (slice 3b, optional) is `personal` or `commercial` and keeps only
runs with that line; without it every run is listed, exactly as before. Another
`line` value or a `limit` outside 1 to 100 is `422 invalid_request`. Personal
lines uses `?line=personal&limit=100`; the Workflows recent-runs list uses
`?line=commercial` (D9).

### `GET /api/runs/{id}`

`Run`.

### `PATCH /api/runs/{id}` (slice 3b, work queue)

```json
{"line": "personal", "client_name": "Cedar Hollow LLC", "queue_state": "done"}
```

Changes a run's tags: any non-empty subset of `line` (the run header's
Commercial / Personal control), `client_name` (the run header's Client field)
and `queue_state` (the queue's Mark done, Dismiss and their Undo), and no other
key. Every value is checked before anything changes, so a bad value next to a
good one changes nothing. Returns `200 Run`.

- `line`: `commercial` or `personal`. Works on an active run too.
- `client_name`: cleaned and limited as in `POST /api/runs`; `""` (or `null`)
  clears the name, and the queue falls back to the extracted named insured.
  Works on an active run too.
- `queue_state`: `open`, `done` or `dismissed`. Sets `queue_changed_at` to now,
  so a run finished long ago still shows in Done recently for 14 days after it is
  marked done (D2). `done` or `dismissed` on an active run is `409 run_active`
  (M1); `open` is allowed.

Errors:

- `404 not_found`: unknown run (checked before the body).
- `400 invalid_run`: an empty body, a body that is not an object, any other key,
  `line` missing its value, `null` or not `commercial` / `personal`, a
  `client_name` that is not text or is over 120 characters, a `queue_state`
  outside `open` / `done` / `dismissed`.
- `409 run_active` (with `run_status`): `queue_state` `done` or `dismissed`
  while the run is queued, running or composing.

### `POST /api/runs/{id}/retry`

`202 {"run": Run}`. Re-runs only the documents that did not finish, then
recomposes (a new artifact version). Cell edits are carried into the new
version and the sections they affect are flagged stale. A retry reopens the
work-queue item (D8): `queue_state` becomes `open` and `queue_changed_at`
`null`, in the same transaction.
`409 not_retryable` (with `run_status`) unless the run is `failed`,
`partial` or `cancelled` and idle (that is, unless `Run.retryable` is true).
After a retry, open the events stream again: it follows the new execution
to its own `run.end` (see below).

### `POST /api/runs/{id}/cancel`

`200 {"run": Run, "cancelled": true}`. `cancelled` is false when nothing was
running. A cancel writes no new artifact version (a report from an earlier
execution stays); unfinished documents fail with `cancelled`, and the run can
be retried.

### `GET /api/runs/{id}/events` (Server-Sent Events)

`text/event-stream`. Use `EventSource`; on reconnect the browser sends
`Last-Event-ID` and the stream resumes after it (or pass
`?last_event_id=N`). The stream first sends `retry: 3000`, then replays the
run's events, then live ones, and **closes after the `run.end` of the current
execution**. Lines starting with `:` are keepalive comments (every 15 s).

Each frame:

```
id: 41
event: document.status
data: {"id":41,"run_id":12,"execution":2,"type":"document.status","data":{...}}
```

The `data` line is the whole event; the payload is its `data` field.
`execution` numbers the run's executions in this server process: 1 for the
first, 2 after the first retry, and so on (0 for a run never started since
the server started, such as a queued run cancelled after a restart).

Retried runs: the history keeps the events of earlier executions, but their
`run.end` is superseded and never sent, so a stream (fresh, or resumed with
`Last-Event-ID`) never stops on an earlier execution's end. The replay
shows the earlier execution's other events (its last `run.status` says, for
example, `partial`), then the retry's `run.status` `queued`/`running`, and
ends on the retry's `run.end`. A client that closes on `run.end` therefore
needs no special case. `Last-Event-ID` resume works across executions: the
stream resumes after the given id, skipping superseded `run.end` events.

| `type` | payload `data` |
|---|---|
| `run.status` | `{"status": "running", "error": null, "retryable": false}` (`retryable` as on `Run`) |
| `document.status` | `RunDocument` |
| `run.usage` | `{"tokens_in": 5120, "tokens_out": 830, "cost_usd": 0.0029}` |
| `artifact.ready` | `{"artifact_id": 7, "version": 1, "fallback": false}` (`fallback` true: template report, see notices) |
| `run.end` | `{"status": "done"}` (final status) |
| `run.snapshot` | `{"run": {"id", "status", "error", "retryable", "tokens_in", "tokens_out", "cost_usd"}, "documents": [RunDocument], "artifact": {"id": 7, "version": 1} or null}` |

`run.snapshot` has no `id` and is sent alone when the server has no history
for the run (for example after a restart, or a `Last-Event-ID` after the last
event) and the run is idle; render from it and close. It also closes an idle
run's replay that has no current `run.end` to end on. For a run that already
ended, the replay ends with the `run.end` of its latest execution.
After `run.end` (or a snapshot), fetch `GET /api/runs/{id}` and the artifact.

## Work queue

Spec: `docs/specs/2026-11-02-work-queue-design.md` (its decision ledger and
amendments override the text above them). Rules: `backend/work_queue.py`.

### `GET /api/queue?line=commercial`

`line` is required: `commercial` (Workflows home) or `personal` (Personal
lines). Missing or any other value is `422 invalid_request`, like `?line=` on
`GET /api/runs`.

```json
{"line": "commercial", "truncated": false,
 "in_progress": [Item], "needs_review": [Item], "ready": [Item],
 "needs_attention": [Item], "done_recently": [Item]}
```

`Item`:

```json
{"run_id": 7, "workflow_slug": "policy-check", "workflow_name": "Policy Check", "line": "personal",
 "client": "Avery and Jordan Pellham", "client_source": "broker", "summary": "Renewal check",
 "open_counts": {"critical": 2, "high": 5, "medium": 1, "low": 1}, "reasons": null,
 "status": "done", "retryable": false, "finished_at": "...", "queue_state": "open",
 "queue_changed_at": null, "proposal_run_id": null}
```

- `client`: the run's `client_name` (`client_source` `"broker"`), else the
  verified `named_insured` the run extracted (`"extracted"`), else `null` for
  both (the UI shows "Unnamed client"). Policy Check takes the renewal's name,
  then the expiring policy's, then the quote's. Compare Quotes takes the name
  most quotes state (compared ignoring case and spacing); a tie goes to the
  leftmost quote. Only facts in state `verified` count.
- `summary`: `"Renewal check"` (Policy Check), `"3 carriers"` (Compare Quotes,
  one per document), or `"Building proposal"` while the comparison's proposal runs.
- `open_counts`: open discrepancies per severity for a Policy Check with a
  report (the same numbers as `open_counts` in `GET /api/runs/{id}/discrepancies`:
  items of the current version only), else `null`.
- `reasons`: plain-language causes, in this order, on Needs attention and Ready
  items, else `null`: "Stopped before finishing" (cancelled), "Interrupted by a
  restart", "Extraction stopped: budget reached", "N document(s) not read",
  "Stopped with an error" (a failed run with no other cause), "Proposal didn't
  finish" (Ready: the comparison's proposal failed or was cancelled).
- `retryable` is `Run.retryable` (show Retry only when true). `status`,
  `finished_at`, `queue_state`, `queue_changed_at` and `proposal_run_id` are the
  run's own fields.

Groups, first match wins:

| Group | Rule |
|---|---|
| (not listed) | Proposal Builder runs (they live under Proposals and Activity) |
| `in_progress` | the run is queued, running or composing; or a comparison whose proposal (`proposal_run_id`) is |
| `needs_attention` | `queue_state` `open` and status `failed`, `partial` or `cancelled` (a partial Policy Check shows here once, with its `open_counts`) |
| `needs_review` | `queue_state` `open`, Policy Check with a report and at least one open discrepancy |
| `ready` | `queue_state` `open`, Compare Quotes `done`, and no proposal, or its proposal failed or was cancelled |
| `done_recently` | anything else whose later of `finished_at` and `queue_changed_at` is within the last 14 days (resolved checks, proposal built (done or partial), marked done, dismissed) |
| (not listed) | older than that: Activity only |

Within a group, items are newest first by the later of `finished_at` and
`queue_changed_at` (then `created_at`), then by run id.

Selection: the line's runs that are active, have an active proposal, are
`queue_state` `open`, or changed in the last 14 days. Actionable rows come first
(active, or open and failed / partial / cancelled, a Policy Check, or a
comparison without a built proposal), then the rest, newest first; at most 500
are used. `truncated` is true when more rows matched (the UI shows a note).

Leaving and re-entering the queue: `PATCH /api/runs/{id}` `queue_state` (Mark
done, Dismiss, Undo), a linked proposal finishing done or partial (the
comparison's `queue_changed_at` is set in the same transaction as the
proposal's final status), resolving a Policy Check's last open discrepancy
(sets `queue_changed_at`), and Retry (back to `open`). The UI polls this route
every 3 s while `in_progress` is non-empty (D5).

## Policy Check discrepancies (slice 3)

A `policy-check` run makes no compose call: its artifact is one "Discrepancies" section of
`discrepancy` blocks built in code from the verified facts (D14), so Regenerate rebuilds it
for free and byte-identically. An AMS document's facts come from the AMS key table, not the
model (D5). A slot takes only what its `accepts` names: an AMS export only in `ams`, a PDF
only in the PDF slots (`400 invalid_run` otherwise).

The broker's decision on each item lives in `discrepancy_state`, outside the artifact.
Every new artifact version (run, retry, regenerate, undo) reconciles it against a
fingerprint of the item's compared slot values (citations excluded): a resolved item whose
values changed reopens with `changed_since_resolved: true`; restoring the resolved values
brings the decision back. Statuses are `open | resolved` only; there is no bulk endpoint (D1, D2).

### `DiscrepancyBlock`

Each item is a `discrepancy` block of the artifact (`GET /api/runs/{id}/artifact`
returns the same blocks without the decision fields):

| Field | Meaning |
|---|---|
| `type` | `"discrepancy"` |
| `id` | `disc-` plus the lower-cased `key` with other characters as `-`; stable across regeneration |
| `key` | canonical fact key (`coverage_a_limit`, `total_premium`, ...) or `form:<normalized form number>` |
| `title` | deterministic title built in code |
| `severity` | `critical`, `high`, `medium` or `low` |
| `item_state` | `discrepancy` (the renewal differs from a compared role) or `needs_review` (an expiring or renewal value is conflicting, unverified or not read; severity is `low`) |
| `lob` | `null` today |
| `prose` | one segment, the deterministic explanation (`{"text", "passage_ids"}`) |
| `slots` | map of role (`expiring`, `quote`, `renewal`, `ams`) to a `Cell` |

The comparison is renewal-centered. Expiring vs renewal is graded by fact
type (limit down more than 10% is `critical`, any other limit change or a removed
limit `high`, deductible increase `critical`, premium up `medium`, deductible
down `medium`, form edition change `medium`, text or address change `medium`,
policy number change `low`, discount dropped `high`, discount amount change
`low`, a key outside the closed list `low`). A quote or AMS value that differs
from the renewal is `high`. `policy_term` is not compared expiring vs renewal
(the term always moves); it is compared with the quote and the AMS value only.
The AMS effective date, expiration date and carrier are not compared (listed in
`ams.unmapped` and in a run notice). Equal values after normalization produce no
item. A report with no items holds one paragraph (`id: "discrepancies-none"`)
instead of blocks, and the list endpoint returns `items: []`.

Slot states are carried in each role's `Cell`:

| Slot | Meaning | Shown as |
|---|---|---|
| role missing from `slots` | no document was assigned to that role | N/A |
| `state: "absent"`, no value | the document was read but does not state this fact | N/A |
| `state: "failed"` | the role's document failed or the budget ran out, or its fact extraction failed | Not read (never N/A) |
| `state: "verified"` or `"scan_read"`, with `value` and `passage_ids` | cited value | the value and its chips |
| `state: "conflicting"`, no `value`, `conflicts` and `passage_ids` listed | the document states different values, each cited | Needs review |
| `state: "not_found"`, no `value` | the value could not be verified in the document | Needs review ("Couldn't verify") |

AMS citations are chips on the rendered AMS PDF (see `POST /api/documents/ams`).

### `GET /api/runs/{id}/discrepancies?severity=critical,high&hide_resolved=true`

Items of the current version in report order (severity, then key). `severity` is one tier
or a comma list of `critical | high | medium | low` (`422` for another). `counts` and
`open_counts` cover every item; the filters do not change them.

```json
{"run_id": 4, "artifact_id": 4, "version": 2, "total": 5, "resolved": 1,
 "counts": {"critical": 2, "high": 2, "medium": 1, "low": 0},
 "open_counts": {"critical": 1, "high": 2, "medium": 1, "low": 0},
 "items": [{"type": "discrepancy", "id": "disc-coverage-a-limit", "key": "coverage_a_limit",
            "title": "Coverage A (Dwelling) decreased by 14.3%", "severity": "critical",
            "item_state": "discrepancy", "lob": null, "prose": [{"text": "...", "passage_ids": []}],
            "slots": {"expiring": Cell, "renewal": Cell, "ams": Cell},
            "status": "resolved", "note": "Insured asked for it.", "changed_since_resolved": false,
            "updated_at": "2026-11-02T10:00:00+00:00", "task": {"id": 3, "status": "open"}}]}
```

`task` (slice 3b, read-only) is the task linked to this item, or `null`: the
first **open** task linked to the item, newest first (`created_at`, then `id`),
else the newest linked task (then `status` is `done`). A task linked only to the
run (no item) is not an item's task. Use it for the card's "Task: Open" /
"Task: Done" link (`#/tasks?task=<id>`). A task never changes the item's
`status`, and resolving the item never changes the task (R5).

Each item also carries `sources` (slice 3 T9, read-only, not part of the artifact): per role, the
cited facts behind that slot's chips, `[{"passage_id": 53, "page_no": 1, "rects": [[x0, y0, x1, y1]],
"field": "Cov C"}]`. `rects` are the fact's own highlight boxes (a chip's `rects` cover every fact
on its page); `field` is set for an AMS document only: the field name, read from the verified quote
(the rendered AMS line `field_name: value`), so the UI labels that chip "AMS · Cov C" and boxes only
that line. A role without a cited value has no entry. PATCH returns the same shape.

A role missing from `slots` is N/A (no document). `404 not_found` for an unknown run,
`404 no_report` before the run has an artifact. A run of another workflow lists no items.

### `PATCH /api/runs/{id}/discrepancies/{item_id}`

Body `{"status": "open" | "resolved", "note": "..." | null}`, either or both (`422` for an
empty body, another status, an unknown key, or a note over 2000 characters). Control
characters are stripped from the note; `note: null` clears it. A status change clears
`changed_since_resolved`. Returns the item in the GET shape (with `task`); `404 discrepancy_not_found`
when the current version has no such item. Resolving an open item that leaves the current version with
no open item sets the run's `queue_changed_at` (work queue, D2: the check moves to Done recently).

## Tasks (slice 3b)

A broker's to-dos: a title, an optional due date, `open` or `done`, and an
optional link to a run or to one Policy Check discrepancy of a run. No notes,
assignees, priorities or reminders (R3). Search, sort and the Open / Done / All
filter are client-side over `GET /api/tasks` (R4). Tasks belong to the local
workspace; there is no workspace filter (as for runs).

Tasks and discrepancy states are independent (R5): marking a task done never
resolves its discrepancy, and resolving a discrepancy never completes its task.
The task shows the discrepancy's live state.

`Task`:

```json
{"id": 3, "title": "Follow up: Coverage A (Dwelling) decreased by 14.3%",
 "due_date": "2026-11-05", "status": "open",
 "run_id": 7, "discrepancy_item_id": "disc-coverage-a-limit",
 "created_at": "2026-11-02T21:04:05.123456+00:00", "updated_at": "2026-11-02T21:04:05.123456+00:00",
 "completed_at": null,
 "run_label": "Policy Check · Run 7", "run_missing": false,
 "discrepancy": {"item_id": "disc-coverage-a-limit", "title": "Coverage A (Dwelling) decreased by 14.3%",
                 "status": "open", "in_report": true}}
```

| Field | Meaning |
|---|---|
| `title` | 1 to 200 characters, control characters removed, trimmed |
| `due_date` | `YYYY-MM-DD` or `null`. Overdue (past due and `open`) is decided by the UI |
| `status` | `open` or `done` |
| `completed_at` | set when the status becomes `done`, `null` again when reopened; `done` to `done` keeps it |
| `run_id` | the linked run id or `null`. A plain id: if the run row is later removed the id stays |
| `discrepancy_item_id` | the linked item id (`DiscrepancyBlock.id`) or `null`; only with `run_id` |
| `run_label` | read-only. `"<workflow_name> · Run <id>"`; `null` with no run or a missing run |
| `run_missing` | read-only. `true` when `run_id` is set and that run no longer exists: show "Linked run removed" (no link). Then `run_label` and `discrepancy` are `null` |
| `discrepancy` | read-only, `null` without an item link (or with a missing run). `title` is the item's live title while it is in the report, else the title saved when the link was made; `status` is the item's live `open` / `resolved` state; `in_report` is `false` when the item is not in the run's current report version (a regenerate dropped it, or the run has no report): show "No longer in the report" and open the run without scrolling (D3, D8). Restoring a version that has the item makes it `true` again |

### `GET /api/tasks`

`{"tasks": [Task]}`, newest first (`created_at`, then `id`). All tasks; nothing
is filtered on the server.

### `POST /api/tasks`

```json
{"title": "Follow up: Coverage A (Dwelling) decreased by 14.3%", "due_date": "2026-11-05",
 "run_id": 7, "discrepancy_item_id": "disc-coverage-a-limit"}
```

Only `title` is required; `due_date`, `run_id` and `discrepancy_item_id` are
optional (`null` = none). A new task is `open`. `201 Task`.

### `PATCH /api/tasks/{id}`

Any of `title`, `due_date`, `status`, `run_id`, `discrepancy_item_id`; `null`
clears `due_date`, `run_id` and `discrepancy_item_id`. `200 Task`. The result is
checked as a whole (the stored task with the patch applied):

- Changing `run_id` to another run, or clearing it, **without** sending
  `discrepancy_item_id` drops the item link (O1). Sending the same `run_id`
  keeps it.
- To link a discrepancy, send `discrepancy_item_id` (with `run_id` unless the
  task already has that run). The item must be in the run's current report.
- A patch that does not send `discrepancy_item_id` never re-checks an existing
  item link, so editing the title of a task whose item left the report works.

`404 not_found` for an unknown task.

### `DELETE /api/tasks/{id}`

`204`. `404 not_found` for an unknown task. There is no server-side undo: the UI
hides the row at once and sends the DELETE when its undo toast closes (D6).

### Task errors

Every validation problem is `400 invalid_task` with `field` and a message safe
to show (checked by the route, so these are never `422`):

```json
{"error": {"code": "invalid_task", "message": "The title can be at most 200 characters.", "field": "title"}}
```

| `field` | When |
|---|---|
| `title` | missing on create, not text, empty after cleaning, or over 200 characters |
| `due_date` | not a `YYYY-MM-DD` string (or `null`), or not a real date |
| `status` | not `open` or `done` (PATCH only) |
| `run_id` | not an integer (or `null`), or no such run |
| `discrepancy_item_id` | not a string id; sent without a run; the run has no report yet; the item is not in the run's current report |
| the field's own name | a key that is not a task field (for example `status` on create, or `note`) |
| absent | the body is not an object, or a PATCH body is empty |

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
  "counts": {"verified": 31, "scan_read": 2, "not_found": 1, "absent": 6, "failed": 0, "conflicting": 0,
             "edited": 1},
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
`conflicting_values` is cell-level only (`Cell.notice`, below), never in
`notices`.

`Cell`:

```json
{"value": "$26,391.14", "passage_ids": [88], "fact_ids": [301], "state": "verified",
 "edited": false, "original_value": null, "original_passage_ids": [],
 "notice": null, "conflicts": []}
```

`notice` is a cell-level Notice or null. `conflicts` is empty except in state
`conflicting` (long documents, T14): the document states two or more
different values for the field, each verified on its own page, and no
endorsement makes one of them effective. Nothing is picked:

```json
{"value": "Conflicting values", "passage_ids": [88, 97], "fact_ids": [301], "state": "conflicting",
 "edited": false, "original_value": null, "original_passage_ids": [],
 "notice": {"kind": "conflicting_values", "retryable": false, "dismissible": false,
            "message": "This document states different values for Deductible / Retention: $5,000 (page 20) and $7,500 (page 90). No value is shown as verified; check which one applies."},
 "conflicts": [{"value": "$5,000", "page_no": 20, "passage_id": 88},
               {"value": "$7,500", "page_no": 90, "passage_id": 97}]}
```

`conflicts` is ordered by page; `passage_ids` holds one chip per value in the
same order, so each value's chip opens its own page. Show the values from
`conflicts` (or the notice text), never one of them as the cell's value.

Evidence states (M1, DG3); the text to show is already in `value`:

| `state` | `value` | Chip |
|---|---|---|
| `verified` | the value | solid chip per passage |
| `scan_read` | the value | dashed chip, tooltip "Read from scan, unverified" |
| `not_found` | `Couldn't verify` | none; triangle glyph |
| `absent` | `Not specified` | none |
| `failed` | `Extraction failed` | none; refresh glyph plus Retry (run retry) |
| `conflicting` | `Conflicting values` | one chip per value (each a verified passage); same unverified treatment as `not_found` (triangle glyph, unverified text color) plus the notice listing every value and page. Never styled as verified. Counted under `counts.conflicting`, not `verified` |

Design mapping note: `conflicting` is new (2026-11-02). Until DESIGN.md
gives it its own treatment, render it like "Couldn't verify" (the
`--evidence-unverified-fg` token and triangle glyph) with the text
"Conflicting values", the cell notice row, and the chips from
`passage_ids`. XLSX and PDF exports write "Conflicting values" with the
values and pages in the cell note.

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

### `GET /api/artifacts/{id}/export?format=xlsx|pdf|docx&version=N`

File download, `Content-Disposition: attachment; filename="compare-quotes-run-12-v3.xlsx"`.

- `xlsx`: **tables only**, one sheet per table plus a `Sources` sheet (chip,
  document, file, page, evidence). Cell comments carry each cell's source
  chips and state (edited with the original value, read from scan, couldn't
  verify). Header `X-Export-Note: XLSX holds the report tables only.` (show it
  in the toast). Values are always text, never formulas.
- `pdf`: the full report (notices, sections with the stale tag, prose with chip
  labels, tables, Sources appendix). When the content has a cover and/or a TOC
  (a section with id `cover` / `toc`, or holding a block with that id or an id
  starting `cover-` / `toc-`), the PDF starts with a cover page and a contents
  page with page numbers. Slice 1 reports have neither and export as before.
- `docx` (slice 2): Word document with a cover page (the cover blocks, else the
  report title), a Word TOC field (pre-filled with the section headings; Word
  fills in page numbers on open), notices, sections, prose, tables, and a
  Sources list at the end. Every citation chip is a numbered Word footnote
  (`Source A1: quote.pdf page 3`; table cells carry the same note as the XLSX
  comment). `Couldn't verify`, `Conflicting values` (with each value and page)
  and `Read from scan, unverified` are written as words with the footnoted
  source, never as a bare value; edited cells read `value (edited)`. Fonts,
  heading colour and heading sizes come from the run's template snapshot
  (`run.parameters["template"]["theme"]`, see `TEMPLATE_SNAPSHOT_KEYS` in
  `backend/export.py`), else a default theme. Exporting the same version twice
  gives identical bytes.

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
answer that cites a scan-read passage use the dashed chip). Segment `text` is
stored as the model wrote it, spaces included: concatenate segments as they
are, adding no separator (`content` is that concatenation, trimmed).

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
