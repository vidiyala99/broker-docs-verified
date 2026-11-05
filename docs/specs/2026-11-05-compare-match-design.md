# Slice 6: Compare Quotes match (formats, upload page, marked differences)

Date: 2026-11-05. Status: design approved in chat by the owner, section by section; spec approved by the owner (00:34),
next `/plan-eng-review`. Visual design of the two screens goes through the design stack (all required skills,
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

## Engineering review (2026-11-05, /plan-eng-review on main 9dc7077)

Target: this spec (sections 1 to 7 above, as approved at 00:34). Report file: this spec. Ledger entries below override
the text above them.

### Scope record
feature answers: none asked (D1-D5 are binding owner decisions; no cut proposed); structure: B, Smaller arrangement
(D1 of this review, owner answer 2026-11-05); accepted scope: sections 1 to 7 unchanged in features, with the Key
difference written as a field of the existing compose call (`compose_report`), and `backend/pipeline/compare_rules.py`
owning the marks, the Key difference check and its template; new modules `backend/office_docx.py` and
`compare_rules.py`; pending remedies: F1, F2, F3, F4, F5.

Scope Challenge result: scope accepted as-is (smaller arrangement, same features).

### Scope Challenge findings
- F1 [CRITICAL] (confidence: 9/10) `backend/pipeline/verify.py:564` `reason="value_not_in_quote"`: a fact verifies only
  when its value is inside its quote. Section 3.1 asks the model for categorical values (Blanket / Scheduled only /
  Not included / Included, watch-list names) that quotes rarely print verbatim, so these cells come back not_found and
  are never marked. Slice 4 precedent: `coi_checks.py:229-259` classifies endorsements in code from a verbatim title.
- F2 [CRITICAL] (confidence: 8/10) `backend/edits.py:134-156` `merge_edits` re-applies broker edits after every
  Rewrite/Retry compose, and `edits.py:122` `apply_cell_edit` changes cells: marks computed only at compose time go wrong
  after an edit, and section 3.2 names no owner for recomputation.
- F3 [CRITICAL] (confidence: 8/10) `backend/edits.py:110-119` `mark_stale` flags a section only when its prose cites an
  edited cell's passages; an edit to an uncited column can move the marks without staling the Key difference. Also
  `edits.py:84-91` `prose_passage_ids` and `schemas.py:994-1007` `passage_ids` enumerate block types by name (prior
  learning brokerdocs-block-union-traversal): a new block's chips are skipped unless added.
- F4 [INFORMATIONAL] (confidence: 9/10) `backend/db.py:311` Compare `quotes` slot `"min": 1`; `runner.py:434-443`
  `unread_required_slots` fails a run below min. Raising min to 2 turns "1 of 2 compared" into a FAILED run;
  26 backend test files and eval batches (`eval_hook.py:111`) start one-document Compare runs.
- F5 [INFORMATIONAL] (confidence: 8/10) `backend/pipeline/runner.py:430` any non-AMS kind fits a PDF slot and
  `POST /api/documents` is shared: a docx/xlsx quote also enters Policy Check and Proposal Builder pickers.
- F6 [INFORMATIONAL] (confidence: 9/10, factual correction, no behaviour change) `backend/pyproject.toml:24` pins
  `python-docx>=1.1,<2` already (section 2 "new dependency" is wrong); `backend/ams.py:120` the AMS renderer draws
  with PyMuPDF (`fitz.Font`), so "reportlab using the AMS renderer's fonts" means the same font file in reportlab
  (D2 keeps reportlab, already pinned at `pyproject.toml:20`). `docx/oxml/parser.py:19` python-docx already parses
  with `resolve_entities=False`.
- F7 [INFORMATIONAL] (confidence: 9/10, factual correction) `backend/db.py:104-135` there are no migrations:
  `Document.source_format` needs a `check_schema` entry and every dev DB is reseeded; "migration default pdf" means
  the column default only.

### Section 1: Architecture
Design-twice. `office_docx`: shape 1 (chosen, D2) renders paragraphs and tables to a PDF record at upload; shape 2
(text straight to parse, no PDF) loses the source viewer and chips, rejected by D2. `compare_rules`: shape 1 (chosen,
review D1 + R2) a pure module `mark_cells(table)`, `check_key_difference(...)`, `template_key_difference(...)`, called
from `artifact_out` and `compose_report`; shape 2 (marks inside `compose.build_tables`) rejected by R2. Depth:
`compare_rules` hides every ranking rule, eligibility rule and direction word behind three calls.
Red flags: (1) information leakage / importable internals: R1 reuses `coi_checks._AI_RE`, `_SCHEDULED_RE`,
`_WAIVER_RE`, `_PNC_RE`, private names; the build makes them public (`coi_checks.AI_RE` etc.) rather than importing
privates (required by R1, no new choice). No other flag: the watch list lives only in `compare_rules` (R1 means the
prompt asks for exclusion titles, not watch-list names, so there is no second copy).

```
upload (.pdf/.xlsx/.docx)
  -> magic bytes + extension agree? --no--> 422 plain refusal (.xls/.doc: "Save it as ...")
  -> zip pre-scan (entries, uncompressed total, vbaProject.bin, TargetMode="External") --fail--> 422
  -> xlsx: sheets.parse_workbook -> plan_pages (count checked against page cap BEFORE rendering) -> render_page*
     docx: office_docx.render (python-docx, resolve_entities=False) -> reportlab PDF
     any parse/lxml/python-docx error ------------------------------------------------> 422 "couldn't read this file"
  -> inspect_pdf (page/size caps) -> store PDF + store_original -> Document(kind=quote, source_format)
run -> parse/extract (wording for 3 terms + exclusion titles, R1) -> verify (unchanged)
    -> compose_report: build_tables -> mark_cells -> one model call (prose + key_difference)
         -> validate_draft + check_key_difference --fail--> template prose and/or template Key difference
artifact_out -> mark_cells(current cells) -> marks on cells; Key difference stale if marks != written-against (R3)
```
Failure per new path: corrupt docx raises inside python-docx/lxml and must become a 422 with a plain message, never a
500 (not in section 2's refusal list; added as required error handling). Compose falls back to `template_report`: the
Key difference must still come from the marks template ("never empty", section 3.3), so `template_report` builds it
too. Compare's `COMPOSE_SYSTEM_PROMPT` (`compose.py:495`) has no BRIEF rule (only `PROPOSAL_SYSTEM_PROMPT`,
`compose.py:591-604`, has one): Additional context needs that rule added, a prompt change covered by the live eval.

Findings:
- F8 [INFORMATIONAL] (confidence: 8/10) section 2: the refusal list lacks a plain "couldn't read" path for corrupt
  Office files (python-docx/lxml exceptions). Required error handling, added to tasks.
- F9 [INFORMATIONAL] (confidence: 8/10) `compose.py:953` `template_report(...)` fallback must carry the Key difference
  template. Required by section 3.3, added to tasks.
Dispositions: F8, F9 accepted as required implementation of approved behaviour (no new choice).

### Section 2: Code quality
Reuse: `prepare_ams` shape for `prepare_office`; `sheets.parse_workbook/plan_pages/render_page`; `DropZone`/`useUploads`
(`launch/Upload.tsx:28,93`) with an `accept` prop; the BRIEF channel (`compose.py:625-664`) for Additional context;
`coi_checks` classifier regexes (R1); `store_original`/`load_original` for Download. New: `office_docx.py`,
`compare_rules.py`, the Key difference block, the `#/compare/new` page and a one-shot upload hand-off from the home
empty state. Shared-code extraction: none proposed beyond making the four regexes public.
- F10 [INFORMATIONAL] (confidence: 7/10) `db.py:311,372` slot `accepts` strings and frontend `accept` attributes list
  formats in two places; R5 (D6) adds the docx/xlsx MIME types to two slots: one constant per format in `db.py`, and
  the upload page derives its `accept` from the workflow's slot definition rather than a second literal.
Disposition: F10 accepted as the implementation of R5 (D6).

### Section 3: Test review
```
CODE PATHS                                              USER FLOWS
[+] uploads.prepare_office / office_docx                [+] #/compare/new
  ├── [GAP] docx paragraphs, tables, empty doc            ├── [GAP][->E2E] 1 file: Compare disabled, 2: enabled (R4)
  ├── [GAP] refusals: zip bomb, macros, external link,    ├── [GAP][->E2E] .xls/.doc refusal messages
  │         extension mismatch, .xls/.doc, corrupt (F8)    ├── [GAP][->E2E] Additional context sent, 1,000-char cap
  ├── [GAP] xlsx page count over cap refused pre-render    └── [GAP][->E2E] home drop -> files arrive on #/compare/new
  └── [GAP] xlsx citation lands on page + row             [+] Comparison
[+] runner.check_slot_kinds (R5)                          ├── [GAP][->E2E] worse outline + glyph + SR text, best colour
  └── [GAP] docx refused in Policy Check/Certificates;    ├── [GAP][->E2E] callout chips; stale after mark-moving edit
            Build proposal from a docx comparison works   └── [GAP][->E2E] marks at 390 and under evidence filters
[+] classifier (R1)                                       [+] Other workflows
  └── [GAP] blanket / scheduled / not included /          └── [★★ TESTED] existing e2e suites (regression)
            unclassifiable -> Not stated; watch-list synonyms; None-of-these rule
[+] compare_rules.mark_cells (R2)
  └── [GAP] Not stated, unverified, single value, missing premium, ties, edited cell
[+] Key difference (review D1, R3)
  ├── [GAP] check: direction mismatch, uncited carrier, bad citation -> template
  ├── [GAP] template when compose falls back (F9)
  └── [GAP] stale: uncited premium edit moves mark -> stale; no mark change -> not stale; Rewrite clears
[+] artifact_out marks (R2)  └── [GAP] after edit, undo, Rewrite; other workflows none
[+] chips traversal (R3)     └── [GAP] Key difference passages counted in passage_ids and prose_passage_ids
LLM: [GAP][->EVAL] extraction prompt (wording + exclusion titles), compose prompt (BRIEF rule + key_difference)
COVERAGE: 0/24 new paths tested (all new) | existing regression suites unchanged and must stay green
```
Regression (IRON RULE): the Compare extraction and compose prompts change for every Compare run. Carried-forward
contract: section 6's paid live re-run of the 23-document eval with 0 hard fails (owner go-ahead before it runs), the
offline eval, and the full backend + e2e suites.
Open test-policy choices: R6 (truth for the new fields on the 23 existing documents), R7 (is a wrong mark a hard fail).

### Section 4: Performance
- xlsx render at upload: a large sheet plans hundreds of pages; counting `plan_pages` against the page cap before
  `render_page` refuses it at once instead of rendering then refusing (same behaviour, required bound).
  Scale: [estimated] ~40 rows per page, so the 300-page cap is ~12k rows.
- Marks on read: rows x columns <= ~30 x 8 per table per response; negligible [estimated].
- Tokens: extraction returns more output per document (three wordings + an exclusion-title list) and compose adds the
  Key difference. [re-measure before gate] in the live eval against the 440k-token 23-document baseline
  (2026-11-02 run, HANDOFF.md); per-document budget 400k (`long_300` is the tight case).
- No new queries in loops: `artifact_out` already loads the version; marks are computed in memory.

## Decision ledger

### R1: how the structured endorsement and exclusion values are read (F1)
Finding: F1, CRITICAL, confidence 9/10, `backend/pipeline/verify.py:564`, reviewer Claude.
Plan baseline: section 3.1 (owner-approved 00:34): the model returns the categorical value; "each value other than Not
stated must quote its source and pass verify"; "None of these" must be supported by a quoted exclusions list.
Runtime evidence: verify rejects a value not inside its quote (`verify.py:2-32, 564`); slice 4 classifies endorsement
kind and blanket/scheduled in code from a verified printed title (`coi_checks.py:199-259`). Live behaviour of the new
fields: unknown (not built).
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1 value source | model returns category; verify needs category in quote | model returns verbatim wording per term (verified as is); code classifies it (reuse `coi_checks` regexes, add blanket/not-included patterns); unclassifiable wording = Not stated | model returns category; verify gets a per-field keyword map for these 4 fields | unchanged |
| R1 "None of these" | model asserts it, must quote a list | derived in code: at least one verified exclusion title, none on the watch list | model asserts it; verify keyword map | unchanged |
| R1 watch-list match | model picks names | code matches verified exclusion titles by synonyms | model picks names, keyword-verified | unchanged |
| R2-R5 | pending | pending | pending | pending |
| D1-D5, review D1 | approved | unchanged | unchanged | unchanged |
Question D2:
D2 — How should the new endorsement and exclusion values be read?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, finding F1.
ELI10: Every value we show must be found word for word inside the passage it cites, or it is thrown out. Quotes say things like "Additional Insured: where required by written contract", not the word "Blanket", so asking the model for "Blanket" means the value fails the check and the row stays empty. Slice 4 hit this and solved it by having the model copy the printed words and deciding the category in code.
Stakes if we pick wrong: the four new rows come back empty on real quotes, so no marks and no Key difference: the headline feature of the slice does not show.
Recommendation: A because it reuses slice 4's proven pattern, keeps verify unchanged for every workflow and makes the category a deterministic rule like D3 asks.
Completeness: A=9/10, B=6/10, C=3/10
Pros / cons:
A) Copy words, classify in code (recommended)
  ✅ Verify stays untouched for every other workflow; the copied wording passes it as any fact does
  ✅ Category, watch-list match and "None of these" become testable code rules, in line with D3
  ❌ Wording that matches no pattern shows as Not stated until a pattern is added
