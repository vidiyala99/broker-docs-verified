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
`document_ids` is the column order (A, B, ...). For `compare-quotes`,
`parameters` is stored as sent and not used. For `proposal-builder`, see
"Proposal runs" below: the parameters are validated and stored with the
template snapshot.

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
document failed, or a crash), `cancelled`. `terminal` is true
once the status is final and no task is running. `artifact` is null until a
report exists.

`retryable` is true exactly when `POST /api/runs/{id}/retry` would be
accepted: the status is `failed`, `partial` or `cancelled` and no task is
running (`active` false). It is false for `done` and for any active run. Use
it to show the Retry button.

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
            "updated_at": "2026-11-02T10:00:00+00:00"}]}
```

A role missing from `slots` is N/A (no document). `404 not_found` for an unknown run,
`404 no_report` before the run has an artifact. A run of another workflow lists no items.

### `PATCH /api/runs/{id}/discrepancies/{item_id}`

Body `{"status": "open" | "resolved", "note": "..." | null}`, either or both (`422` for an
empty body, another status, an unknown key, or a note over 2000 characters). Control
characters are stripped from the note; `note: null` clears it. A status change clears
`changed_since_resolved`. Returns the item in the GET shape; `404 discrepancy_not_found`
when the current version has no such item.

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
