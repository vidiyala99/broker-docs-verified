# Slice 6: Compare Quotes match (formats, upload page, marked differences)

Date: 2026-11-05. Status: design approved in chat by the owner, section by section; this spec awaits owner review,
then `/plan-eng-review`. Visual design of the two screens goes through the design stack (all required skills,
Playwright at 1440/1024/390, max 3 rounds), not this spec.

## 1. Purpose and decisions

the reference product's own demo ("Three carriers. Three formats. Meeting at noon.") shows three things our Compare Quotes lacks:
quotes in mixed formats (PDF quote, PDF proposal deck, xlsx summary), a dedicated upload screen ("Drop them all in.
No prompt needed."), and a comparison that marks the weaker coverage terms, highlights the lowest premium and states
one "Key difference" ("The lowest premium isn't the same coverage."). This slice matches that behaviour and hierarchy
in our own design system. We match function, not their branding: no orange, no copied names or wording beyond the
generic product terms.

Owner decisions (2026-11-05, do not re-ask):
- **D1 Formats:** Compare Quotes accepts `.pdf`, `.xlsx` and `.docx`.
- **D2 Rendering:** xlsx and docx are rendered to a PDF record at upload in Python (reuse the loss-runs sheet
  renderer; python-docx + reportlab for docx). No LibreOffice.
- **D3 Marks:** worse terms and the best premium come from deterministic rules; one model-written Key difference
  sentence must cite the marked cells and is checked in code, with a template fallback.
- **D4 Structured terms:** Additional insured, Waiver of subrogation, Primary & non-contributory, and Notable
  exclusions from a fixed watch list become structured fields.
- **D5 Upload page:** a new route `#/compare/new`; the Compare Quotes home stays the work queue with a primary
  "Compare quotes" button; the left-panel Upload accordion goes away.

Defaults taken (owner may veto): Compare needs at least 2 ready files (today 1); Additional context capped at 1,000
characters; the Activity/queue lane's change 2 (retitle the Compare Quotes home heading) moves into this slice.

Order: the parked Activity/queue change 1 (failed proposal -> Needs attention, worktree
`agent-aeb680885ec25e0da`) ships first, on its own.

## 2. Intake (backend)

- `POST /api/documents` accepts `.pdf`, `.xlsx`, `.docx` for Compare Quotes. The format is decided by magic bytes, not
  only the extension: xlsx/docx must be a zip (`PK\x03\x04`), PDF must start `%PDF`. Mismatch is refused.
  `.xls` -> "Save it as .xlsx and upload again."; `.doc` -> "Save it as .docx and upload again."
- New `uploads.prepare_office(data, filename, limits) -> PreparedOffice(pdf, original, page_count, source_format)`,
  the same shape as `prepare_ams`:
  - **xlsx:** `loss_runs.sheets.parse_workbook` (hardened XML scan and caps), `plan_pages`, `render_page` per page,
    concatenated into one PDF. Each page prints its sheet name.
  - **docx:** new `backend/office_docx.py`: body paragraphs and tables in document order via python-docx, laid out
    with reportlab using the AMS renderer's fonts and margins; tables drawn as ruled grids. Headers, footers,
    images, comments and tracked changes are not rendered (tracked changes: the accepted text is shown).
  - The rendered PDF passes `inspect_pdf` (same page and size caps as any PDF). The original is kept
    (`store_original`) and is what Download returns.
- **Untrusted zips:** before parsing, cap total uncompressed size and entry count (reuse the sheets XML-scan limits);
  refuse files containing `vbaProject.bin` (macros) or external relationships (`TargetMode="External"`); XML parsed
  with entity resolution off. Each refusal has a plain message naming the reason.
- **Model:** `Document.source_format` (`pdf | xlsx | docx`), migration default `pdf`, returned by the documents and
  run APIs.
- Everything after upload is unchanged: parse, extract, verify, chips and the source viewer work on the PDF record.
- New dependency: `python-docx` (pinned). Added to README install line and the deploy image.
- Out of scope: `.pptx`, `.csv` quotes, docx images/logos.

## 3. Structured terms, rules, Key difference

### 3.1 New Compare fact fields (same extraction call)

| key | label | values |
|---|---|---|
| `additional_insured` | Additional insured | Blanket / Scheduled only / Not included / Not stated |
| `waiver_of_subrogation` | Waiver of subrogation | Blanket / Scheduled only / Not included / Not stated |
| `primary_noncontributory` | Primary & non-contributory | Included / Not included / Not stated |
| `notable_exclusions` | Notable exclusions | subset of the watch list, or "None of these" |
| `document_kind` | (display only) | quote / proposal / summary / other |

- Watch list (fixed, in code): action over; residential / habitational work; subsidence / earth movement; designated
  ongoing operations; wrap-up / OCIP.
- Each value other than "Not stated" must quote its source and pass `verify` like every fact. **"Not stated" means
  the document is silent; it is never treated as "Not included"** and is never marked.
- "None of these" must be supported by an exclusions list the model quoted; with no exclusions schedule in the
  document the value is Not stated.
- `document_kind` is display-only (column label); it is not a fact row and is never marked.
- Bump `EXTRACTION_VERSION` (`backend/pipeline/cache.py`).

### 3.2 Rules: `backend/pipeline/compare_rules.py`

Pure function `mark_cells(table) -> dict[(row_key, column_key), Mark(kind: "worse" | "best", reason: str)]`, run at
compose time and again after any cell edit.

- Eligible cell: verified or broker-edited. Unverified, conflict, failed and Not stated cells are never marked and do
  not count as comparable.
- A row needs at least 2 comparable values; otherwise no marks in that row.
- Endorsement rows: rank Blanket > Scheduled only > Not included (P&NC: Included > Not included). A cell ranking below
  the row's best is `worse`, reason "Others: <best value>".
- Notable exclusions: a cell is `worse` when it has a watch-list exclusion that another comparable cell lacks;
  reason names the exclusion.
- Total premium: the strictly lowest gets `best` only when every column has an eligible premium. Ties: no mark.
- Limits, deductibles/retentions, rate, fees and free-text rows are never marked.
- Marks are stored on the table cell (`Cell.mark`, `Cell.mark_reason`), nullable; old runs render without marks.

### 3.3 Key difference

- One model call after compose, only when at least one cell is marked. Input: the marked cells, all premiums, their
  cell references, and the run's Additional context in the delimited brief channel. Output: 1 to 2 sentences, every
  claim with cell citations, rendered as segments with chips.
- Code check: every carrier and term named must be among the cited cells; stated direction must agree with the marks
  (never "broader"/"better" for a `worse` cell, never "cheapest" for a non-`best` premium); citations must resolve.
- Fail -> template from the marks, e.g. "<Carrier> has the lowest premium (<amount>) but <worse term reasons>." or,
  with no premium mark, "<Carrier> <worse term reasons>." The callout is never empty and never unchecked.
- After an edit: marks recompute at once; the callout gets the existing stale tag and Rewrite link.
- Stored as a new block `key_difference` at the top of the report. No marks -> no block.
- Out of scope: Proposal Builder / DOCX export shows neither marks nor the callout.

## 4. Upload page and home (frontend)

### 4.1 `#/compare/new` (`?line=personal` from Personal lines)

- Header: crumbs "<Line> Insurance > Compare Quotes", title, one-line description, primary **Compare** at top right,
  disabled until 2 files are ready ("Add one more quote to compare").
- Large dropzone: "Upload quotes", "PDF, Word and Excel quotes, proposals and summaries"; `accept=".pdf,.xlsx,.docx"`.
  Reuses `DropZone` / `useUploads`; caps and rejection messages unchanged, plus the new format refusals.
- File list: format icon, status, remove. List order = column order.
- `ClientField`, `LineChoice` (preset from the entry).
- **Additional context:** optional, placeholder "Leave blank. BrokerDocs already knows what a quote comparison needs.",
  max 1,000 characters, sent with `startRun`, stored on the run, used only by compose and Key difference prompts via
  the delimited brief channel; never a fact, never affects extraction.
- No-model-key hint and start error as today. Success -> `#/runs/{id}`.

### 4.2 Home `#/`

- Remove the Upload and Input parameters accordions.
- Primary **Compare quotes** -> `#/compare/new`; secondary "Check a renewal".
- Empty state keeps the large dropzone; dropped files carry to `#/compare/new` through a shared upload store.
- Retitle the heading (Activity/queue change 2: it lists all commercial work, not only comparisons); Personal lines
  consistent. Remaining left-panel layout is decided in the design stack.

## 5. Comparison screen (frontend)

- Column head: muted label under the carrier: PDF -> "PDF quote" / "PDF proposal" / "PDF summary" by
  `document_kind`; xlsx -> "Spreadsheet"; docx -> "Word quote" / "Word proposal"; kind other/missing -> "PDF" /
  "Word document". Document letter and chips unchanged.
- New rows at the top of the Endorsements section: Additional insured, Waiver of subrogation, Primary &
  non-contributory, Notable exclusions; free-text Key endorsements / Key exclusions stay below.
- `worse`: outlined value + small warning glyph, reason in a tooltip; screen readers: "worse than other quotes:
  <reason>". Never colour alone.
- `best`: value in the success colour; screen readers: "lowest premium".
- Marks show under every evidence filter and in the phone layout.
- Key difference callout above the first report section: title, sentence with chips, stale tag + Rewrite after edits.
- Header status: "<n> quotes compared"; partial: "<k> of <n> quotes compared".

## 6. Testing and eval

- Unit: docx renderer (paragraphs, tables, empty doc); refusals (zip bomb, macros, external links, extension
  mismatch, .xls/.doc); xlsx record build and a citation landing on the right page and row; `compare_rules` truth
  table (Not stated, unverified, single comparable value, missing premium, ties, edited cell); Key difference check
  (direction mismatch, uncited carrier -> template).
- Fixtures: synthetic three-format scenario (PDF quote, PDF proposal, xlsx summary) plus a docx variant; new client and
  carrier names (not the reference product's demo names); a dev-seed run for the demo.
- Eval: offline replay with recordings for the new fields; paid live re-run of the 23-document Compare eval plus the new
  fixtures, 0 hard fails, exact-match truth for the new fields (needs owner go-ahead before it runs).
- e2e + axe: upload page (formats, 2-file minimum, Additional context, refusals), home button and empty-state hand-off,
  marks and callout on a fresh seed, at 1440 / 1024 / 390.

## 7. Build lanes

1. Activity/queue change 1 (Needs attention) merges first.
2. Lane A (backend, Opus): intake, fields, `compare_rules`, Key difference, API fields, fixtures, offline eval.
3. Lane B (UI, Opus, after lane A's API shape is fixed): upload page, home, column labels, marks, callout; design stack,
   max 3 review rounds.
4. Paid live eval after owner go-ahead; then e2e on a fresh seed, merge, HANDOFF, public repo refresh.

Memory: agents stalled today at ~1 GB free. Before lanes: `wsl --shutdown`, close spare tabs; max 3 lanes.