B) Keyword map inside verify
  ✅ Keeps the extraction schema as the spec wrote it, one value per row
  ✅ Smaller prompt change than asking for wording
  ❌ Puts field-specific rules inside verify, which every workflow shares, and the model still picks the category
C) Leave as written
  ✅ No extra work in lane A beyond the spec as approved
  ✅ Nothing in verify or extraction changes
  ❌ Most new cells fail verify on real quotes, so the rows and marks rarely appear
Net: copy-then-classify costs a regex table and buys rows that actually verify.
Header: Field values
Options:
A) Copy words, classify in code (recommended)
The model returns the printed wording for each term (verified as is); code classifies it into Blanket / Scheduled only / Not included / Included, reusing the coi_checks regexes plus blanket and not-included patterns; unclassifiable wording shows Not stated and is never marked. Exclusions: verified exclusion titles, matched to the watch list in code; "None of these" when at least one verified title and none match. Tests: classifier truth table, watch-list synonyms, None-of-these rule. (human: ~1 day / CC: ~30 min)
B) Keyword map inside verify
The model returns the category as the spec says; verify accepts it when its quote contains a keyword from a per-field map (for example "written contract" for Blanket). Touches verify, shared by all workflows; needs regression runs of every workflow's eval. (human: ~1 day / CC: ~30 min)
C) Leave as written
Section 3.1 unchanged: categorical values must appear inside their quotes. Expect most new cells to be not_found on real quotes. (no extra work)

State: approved
Actual answer: A) Copy words, classify in code (owner, D2, 2026-11-05)
Accepted scope: the model returns the printed wording per term (verified as any fact); code classifies it into Blanket / Scheduled only / Not included / Included, reusing the `coi_checks` regexes plus blanket and not-included patterns; unclassifiable wording = Not stated, never marked. Exclusions: verified exclusion titles, matched to the watch list in code by synonyms; "None of these" when at least one verified title and none match. Tests: classifier truth table, watch-list synonyms, None-of-these rule. Verify unchanged.
History: none

### R2: where the marks are computed (F2)
Finding: F2, CRITICAL, confidence 8/10, `backend/edits.py:122-156`, reviewer Claude.
Plan baseline: section 3.2 (owner-approved 00:34): `mark_cells` runs at compose time and again after any cell edit;
marks stored on the cell (`Cell.mark`, `Cell.mark_reason`), nullable; old runs render without marks.
Runtime evidence: cells change in `apply_cell_edit` (`edits.py:122`) and `merge_edits` (`edits.py:134`, after every
Rewrite and Retry compose, `edits.py:301`, `merge_edits_after_retry` `edits.py:159`); every report response is built by
`artifact_out` via `artifact_json` (`routes/artifacts.py:31-39`). Marks code: not built.
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R2 where marks live | stored on Cell, recompute "after any cell edit" (writers unnamed) | derived: `compare_rules.mark_cells` runs in `artifact_out` for Compare Quotes runs and inside `compose_report` for the Key difference; nothing stored | stored on Cell; recomputed in compose (`build_tables`), `apply_cell_edit` and `merge_edits`, one test per writer | stored on Cell at compose only |
| R2 old Compare runs | no marks | premium `best` mark appears when every column has an eligible premium; no endorsement rows (fields absent) | no marks until recomposed | no marks |
| R2 Proposal Builder, Policy Check, others | no marks | no marks (Compare slug only) | no marks | no marks |
| R1 | approved A | unchanged | unchanged | unchanged |
| R3-R5 | pending | pending | pending | pending |
Question D3:
D3 — Where should the worse/best marks be computed?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, finding F2.
ELI10: A mark (the outline on a weaker term, the green lowest premium) depends on every quote in the row. The spec stores marks on each cell and says to recompute them after edits, but cells change in three places in the code (compose, a broker edit, and re-applying edits after Rewrite or Retry). Miss one and the screen shows a stale mark. Computing marks when the report is sent to the browser means there is nothing stored that can go stale.
Stakes if we pick wrong: after a broker edit or a Rewrite, a quote keeps a "worse" outline or "lowest premium" it no longer deserves, on the screen a broker trusts.
Recommendation: A because one function at one serialization point cannot drift, and it is a pure function of the cells already stored.
Completeness: A=9/10, B=8/10, C=4/10
Pros / cons:
A) Compute on read (recommended)
  ✅ One call site in artifact_out; no stored mark can disagree with the cells
  ✅ Undo, Rewrite, Retry and edits need no extra code: the next response is right
  ❌ Older Compare runs start showing the lowest-premium mark when all premiums verify
