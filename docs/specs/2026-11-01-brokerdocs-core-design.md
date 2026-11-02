# BrokerDocs, Slice 1: Core Engine (Compare Quotes)

Date: 2026-11-01. Status: DRAFT, awaiting owner review.
Source of truth for observed behavior: `rea-out/` (REA evidence, bundle
analysis, demo-video frames) and `BrokerDocs-REA-Teardown.md`.

## 1. Intent

**Outcome:** a local, single-user web app that behaves like BrokerDocs AI's
core loop: drop in insurance documents, get a cited AI-generated artifact,
review and edit it in place, ask follow-ups, export.

**Fidelity target:** match observed *behavior* and UI shape. Internals
(prompts, schemas, model routing, AMS sync) were never observed and are our
own design. Clean-room: no copied shipped JS, assets, or branding.

**Constraints (owner-stated):** runs locally; OpenAI is the model backend
(owner supplies key); not a job-hunt artifact.

**Assumed (not stated, correct me):** single local user, no auth;
synthetic test PDFs unless owner supplies real ones.

**Success criteria:**
1. Upload N quote PDFs, run Compare Quotes, get a sectioned report with
   a carrier-comparison table and numbered citation chips.
2. Every non-"Not specified" value has a chip that opens the source PDF at
   the cited page with the passage highlighted.
3. Citation verification rate and field accuracy are measured against
   synthetic ground truth, and reported per run of the eval suite.
4. Cell edits persist, snapshot a version, and mark the cell "edited".
5. Follow-up chat answers from the run's documents with citations.
6. Export to XLSX and PDF.

**Out of scope for slice 1:** auth/SSO, AMS, vault UI, proposal builder,
ACORD fill, assistant/market search, other workflows (slices 2 to 6).

## 2. Observed behavior (REA frames, `rea-out/frames/`)

These drive the design. Vendor-claim items are excluded.

- **Artifact is a sectioned report, not a bare grid.** Compare Quotes output
  has a right-hand outline: 1 Executive Summary, 2 Common Declarations,
  3 Rate Analysis and Premium Summary (3.1 Rate, 3.2 Premium), 4 Coverage
  Specifications, 5 Key Endorsements and Exclusions, 6/7 line-specific
  features, Subjectivities and Binding Terms, Cross-Quote Consistency Flags.
  Tables sit inside prose sections.
- **Comparison table:** rows = Named Insured, Address, Admitted Status,
  AM Best Rating, Policy Term, Territory, Producer, Policy Form; one column
  per carrier. Missing value renders `Not specified`, with no chip.
- **Citation chips are numbered and colored per source document.** A chip
  number is reused across many cells in one column (for example `2` on
  Admitted Status, Policy Term, Producer, Policy Form for one carrier).
  So a chip points at a **source passage/page reference**, not one field.
  Hover shows `<filename>.pdf` and `Page: N`. Click opens the PDF in a new
  tab beside the artifact, with a dashed highlight box on the page.
- **Shell:** breadcrumb `Workflows > X > X Conversation`; left panel with
  `Upload (N)` and `Input Parameters` accordions, then the conversation and
  a `Suggestions` list of follow-up prompts, then a message box with attach
  and mic; right pane with tabs `All Artifacts`, the artifact, and opened
  source PDFs; artifact toolbar: thumbs up/down, copy, export (three
  formats), upload, email, side-by-side toggle, outline toggle.
- **Narrative artifacts** (Coverage Gap, Tower Analysis) cite inline in prose
  and list `Supporting Evidence` as chip-tagged bullets. Tower issues render
  as `ISSUE-NNN` cards with Severity, Category, Impact, Affected Layers,
  Follow-Form.

## 3. Architecture

```
broker-docs-verified/
  backend/    FastAPI, SQLite via SQLAlchemy, PyMuPDF, OpenAI SDK
  frontend/   Vite + React + TypeScript
  data/       files + sqlite db (gitignored)
```

- Single implicit workspace; schema keeps a `workspace_id` so slice 6 does
  not need a rewrite.
- Runs execute as background tasks; the UI follows progress by SSE.
- `Provider` interface wraps the model. Only `OpenAIProvider` exists now.
  Key from `OPENAI_API_KEY`; model names are config, not code.
- REST boundary between backend and frontend (shape informed by the static
  endpoint candidates in the teardown, but our own routes).

### Data model

| Table | Holds |
|---|---|
| `workflow` | name, category, input slots, instructions, section plan, fact schema. Compare Quotes is seeded as data so slice 3 (custom workflows) needs no rewrite. |
| `document` | file, page count, kind |
| `page` | document, page no, text, word boxes (JSON), `ocr` flag |
| `run` | workflow, parameters, status, doc-to-slot map, token/cost totals |
| `message` | conversation thread for a run |
| `passage` | the citation target: document, page, quote, bbox, `verified` |
| `artifact` + `artifact_version` | report as structured blocks; snapshot per edit |

Artifact content is a list of blocks (heading, paragraph, table, issue card).
Any text or table cell may carry `passage_ids`. A cell edited by the user
carries `edited: true` and drops its passage chips from that cell's value
(the chips stay available as "original source").

## 4. Pipeline

1. **Parse.** PyMuPDF text and word boxes per page. A page with almost no
   text is rendered and sent through an OpenAI vision pass, flagged `ocr`.
2. **Extract facts (per document, parallel).** Page-tagged text plus the
   workflow's fact schema, via structured outputs. Each fact returns
   `{field, value, page, quote}`. Page numbers stay local to one document.
3. **Verify.** The quote must appear on the cited page (normalized
   whitespace match, fuzzy fallback with a threshold). Verified facts
   become `passage` rows with a bbox. Unverified facts are dropped to
   `Not specified` and logged. No unverified value is ever shown as cited.
4. **Compose report.** A second model call receives only verified facts and
   the workflow's section plan, and writes the sectioned report. Prose
   cites by `passage_id`. A validator rejects any `passage_id` not in the
   verified set and any number in the prose absent from the facts, then
   retries once.
5. **Review.** Edit in place, snapshot versions, thumbs feedback stored.
6. **Chat.** Follow-ups see the run's page text and verified passages, answer
   with citations, and may propose artifact edits that the user accepts.
7. **Export.** XLSX (tables) and PDF (full report).

Suggestions (follow-up prompts) come from one cheap call after composition.

## 5. UI