B) Store and recompute in each writer
  ✅ Matches the spec text: marks live in the stored version, old runs stay unmarked
  ✅ The stored JSON shows exactly what the broker saw at each version
  ❌ Three writers to keep in sync; a future fourth writer that forgets is a silent wrong mark
C) Store at compose only
  ✅ Least code: marks written once with the tables
  ✅ No change to the edit paths
  ❌ Marks are wrong after any edit or Rewrite until the next full run
Net: computing on read trades "old runs stay unmarked" for marks that can never go stale.
Header: Marks
Options:
A) Compute on read (recommended)
`compare_rules.mark_cells(table)` runs in `artifact_out` for Compare Quotes runs (API adds `mark` and `mark_reason` per cell) and inside `compose_report` for the Key difference; nothing stored on Cell. Older Compare runs gain the premium mark when every column has an eligible premium. Tests: response carries marks after edit, undo and Rewrite; other workflows get none. (human: ~half a day / CC: ~15 min)
B) Store and recompute in each writer
`Cell.mark` / `Cell.mark_reason` stored; recomputed in `build_tables`, `apply_cell_edit` and `merge_edits` (covers Rewrite and Retry); old runs unmarked. Tests: one per writer plus undo. (human: ~1 day / CC: ~25 min)
C) Store at compose only
Marks written once by compose; edits and Rewrite leave them as they were until a new run. (no extra work)

State: approved
Actual answer: A) Compute on read (owner, D3, 2026-11-05)
Accepted scope: `compare_rules.mark_cells(table)` runs in `artifact_out` for Compare Quotes runs (API adds `mark` and `mark_reason` per cell) and inside `compose_report` for the Key difference; nothing stored on Cell (section 3.2's `Cell.mark` storage is dropped). Older Compare runs gain the premium mark when every column has an eligible premium; other workflows get no marks. Tests: response carries correct marks after edit, undo and Rewrite; other workflows get none.
History: none

### R3: when the Key difference counts as stale (F3)
Finding: F3, CRITICAL, confidence 8/10, `backend/edits.py:110-119`, reviewer Claude.
Plan baseline: section 3.3 (owner-approved 00:34): "After an edit: marks recompute at once; the callout gets the
existing stale tag and Rewrite link." Review D1: the sentence is a field of the compose call. R2: marks computed on read.
Runtime evidence: `mark_stale` flags a section only when its prose cites one of the edited cell's original passages
(`edits.py:110-127`); an edit to a column the sentence does not cite can change which cell is marked. Required with any
option (approved behaviour, not a choice): the new block is added to `prose_passage_ids` (`edits.py:84-91`) and
`ArtifactContent.passage_ids` (`schemas.py:994-1007`), with a test that its chips are counted (prior learning
brokerdocs-block-union-traversal).
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R3 stale trigger | a cited cell is edited | the block stores the marks it was written against (row, column, kind); `artifact_out` sets `stale` when the current marks differ, or when a cited cell is edited | any cell edit in the run stales it | a cited cell is edited (unchanged) |
| R3 Rewrite | existing regenerate | existing regenerate (refreshes the sentence, review D1) | same | same |
| R1, R2 | approved A, A | unchanged | unchanged | unchanged |
| R4, R5 | pending | pending | pending | pending |
Question D4:
D4 — When should the Key difference sentence show as out of date?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, finding F3.
ELI10: The Key difference says something like "Harbor has the lowest premium but only scheduled additional insured". If the broker fixes another carrier's premium so Harbor is no longer lowest, the marks move at once, but today's out-of-date check only fires when an edited cell is one the sentence cites. The sentence would keep claiming Harbor is cheapest with no warning.
Stakes if we pick wrong: the one sentence a broker reads first states the wrong carrier as cheapest, with no stale tag prompting a Rewrite.
Recommendation: A because it fires exactly when the sentence's basis changed, using marks already computed on read (R2).
Completeness: A=9/10, B=7/10, C=4/10
Pros / cons:
A) Stale when the marks change (recommended)
  ✅ Fires exactly when what the sentence describes has changed, never on unrelated edits
  ✅ Uses the marks already computed on read, so there is no stored flag to keep in sync
  ❌ The block carries a small copy of the marks it was written against
B) Stale on any edit
  ✅ Simplest rule; impossible to miss a case
  ✅ No mark comparison code
  ❌ Every typo fix in any cell asks the broker to Rewrite, which teaches them to ignore the tag
C) Cited cells only
  ✅ No new code: the existing section rule applies
  ✅ Matches how every other prose section works today
  ❌ An edit to an uncited column can make the sentence false with no warning
Net: comparing marks costs a stored list and keeps the tag honest without crying wolf.
Header: Key difference
Options:
A) Stale when the marks change (recommended)
The block stores the marks it was written against (row, column, kind); `artifact_out` sets `stale: true` when the current marks differ, or when a cited cell is edited; Rewrite refreshes it. Tests: edit an uncited premium that moves the best mark -> stale; edit that changes no mark -> not stale; Rewrite clears it. (human: ~half a day / CC: ~15 min)
B) Stale on any edit
Any cell edit in a Compare run marks the Key difference stale until Rewrite. Tests: any edit -> stale; Rewrite clears. (human: ~2 h / CC: ~10 min)
C) Cited cells only
The existing section rule: stale only when a cell the sentence cites is edited. (no extra work)

State: approved
Actual answer: A) Stale when the marks change (owner, D4, 2026-11-05)
Accepted scope: the Key difference block stores the marks it was written against (row, column, kind); `artifact_out` sets `stale: true` when the current marks differ, or when a cited cell is edited; Rewrite refreshes it. Plus the required traversal work: the block in `prose_passage_ids` and `ArtifactContent.passage_ids`, with a chips-counted test. Tests: edit an uncited premium that moves the best mark -> stale; edit that changes no mark -> not stale; Rewrite clears it.
History: none

### R4: where the 2-file minimum is enforced (F4)
Finding: F4, INFORMATIONAL, confidence 9/10, `backend/db.py:311`, reviewer Claude.
Plan baseline: section 1 default (approved with the spec at 00:34, owner may veto): "Compare needs at least 2 ready
files (today 1)"; section 4.1: Compare disabled until 2 files are ready; section 5: partial "<k> of <n> quotes compared".
Runtime evidence: Compare `quotes` slot `"min": 1` (`db.py:311`); `unread_required_slots` fails a run whose slot has
fewer done documents than `min` (`runner.py:434-443`); 26 backend test files post one-document Compare runs (for
example `tests/test_api_flow.py:131`); the eval hook batches documents per run (`eval_hook.py:111-118`).
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R4 upload page | n/a | Compare disabled until 2 files are ready | same | same |
| R4 API start (POST /api/runs) | 1 document allowed | 1 allowed (eval, tests, API users) | 400 `invalid_run` below 2 for Compare Quotes; tests and eval batches updated | 400 below 2 (via slot) |
| R4 slot min / read failure | min 1; one of two fails = partial | min 1; "1 of 2 quotes compared" | min 1; "1 of 2 quotes compared" | min 2; one of two fails = FAILED run |
| R1-R3 | approved | unchanged | unchanged | unchanged |
| R5 | pending | pending | pending | pending |
Question D5:
D5 — Where should "at least 2 quotes" be enforced?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, finding F4.
ELI10: The spec says a comparison needs two quotes. If we put that rule in the workflow's slot settings, a comparison of two quotes where one file fails to read becomes a failed run instead of "1 of 2 quotes compared". If we put it only on the new upload page, brokers can't start a one-quote comparison, but tests, the eval and the API keep working with one document.
Stakes if we pick wrong: a broker who uploads two quotes and has one fail gets a failed run with no report, or dozens of tests and eval batches break for no user gain.
Recommendation: A because the broker-facing rule lives where brokers act, and read failures still produce a usable partial report.
Completeness: A=8/10, B=9/10, C=5/10
Pros / cons:
A) Upload page only (recommended)
  ✅ One-failure comparisons still show the readable quote as "1 of 2 compared"
  ✅ Eval batches, 26 test files and API callers keep working unchanged
  ❌ The API still accepts a one-document Compare run if called directly
B) Upload page and API
  ✅ The rule holds for every caller, not only the browser
  ✅ Read failures still give a partial report (slot min stays 1)
  ❌ Updates 26 test files and any single-document eval batch to send two documents
C) Slot minimum of 2
  ✅ One setting enforces it everywhere, including reads
  ✅ No new validation code
  ❌ One unreadable file in a two-quote comparison fails the whole run
Net: the rule belongs where the broker clicks; reads should degrade to partial, not fail.
Header: Minimum
Options:
A) Upload page only (recommended)
Compare on `#/compare/new` stays disabled until 2 files are ready; POST /api/runs keeps accepting 1 document; slot min stays 1, so one failed read gives "1 of 2 quotes compared". Test: e2e button disabled with 1 file, enabled with 2. (human: ~1 h / CC: ~5 min)
B) Upload page and API
As A, plus POST /api/runs returns 400 `invalid_run` for a Compare Quotes run with fewer than 2 documents; 26 backend test files and single-document eval batches updated to two documents. (human: ~1 day / CC: ~40 min)
C) Slot minimum of 2
Compare `quotes` slot `min: 2`; a run where only 1 of 2 reads fails as a required-slot failure. (human: ~1 h / CC: ~10 min, plus test updates)

State: approved
Actual answer: A) Upload page only (owner, D5, 2026-11-05)
Accepted scope: Compare on `#/compare/new` stays disabled until 2 files are ready; POST /api/runs keeps accepting 1 document; slot min stays 1, so one failed read gives "1 of 2 quotes compared". Test: e2e button disabled with 1 file, enabled with 2.
History: none

### R5: which workflows take Word and Excel quotes (F5)
Finding: F5, INFORMATIONAL, confidence 8/10, `backend/pipeline/runner.py:414-431`, reviewer Claude.
Plan baseline: section 2 (owner-approved 00:34): `POST /api/documents` accepts `.pdf`, `.xlsx`, `.docx` "for Compare
Quotes"; D1 names Compare Quotes only. Other workflows not mentioned.
Runtime evidence: `POST /api/documents` is shared by every workflow; `check_slot_kinds` lets any non-AMS,
non-spreadsheet document into a PDF slot (`runner.py:428-431`); Build proposal preselects the comparison's documents
(work queue D6, `QueueView.tsx` `actions.buildProposal`), so the Proposal Builder `quotes` slot (`db.py:372`) receives
whatever Compare accepted.
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R5 Compare Quotes | PDF only | PDF, docx, xlsx | PDF, docx, xlsx |
| R5 Proposal Builder | PDF only | PDF, docx, xlsx (needed for Build proposal) | PDF, docx, xlsx (needed for Build proposal) |
| R5 Policy Check, Certificates | PDF only | also docx, xlsx records (no eval coverage) | refused: `check_slot_kinds` reads `source_format` against the slot's `accepts` list; 400 with "Policy Check takes PDF documents." |
| R5 upload `accept` attributes | per wizard | only `#/compare/new` offers the new formats | same |
| R1-R4 | approved | unchanged | unchanged |
Question D6:
D6 — Which workflows should take Word and Excel quotes?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, finding F5.
ELI10: Uploads go into one shared document library. Once a Word or Excel quote is uploaded for a comparison, it also shows up when the broker starts a Policy Check or a certificate, and today nothing stops it being used there. Those workflows were never tested on converted Word or Excel files. Proposal Builder must take them, because "Build proposal" reuses the comparison's files.
Stakes if we pick wrong: a Policy Check or certificate quietly runs on a converted spreadsheet nobody evaluated, or Build proposal breaks on a comparison that included a Word quote.
Recommendation: B because it ships the formats where they were designed and tested, keeps Build proposal working, and gives a clear message elsewhere.
Completeness: A=7/10, B=9/10
Pros / cons:
A) Every workflow
  ✅ No new check: converted files are PDF records like any other
  ✅ Brokers can reuse an uploaded Word quote anywhere
  ❌ Policy Check and certificates run on formats with no eval coverage, so errors there go unmeasured
B) Compare and Proposal only (recommended)
  ✅ Formats appear only where the new eval and fixtures cover them, plus Build proposal
  ✅ Other workflows refuse them with a plain message instead of failing later
  ❌ A small check in check_slot_kinds and slot accepts lists to keep current
Net: a slot check buys "only where tested" without breaking Build proposal.
Header: Formats
Options:
A) Every workflow
docx/xlsx quotes are PDF records with `kind="quote"`, accepted in every PDF slot (Compare, Proposal, Policy Check, Certificates); only `#/compare/new` offers the formats in its picker. No new check. (no extra work)
B) Compare and Proposal only (recommended)
Compare and Proposal Builder `quotes` slots list the docx and xlsx types in `accepts`; `check_slot_kinds` refuses a docx/xlsx `source_format` in a slot that does not list it (Policy Check, Certificates) with "<slot> takes PDF documents."; pickers there hide them. Tests: refusal per workflow, Build proposal from a comparison with a docx quote. (human: ~half a day / CC: ~15 min)

State: approved
Actual answer: B) Compare and Proposal only (owner, D6, 2026-11-05)
Accepted scope: Compare and Proposal Builder `quotes` slots list the docx and xlsx types in `accepts`; `check_slot_kinds` refuses a docx/xlsx `source_format` in a slot that does not list it (Policy Check, Certificates) with "<slot> takes PDF documents."; pickers there hide them. Tests: refusal per workflow, Build proposal from a comparison with a docx quote.
History: none

### R6: truth for the new fields on the 23 existing eval documents (Section 3)
Finding: Section 3 test review, INFORMATIONAL, confidence 8/10, spec section 6 ("exact-match truth for the new fields"), reviewer Claude.
Plan baseline: section 6 (owner-approved 00:34): new synthetic three-format fixtures with exact-match truth for the new fields; paid live re-run of the 23-document eval plus the new fixtures, 0 hard fails. Whether the 23 existing documents get truth for the new fields is not stated.
Runtime evidence: the 23-document truth has no entries for fields that do not exist yet; a field with no truth is not scored (unknown how the Compare gate treats an unscored new key; to confirm in lane A).
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R6 truth on 23 existing documents | none for new fields | truth added for the 4 new fields (most will be Not stated), scored exact-match | new fields unscored on the 23 documents |
| R6 truth on new fixtures | exact-match (section 6) | unchanged | unchanged |
| R7 | pending | pending | pending |
| R1-R5 | approved | unchanged | unchanged |
Question D7:
D7 — Should the 23 existing eval documents get answers for the four new fields?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, Section 3 test review.
ELI10: The eval compares what we extract against a hand-written answer key. The new fixtures will have answers for the four new fields, but the 23 documents we already test on do not. Without answers there, a made-up "Not included" on one of those quotes would go unnoticed, even though it would draw a "worse" mark on screen.
Stakes if we pick wrong: a false "Not included" on a real-looking quote passes the gate and puts a wrong warning in front of a broker.
Recommendation: A because most answers are "Not stated", so the key is cheap to write, and it is the only way to catch invented values on documents that never mention these terms.
Completeness: A=9/10, B=6/10
Pros / cons:
A) Add answers for all 23 (recommended)
  ✅ Catches invented values on documents that never mention these terms, the most likely error
  ✅ Most answers are "Not stated", so the answer key is quick to write and review
  ❌ About an hour of careful reading per carrier family to get the few real values right
B) New fixtures only
  ✅ No change to the existing answer key or its history
  ✅ Lane A starts sooner
  ❌ An invented value on any of the 23 documents passes the gate unseen
Net: a mostly "Not stated" answer key buys detection of the error that would show up as a false warning.
Header: Eval truth
Options:
A) Add answers for all 23 (recommended)
Truth for additional_insured, waiver_of_subrogation, primary_noncontributory and notable_exclusions added to all 23 existing documents (most "Not stated"), scored exact-match like the new fixtures; offline replay updated. (human: ~half a day / CC: ~30 min)
B) New fixtures only
The 23 documents stay unscored on the new fields; only the new three-format fixtures carry truth for them. (no extra work)

State: approved
Actual answer: A) Add answers for all 23 (owner, D7, 2026-11-05)
Accepted scope: truth for additional_insured, waiver_of_subrogation, primary_noncontributory and notable_exclusions added to all 23 existing documents (most "Not stated"), scored exact-match like the new fixtures; offline replay updated.
History: none