Layout per section 2. **Visual source of truth:
`docs/reference/brokerdocs-ui-reference.md`** (design review decision D2,
2026-11-01): match the reference product's measured tokens (Inter, primary `#6356F0`,
text `#35383B`, 6px radius, 44px buttons, 48px inputs), shell, chip
behavior and states; use our own logo, name and copy; embed no BrokerDocs
images or shipped code. Tokens live in CSS variables so a restyle is a
one-file change. The four evidence states (verified, scan-read, couldn't
verify, failed), the stale-section flag, the failed-column state and
run progress are not shown in any observed frame, so they are our own
design; they must follow the reference tokens and never rely on color
alone (shape and label as well). Mockup generation was cancelled in
favor of the real frames. Source-PDF viewer uses pdf.js (the original
uses react-pdf) with a highlight overlay from `passage.bbox`.

## 6. Errors

- Rate limits and 5xx: retry with backoff, bounded.
- A document that fails to parse, is password-protected, or is empty
  marks only its column failed; the run completes with a visible banner.
- Provider output that fails schema validation: one retry, then that
  document's facts are `Not specified`.
- All model calls log tokens and estimated cost on the run.

## 7. Testing

- Synthetic quote-PDF generator with known ground truth, in clean,
  multi-column, and scanned variants.
- Eval harness: field-level accuracy, citation verification rate,
  hallucinated-value rate (value not in source), reported per suite run.
- Unit tests: verifier (exact, whitespace, fuzzy, miss), validator
  (bad `passage_id`, invented number), version snapshots.
- API tests with a mocked `Provider`.
- One Playwright end-to-end: upload, run, click chip, edit cell, export.

## 8. Change from the design presented in chat

The chat design had one citation per field. The frames show chips map to
reused source passages and the artifact is a composed sectioned report. The
pipeline therefore adds a compose step (4) and the `passage` table. The
verify-before-cite rule is unchanged.

## 9. Open questions

1. Real sample PDFs from the owner, or synthetic only?
2. Which OpenAI models for extraction vs composition (config default to
   be confirmed against current availability at plan time)?
3. Export formats beyond XLSX and PDF (the toolbar showed three)?

## Engineering review (plan-eng-review, 2026-11-01)

Target: this spec. Report file: this spec (selected plan). Not a git repo,
so git-history checks are n/a.

### Scope record
feature answers: none proposed (no cuts); structure: A) Original arrangement
(answer to D1); accepted scope: slice 1 as written in sections 1 to 7, one file
per responsibility (about 22 backend, 14 frontend, 12 test files, 11 services);
pending remedies: none raised at this point (review findings follow).

## Decision ledger

### R1: terminal behavior when compose validation fails twice
Finding: A1, P1, 9/10, spec section 4 step 4, reviewer: Claude
Plan baseline: original proposal: validator rejects, retries once, then unspecified.
Runtime evidence: unknown (no code yet).
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1 failure ending | unspecified | template report + banner | fail run | ship prose, flag unverified |
| R2 size limits | pending | pending | pending | pending |
| R3 security baseline | pending | pending | pending | pending |
Question D2:
D2 — What should happen if the report-writing step fails validation twice?
Project/branch/task: broker-docs-verified slice 1, spec section 4 step 4 (compose and validate).
ELI10: After facts are verified, a second model call writes the report prose. A checker rejects it if it cites an unverified source or states a number that is not in the facts. The spec retries once but never says what happens if the second try also fails.
Stakes if we pick wrong: the run hangs, crashes, or ships prose the checker already rejected.
Recommendation: A because the user always gets a usable, fully cited artifact, with a visible banner saying prose generation failed.
Note: options differ in kind, not coverage, so no completeness score. The number check examines only money, percent, date and limit-like numbers (not section numbers or counts), whichever option is chosen. Pending remedies not decided here: R2, R3.
Header: Compose failure
Options:
A) Template report fallback (recommended)
Build the tables and a facts list directly from verified facts with no model prose, show a banner and a Retry prose button. Chips stay verified. Human ~3h / CC ~15min. Risk: plainer output. Maintenance: one extra renderer.
B) Fail the run
Mark the run failed with the validator reason and create no artifact. Human ~30min / CC ~5min. Risk: a flaky checker blocks all output. Maintenance: none.
C) Ship prose with flags
Keep the prose, strip chips from rejected sentences and mark them unverified. Human ~1h / CC ~10min. Risk: report contains statements the checker rejected. Maintenance: flag rendering.

State: approved
Actual answer: A) Template report fallback (answer to D2)
Accepted scope: after two failed compose validations, build tables and a facts list from verified facts with no model prose, show a banner and a Retry prose button; chips stay verified; the number check covers money, percent, date and limit-like numbers only.
History: none

### R2: size and cost limits per document and run
Finding: A2, P2, 9/10, spec section 4 step 2, reviewer: Claude
Plan baseline: original proposal: no limits stated.
Runtime evidence: unknown (no code yet).
Comparison grid: R1 pending; R2 current none, A hard caps, B chunked extraction, C none; R3 pending.
Question D3:
D3 — How should slice 1 handle very large documents and runaway cost?
Project/branch/task: broker-docs-verified slice 1, spec section 4 step 2 (extract).
ELI10: Each document is sent to the model as full page-tagged text. A 300-page policy would overflow the model's context or run up a large bill, and nothing in the spec stops it.
Stakes if we pick wrong: a raw provider error with no explanation, or an unexpectedly large API bill on a single run.
Recommendation: A because slice 1 handles quotes (about 10 pages each); long policies arrive in slice 2, where chunking can be designed against real needs.
Note: options differ in kind, not coverage, so no completeness score. Pending remedies not decided here: R3.
Header: Size limits
Options:
A) Hard caps, reject early (recommended)
Defaults, all configurable: 80 pages and 15 MB per document, 8 documents per run, a per-run input-token budget (600k) checked before any model call; over a cap the user gets a message naming the limit. Chunked extraction is logged as a slice 2 TODO. Human ~2h / CC ~10min.
B) Chunked extraction now
Split long documents into page windows, extract per window, merge and de-duplicate facts. No page cap. Human ~2 days / CC ~1.5h. Risk: merge conflicts between windows. Maintenance: more pipeline code.
C) No limits
Send everything and surface provider errors as they come. Human 0 / CC 0. Risk: opaque failures and unbounded cost.