### R7: is a wrong mark a hard fail in the Compare gate (Section 3)
Finding: Section 3 test review, INFORMATIONAL, confidence 8/10, spec section 6 ("0 hard fails"), reviewer Claude.
Plan baseline: section 6: live eval "0 hard fails" under the existing hard-fail rules; marks are not mentioned.
Runtime evidence: the Compare gate's hard fails today cover verified wrong values and canaries (handoff 2026-11-01, lane C rules); marks do not exist yet.
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R7 wrong mark | n/a | hard fail: the marks computed from extracted cells differ from the marks computed from truth (a missing or extra worse/best) | counted in an accuracy figure only |
| R6 | approved A (D7) | unchanged | unchanged |
| R1-R5 | approved | unchanged | unchanged |
Question D8:
D8 — Should a wrong "worse" or "lowest premium" mark fail the eval?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, Section 3 test review.
ELI10: Marks are the most visible claim the comparison makes: "this quote is worse here", "this one is cheapest". We can compute the marks twice in the eval, once from what we extracted and once from the answer key, and compare. The question is whether any difference blocks a release or only lowers a score.
Stakes if we pick wrong: a release ships that tells brokers the wrong carrier is cheapest or flags the wrong quote, or the gate blocks on noise.
Recommendation: A because a wrong mark is a false claim in front of the broker, the same class as a verified wrong value, which already hard-fails.
Completeness: A=9/10, B=6/10
Pros / cons:
A) Wrong mark = hard fail (recommended)
  ✅ Treats the headline claim like a verified wrong value, which already blocks
  ✅ Deterministic: both mark sets come from the same compare_rules code
  ❌ A truth-key mistake can block a release until the key is fixed
B) Accuracy figure only
  ✅ The gate never blocks on a mark alone
  ✅ Easier first live run while the classifier patterns settle
  ❌ A wrong cheapest or worse mark can ship as long as the overall score holds
Net: blocking on wrong marks holds the slice's main claim to the same bar as wrong values.
Header: Mark gate
Options:
A) Wrong mark = hard fail (recommended)
The Compare gate computes marks from extracted cells and from truth with compare_rules; any missing or extra worse/best mark is a hard fail; offline gate test with a planted wrong mark. (human: ~2 h / CC: ~15 min)
B) Accuracy figure only
Mark agreement reported as a ratio in the eval output; never blocks. (human: ~1 h / CC: ~10 min)

State: approved
Actual answer: A) Wrong mark = hard fail (owner, D8, 2026-11-05)
Accepted scope: the Compare gate computes marks from extracted cells and from truth with compare_rules; any missing or extra worse/best mark is a hard fail; offline gate test with a planted wrong mark.
History: none

### Outside voice (Codex, gpt-6-astra, 2026-11-05): 6 findings, all verified in code
- O1 High (confidence 9/10) `backend/loss_runs/sheets.py:533-539` `fit()` cuts each cell to one line with "...";
  `sheets.py:65-66` caps 80 columns and 300 characters per cell: an xlsx quote rendered this way loses wording.
- O2 High (confidence 8/10) R1's "None of these" (and the exclusion `worse` rule) treat a partial list as a complete one.
- O3 High (confidence 9/10) `compose.py:413` tables come from facts; `edits.py:301` overlays edits after compose:
  compose-time marks ignore edits. Required fix (approved R2/R3 behaviour, no new choice): `compose_report` applies
  `inp.edits` to the tables before `mark_cells` and before storing the marks the Key difference was written against.
- O4 High (confidence 9/10) `schemas.py:1025` edited cells lose passages; `compose.py:560-564` forbids citing a passage
  for an edited value; the callout needs a citation per claim.
- O5 High (confidence 9/10) `compose.py:680-685` Compare allows any run number in any segment: "A is cheapest at
  $2,000" passes when $2,000 is B's premium.
- O6 Medium (confidence 8/10) `eval_hook.py:347-352` the eval reads facts, not the artifact response; R7 computes both
  mark sets with the same code.

### R8: how xlsx quotes are rendered (O1)
Finding: O1, High, confidence 9/10, `backend/loss_runs/sheets.py:533`, reviewer Codex (verified by Claude).
Plan baseline: section 2 + D2: xlsx rendered at upload by reusing the loss-runs `render_page`.
Runtime evidence: `render_page` truncates every cell to one line ("..."); the parser keeps 300 characters per cell and
80 columns (`sheets.py:65-66, 132`).
Comparison grid:
| Choice | Current | A | B | C | D |
|---|---|---|---|---|---|
| R8 xlsx rendering | loss-runs `render_page` (truncating) | a new Compare sheet renderer beside the docx renderer that wraps each cell's full text across lines and pages (header row repeated, sheet name printed), reusing `parse_workbook` with a higher per-cell cap; any cell over the cap or sheet over 80 columns refuses the file with a plain message | keep `render_page` as is | investigate: measure truncation on the new xlsx fixture, no build | defer this change only |
| D2 (python renderers, no LibreOffice) | approved | unchanged | unchanged | unchanged | unchanged |
Question D9:
D9 — How should Excel quotes be drawn into the PDF we read?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, outside-voice finding O1.
ELI10: The plan reuses the loss-runs sheet drawer to turn an Excel quote into a PDF, and the PDF is what the model reads. That drawer cuts every cell to one line and adds "...", which is fine for claim rows but loses wording like "Blanket AI where required by written contract" in a coverage summary. Text that was cut off can't be read, cited or marked.
Stakes if we pick wrong: Excel quotes show Not stated for terms they actually state, so the comparison quietly favours the PDF quotes.
Recommendation: A because a quote summary is long text in cells, and only a wrapping renderer keeps every word readable and citable.
Completeness: A=9/10, B=4/10, C=5/10, D=3/10
Pros / cons:
A) Wrap full cell text (recommended)
  ✅ Every word in the sheet reaches the model and can be cited and verified
  ✅ Oversized cells or sheets are refused plainly instead of silently cut
  ❌ A second sheet renderer to maintain beside the loss-runs one
B) Keep the loss-runs renderer
  ✅ No new rendering code; one renderer for every sheet
  ✅ Loss-run behaviour untouched
  ❌ Long coverage wording is cut to one line and never verifies
C) Investigate first
  ✅ Measures how much the fixture loses before building anything
  ✅ Keeps both options open
  ❌ Delays lane A and the answer is predictable from the code
D) Defer
  ✅ Lane A ships the other formats sooner
  ✅ No renderer decision now
  ❌ xlsx support ships with silent truncation
Net: a wrapping renderer is the only option where Excel quotes get the same evidence bar as PDFs.
Header: Excel render
Options:
A) Wrap full cell text (recommended)
New Compare sheet renderer beside the docx renderer: full cell text wrapped across lines and pages, header row repeated, sheet name printed; reuses `parse_workbook` with a higher per-cell cap; a cell over the cap or a sheet over 80 columns refuses the file with a plain message. Tests: long cell wraps and verifies; over-cap refusal. (human: ~1 day / CC: ~30 min)
B) Keep the loss-runs renderer
Reuse `render_page` unchanged; long cells are cut to one line. (no extra work)
C) Investigate first
Render the planned xlsx fixture with `render_page`, count truncated cells, report; no build until answered again. (human: ~1 h / CC: ~10 min)
D) Defer
Leave this row unresolved; lane A builds docx and PDF intake first. (no extra work now)

State: approved
Actual answer: A) Wrap full cell text (owner, D9, 2026-11-05)
Accepted scope: new Compare sheet renderer beside the docx renderer: full cell text wrapped across lines and pages, header row repeated, sheet name printed; reuses `parse_workbook` with a higher per-cell cap; a cell over the cap or a sheet over 80 columns refuses the file with a plain message. Tests: long cell wraps and verifies; over-cap refusal.
History: none

### R9: when exclusions may be marked or called "None of these" (O2, reopens part of R1)
Finding: O2, High, confidence 8/10, `backend/pipeline/verify.py:533`, reviewer Codex (verified by Claude).
Plan baseline: R1 (D2, approved): "None of these" when at least one verified exclusion title and none match; section
3.2: a cell is `worse` when it has a watch-list exclusion another comparable cell lacks. R1's classifier for the three
endorsement rows is not reopened.
Runtime evidence: verify checks each quote alone, never that a list is complete; a summary or proposal deck may print a
few exclusions or none.
Comparison grid:
| Choice | Current (R1) | A | B | C | D |
|---|---|---|---|---|---|
| R9 evidence a list is complete | none: one verified title counts | the document prints a verified forms/exclusions schedule (heading such as "Exclusions" or "Schedule of Forms and Endorsements" quoted and verified); only such a cell counts as complete | none (keep R1) | investigate on the fixtures | defer |
| R9 "None of these" | at least one verified title, none match | only for a complete cell with no watch-list match; otherwise Not stated | as R1 | pending | pending |
| R9 exclusion `worse` mark | has one another comparable cell lacks | only when the other cell is complete and lacks it | as R1 | pending | pending |
| R1 endorsement rows | approved | unchanged | unchanged | unchanged | unchanged |
Question D10:
D10 — When can a quote be marked worse for an exclusion, or said to have none of them?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, outside-voice finding O2 (reopens part of D2).
ELI10: D2 said "None of these" when we read at least one exclusion title and none are on the watch list. But a summary sheet might list only one exclusion and leave out the rest. Then quote A lists "action over" and gets marked worse, while quote B just didn't print its exclusions. Only a document that prints its full schedule of exclusions can prove something is absent.
Stakes if we pick wrong: the more honest, more complete quote gets the warning mark, and the one that printed less looks better.
Recommendation: A because absence can only be claimed from a document that prints its full schedule, and the schedule heading is verifiable like any quote.
Completeness: A=9/10, B=5/10, C=6/10, D=3/10
Pros / cons:
A) Require a printed schedule (recommended)
  ✅ A quote is never marked worse because another one printed less
  ✅ The schedule heading is a verified quote, so the rule stays deterministic
  ❌ Summaries without a schedule show Not stated for exclusions, so fewer marks appear
B) Keep D2 as approved
  ✅ More exclusion marks on screen, closer to the demo
  ✅ No schedule detection to build
  ❌ Partial lists count as complete, so marks can point at the wrong quote
C) Investigate first
  ✅ Checks how often fixtures print a full schedule before choosing
  ✅ No rule change yet
  ❌ The risk is clear from the rule itself; delays lane A
D) Defer
  ✅ Lane A proceeds on the other rows
  ✅ No decision now
  ❌ The exclusion row ships with the partial-list risk
Net: requiring a printed schedule trades a few marks for marks that never punish completeness.
Header: Exclusions
Options:
A) Require a printed schedule (recommended)
Extraction also returns the forms/exclusions schedule heading (verified quote); a cell counts as complete only with it. "None of these" only for a complete cell with no watch-list match, else Not stated; an exclusion `worse` mark only when the other cell is complete and lacks it. Tests: partial list -> no mark, Not stated; complete vs complete -> mark. (human: ~half a day / CC: ~20 min)
B) Keep D2 as approved
One verified exclusion title makes a list count as complete. (no extra work)
C) Investigate first
Count schedule headings in the 23 documents and new fixtures; report; decide again. (human: ~1 h / CC: ~10 min)
D) Defer
Leave this row unresolved for now. (no extra work now)

State: approved
Actual answer: A) Require a printed schedule (owner, D10, 2026-11-05)
Accepted scope: extraction also returns the forms/exclusions schedule heading (verified quote); a cell counts as complete only with it. "None of these" only for a complete cell with no watch-list match, else Not stated; an exclusion `worse` mark only when the other cell is complete and lacks it. Tests: partial list -> no mark, Not stated; complete vs complete -> mark. Supersedes R1's "None of these" clause.
History: R1 (D2) approved "None of these when at least one verified title and none match"; reopened by O2 for the completeness evidence only.

### R10: how the Key difference cites values (O4)
Finding: O4, High, confidence 9/10, `backend/schemas.py:1025` and `backend/pipeline/compose.py:560-564`, reviewer Codex (verified by Claude).
Plan baseline: section 3.3: "every claim with cell citations, rendered as segments with chips"; section 3.2: broker-edited cells are eligible for marks.
Runtime evidence: an edit clears the cell's passages; `EDITS_NOTE` tells the model to name the edited value as edited by the user and cite no passage for it.
Comparison grid:
| Choice | Current | A | B | C | D |
|---|---|---|---|---|---|
| R10 citation unit | passage ids (segments) | cell references (row, column): a chip opens the cell's source when it has one, and reads "edited by you" when the cell is edited | passage ids; a claim about an edited cell sends the callout to the template | investigate | defer |
| R10 chips traversal (R3 required work) | passages | the cited cells' current passages count | passages | pending | pending |
| R3, R2 | approved | unchanged | unchanged | unchanged | unchanged |
Question D11:
D11 — How should the Key difference point at its evidence when a value was edited?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, outside-voice finding O4.
ELI10: Chips in our prose point at a passage in a PDF. When a broker corrects a cell, that cell no longer has a passage, and the existing rules say "don't cite one". But an edited premium can still be the lowest and draw a mark, so the Key difference must talk about it. Pointing at the cell itself (which then shows its source, or "edited by you") works for both.
Stakes if we pick wrong: after any edit to a marked cell the Key difference falls back to the template every time, or cites a passage for a number the broker overwrote.
Recommendation: A because the claims are about cells, and a cell chip covers both read and edited values honestly.
Completeness: A=9/10, B=6/10, C=5/10, D=3/10
Pros / cons:
A) Cite cells (recommended)
  ✅ Edited values are cited honestly as "edited by you", read values open their source page
  ✅ The checker validates cell references directly against the marks
  ❌ A new chip kind in the frontend beside passage chips
B) Passages; template on edits
  ✅ Reuses the existing passage chips unchanged
  ✅ No new segment shape
  ❌ Any edit to a marked cell forces the template, losing the model sentence
C) Investigate first
  ✅ Lets the design stack weigh in on a cell chip
  ✅ No contract change yet
  ❌ Lane A's block shape waits on it
D) Defer
  ✅ No decision now
  ✅ Lane A can start on intake
  ❌ The callout's citation contract stays contradictory
Net: citing cells costs one chip kind and makes edited values citable.
Header: Citations
Options:
A) Cite cells (recommended)
Key difference segments cite (row, column) cell references; the chip opens the cell's source passage when it has one and shows "edited by you" when edited; `passage_ids()` and `prose_passage_ids` count the cited cells' current passages; the checker resolves each reference against the table. Tests: edited marked cell cited -> valid; unknown cell -> template. (human: ~half a day / CC: ~20 min)
B) Passages; template on edits
Segments cite passage ids; a claim about an edited cell fails the check and the callout uses the template. (human: ~1 h / CC: ~5 min)
C) Investigate first
Mock both chip kinds in the design stack; decide again. (human: ~2 h / CC: ~20 min)
D) Defer
Leave this row unresolved for now. (no extra work now)

State: approved
Actual answer: A) Cite cells (owner, D11, 2026-11-05)
Accepted scope: Key difference segments cite (row, column) cell references; the chip opens the cell's source passage when it has one and shows "edited by you" when edited; `passage_ids()` and `prose_passage_ids` count the cited cells' current passages; the checker resolves each reference against the table. Tests: edited marked cell cited -> valid; unknown cell -> template.
History: none

### R11: how strictly the Key difference sentence is checked (O5)
Finding: O5, High, confidence 9/10, `backend/pipeline/compose.py:680-685`, reviewer Codex (verified by Claude).
Plan baseline: D3 (owner, binding): one model-written sentence that cites the marked cells, checked in code, template fallback; section 3.3 check: carriers and terms named are among the cited cells, direction agrees with the marks, citations resolve.
Runtime evidence: for Compare, `allowed_for` scopes allowed numbers to the whole run, not to a segment's citations.
Comparison grid:
| Choice | Current | A | B | C | D |
|---|---|---|---|---|---|
| R11 numbers | any number in the run | each number in a segment must equal a value of a cell that segment cites | model picks structured claims; code renders amounts and wording (changes D3: not model-written; needs owner to reopen D3) | investigate | defer |
| R11 carriers | among cited cells (whole sentence) | each carrier named in a segment owns a cell that segment cites | rendered by code | pending | pending |
| R11 direction words | agree with marks | unchanged | rendered by code | pending | pending |
| D3 | approved, binding | unchanged | reopened | unchanged | unchanged |
Question D12:
D12 — How strictly should the Key difference sentence be checked?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, outside-voice finding O5.
ELI10: Today's report checker lets any number from the comparison appear anywhere in the prose. So the sentence "Harbor is cheapest at $2,000" would pass even if Harbor's premium is $1,000 and $2,000 is another carrier's. Tying every number and carrier to the cells cited in the same phrase closes that gap and still lets the model write the sentence, as D3 says.
Stakes if we pick wrong: the first sentence a broker reads pairs a carrier with someone else's premium, and the checker passes it.
Recommendation: A because it closes the mismatch inside D3 (model-written, checked in code) without reopening an owner decision.
Completeness: A=9/10, B=9/10, C=5/10, D=3/10
Pros / cons:
A) Check per phrase (recommended)
  ✅ Every number and carrier must match a cell cited in the same phrase
  ✅ Keeps D3: the model still writes the sentence
  ❌ More model sentences fail the check and fall back to the template