State: approved
Actual answer: A) Hard caps, reject early (answer to D3)
Accepted scope: configurable defaults of 80 pages and 15 MB per document, 8 documents per run, 600k input-token budget per run checked before any model call; over a cap the user sees a message naming the limit; chunked extraction logged as a slice 2 TODO.
History: none

### R3: security baseline for a local app
Finding: A3, P2, 8/10, spec sections 3 and 6, reviewer: Claude
Plan baseline: original proposal: no stance on uploads, injection or key handling.
Runtime evidence: unknown (no code yet).
Comparison grid: R1 pending; R2 pending; R3 current none, A baseline, B skip.
Question D4:
D4 — Which security baseline should the local app ship with?
Project/branch/task: broker-docs-verified slice 1, spec sections 3 and 6.
ELI10: It is a local single-user app, but it parses untrusted PDFs, writes uploaded files to disk, and sends document text to a model. Documents can contain text like "ignore previous instructions". The spec says nothing about any of that.
Stakes if we pick wrong: a crafted filename writes outside the data folder, an injected instruction changes an extracted value, or the API key leaks into the browser or logs.
Recommendation: A because it is a few hours of work and the verifier already blocks fabricated quotes, so the remaining gaps are the cheap ones.
Note: options differ in kind, not coverage, so no completeness score. Pending remedies not decided here: none.
Header: Security baseline
Options:
A) Add the baseline (recommended)
Bind 127.0.0.1 only, CORS limited to the UI origin, uploads checked by PDF magic bytes and size, files stored under generated UUID names, document text sent inside a delimited data block with an instruction to ignore directives in it, no tools or web access in extraction, API key read from env server-side only and never logged or sent to the browser, plus an injection test case. Human ~3h / CC ~20min.
B) Skip it
Rely on the app being local. Human 0 / CC 0. Risk: filename path tricks and prompt injection stay unaddressed.

State: approved
Actual answer: A) Add the baseline (answer to D4)
Accepted scope: bind 127.0.0.1 only; CORS limited to the UI origin; uploads checked by PDF magic bytes and size; files stored under generated UUID names; document text sent inside a delimited data block with an instruction to ignore directives in it; no tools or web access during extraction; API key read from env server-side only, never logged or sent to the browser; one prompt-injection test case.
History: none

### R4: trust level for citations on scanned pages
Finding: Q2, P1, 8/10, spec section 4 steps 1 and 3, reviewer: Claude
Plan baseline: original proposal: vision OCR text, quote verified against that same text.
Runtime evidence: tesseract not installed on this machine (probe: command -v tesseract). PyMuPDF search_for returns bbox for digital text (probe, PyMuPDF 1.28.0).
Comparison grid: R4 current: model-read text counts as verified; A local OCR with word boxes; B model OCR labeled unverified with page-level highlight; C scanned pages unsupported in slice 1.
Question D5:
D5 — How much should a citation on a scanned page be trusted?
Project/branch/task: broker-docs-verified slice 1, spec section 4 steps 1 and 3 (parse and verify).
ELI10: For normal PDFs we check a quote against the PDF's own text, so the model cannot make a citation up. For scanned pages there is no text, so the spec has the model read the page and then checks the quote against what the model just wrote. A misread would still pass the check.
Stakes if we pick wrong: a wrong number on a scanned quote shows a green verified chip that points at nothing real.
Recommendation: B because it keeps scans working with no install while being honest that those chips are not independently verified.
Note: options differ in kind, not coverage, so no completeness score. Pending remedies not decided here: none.
Header: Scanned citations
Options:
A) Local OCR with word boxes
Use PyMuPDF with Tesseract so the text and boxes come from a deterministic reader, not the model, and verification stays independent. Needs Tesseract installed on this machine (not installed now) and its accuracy on messy scans is lower than model vision. Human ~3h / CC ~25min plus install.
B) Model OCR, labeled unverified (recommended)
Keep the vision pass, mark those chips Read from scan, unverified with a distinct style and page-level highlight, and exclude them from the verification-rate metric. No install. Human ~2h / CC ~15min. Risk: no independent check on scans. Later upgrade to A is possible behind the same parse seam.
C) Scanned pages unsupported for now
Digital PDFs only; a scanned document fails with a clear message and OCR moves to slice 2. Removes the scanned test variant. Human ~30min / CC ~5min. Risk: loss runs and many real quotes are scans.

State: approved
Actual answer: B) Model OCR, labeled unverified (answer to D5)
Accepted scope: keep the vision OCR pass for sparse pages; citations on scanned pages render as Read from scan, unverified with a distinct style and page-level highlight; excluded from the verification-rate metric; parse returns word boxes as optional so a local OCR can be added later behind the same seam.
History: none

### R5: citation page correction
Finding: T1, P2, 8/10, spec section 4 step 3, reviewer: Claude
Plan baseline: original proposal: quote must appear on the cited page, otherwise Not specified.
Runtime evidence: PyMuPDF search_for works per page (probe); wrong-page behavior of the chosen model unknown (no key set).
Comparison grid: R5 current strict cited page; A search whole document and re-point; B strict cited page.
Question D6:
D6 — If the model cites the wrong page for a real quote, what should we do?
Project/branch/task: broker-docs-verified slice 1, spec section 4 step 3 (verify).
ELI10: The model returns a quote and a page number, and page numbers are the easiest thing for it to get off by one. The spec only looks on the cited page, so a correct quote on page 4 cited as page 3 is thrown away and the cell shows Not specified even though the value is real.
Stakes if we pick wrong: real values silently disappear from reports and look like the quote simply does not say it.
Recommendation: A because the quote is still genuinely found in the document, so the verify-before-cite rule is intact and recall goes up.
Note: options differ in kind, not coverage, so no completeness score. Both options keep nothing cited unless the quote is found in the source. Pending remedies not decided here: R6.
Header: Wrong page cited
Options:
A) Search the document and re-point (recommended)
If the quote is not on the cited page, search the rest of the same document; if found, re-point the citation to the nearest page where it is found and mark it verified; if found nowhere, Not specified as before. Corrections are logged. Human ~1h / CC ~10min. Risk: a short quote that repeats on many pages points at the wrong occurrence, so the nearest page to the cited one wins.
B) Strict cited page only
Keep the spec: a quote not on the cited page is dropped. Human 0 / CC 0. Risk: off-by-one pages silently remove real values.

State: approved
Actual answer: A) Search the document and re-point (answer to D6)
Accepted scope: if a quote is not on the cited page, search the rest of the same document; if found, re-point to the nearest page where it is found and mark verified; if found nowhere, Not specified; corrections are logged.
History: none