B) Code writes the sentence
  ✅ Amounts and comparison words can never be wrong
  ✅ No prose checker needed for the callout
  ❌ Overrides D3 (model-written sentence), which you approved
C) Investigate first
  ✅ Measures how often model sentences mix values on the fixtures
  ✅ No rule change yet
  ❌ The gap is visible in the code today
D) Defer
  ✅ No decision now
  ✅ Lane A can start on intake
  ❌ The callout ships with a checker that misses swapped values
Net: per-phrase checks close the swap without taking the sentence away from the model.
Header: KD check
Options:
A) Check per phrase (recommended)
`check_key_difference`: each segment's numbers must equal values of the cells that segment cites; each carrier named in a segment must own a cited cell; direction words agree with the marks; any failure -> template. Tests: swapped premium -> template; correct -> kept. (human: ~half a day / CC: ~20 min)
B) Code writes the sentence
The model returns structured claims (cells and their order); code renders amounts and comparative wording. Reopens D3. (human: ~1 day / CC: ~30 min)
C) Investigate first
Run the fixture compose offline and count mixed-value sentences; decide again. (human: ~1 h / CC: ~10 min)
D) Defer
Leave this row unresolved for now. (no extra work now)

State: approved
Actual answer: A) Check per phrase (owner, D12, 2026-11-05)
Accepted scope: `check_key_difference`: each segment's numbers must equal values of the cells that segment cites; each carrier named in a segment must own a cited cell; direction words agree with the marks; any failure -> template. Tests: swapped premium -> template; correct -> kept. D3 unchanged.
History: none

### R12: what the mark gate scores (O6, reopens R7's mechanism)
Finding: O6, Medium, confidence 8/10, `backend/eval_hook.py:347-352`, reviewer Codex (verified by Claude).
Plan baseline: R7 (D8, approved): wrong mark = hard fail, marks computed from extracted cells and from truth with compare_rules.
Runtime evidence: the eval hook returns facts per document, not the artifact response; both mark sets would come from one implementation, so a ranking bug cancels out.
Comparison grid:
| Choice | Current (R7) | A | B | C | D |
|---|---|---|---|---|---|
| R12 expected marks | compare_rules over truth | hand-written expected marks per fixture run in the truth file | compare_rules over truth (keep) | investigate | defer |
| R12 actual marks | compare_rules over extracted facts | the marks in the run's artifact response (eval hook adds them) | compare_rules over facts (keep) | pending | pending |
| R7 hard-fail rule | approved | unchanged | unchanged | unchanged | unchanged |
Question D13:
D13 — What should the mark gate compare?
Project/branch/task: broker-docs-verified main, eng review of the slice 6 spec, outside-voice finding O6 (refines D8).
ELI10: D8 made a wrong mark a hard fail. As planned, the eval computes both the expected and the actual marks with the same code, so a bug in that code shows up on both sides and cancels out, and the marks the browser actually receives are never checked. Writing the expected marks by hand and reading the real response fixes both.
Stakes if we pick wrong: a ranking bug or a broken response passes the gate while brokers see wrong marks.
Recommendation: A because a gate is only independent if its expected answer is written by a person and its actual answer is what users get.
Completeness: A=9/10, B=6/10, C=5/10, D=3/10
Pros / cons:
A) Hand-written vs real response (recommended)
  ✅ Catches ranking bugs and response wiring bugs that self-comparison hides
  ✅ The expected marks double as readable documentation of each fixture
  ❌ Expected marks must be kept in step when a fixture changes
B) Keep D8's mechanism
  ✅ No extra truth to write
  ✅ Simpler eval hook
  ❌ A bug in compare_rules or the response passes unseen
C) Investigate first
  ✅ Looks at how the eval hook can expose the artifact
  ✅ No change yet
  ❌ Delays the gate design
D) Defer
  ✅ No decision now
  ✅ Lane A starts sooner
  ❌ The gate checks the code against itself
Net: hand-written expectations against the real response make the gate independent.
Header: Mark scoring
Options:
A) Hand-written vs real response (recommended)
Truth files gain hand-written expected marks per fixture run; the eval hook returns the run's artifact marks; the gate hard-fails on any missing or extra mark; compare_rules recomputation kept only as a diagnostic. Tests: offline gate with a planted wrong mark in the response. (human: ~half a day / CC: ~20 min)
B) Keep D8's mechanism
Both mark sets computed with compare_rules. (no extra work)
C) Investigate first
Map how the eval hook can return artifact marks; decide again. (human: ~1 h / CC: ~10 min)
D) Defer
Leave this row unresolved for now. (no extra work now)

State: approved
Actual answer: A) Hand-written vs real response (owner, D13, 2026-11-05)
Accepted scope: truth files gain hand-written expected marks per fixture run; the eval hook returns the run's artifact marks; the gate hard-fails on any missing or extra mark; compare_rules recomputation kept only as a diagnostic. Tests: offline gate with a planted wrong mark in the response. Supersedes R7's mechanism; R7's hard-fail rule stands.
History: R7 (D8) approved the hard-fail rule; this row refines only how expected and actual marks are produced.

Approval readiness: PASS. Checked: review D1 (structure B), R1 (D2 A, None-of-these clause superseded by R9), R2 (D3 A),
R3 (D4 A), R4 (D5 A), R5 (D6 B), R6 (D7 A), R7 (D8 A, mechanism refined by R12), R8 (D9 A), R9 (D10 A), R10 (D11 A),
R11 (D12 A), R12 (D13 A). Required work without a new choice: F6, F7 corrections; F8, F9, F10; O3; R3 traversal;
R1 public regex names; Section 4 pre-render page count.

## Amendments after review (override sections 1 to 7)
- Section 2: `python-docx` is already pinned (F6). Office rendering lives beside `ams.py` in python (D2); docx via
  python-docx + reportlab with the AMS font file; xlsx via a new wrapping sheet renderer (R8), not loss-runs `render_page`.
  Corrupt Office files -> 422 "couldn't read this file" (F8). xlsx page count checked before rendering (Section 4).
- Section 2 model: `Document.source_format` column + `check_schema` entry; no migrations, reseed dev DBs (F7).
  Formats accepted only in Compare and Proposal Builder slots; refused elsewhere (R5).
- Section 3.1: the model returns printed wording for the three endorsement terms and the verified exclusion titles plus
  the schedule heading; code classifies (R1, R9). "None of these" only for a complete schedule (R9).
- Section 3.2: marks computed on read in `artifact_out` and inside `compose_report`, never stored (R2); compose applies
  `inp.edits` before marking (O3); exclusion `worse` only against a complete schedule (R9).
- Section 3.3: the Key difference is a field of the existing compose call (review D1); segments cite cells (R10);
  per-segment number and carrier checks (R11); stale when marks differ from those it was written against (R3);
  `template_report` carries the Key difference template (F9); Compare compose prompt gains the BRIEF rule.
- Section 4.1: 2-file minimum on the upload page only; API and slot min unchanged (R4).
- Section 6: truth for the 4 new fields on all 23 documents (R6); hand-written expected marks per fixture run, scored
  against the artifact response; any wrong mark is a hard fail (R7, R12).

## NOT in scope
- `.pptx`, `.csv` quotes and docx images/logos (spec section 2, owner).
- Marks and the Key difference in Proposal Builder / DOCX export (spec section 3.3).
- Word/Excel quotes in Policy Check and Certificates (R5): no eval coverage there.
- An API-level 2-quote minimum (R4).

## What already exists
- `uploads.prepare_ams` / `store_original` / `load_original` (`uploads.py:91-165`): the shape and Download path for rendered Office files.
- `loss_runs.sheets.parse_workbook` and its zip/XML caps (`sheets.py:163-365`): reused for xlsx parsing; its `render_page` is not reused (R8).
- `coi_checks` endorsement regexes (`coi_checks.py:199-202`): reused for classification (R1), made public.
- BRIEF channel (`compose.py:625-664`): reused for Additional context.
- `DropZone` / `useUploads` (`launch/Upload.tsx:28,93`): reused on `#/compare/new`.
- `mark_stale`, `merge_edits`, `regenerate_prose` (`edits.py`): Rewrite path refreshes the Key difference with no new wiring (review D1).