### R6: accuracy bar for the test suite
Finding: T2, P2, 9/10, spec sections 1 and 7, reviewer: Claude
Plan baseline: original proposal: accuracy and verification rates are measured and reported, no pass mark.
Runtime evidence: OPENAI_API_KEY unset (probe), so no live eval has run; reportlab 4.5.1 and pytest 8.3.3 present.
Comparison grid: R5 pending; R6 current report only; A fixed gates; B report only.
Question D7:
D7 — What accuracy bar should the test suite enforce?
Project/branch/task: broker-docs-verified slice 1, spec section 7 (testing).
ELI10: The spec says we measure field accuracy and citation verification but sets no pass mark. Without one, a prompt change that makes extraction worse still goes green. The synthetic PDFs are generated with known answers, so the bar can be exact. Real model runs need your OpenAI key, which is not set here, so they run only on request.
Stakes if we pick wrong: quality regressions from prompt or model changes go unnoticed.
Recommendation: A because the bars are checkable against known answers and the hard rule (no verified citation with a wrong value) must never regress.
Note: options differ in kind, not coverage, so no completeness score. Pending remedies not decided here: R5.
Header: Accuracy bar
Options:
A) Gate with fixed bars (recommended)
On the synthetic set (4 carriers x clean, multi-column, scanned, plus one injection PDF): a verified citation whose value does not match the source must be 0 (hard fail); field accuracy at least 90 percent on clean and 80 percent on multi-column PDFs; scanned results reported, not gated. Mocked-provider tests always run; the live eval runs only with pytest -m live and a key, within the D3 token budget. Bars are initial and tuned after the first live run. Human ~3h / CC ~25min.
B) Report only
Print the numbers with no pass or fail. Human ~1h / CC ~10min. Risk: no regression gate.

State: approved
Actual answer: A) Gate with fixed bars (answer to D7)
Accepted scope: synthetic set of 4 carriers x clean, multi-column and scanned PDFs plus one injection PDF; hard fail if any verified citation has a value that does not match the source; field accuracy at least 90 percent on clean and 80 percent on multi-column; scanned reported, not gated; mocked-provider tests always run; live eval only with pytest -m live and an API key, within the D3 token budget; bars tuned after the first live run.
History: none

### R7: what a document can represent in a comparison
Finding: Outside voice #2, High, spec sections 1 and 4, reviewer: Codex (gpt-6-astra)
Plan baseline: original proposal: one document is one carrier column.
Runtime evidence: unknown (no real multi-option documents inspected; the demo frames show 4 documents, 4 carriers).
Comparison grid: R7 current implicit one quote per document; A one primary option per document, others flagged; B one column per detected option; C unchanged.
Question D8:
D8 — What should happen when one PDF contains more than one quote option?
Project/branch/task: broker-docs-verified slice 1, spec sections 1 and 4 (compare quotes).
ELI10: The comparison puts one carrier in each column and assumes one PDF holds one quote. Real quotes often hold several options (different limits or retentions) in one file. If the app silently picks one, the report looks fully cited but compares unlike things.
Stakes if we pick wrong: a believable, cited comparison that mixes up options and misleads a client decision.
Recommendation: A because it keeps the observed one-column-per-document layout and makes the limitation visible instead of silent.
Note: options differ in kind, not coverage, so no completeness score. Pending remedies not decided here: R8.
Header: Multi-option PDFs
Options:
A) One primary option, flag the rest (recommended)
Extraction compares the first option presented, records how many options were detected, and the report shows a visible warning on that column naming the option compared and the count. Human ~2h / CC ~15min. Risk: the user must upload separate files to compare other options.
B) One column per detected option
Extraction returns every option and the table gets a column per option, labeled with its document and option name. Human ~2 days / CC ~1.5h. Risk: larger schema, harder compose and validation.
C) Leave as specified
No detection or warning. Human 0 / CC 0. Risk: silent mix-ups.

State: approved
Actual answer: A) One primary option, flag the rest (answer to D8)
Accepted scope: extraction compares the first option presented, records how many options were detected, and the report shows a visible warning on that column naming the option compared and the count; an adversarial fixture with two options in one PDF is added.
History: none

### R8: consistency after a user edits the report
Finding: Outside voice #7, High, spec section 4 steps 5 and 6, reviewer: Codex (gpt-6-astra)
Plan baseline: original proposal: an edit snapshots a version and shows an edited badge; chat sees documents and passages.
Runtime evidence: unknown (no code yet).
Comparison grid: R7 pending; R8 current badge only; A staleness flags and current-artifact chat; B badge only, chat gets edited artifact; C auto-regenerate prose on edit.
Question D9:
D9 — What should happen to the report prose and chat after a user edits a cell?
Project/branch/task: broker-docs-verified slice 1, spec section 4 steps 5 and 6 (review and chat).
ELI10: If you change a premium in the table, the summary paragraph that mentions that premium still says the old number, and chat may answer from the original document instead of your edit. The spec only adds an edited badge and a version snapshot.
Stakes if we pick wrong: a report that contradicts itself, which defeats the point of reviewing and correcting it.
Recommendation: A because it keeps your hand edits and tells you exactly which sentences may now be wrong, without spending a model call on every edit.
Note: options differ in kind, not coverage, so no completeness score. Pending remedies not decided here: R7.
Header: Edits and prose
Options:
A) Staleness flags and edit-aware chat (recommended)
Sections whose prose cites an edited cell are marked may be out of date with a Regenerate prose button; chat receives the latest edited artifact (edits override extracted values, original shown for reference); chat-proposed edits apply to the latest version. Human ~4h / CC ~30min.
B) Badge only, chat sees edits
No staleness tracking; chat is given the edited artifact. Human ~1.5h / CC ~10min. Risk: stale conclusions stay unmarked.
C) Auto-regenerate prose after every edit
Always consistent, but costs a model call per edit and can overwrite prose the user hand-edited. Human ~3h / CC ~25min.

State: approved
Actual answer: A) Staleness flags and edit-aware chat (answer to D9)
Accepted scope: report sections whose prose cites an edited cell are marked may be out of date with a Regenerate prose button; chat receives the latest edited artifact (edits override extracted values, original shown for reference); chat-proposed edits apply to the latest version.
History: none