## Failure modes
| New path | Realistic failure | Covered by | User sees |
|---|---|---|---|
| docx intake | corrupt XML inside a valid zip | F8 422 + unit test | plain refusal |
| xlsx intake | 50k-row sheet | pre-render page count + test | plain refusal |
| xlsx render | long coverage wording | R8 wrapping renderer + test | full text, citable |
| classification | unusual wording | R1 -> Not stated + truth table | Not stated, no mark |
| exclusions | summary prints a partial list | R9 schedule rule + test | Not stated, no mark |
| marks after edit/Rewrite | stale stored marks | R2 on read + O3 edits before marking + tests | correct marks |
| Key difference | swapped premium in prose | R11 per-segment check + test | template sentence |
| Key difference | basis changed after edit | R3 + test | stale tag + Rewrite |
| compose fallback | provider error | F9 template + test | template callout |
| eval | ranking bug | R12 hand-written marks vs response | gate fails |
No critical gaps: every row has a test and visible handling.

## Worktree parallelization strategy
| Step | Modules touched | Depends on |
|------|----------------|------------|
| A1 intake | backend/uploads, backend/office render module, backend/routes/documents, backend/models + db (source_format, slots) | — |
| A2 rules + compose | backend/pipeline (extract fields, compare_rules, compose), backend/routes/artifacts, backend/schemas | — |
| A3 eval | tests/eval (truth, gate, hook), tests/fixtures, backend/eval_hook | A2 field keys |
| B UI | frontend/src (compare/new, launch, run table, callout), frontend/e2e | A1 + A2 API shape |
Lane A1 + lane A2 in parallel (shared: `backend/schemas.py`, `backend/API.md`, `frontend/src/api/types.ts`: merge A1
first, A2 rebases). Then A3 (fixtures, truth, offline gate) once A2's field keys are fixed. Then B through the design
stack. Paid live eval after owner go-ahead. Memory: at most 3 lanes at once (handoff).

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific finding above.

- [ ] **T1 (P1, human: ~1 day / CC: ~40min)** — intake — docx/xlsx upload, refusals, source_format, slot accepts
  - Surfaced by: F6, F7, F8, R5, Section 4 (pre-render page count)
  - Files: backend/uploads.py, backend/office_docx.py (new), backend/routes/documents.py, backend/models.py, backend/db.py, backend/pipeline/runner.py
  - Verify: backend pytest (new intake tests: paragraphs/tables/empty, zip bomb, macros, external link, mismatch, .xls/.doc, corrupt, over-cap; R5 refusals)
- [ ] **T2 (P1, human: ~1 day / CC: ~30min)** — intake — wrapping xlsx renderer
  - Surfaced by: R8 (O1)
  - Files: backend/office_docx.py (sheet renderer beside docx), backend/loss_runs/sheets.py (limits param only)
  - Verify: long cell wraps and verifies; citation lands on page and row; over-cap refusal
- [ ] **T3 (P1, human: ~1 day / CC: ~40min)** — extraction — wording + schedule heading + code classifier
  - Surfaced by: R1, R9, F1
  - Files: backend/schemas.py, backend/pipeline/extract.py, backend/pipeline/coi_checks.py (public regexes), backend/pipeline/compare_rules.py (new), backend/pipeline/cache.py (EXTRACTION_VERSION)
  - Verify: classifier truth table; watch-list synonyms; None-of-these only with a schedule
- [ ] **T4 (P1, human: ~1 day / CC: ~40min)** — marks — mark_cells on read and in compose
  - Surfaced by: R2, O3, R9
  - Files: backend/pipeline/compare_rules.py, backend/routes/artifacts.py (artifact_out), backend/pipeline/compose.py
  - Verify: truth table (Not stated, unverified, single value, missing premium, ties, edited); marks after edit/undo/Rewrite; other workflows none
- [ ] **T5 (P1, human: ~1 day / CC: ~45min)** — Key difference — compose field, cell citations, checks, stale, template
  - Surfaced by: review D1, R3, R10, R11, F9, Section 1 (BRIEF rule for Compare)
  - Files: backend/schemas.py (block + traversal), backend/edits.py (prose_passage_ids), backend/pipeline/compose.py, backend/pipeline/compare_rules.py, backend/routes/runs.py (additional_context)
  - Verify: swapped premium -> template; edited cell citable; uncited mark-moving edit -> stale; compose fallback carries template; chips counted
- [ ] **T6 (P1, human: ~1 day / CC: ~40min)** — eval — truth for 23 docs, new fixtures, mark gate
  - Surfaced by: R6, R7, R12
  - Files: tests/fixtures (new three-format + docx scenario), tests/eval (truth, gate, replay), backend/eval_hook.py
  - Verify: offline eval green; planted wrong mark in response -> hard fail; paid live run only with owner go-ahead
- [ ] **T7 (P1, human: ~2 days / CC: ~1.5h)** — UI — upload page, home, column labels, marks, callout
  - Surfaced by: spec sections 4-5, R4, R10 (cell chip), R5 (pickers hide formats)
  - Files: frontend/src (compare/new, launch, run table, callout, api/types.ts), frontend/e2e
  - Verify: e2e + axe at 1440/1024/390 on a fresh seed; design stack, max 3 rounds
- [ ] **T8 (P2, human: ~2h / CC: ~15min)** — docs + seed — API.md, README, dev seed demo run
  - Surfaced by: spec section 6, F6, F7
  - Files: backend/API.md, frontend/README.md, backend/dev_seed.py
  - Verify: seed test; reseed note
- [ ] **T9 (P3, human: 1h / CC: 10m)** — diff — Reuse verify value helpers (deferred), carried from the slice 5a review
  - Surfaced by: Code Quality, verify.py:145-165 (earlier review)
  - Files: backend/pipeline/verify.py, backend/pipeline/diff.py
  - Verify: backend pytest

## Completion summary
- Step 0: Scope Challenge — scope accepted as-is (smaller arrangement, review D1)
- Reality rows: 7/7 applicable rows emitted (tier 1), 0 unverified premises
- Architecture Review: 3 issues found (F8, F9, private regex reuse)
- Code Quality Review: 1 issue found (F10)
- Test Review: diagram produced, 24 gaps identified
- Performance Review: 1 issue found (pre-render page count)
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 0 items proposed to user
- Failure modes: 0 critical gaps flagged
- Unresolved decisions: 0 in this review
- Outside voice: Codex (gpt-6-astra), completed, 6 findings, all verified; 5 decided (D9-D13), 1 required fix (O3)
- Parallelization: 4 steps, 2 parallel (A1, A2) / 2 sequential (A3, B)
- Lake Score: 0/12 (no option scored 10/10; the top-scored option was picked in 11 of 12 scored answers)

Reality rows:
REALITY: premise-table pass 9 spec claims checked, 2 false (python-docx "new", reportlab "AMS fonts"), unverified=0 backend/pyproject.toml:24
REALITY: already-done pass no slice 6 code on main at 9dc7077 (git log -G compare_rules|office_docx|key_difference|source_format hits only the spec commit 6e8bfda) backend/uploads.py:91
REALITY: surface-check pass routes/functions named exist: POST /api/documents, prepare_ams, parse_workbook, inspect_pdf, store_original backend/routes/documents.py:68
REALITY: binding-decisions pass D1-D5 kept; D3 not reopened (R11 chose A) docs/specs/2026-11-05-compare-match-design.md:16
REALITY: numbers finding token baseline 440k [re-measure before gate]; ~12k-row xlsx cap [estimated] HANDOFF.md:89
REALITY: deferred-asks pass no linked issue docs/specs/2026-11-05-compare-match-design.md:1
REALITY: pr-count pass one local merge per lane, no PR queue (no remote) docs/specs/2026-11-05-compare-match-design.md:156

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | Codex (gpt-6-astra), automatic in `/plan-eng-review` | Independent 2nd opinion | 1 | completed | 6 findings (all High/Medium), all verified, all resolved (D9-D13, O3 required) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES OPEN (mapped to T1-T8) | 29 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** Codex, plan-review phase, completed; 6 findings (5 High, 1 Medium), each verified in code by Claude; 5 resolved by owner answers D9-D13, 1 (O3) applied as required work.
- **CROSS-MODEL:** Claude's review found the verify/categorical gap (F1), stale stored marks (F2), Key difference staleness (F3); Codex found what the native pass missed: truncating sheet renderer (O1), partial exclusion lists (O2), marks computed before edits at Rewrite (O3), edited cells uncitable (O4), whole-run number scope (O5), self-referential mark gate (O6). No disagreements.
- **VERDICT:** ENG REVIEWED: all decisions answered and mapped to T1-T8; ready for lanes A1 + A2. Eng Review status is issues_open (mapped work, not failure), so eng review required per dashboard rule until the build lands.

NO UNRESOLVED DECISIONS