### R9: TODO, chunked extraction for long documents
Finding: D3 follow-up, reviewer: Claude
Plan baseline: original proposal: no TODO exists (no TODOS.md in the project).
Runtime evidence: none.
Comparison grid: R9 current none; A add to TODOS.md; B skip; C build in slice 1.
Question D10:
D10 — Record chunked extraction for long documents as a TODO?
What: split documents over the 80-page cap into page windows, extract per window, merge and de-duplicate facts. Why: policies (slice 2) are long and D3 rejects them. Pros: removes the page cap. Cons: merge conflicts between windows, more pipeline code. Context: D3 chose hard caps for slice 1. Depends on: slice 1 pipeline.
Options: A) Add to TODOS.md (recommended); B) Skip; C) Build it now in slice 1.
State: approved
Actual answer: A) Add to TODOS.md (answer to D10)
Accepted scope: write the chunked-extraction item to a new TODOS.md in the project.
History: none

### R10: TODO, claim-level validation of report prose
Finding: Outside voice #1 follow-up, reviewer: Codex
Plan baseline: original proposal: no TODO exists.
Runtime evidence: none.
Comparison grid: R10 current none; A add to TODOS.md; B skip; C build in slice 1.
Question D11:
D11 — Record claim-level validation of report prose as a TODO?
What: check each prose claim (for example "Carrier A is cheaper than B") against the facts it cites, not only that cited numbers exist. Why: the number check cannot catch reversed meanings or numbers reassigned between carriers. Pros: closes the biggest remaining gap in prose trust. Cons: needs a second model or rules and an eval set. Context: M2 and D2 cover quotes and numbers only. Depends on: slice 1 compose step.
Options: A) Add to TODOS.md (recommended); B) Skip; C) Build it now in slice 1.
State: approved
Actual answer: A) Add to TODOS.md (answer to D11)
Accepted scope: write the claim-level validation item to TODOS.md.
History: none

### R11: TODO, local OCR for independent scan verification
Finding: D5 follow-up, reviewer: Claude
Plan baseline: original proposal: no TODO exists.
Runtime evidence: tesseract not installed (probe).
Comparison grid: R11 current none; A add to TODOS.md; B skip; C build in slice 1.
Question D12:
D12 — Record a local OCR upgrade for scanned pages as a TODO?
What: add Tesseract-based OCR with word boxes so scanned citations are verified independently of the model. Why: D5 left scanned chips labeled unverified. Pros: scanned citations reach the same trust level as digital ones. Cons: needs a Tesseract install and has lower accuracy on messy scans. Context: parse already returns optional word boxes so this slots in. Depends on: D5 seam.
Options: A) Add to TODOS.md (recommended); B) Skip; C) Build it now in slice 1.
State: approved
Actual answer: A) Add to TODOS.md (answer to D12)
Accepted scope: write the local OCR upgrade item to TODOS.md.
History: none

### Carried mechanics (no separate choice; they realize or tighten approved choices)
M1 (realizes D5, D2): each fact has an evidence state verified / scan_read / not_found / failed. Compose, chat, export and the metrics handle each explicitly. Absent stays "Not specified"; rejected shows "Couldn't verify"; failed shows "Extraction failed" with retry. Source: Codex #3 and #6.
M2 (tightens verify): the extracted value must appear inside its own quote (normalized); the D7 hard-fail checks it. Claim-level validation of prose conclusions is a TODO. Source: Codex #1.
M3 (extends parse): also OCR a page when images cover 40 percent or more of its area and it has fewer than 600 characters; mixed-page fixture added. Source: Codex #4.
M4 (refines D3): the budget counts every model call (extract, vision, compose, chat) with reserved output; a per-call context check runs before sending; on exhaustion the remaining documents fail with "budget exhausted" and the run completes partial. Source: Codex #5.
M5 (refines D7): field accuracy = correct among fields present in ground truth; fields absent in ground truth must return null; fixtures added for two options in one PDF and a conflicting endorsement. Source: Codex #8.
M6 (A4, P2): startup marks orphaned running runs failed and retryable; at most 4 concurrent model calls; parse and verify run in a thread pool.
M7 (Q1): a persisted fact table; passage keyed by (document, page); chip numbers derived from passage.
M8 (SC3): defaults OCR trigger 25 non-whitespace characters, token-set match 0.90 via difflib, 3 retries at 1/2/4 seconds.

Approval readiness: PASS. Checked R1 to R11 (D2 to D12), each with its own actual answer recorded above; M1 to M8 are mechanics that realize or refine those answers and add no independent choice.

## Review body

### Reality rows
REALITY: premise-table pass 9 claims checked, unverified=2 (real-quote multi-option frequency; chosen model's wrong-page rate, no key set) docs/specs/2026-11-01-brokerdocs-core-design.md:79
REALITY: already-done pass no implementation exists to duplicate: the project folder holds only docs, HANDOFF.md and TODOS.md, and the handoff states no code written HANDOFF.md:4
REALITY: surface-check pass PyMuPDF 1.28.0 search_for and get_text("words") ran; FastAPI 0.115.0, openai 3.13.0, SQLAlchemy 2.0.35, pytest 8.3.3, reportlab 4.5.1 installed; strict structured-output rules read from the OpenAI docs; tesseract absent docs/specs/2026-11-01-brokerdocs-core-design.md:112
REALITY: binding-decisions pass gstack-decision-search returned none; the spec has no Binding decisions section docs/specs/2026-11-01-brokerdocs-core-design.md:1
REALITY: numbers pass caps 80 pages and 15 MB and 8 docs and 600k tokens are [chosen] by D3 docs/specs/2026-11-01-brokerdocs-core-design.md:232 ; 320k tokens for a full run is [estimated] from 8 docs x 80 pages x 500 tokens docs/specs/2026-11-01-brokerdocs-core-design.md:445 ; 2 MB word boxes per document is [estimated] docs/specs/2026-11-01-brokerdocs-core-design.md:445 ; accuracy bars 90 and 80 percent are [initial, re-measure after first live run] by D7 docs/specs/2026-11-01-brokerdocs-core-design.md:330
REALITY: deferred-asks finding the owner asked for a close 1:1 clone; slice 1 delivers one workflow of 20+, with slices 2 to 6 deferred and stated in section 1 docs/specs/2026-11-01-brokerdocs-core-design.md:30
REALITY: pr-count pass no git repo and no .gstack/ship-policy.json, so the default of one PR per wave applies; zero extra PRs proposed, no queue cost docs/specs/2026-11-01-brokerdocs-core-design.md:1

### NOT in scope
- Chunked extraction for long documents: slice 2 (TODOS.md), D3 chose hard caps.
- Claim-level validation of prose: later slice (TODOS.md), needs its own design and eval set.
- Local OCR with independent word boxes: later slice (TODOS.md); D5 keeps model OCR labeled unverified.
- One column per quote option: D8 chose flag-and-compare-primary.
- Auth, SSO, AMS, vault UI, proposals, ACORD fill, assistant, market search, other workflows: slices 2 to 6, unchanged from section 1.
- Auto-regeneration of prose on edit: D9 chose staleness flags.

### What already exists
Nothing in this project (greenfield). Reused rather than rebuilt: PyMuPDF `get_text("words")` and `search_for` for text, boxes and quote location (verified by probe); `difflib` from the standard library for fuzzy matching (no new dependency); the installed FastAPI, SQLAlchemy, openai, pytest and reportlab. The OpenAI SDK's strict structured outputs and refusal signal replace hand-written JSON repair. No shared-code extraction proposed: no caller pairs exist yet.

### Diagram (updated pipeline)
```
upload ─► caps check (D3) ─► PARSE ───────► EXTRACT ×N (sem 4) ─► VERIFY ───────────────► COMPOSE
            reject w/ msg     PyMuPDF         strict schema         search_for page,        facts only
                              OCR if <25ch    {field,value,page,    else whole doc (D6),    validate
                              or image-heavy   quote}; refusal →    value ⊂ quote (M2),       │ fail ×2
                              (M3, D5)         doc failed           state per fact (M1)       ▼
                                  │                  │                  │                 template fallback
                                  ▼                  ▼                  ▼                 + banner (D2)
                               page rows          budget (M4)        fact + passage             │
                                                                     (doc,page) chips ◄─────────┘
                                                                              │
                        EDIT ► snapshot, edited badge, stale-section flags (D9) ► CHAT (edit-aware) ► EXPORT
```
Files needing inline diagrams once built: `pipeline/runner.py` (state machine), `pipeline/verify.py` (decision order).

### Failure modes
| Path | Realistic failure | Test | Handling | User sees |
|---|---|---|---|---|
| parse | corrupt or password-protected PDF | planned (T3) | that document fails | its column shows the failure, run completes |
| extract | provider 429 or 5xx | planned (T5) | 3 retries, 1/2/4 s | retry, then column fails |
| extract | model refusal | planned (T5) | document failed | "Extraction failed" with retry |
| verify | wrong page cited | planned (T4) | whole-doc re-point | value kept, chip verified |
| verify | value not in its quote | planned (T4) | rejected | "Couldn't verify" |
| compose | invalid output twice | planned (T5) | template fallback | banner and Retry prose |
| runner | restart mid-run | planned (T6) | startup reconcile | run failed, retry offered |
| budget | cap exhausted mid-run | planned (T6) | remaining docs fail | "budget exhausted", partial report |
Critical gaps (no test, no handling, silent): 0.

### Worktree parallelization strategy
| Step | Modules touched | Depends on |
|---|---|---|
| T1 data model | backend/db, backend/models | none |
| T2 providers and budget | backend/providers | none |
| T3 to T6 pipeline | backend/pipeline | T1, T2 |
| T7 security baseline | backend (main, routes) | T1 |
| T8 routes, chat, export | backend/routes, backend/chat, backend/export | T1, T3 to T6 |
| T9 synthetic PDFs and eval | tests/eval | none (needs T3 to T6 to run) |
| T10 frontend | frontend | T8 contract, design step |

Lane A: T1 → T3 → T4 → T5 → T6 (shared backend/db and backend/pipeline, sequential).
Lane B: T2 (independent, then feeds T5).
Lane C: T9 fixtures and generator (independent).
Lane D: UI design step through the design stack (independent), then T10.
Execution order: launch A + B + C + D. Merge B before T5. After A, run T7 and T8. Then T10.
Conflict flags: T5 and T6 both touch backend/pipeline; T7 and T8 both touch backend routes, so sequence them.

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~1 day / CC: ~1h)** — backend/db — data model with fact, passage keyed (document, page), evidence states, artifact versions
  - Surfaced by: Section 2 Q1 and Codex #3/#6 — spec section 3 has no fact table; chips are page-level
  - Files: backend/db.py, backend/models.py, backend/schemas.py
  - Verify: model tests for state transitions and version snapshots
- [ ] **T2 (P1, human: ~1 day / CC: ~1h)** — backend/providers — Provider interface, strict schema builder, budget accounting (M4)
  - Surfaced by: Section 1 A2, Section 2 Q3, Codex #5
  - Files: backend/providers.py (interface and OpenAI)
  - Verify: mocked-provider tests for refusal, schema retry, budget exhaustion
- [ ] **T3 (P1, human: ~1 day / CC: ~45min)** — backend/pipeline — parse with OCR triggers (25 chars or image-heavy) and caps
  - Surfaced by: SC3, Codex #4, D3, D5
  - Files: backend/pipeline/parse.py
  - Verify: fixtures for digital, sparse, mixed, corrupt, password, over-cap
- [ ] **T4 (P1, human: ~1 day / CC: ~1h)** — backend/pipeline — verify: search_for, whole-doc re-point, value in quote, evidence states
  - Surfaced by: SC2, T1, Codex #1, D6, M1, M2
  - Files: backend/pipeline/verify.py
  - Verify: exact, whitespace, hyphen, fuzzy 0.90, wrong page, value not in quote
- [ ] **T5 (P1, human: ~1.5 days / CC: ~1.5h)** — backend/pipeline — extract, compose, validator, template fallback
  - Surfaced by: A1, D2, D8 (option detection)
  - Files: backend/pipeline/extract.py, backend/pipeline/compose.py
  - Verify: bad passage_id, invented number, two failures to fallback, multi-option warning
- [ ] **T6 (P1, human: ~1 day / CC: ~1h)** — backend/pipeline — runner, SSE, reconcile, semaphore 4, thread pool
  - Surfaced by: A4, P2, M4, M6
  - Files: backend/pipeline/runner.py
  - Verify: restart mid-run, budget exhaustion partial, double-click Run
- [ ] **T7 (P1, human: ~3h / CC: ~20min)** — backend — security baseline
  - Surfaced by: A3, D4
  - Files: backend/main.py, backend/routes/*
  - Verify: bind address, CORS, magic bytes, UUID names, injection fixture
- [ ] **T8 (P2, human: ~1.5 days / CC: ~1.5h)** — backend/routes, chat, export — edit, staleness flags (D9), edit-aware chat, XLSX and PDF
  - Surfaced by: Codex #7, D9
  - Files: backend/routes/*.py, backend/chat.py, backend/export.py
  - Verify: edit marks dependent sections stale; chat sees edited values
- [ ] **T9 (P2, human: ~1 day / CC: ~1h)** — tests/eval — synthetic PDF generator and eval gates
  - Surfaced by: T2, Codex #8, D7, M5
  - Files: tests/eval/*, tests/fixtures/*
  - Verify: gate numbers computed; live eval under pytest -m live only
- [ ] **T10 (P2, human: ~3 days / CC: ~3h)** — frontend — shell, report view, chips, PDF viewer; blocked on the design step
  - Surfaced by: spec section 5, section 2 observed behavior
  - Files: frontend/src/*
  - Verify: Playwright flow in the test plan
- [ ] **T11 (P3, human: ~5min / CC: ~1min)** — TODOS.md — three items recorded (D10 to D12), done
  - Surfaced by: final planning decisions
  - Files: TODOS.md
  - Verify: file exists

Effort basis: tests ~50x and features ~30x human over CC time, architecture ~5x; these are estimates.

### Unresolved decisions
None in this review.

### Suppressed findings (confidence below 5)
None.

### Completion summary
- Step 0: Scope Challenge: scope accepted as-is (structure: original arrangement, D1)
- Reality rows: 7/7 applicable rows emitted (PLAN_REALITY line follows in the saved file check), 2 unverified premises
- Architecture Review: 4 issues found (A1 to A4)
- Code Quality Review: 3 issues found (Q1 to Q3)
- Test Review: diagram produced, 31 gaps identified (T1 to T3 findings)
- Performance Review: 4 issues found (P1 to P4, 1 actionable)
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 3 items proposed to user, 3 accepted
- Failure modes: 0 critical gaps flagged
- Unresolved decisions: 0 in this review
- Outside voice: Codex (gpt-6-astra) completed; 8 findings: 6 accepted as mechanics, 2 became decisions (D8, D9)
- Parallelization: 4 lanes, 3 parallel / 1 sequential
- Lake Score: N/A (all questions differed in kind, so none carried a completeness score)

## Design review (plan-design-review, 2026-11-01)

Target: this spec plus `docs/reference/brokerdocs-ui-reference.md`. Mode: OPERATE (users finish a task). Text-only; image mockups were cancelled by the owner. No DESIGN.md existed at review start. Earlier design decisions: D1 review mode, D2 real tokens with own logo (reused, not re-asked). Decisions below are DG1 to DG12.

### Design decisions
| # | Decision | Chosen |
|---|---|---|
| DG1 | Launch screen and rail | **Full rail copied from the reference, future items disabled** (tooltip "Coming in a later slice", not links, not hidden). Launch lands on the Compare Quotes page; Recent runs list where the artifact goes. |
| DG2 | Trust summary location | **Counts live in the outline sidebar header** (verified, scan-read, couldn't verify), each a filter that scrolls to the first match. No top strip. |
| DG3 | Evidence-state look | **Silhouette per state, never color alone.** Verified = observed chip. Scan-read = dashed border plus scan glyph, tooltip "Read from scan, unverified". Couldn't verify = no chip, text "Couldn't verify" with triangle glyph in `#4B5563`. Failed = "Extraction failed" with refresh glyph and Retry (also on the column header). Edited = pencil glyph, chips fold into "original source". |
| DG4 | Run progress | **Per-document rows in the left Upload accordion** (Queued, Reading pages 4/10, Extracting, Verifying, Done, Failed with reason); centered "Writing report..." only after all documents settle; Cancel on the run header. |
| DG5 | Warning placement | **Scoped to the affected thing.** Run-level (compose fallback with Retry prose, budget exhausted) = full-width row at top of artifact, max 2. Column-level (failed document, multi-option "Compared option 1 of 3") = row in the column header. Section-level = "May be out of date" tag with Regenerate prose on the section heading. Glyph plus text, one line; only informational rows dismissible. |
| DG6 | Empty states | **Upload-first.** Empty Recent runs = drop zone "Drop quote PDFs to compare" stating limits (PDF, 80 pages / 15 MB each, 8 files), "Choose files" primary button, link to sample quotes if present; rejections inline naming the limit. Chat disabled with "Run a comparison to ask questions"; suggestions and outline hidden until a report exists. |
| DG7 | Name and logo | **Text wordmark "BrokerDocs" from one config constant**, plain CSS glyph in `#6356F0`, no orange mark. Overrides D2's "own name" for the name only; rename if the app ever leaves the local machine. |
| DG8 | Design system | **Write a compact `DESIGN.md`** (task T12) from the reference: tokens, 4px spacing, Inter 12/14/16/20/36 with tabular numerals in tables, 5 document hue pairs with text colors at 4.5:1 or better, surfaces (white, canvas `#F3F4F6`, hairline `#E5E7EB`), evidence tokens from DG3, contrast rules (body text never lighter than `#4B5563`; `#9CA3AF`, `#F97066`, `#3CCB7F` never as text), all as CSS variables. |
| DG9 | Viewports | **Desktop-first with a floor.** 1280 to 1920 full. 1024 to 1279: left panel collapses to a 56px strip, PDF side-by-side becomes a tab over the report. Under 1024: "Best viewed on a wider screen" notice, report stays readable (sticky first column, horizontal scroll). No phone design. |
| DG10 | Accessibility | **Specific baseline.** Chips are buttons (Tab, Enter opens PDF at the page, Esc exits side-by-side), tooltip text also in aria-label and on focus; real table semantics with per-carrier column headers; cell edit by Enter, save Enter, cancel Esc, "Saved" in a polite live region; progress role=status, failures role=alert; 2px `#6356F0` focus ring with 2px offset; 44px hit area on chips; contrast 4.5:1 text and 3:1 UI; prefers-reduced-motion; labeled landmarks. axe runs in the Playwright flow. |
| DG11 | Chips with 8 documents | **5 hues cycling plus a document letter A to H** ("B2"); the letter also prefixes the column header. |
| DG12 | Cell editing | **Explicit edit with undo.** Double-click or Enter opens a 48px inline input with the old value selected; Enter saves, Esc cancels; "Saving..." in the cell; failure keeps the input with "Couldn't save. Retry"; success shows a 6-second "Edited. Undo" toast that restores the prior version. |

### Interaction state table
| Feature | Loading | Empty | Error | Success | Partial |
|---|---|---|---|---|---|
| Upload | per-file progress bar in its row | drop zone with limits (DG6) | inline rejection naming the cap or "Not a PDF" | row with page count | some files rejected, rest kept |
| Run | per-document status rows (DG4), centered "Writing report..." | Recent runs drop zone | run failed row with reason and Retry | report opens | "Budget exhausted" top row, remaining columns failed (DG5) |
| Comparison table | spinner in artifact tab | hidden until report | failed column header with reason and Retry (DG5) | chips (DG3) | scan-read dashed chips, "Couldn't verify" text |
| Report prose | skeleton lines in section | n/a | compose fallback row plus Retry prose (DG5) | prose with chips | stale tag plus Regenerate prose (DG5) |
| Source PDF viewer | page skeleton | n/a | "Couldn't open this file" with Retry | page with dashed highlight | scan-read: page-level highlight only |
| Cell edit | "Saving..." (DG12) | n/a | inline "Couldn't save. Retry" | "Edited. Undo" toast | n/a |
| Chat | typing indicator | disabled, reason shown (DG6) | "Couldn't answer" with Retry | answer with chips | answer cites scan-read passages with dashed chips |
| Export | button spinner | disabled until a report exists | toast "Export failed. Retry" | file downloads | XLSX holds tables only; noted in the toast |

### User journey
| Step | User does | Feels | Plan specifies |
|---|---|---|---|
| 1 | Opens app | Oriented | Full rail, drop zone with limits (DG1, DG6) |
| 2 | Drops quote PDFs | In control | Inline cap messages, per-file rows |
| 3 | Clicks Run, waits 1 to 3 min | Anxious | Per-document status, Cancel (DG4) |
| 4 | Report opens | Relieved, then checking | Outline counts (DG2), banners at the point of trouble (DG5) |
| 5 | Clicks a chip | Convinced | PDF beside the report at the page, dashed highlight; Esc exits (DG10) |
| 6 | Hits a dashed or "Couldn't verify" value | Cautious, not alarmed | Distinct silhouette, tooltip says why (DG3) |
| 7 | Edits a cell | Careful | Explicit edit, Undo toast (DG12); stale tag on affected prose (DG5) |
| 8 | Asks a follow-up | Curious | Chips in answers; chat sees edits (D9) |
| 9 | Exports | Done | XLSX and PDF |
Time horizons: 5 seconds = a clean shell and one obvious action; 5 minutes = every number clickable to its page; 5 years = trusting a tool that told you what it could not verify.

### Pass scores
| Pass | Before | After | Remaining gap |
|---|---|---|---|
| 1 Information Architecture | 5 | 8 | collapsed-outline behavior (see Unresolved) |
| 2 Interaction States | 3 | 8 | edge copy strings for each state, written during T10 |
| 3 User Journey | 5 | 8 | none beyond the above |
| 4 AI Slop | 7 | 8 | Inter and `#6356F0` are the default look; accepted by D2 |
| 5 Design System | 5 | 8 | DESIGN.md decided (DG8) but not yet written (T12); becomes 9 on delivery |
| 6 Responsive and A11y | 2 | 8 | real contrast values confirmed when DESIGN.md is written |
Overall: 2/10 before, 8/10 after approved fixes. Step 0 initial impression was 4/10.

### Hard rejection and litmus (Pass 4, OPERATE mode)
Hard rejections: none. No hero, no card grid, no carousel; the layout is rail, panel, report. Litmus: brand clear on first screen YES (wordmark, accent); one visual anchor YES (the report table); scannable by headings YES (outline); one job per section YES; cards necessary NO (none planned, good); motion improves hierarchy YES if limited to progress and toast; premium without shadows YES (hairlines only).

### NOT in scope (design)
- Phone and tablet-portrait layouts (DG9).
- Mockup generation; the real UI is the reference.
- Dark mode (not observed in the original).
- Navigation items beyond the disabled rail placeholders (DG1).
- Localization and right-to-left layouts.

### What already exists (design)
`docs/reference/brokerdocs-ui-reference.md` (tokens, shell, chips, states), the live sign-in screenshot `docs/reference/app-signin-1440.png`, and the demo frames in `../rea-out/`. No components or DESIGN.md exist yet (greenfield).

### Unresolved design decisions
- Collapsed-outline behavior (from DG2): the trust counts live in the collapsible outline, so a collapsed outline can hide them. Proposed: a one-glyph warning mark on the collapse handle whenever unverified or failed values exist. Not decided.

### Design tasks
- [ ] **T12 (P1, human: ~3h / CC: ~30min)**: DESIGN.md. Surfaced by: Pass 5, DG8. Files: `DESIGN.md`, `frontend/src/styles/tokens.css`. Verify: every token used in components exists in the file; contrast table checked.
- [ ] **T13 (P2, human: ~2h / CC: ~15min)**: run Cancel in the backend runner. Surfaced by: DG4 (Cancel on the run header; the spec had no cancel). Files: `backend/pipeline/runner.py`, run route. Verify: cancelling mid-run leaves a partial or cancelled run with no orphaned tasks.
- T10 gains: build the states in the state table; sample-quotes link; wordmark constant; disabled rail; viewport behaviors; axe check in the Playwright flow.
- After build: run `/design-review` against the live app.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | Codex (gpt-6-astra), automatic | Independent 2nd opinion | 1 | completed | 8 findings: 6 accepted as mechanics, 2 decisions |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES OPEN (not persisted until logged) | 14 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | issues_open | score: 2/10 → 8/10, 12 decisions |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**OUTSIDE COVERAGE:** Eng pass: Codex plan-review, completed, 8 findings. Design pass: outside voices skipped by choice (no mockups, Codex already covered the spec); native only.
**VERDICT:** Eng Review issues open: all 14 issues mapped to accepted decisions (D1 to D12) and mechanics (M1 to M8). Design Review: 12 decisions applied (DG1 to DG12), one open item. Ready for backend lanes; frontend (T10) can start once DESIGN.md (T12) is written.

**UNRESOLVED DECISIONS:**
- Collapsed-outline behavior: whether a collapsed outline shows a warning mark when unverified or failed values exist.
