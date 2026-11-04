# Slice 5a: Loss runs (checked claims table)

Date: 2026-11-03. Status: owner-approved spec (2026-11-03 23:24).
Next: `/plan-eng-review`, then lanes. Rail item: Analytics (goes live this slice). Insights stays for 5b.
Evidence: `docs/reference/brokerdocs-workflows-evidence.md` section 5 (Loss Run); `docs/slice-map.md`; PRODUCT.md.

## 1. Purpose and decisions

At renewal a broker gathers the client's loss runs (each carrier's list of claims, valued as of a date) to tell
the story of the account to underwriters. The files overlap, use different layouts and spellings, and print their
own totals. The real product shows a claims table that is not deduplicated or normalized (`yt/frame_0022`: the
same claim twice, statuses `Opened` / `O` / `Open`). This slice proves the engine's promise on numbers: every claim
row cited, every total computed in code and reconciled against the totals the carrier printed.

Owner decisions (2026-11-03, do not re-ask):
- L1 Scope: slice 5 is cut in two. **5a (this spec)**: ingest, cited claims table, dedup and status normalization,
  reconciliation against printed totals. **5b (own spec later)**: Insights dashboard and a renewal loss summary
  composed only from computed numbers.
- L2 Placement: the **Analytics** rail item goes live as the loss-run home. Insights waits for 5b.
- L3 Unit: one loss review = one client and that client's loss-run files (several carriers and years). No
  agency-wide claims view in 5a.
- L4 Formats: **PDF and Excel** (`.xlsx`, `.csv`). DOCX deferred (the evidence shows only `.xlsx` and `.pdf`).
- L5 Duplicates across files: match on carrier + normalized claim number; **the latest valuation date wins** and
  counts in totals; older valuations fold under it as history with their citations. Same valuation date with
  different amounts = **Conflicting**, kept out of totals until the broker picks one.
- L6 Reconciliation: each printed total becomes a check row (Matches / Off by $X, citing the printed line). A
  mismatch puts the review in Needs review; the broker fixes or adds rows, or accepts the gap with a note.
- L7 Extraction approach (hybrid): spreadsheets parsed in code (header synonym table; a model call only for
  headers the table does not know); PDFs read by the model per page window; every row verified either way.
- L8 Limits: spreadsheets 100,000 rows per file and 200,000 per review, sheet pages rendered on demand; PDFs keep
  the 300-page cap with a loss-run token budget sized from the eval fixtures (never a silent truncation).
- L9 Data: synthetic loss runs only, labelled "Synthetic sample data" (slice map, PRODUCT.md "mocks say they are
  mocks"). Verify-before-cite still applies.

Out of scope (5a): Dashboard tab, AI summary, Insights (5b); export to Excel/PDF (5b, with the summary); DOCX;
agency-wide claims view; editing claimant or status text; loss ratios (need premium, 5b); client entity pages
(PRODUCT.md); real carrier files.

## 2. Flow and states

1. **Start** from Analytics (`#/analytics`, "New loss review") or a client's queue row menu ("Review losses",
   prefills client). Wizard step 1 **Who**: client (typed, past-client picks).
2. Step 2 **Files**: drop PDFs and spreadsheets, or pick the client's uploaded files (deduped by sha256). Each file
   shows its kind; spreadsheets list their sheets and the mapped columns ("Clm # → Claim number"), with unmapped
   columns listed (never guessed).
3. Step 3 **Read**: per-file progress with the reading mark; cached documents skip the read.
4. **Review page** (run layout: left panel files, main report, source viewer): totals strip, check list, claims table.
5. **Queue**: the review appears as a client row. Needs review while any check, conflict or unverified row is open;
   Ready when none is open; Done when the broker marks it done (same rules as other workflows,
   `docs/specs/2026-11-02-work-queue-design.md`). Activity lists the run.

Run states are the existing ones (queued, running, done, partial, failed, cancelled). A file that cannot be read
shows in the left panel with its reason and Retry; the rest of the review still builds (partial).

## 3. Data model

- **Workflow row** `loss_run` (workflow-as-data, slice 1). Slug comes from the shared workflow slug source (slice 4 A1).
- **Spreadsheets as pages.** Following the AMS precedent (slice 3 D4), a spreadsheet upload keeps the original as a
  `<uuid>.orig` sidecar. Each sheet is parsed in code. Rendered PDF pages for a sheet are produced **on demand**
  (when a citation opens one), not at upload, so page count never grows with file size. A sheet page records
  its sheet name and source row range; a spreadsheet citation reads "Sheet 2024, row 17" and opens that rendered
  page highlighted in the existing viewer.
- **Table `claim_row`** (one per extracted claim):
  - identity: run_id, document_id, claim_number_raw, claim_key (normalized), carrier, policy_number, line,
    policy_year;
  - details: claimant, date_of_loss, status_raw, status (open | closed | reopened);
  - money in **integer cents**: paid, reserve, incurred, recovery, deductible (nullable where the file has no column);
  - evidence: page_no (PDF) or sheet + row (spreadsheet), quote / cell refs, rects, verify state (verified |
    unverified);
  - role: counted | superseded | conflicting | left_out;
  - source: extracted | edited | added; for edited rows the original extracted values are kept beside the edit;
  - warnings: e.g. `incurred_not_paid_plus_reserve`.
- **Document facts** in the existing `Fact` table with `lr:` keys: carrier, valuation date ("valued as of"),
  policy numbers and years, and each printed total with its scope (e.g. `lr:total_incurred[2024]`,
  `lr:total_paid[all]`). Cited and verified like other facts.
- **Broker decisions** in `discrepancy_state`, workflow-scoped items (slice 4 D1/D10 pattern): one item per
  reconciliation check, per conflicting claim, and **one item per document for its unverified rows** ("12 rows
  in Travelers 2019-2025 not verified"; each row inside is confirmed, fixed or left out, and the item resolves
  when none is left undecided), so a long PDF does not flood the check list. Fingerprinted; a re-read that changes the
  values reopens the decision ("Changed since you resolved it").
- **What counts in totals**: rows with role `counted` that are verified, or unverified rows the broker confirmed.
  Unverified rows keep role `counted` with state unverified and are excluded until confirmed; `left_out` is only
  a broker choice.
- All sums, counts and reconciliation run in code over integer cents. The model never adds.

## 4. Pipeline (reuses runner, windows, verify, extraction cache, budgets)

1. **Ingest.** PDF: parsed as today. Spreadsheet, in code (openpyxl / csv): find the header row (first row with
   3 or more known column names); map headers through a synonym table kept as data
   (`backend/loss_runs/headers.py`); headers the table does not know go to one small model call per sheet that may
   only map to our own columns (anything left unmapped is shown). Rows labelled as totals or subtotals are kept
   out of the claims and captured as printed totals. Blank and repeated header rows are skipped.
2. **Document facts.** PDF: the existing fact extraction with `lr:` fields. Spreadsheet: labelled cells read in code.
3. **Claim rows from PDF.** The model returns rows per 15-page window (2-page overlap). A row **verifies** only if
   its claim number and every amount it gives appear on the cited page, with money formats normalized (`$`,
   commas, `(1,200.00)` negatives, `-`). Otherwise the row is unverified and kept out of totals. Rows repeated in
   window overlaps are dropped by claim number + page.
4. **Normalize** (code, rules as data). Claim key: case and whitespace only (no leading-zero stripping: that could
   merge two claims). Status map: `O`, `Open`, `Opened` → open; `C`, `Closed` → closed; `Re-opened`, `Reopen`,
   `RO` → reopened; unknown spelling → kept raw, status unknown, shown. Dates → ISO. Money → cents.
   Incurred check: if incurred ≠ paid + reserve (more than 1 cent) the row gets a warning, not an exclusion
   (some carriers include expense in incurred). If incurred is missing it is computed and marked computed.
5. **Across files.** Group by carrier + claim key. Latest valuation date → counted; older → superseded (folded
   under the counted row as history). Same valuation date and different amounts → conflicting (both kept out of
   totals until resolved). Same valuation date and the same amounts → one counted, the other superseded
   ("same claim, same valuation, also in File B").
6. **Reconcile.** Each printed total is checked against **that document's own rows** (including rows another file
   later superseded, because the printed total describes that document), in the printed scope (all, policy year,
   policy number). Difference of $1.00 or less = Matches; more = check item. A document with no printed totals
   gets a notice ("Travelers 2025 prints no totals: nothing to reconcile"), not a check.
7. **Report.** No model call. Blocks built in code: totals strip, check list, claims table, notices (unverified
   rows, files not read, sheets without a claims table). Regenerated after every broker action.
8. **Limits and failures.** Spreadsheets: 100,000 rows per file, 200,000 per review, 20 sheets per file; over the
   cap the file is refused at upload with the reason. PDFs: 300 pages, loss-run token budget per document set from
   the fixture measurements; if reached: "Stopped at page 180: budget reached" with Retry, never silent. A sheet
   with no recognizable header: notice "No claims table found on sheet 2023". A failed window: Retry. Cache keys
   include the loss-run fields and prompts; bump `EXTRACTION_VERSION` (CLAUDE.md rule).

## 5. Broker actions

- **Printed total mismatch** ("2024 policy year: printed incurred $1,406,270.49, claims add up to $1,393,870.49,
  off by $12,400.00", citing the printed line): open the rows in that scope (table filtered); **fix a row**
  (edit an amount; "Edited by you", original cited value shown beside it); **add a missed claim** (same fields;
  "Added by you", no citation chip); **accept the gap** with a required note. The check recomputes after every
  fix and resolves itself when the totals match.
- **Conflicting claim**: both rows side by side with their sources; the broker picks one, the other becomes
  superseded with "Not used (you picked File B)".
- **Unverified row**: kept out of totals; the broker can **confirm** it with a reason (counts, "Confirmed by you"),
  fix it, or leave it out.
- Every action has Undo. Extracted rows cannot be deleted (the evidence stays); "Leave out of totals" is the visible
  alternative (role `left_out`, with a note).
- Editing is limited to amounts in 5a (totals depend on them).

## 6. Screens (look via the design stack; this section is content and behaviour only)

- **Analytics `#/analytics`**: list of loss reviews by client (serif client name, files, latest valuation date,
  open checks), "New loss review". Phone: status only (PRODUCT.md).
- **Wizard**: three steps as in section 2, same pattern as Policy Check and Certificates.
- **Review page**:
  - **Totals strip**: claims, open, incurred, paid, reserve, recoveries, deductible; each "from N counted claims";
    marked unreconciled while a check is open.
  - **Check list** above the table, slice 4 style: To resolve, Handled by you, Matches (folded).
  - **Claims table**: virtualized, sortable, column picker; quick filters from the evidence: Over $100K, Open only,
    Last 3 months, Open reserve. Each row has a source chip ("Travelers p.14", "Sheet 2024, row 17") opening the
    cited page highlighted. Superseded valuations fold under their claim. Row states always shown: unverified,
    conflicting, edited, added, left out, warning.
  - "Synthetic sample data" label on seeded reviews and fixtures.
- **Queue row**: "Review losses" primary action; Activity lists the run.

## 7. Errors and edge cases

- Password-protected or corrupt file: shown with its reason, the rest of the review builds.
- A spreadsheet with several claims tables on one sheet: the first table is read, notice for the rest (5a limit).
- Same claim number at two carriers: two claims (carrier is part of the key).
- Claim numbers that differ only by leading zeros: two claims.
- A file with no valuation date: rows count, but it never supersedes another file's rows and loses a tie;
  notice "No valuation date found in File C".
- Negative paid amounts (recoveries booked as negative paid): kept as printed; totals follow the file.
- Injection text in a claimant or description cell: treated as data, never as instructions (existing canary rule).
- Free text (claimant, notes) escaped and bounded (slice 4 A3).

## 8. Testing and eval

- **Fixtures, truth first**: each fixture client's claims are generated as JSON, then rendered into carrier files,
  so truth is exact (`tests/fixtures/loss_runs/`). Files (8 to 10): clean `.xlsx`; messy `.xlsx` (title rows above
  the header, merged header, subtotal rows, unfamiliar column names, three year sheets); `.csv`; two digital PDF
  layouts with different column orders; a PDF with wrapped claimant names and page-break subtotals; a long PDF
  (about 120 pages); one scanned PDF.
- **Planted traps**: the same claim in two files with different valuation dates; one same-date conflict; one
  printed total off because the carrier's total includes a claim missing from its rows; rows where incurred ≠
  paid + reserve; mixed status spellings; `(1,200.00)` negatives; claim numbers differing only by leading zeros;
  an injection canary in a claimant field.
- **Gates**: offline (fake provider and recorded replies) in the normal suite; live `-k loss_run`, paid, run only
  with owner approval each time.
  - Hard fails: a wrong amount on a counted row; an unverified row counted; a wrong reconciliation verdict; a wrong
    supersede/conflict role; two claims merged; a canary followed.
  - Thresholds: spreadsheet rows 1.000 (code path); PDF row recall ≥ 0.95; amount accuracy on verified rows ≥ 0.99.
  - Tokens per document recorded; the loss-run budget is set from them.
- **Unit tests**: header mapping and synonyms, status map, money parsing, claim key, dedup/supersede/conflict,
  reconciliation scopes and tolerance, on-demand sheet page rendering, row caps, decision fingerprints.
- **e2e + axe** at 1440 / 1024 / 390 for the wizard, review page, checks and queue row. Any e2e upload that must be
  "new" uses a unique name and bytes (handoff pattern, 2026-11-03).
- **Dev seed**: one loss review for an existing seeded client with 4 files, labelled synthetic sample data, with one
  open mismatch and one conflict; seeded with the cache off.

## 9. Build order (model policy: Opus for pipeline, checks, migrations, design)

Provisional; `/plan-eng-review` settles tasks and lanes.
- Lane A (backend): sheet ingest and on-demand rendering, `claim_row` + migration-free schema check, PDF row
  extraction + row verify, normalization, cross-file roles, reconciliation, decisions, routes, queue rules, API.md.
- Lane B (fixtures, truth, offline gate, then live gate on main with owner approval).
- Lane C (UI through the full design stack, Playwright MCP, max 3 rounds; Analytics rail item live).
- Lane D (docs: spec amendments, slice map, HANDOFF, DESIGN.md section).

## 10. Related, not in this slice

5b: Insights dashboard (by year, line, carrier; frequency and severity; large losses), renewal loss summary
composed only from computed numbers and cited large-loss rows (validator rejects any number not in the aggregate
set), export. Later: DOCX loss runs, agency-wide claims view, loss ratios with premium.

## Eng review (2026-11-03, /plan-eng-review)

Target: this spec (`docs/specs/2026-11-03-loss-runs-design.md`), fixed. Report file: this spec. Decisions L1 to L9 are
binding and not re-asked. Ledger items below override the text above them once approved.

### Scope record
feature answers: none proposed (L1 already cut slice 5 to 5a); structure: B, Smaller arrangement (D1, owner 2026-11-03);
accepted scope: all of sections 1 to 8 as written, backend as 5 new files: `backend/loss_runs/headers.py` (synonym
data), `backend/loss_runs/sheets.py` (parse + render), `backend/loss_runs/pdf_rows.py` (extract + verify),
`backend/loss_runs/ledger.py` (one pure `build_ledger(rows, documents, decisions)` giving roles, totals, checks and
notices), `backend/routes/loss_runs.py`; pending remedies: R1 to R7.

## Decision ledger

### R1: Where the claims table lives
Finding: 1, CRITICAL, confidence 9/10, spec section 4 step 7 ("Blocks built in code: totals strip, check list, claims table ... Regenerated after every broker action") with `backend/models.py:375` (`content: Mapped[dict[str, Any]] = mapped_column(JSON)` on artifact_version); reviewer: Claude (eng review).
Plan baseline: original proposal, claims table is a block in the report artifact, regenerated (new version) after every broker action.
Runtime evidence: each artifact version stores its whole content as one JSON column; slice 4 handoff: every decision makes a new version (seed run reaches v8 to v9). At the L8 cap (200,000 rows, ~400 bytes each [estimated]) one version is ~80 MB, written again on every click.
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R1 claims table storage | block inside each artifact version | claim_row table only; paged API (sort, filter, quick filters server side); artifact keeps totals, checks, notices and counts | unchanged: block in every version |
| R2 to R7 | pending | pending | pending |
Question D2:
D2 — Where should the claims table live?
Project/branch/task: BrokerDocs on main, eng review of the slice 5a loss-runs spec.
ELI10: The spec rebuilds the report after every broker click, and the report would include the whole claims table. Every rebuild saves a full copy. With a big review (up to 200,000 claims) that is about 80 MB written per click, and the browser would have to load all of it too.
Stakes if we pick wrong: Big reviews become slow to open and every fix, confirm or undo takes seconds and fills the disk.
Recommendation: A because the rows already live in claim_row; the report only needs the totals and checks, and the table can page from the database.
Completeness: A=10/10, B=6/10
Pros / cons:
A) Table from claim_row via a paged API (recommended)
  ✅ Each click saves only totals and checks (a few KB), so actions stay instant at 200k rows
  ✅ Sort, filters and quick filters run in SQL, so the browser holds only the visible page
  ❌ One more API route and a paged table component (human: ~1 day / CC: ~30 min)
B) Keep the table inside the report
  ✅ One source for the screen, same as Compare Quotes and Policy Check today
  ❌ About 80 MB per version at the cap; the browser loads every row on open
Net: a small extra route buys a review page that stays fast at the size L8 allows.
Header: Claims table
Options:
A) Paged API from claim_row (recommended)
Claims live only in claim_row; GET route pages, sorts and filters in SQL; the report artifact keeps totals strip, checks, notices and row counts. Tests: paging, sort, each quick filter, counts match ledger. Effort human ~1 day / CC ~30 min.
B) Table block in report
Keep the spec as written: the claims table is a block in every artifact version. No new route; large reviews save and load the full table on every action.

State: approved
Actual answer: A) Paged API from claim_row (D2, owner 2026-11-04)
Accepted scope: Claims live only in claim_row; a GET route pages, sorts and filters in SQL (including the four quick filters); the report artifact keeps the totals strip, check list, notices and row counts, no claims block. Tests: paging, sort, each quick filter, counts match the ledger. Section 4 step 7 and section 6 "virtualized" table read against this.
History: none

### R2: PDF row windows versus the model's output limit
Finding: 2, CRITICAL, confidence 8/10, spec section 4 step 3 ("The model returns rows per 15-page window (2-page overlap)") with `backend/providers/config.py:40` (`CallRole.EXTRACT: 8_000,` max output tokens) and `backend/pipeline/windows.py:66` (`size: int = 15`); reviewer: Claude (eng review).
Plan baseline: original proposal, reuse the 15-page / 2-overlap windows for claim rows.
Runtime evidence: EXTRACT output is capped at 8,000 tokens. A loss-run page holds about 20 to 30 claims at about 50 tokens per row [estimated], so a 15-page window needs about 15,000 to 22,000 output tokens. The reply would be cut off, or the model would drop rows to fit. Truncation handling for this case not traced (unknown).
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R1 claims table | approved A (D2) | approved A | approved A |
| R2 row windows | 15 pages, 2 overlap, 8k output | row windows sized from the output budget (start 2 pages, 1 overlap, own LOSS_ROWS output cap); a cut-off reply (length stop or unparsable) splits the window in half and retries down to 1 page; a 1-page cut-off becomes a "page not fully read" notice with Retry, never silent | keep 15-page windows; raise the EXTRACT output cap only |
| R3 to R7 | pending | pending | pending |
Question D3:
D3 — How big should the PDF windows be when reading claim rows?
Project/branch/task: BrokerDocs on main, eng review of the slice 5a loss-runs spec.
ELI10: The spec reuses the 15-page reading window from quotes. Quotes give back a few dozen facts per window; a loss run gives back every claim row, about 20 to 30 per page. Fifteen pages of rows is two to three times more text than the model is allowed to write back, so rows at the end of a window would go missing.
Stakes if we pick wrong: Claims silently missing from long PDFs, totals that never reconcile, and the 0.95 recall gate fails.
Recommendation: A because small windows always fit, and splitting on a cut-off reply means no row is ever dropped without a notice.
Completeness: A=10/10, B=5/10
Pros / cons:
A) Small row windows with split-and-retry (recommended)
  ✅ Each reply fits the output cap, so a 120-page PDF reads every row
  ✅ A cut-off reply is caught and split, never a silent gap in the table
  ❌ More calls per PDF (about 60 to 120 for the long fixture), cost set by the budget (human: ~1 day / CC: ~30 min)
B) Keep 15-page windows, raise the output cap
  ✅ Fewer calls, reuses windows.py unchanged
  ❌ Long replies are slow and still overflow on dense pages; no rule for a cut-off reply
Net: more small calls buy rows that are never lost.
Header: Row windows
Options:
A) Small windows, split on cut-off (recommended)
Row windows start at 2 pages with 1 overlap and a LOSS_ROWS output cap; a cut-off reply splits the window and retries down to 1 page; a 1-page cut-off is a "page not fully read" notice with Retry. Token budget from fixtures (L8). Tests: split on fake cut-off, notice at 1 page, overlap dedup. Effort human ~1 day / CC ~30 min.
B) 15-page windows, bigger cap
Keep the spec's 15-page / 2-overlap windows and raise the output cap for loss-run calls. No split rule; a cut-off reply is handled as today.

State: approved
Actual answer: A) Small windows, split on cut-off (D3, owner 2026-11-04)
Accepted scope: Row windows start at 2 pages with 1 overlap and a LOSS_ROWS output cap; a cut-off reply (length stop or unparsable) splits the window in half and retries down to 1 page; a 1-page cut-off is a "page not fully read" notice with Retry, never silent. Token budget from fixtures (L8). Tests: split on fake cut-off, notice at 1 page, overlap dedup. Section 4 step 3 reads against this.
History: none

### R3: Who owns broker edits and decisions on rows
Finding: 3, CRITICAL, confidence 7/10, spec section 3 ("role: counted | superseded | conflicting | left_out; source: extracted | edited | added; for edited rows the original extracted values are kept beside the edit") and section 2 (failed file "Retry"), section 3 ("a re-read that changes the values reopens the decision"); reviewer: Claude (eng review).
Plan baseline: original proposal, edits, confirms, leave-outs and the conflict pick are stored on the claim_row rows themselves (role and source columns), plus discrepancy_state items.
Runtime evidence: claim_row does not exist yet. A Retry or re-read of a document must replace that document's extracted rows; any edit, confirm or leave-out stored on those rows goes with them. The conflict pick would be written twice (claim_row.role and a discrepancy_state item): two owners of one decision (red flag 5).
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R1, R2 | approved A (D2, D3) | approved A | approved A |
| R3 decision ownership | on claim_row (role, source) + discrepancy_state | claim_row = extracted values only, replaced per document on re-read; broker rows (added) are their own claim_row rows with source added and never replaced; a `claim_decision` table keyed (run_id, row_key = document_id + claim_key + nth occurrence in that document) holds edit (new amounts + the extracted values it was made on), confirm (reason), leave_out (note); conflict picks and accepted gaps stay in discrepancy_state; role is computed only by build_ledger and written to claim_row as a derived column for SQL filtering; a decision whose saved extracted values differ after a re-read reopens ("Changed since you resolved it") | as written: decisions on claim_row columns |
| R4 to R7 | pending | pending | pending |
Question D4:
D4 — Where are the broker's row edits and decisions kept?
Project/branch/task: BrokerDocs on main, eng review of the slice 5a loss-runs spec.
ELI10: The spec saves a broker's fix (edited amount, confirm, leave out) on the same row the model extracted. When a file is read again (Retry, or a re-read after a fix), its rows are replaced, and the broker's work would vanish with them. The spec also stores the "which conflicting row did you pick" answer in two places.
Stakes if we pick wrong: A broker fixes 12 rows, clicks Retry on one file, and their fixes are gone or silently re-applied to the wrong row.
Recommendation: A because decisions kept apart from extracted rows survive every re-read, and one owner per decision means the screen and the totals never disagree.
Completeness: A=10/10, B=5/10
Pros / cons:
A) Separate decision table, roles computed (recommended)
  ✅ Retry or re-read never loses an edit, confirm or leave-out
  ✅ A re-read that changes a decided row reopens it, as the spec wants
  ❌ One more table plus a stable row key rule (human: ~1 day / CC: ~30 min)
B) Keep decisions on claim_row
  ✅ Fewer tables, one row holds everything the screen shows
  ❌ Re-read loses decisions unless copied by hand; conflict pick has two owners
Net: one extra table makes broker work survive re-reads.
Header: Row decisions
Options:
A) Decision table, roles computed (recommended)
claim_row holds extracted values only (replaced per document on re-read); broker-added rows are claim_rows with source added, never replaced; claim_decision keyed (run_id, document_id + claim_key + nth occurrence) holds edit/confirm/leave_out with the extracted values it was made on; conflict picks and accepted gaps in discrepancy_state; role computed by build_ledger only. Tests: decision survives re-read, changed value reopens, undo. Effort human ~1 day / CC ~30 min.
B) Decisions on claim_row
Keep the spec as written: role and source columns on claim_row carry the broker's decisions, plus discrepancy_state items. Re-read handling left to the build.

State: approved
Actual answer: A) Decision table, roles computed (D4, owner 2026-11-04)
Accepted scope: claim_row holds extracted values only, replaced per document on re-read; broker-added rows are claim_rows with source added, never replaced; a claim_decision table keyed (run_id, row_key = document_id + claim_key + nth occurrence in that document) holds edit (new amounts + the extracted values it was made on), confirm (reason) and leave_out (note); conflict picks and accepted gaps stay in discrepancy_state; role is computed only by build_ledger and written to claim_row as a derived column for SQL filtering; a decision whose saved extracted values differ after a re-read reopens ("Changed since you resolved it"). Tests: decision survives re-read, changed value reopens, undo. Section 3 role/source bullets read against this.
History: none

### R4: The same claim number on several lines of one file
Finding: 4, CRITICAL, confidence 7/10, spec section 4 step 5 ("Group by carrier + claim key. Latest valuation date → counted; older → superseded ... Same valuation date and different amounts → conflicting") and section 7 (no within-file rule); reviewer: Claude (eng review).
Plan baseline: original proposal, grouping by carrier + claim key with no rule for repeats inside one document.
Runtime evidence: not code (claim_row does not exist). Domain: loss runs often print one claim on several lines (one per coverage or feature: bodily injury, property damage, medical; or per claimant), all with the same claim number and the same valuation date. Under step 5 those lines have the same valuation date and different amounts, so they would all become Conflicting and leave the totals, and the printed total would never reconcile. No fixture covers it (section 8 trap list).
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R1 to R3 | approved A (D2 to D4) | approved A | approved A |
| R4 within-file repeats | unspecified (falls into step 5 as conflicting) | inside one document, lines sharing a claim key are lines of one claim: each line kept, cited and counted; the claim's amounts for cross-file matching are the sum of its lines in that document; supersede and conflict compare those per-document sums; "claims" count = distinct claim keys, not lines; lines identical in every field get a `repeated_line` warning and still count; new fixture trap: one claim on 3 feature lines | treat within-file repeats like cross-file duplicates (step 5 as written) |
| R5 to R7 | pending | pending | pending |
Question D5:
D5 — What happens when one file prints the same claim number on several lines?
Project/branch/task: BrokerDocs on main, eng review of the slice 5a loss-runs spec.
ELI10: Carriers often print one claim as several lines, one per coverage (injury, property damage, medical), each with the same claim number. The spec's duplicate rule only thinks about the same claim showing up in two different files, so these lines would be flagged as conflicting copies and dropped from the totals.
Stakes if we pick wrong: Common loss runs never reconcile, brokers face dozens of false conflicts, and totals understate losses.
Recommendation: A because lines of one claim inside one file are parts, not copies; summing them per file keeps the cross-file rule (L5) exactly as approved.
Completeness: A=10/10, B=4/10
Pros / cons:
A) Lines of one claim, summed per file (recommended)
  ✅ Multi-line claims count fully and the carrier's printed total matches
  ✅ L5's latest-valuation and conflict rules still apply, on per-file claim sums
  ❌ An accidental double print inside one file counts twice (flagged with a warning) (human: ~4h / CC: ~15 min)
B) Same rule as across files
  ✅ One rule for every repeat, no extra grouping step
  ❌ Feature lines become Conflicting and leave the totals; false checks everywhere
Net: treating in-file repeats as parts fixes a common layout at the cost of one warning case.
Header: In-file repeats
Options:
A) Lines of one claim, summed per file (recommended)
Inside one document, lines sharing a claim key are lines of one claim: each kept, cited, counted; cross-file supersede/conflict compares per-document sums; claims count = distinct keys; identical lines get a repeated_line warning and still count; new fixture trap (one claim on 3 feature lines). Effort human ~4h / CC ~15 min.
B) Same rule as across files
Within-file repeats go through step 5 as written: same valuation date with different amounts becomes Conflicting and leaves the totals.

State: approved
Actual answer: A) Lines of one claim, summed per file (D5, owner 2026-11-04)
Accepted scope: Inside one document, lines sharing a claim key are lines of one claim: each line kept, cited and counted; the claim's amounts for cross-file matching are the sum of its lines in that document; supersede and conflict compare those per-document sums; the claims count = distinct claim keys, not lines; lines identical in every field get a `repeated_line` warning and still count; new fixture trap: one claim on 3 feature lines. Section 4 step 5 and section 8 traps read against this.
History: none

### R5: Sheet pages in the viewer (necessary implementation of L8, no question)
Finding: 5, INFORMATIONAL, confidence 7/10, spec section 3 ("Rendered PDF pages for a sheet are produced on demand ... opens that rendered page highlighted in the existing viewer") with `backend/models.py:133` (`UniqueConstraint("document_id", "page_no")` on page), `backend/models.py:300` (same on passage) and `backend/routes/documents.py:174` (`@router.get("/documents/{document_id}/file")`, one PDF per document); reviewer: Claude (eng review).
Plan baseline: L8 (owner): sheet pages rendered on demand, cited in the existing viewer.
Runtime evidence: the viewer loads one PDF per document from `/file`; Page and Passage rows are keyed by page number; nothing renders part of a document today.
Record (carries L8 forward, approves nothing new): a spreadsheet document has no Page or Passage rows; its pages are numbered deterministically (sheets in workbook order, a fixed number of data rows per page with the header repeated); `document.page_count` = that count; the parsed rows are cached beside the `.orig` so a render never re-parses the workbook; `GET /documents/{id}/sheet-pages/{n}` returns a one-page PDF rendered from the cached rows (disk cache keyed by sha256 + renderer version + n) and the row's highlight box; the viewer opens that page for a "Sheet 2024, row 17" chip. Tests: page numbering stable across uploads of the same bytes, render cache hit, highlight box on the cited row, page out of range = 404.
State: approved (L8 carried forward)
Actual answer: no question; necessary implementation of L8
Accepted scope: as in Record above.
History: none

### R6: How strict the PDF row check is
Finding: 6, INFORMATIONAL, confidence 6/10, spec section 4 step 3 ("A row verifies only if its claim number and every amount it gives appear on the cited page"); reviewer: Claude (eng review). Medium confidence: depends on how often amounts repeat on a page.
Plan baseline: original proposal, claim number and amounts each found anywhere on the cited page.
Runtime evidence: Page rows keep word boxes (`backend/models.py:143`, `words: [[x0, y0, x1, y1, text, block, line, word], ...]`), so position is available for digital pages. A loss-run page prints many small and repeated amounts (0.00, deductibles, round reserves); a model that moves claim B's reserve onto claim A still verifies under "anywhere on the page". The hard fail "a wrong amount on a counted row" (section 8) would pass the check.
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R1 to R5 | approved | approved | approved |
| R6 row check | claim number + every amount anywhere on the cited page | claim number found on the page, then every amount found inside that claim's printed band (from the claim number's line down to the next claim number's line, so wrapped lines count); pages with no word boxes (scans) keep the spec's rule (unverified until the broker confirms) | as written: anywhere on the page |
| R7 | pending | pending | pending |
Question D6:
D6 — Should a PDF row's amounts have to sit on that claim's own printed lines?
Project/branch/task: BrokerDocs on main, eng review of the slice 5a loss-runs spec.
ELI10: To trust a row, the spec checks that its claim number and its amounts are printed somewhere on the page. Loss-run pages repeat the same numbers a lot (0.00, $1,000 deductibles, round reserves), so a model that copies another claim's amount onto this row would still pass. Checking that the amounts sit on the claim's own lines closes that gap.
Stakes if we pick wrong: A wrong amount is counted as verified, which is the exact hard fail the gate is meant to catch.
Recommendation: A because word positions already exist for digital pages, and "verified" should mean the number is on that claim's lines.
Completeness: A=10/10, B=6/10
Pros / cons:
A) Amounts must sit in the claim's printed band (recommended)
  ✅ A moved amount from another claim fails the check and stays out of totals
  ✅ Wrapped claimant lines still pass because the band runs to the next claim
  ❌ Odd layouts (amounts in a separate column block) may verify less often (human: ~1 day / CC: ~30 min)
B) Anywhere on the cited page
  ✅ Simple, never rejects a correct row because of layout
  ❌ Repeated numbers let a wrong amount verify and count in totals
Net: a position check turns "verified" into a real guarantee for digital PDFs.
Header: Row check
Options:
A) Amounts in the claim's band (recommended)
Claim number found on the page, then every amount found between that claim number's line and the next claim number's line (wrapped lines included); scans keep the unverified rule. Tests: moved amount fails, wrapped row passes, last row on page runs to page end. Effort human ~1 day / CC ~30 min.
B) Anywhere on the page
Keep the spec's rule: claim number and every amount appear anywhere on the cited page.

State: approved
Actual answer: A) Amounts in the claim's band (D6, owner 2026-11-04)
Accepted scope: A PDF row verifies only if its claim number is found on the cited page and every amount it gives is found between that claim number's line and the next claim number's line (wrapped lines included; the last claim on a page runs to the page end); pages with no word boxes (scans) keep the unverified rule. With R4, each line of a multi-line claim carries the claim number and gets its own band. Tests: moved amount fails, wrapped row passes, last row runs to page end. Section 4 step 3 reads against this.
History: none

### R7: Spreadsheet parsing rules (necessary implementation of L7, L8 and "every row verified", no question)
Finding: 7, INFORMATIONAL, confidence 7/10, spec section 4 step 1 ("Spreadsheet, in code (openpyxl / csv)") and step 8 limits; reviewer: Claude (eng review).
Plan baseline: L7 (parse in code), L8 (row and sheet caps), section 7 (corrupt file shown with reason).
Runtime evidence: `openpyxl>=3.1,<4` is already a dependency (`backend/pyproject.toml:19`). openpyxl's default mode loads the whole workbook into memory; `data_only=True` returns None for a formula cell whose value was never calculated (files written by libraries, not Excel).
Record: open with `read_only=True, data_only=True`; refuse before parsing when the uncompressed size or cell count is over a cap derived from L8 (zip bomb guard, reason shown); a formula cell with no stored value makes its row unverified with the reason "formula not calculated" (never read as 0); Excel date serials and text dates both go to ISO; money via `Decimal(str(value))` rounded half-up to cents, never float math; merged header cells take the merged value; `.csv` sniffed for delimiter and decoded UTF-8 (with BOM) then cp1252, otherwise refused with the reason. Tests: one row per rule above.
State: approved (L7, L8 carried forward)
Actual answer: no question; necessary implementation
Accepted scope: as in Record above.
History: none

### R8: Cache key for code-parsed sheets (necessary implementation of the CLAUDE.md cache rule, no question)
Finding: 8, INFORMATIONAL, confidence 7/10, spec section 4 step 8 ("Cache keys include the loss-run fields and prompts; bump EXTRACTION_VERSION") with `backend/pipeline/cache.py:69` (`def extraction_key(fields, instructions, config, *, ...)`); reviewer: Claude (eng review).
Record: a spreadsheet read is reused only when the key also includes a hash of the header synonym table (`headers.py`) and the parser version, so a synonym change never serves an old mapping; PDF row reads include the LOSS_ROWS prompt and window config (R2). Test: changing one synonym misses the cache.
State: approved (CLAUDE.md rule carried forward)
Actual answer: no question; necessary implementation
Accepted scope: as in Record above.
History: none

### Outside voice (Codex, gpt-6-astra, completed 2026-11-04): 7 findings, all verified against code by the parent
Each item uses the outside-voice menu: A) Apply this change; B) Keep the current value; C) Investigate before
choosing; D) Defer this change only. Every option keeps R1 to R8 as approved; only the named row changes.

### O1: Row check verifies presence, not column (reopens R6 for a concrete new risk)
Finding: outside 1, High, confidence 8/10, R6 accepted scope (amounts inside the claim's band); reviewer: Codex. Swapping paid $10,000 and reserve $90,000 still passes (both in band, incurred still = paid + reserve).
Plan baseline: R6 A (D6). Runtime evidence: Page.words carries x0/x1 per word (`backend/models.py:143`), so column x-ranges can be found from the header row.
Comparison grid: O1 current = band only (R6). A = also each amount must sit under its own column (x-overlap with that column's header span on the page); a page whose header columns are not found keeps the row unverified with the reason; swapped-columns fixture trap. B = band only. C = measure on the 2 PDF fixture layouts first, no change approved. D = defer, band only stays.
Question D7 (header "Column check"): Should each amount also have to sit under its own column header?
Recommendation: A because a swapped paid/reserve is a wrong amount on a counted row, a hard fail.
State: approved
Actual answer: A) Apply (D7, owner 2026-11-04)
Accepted scope: each amount must also sit under its own column (x-overlap with that column's header span on the page); a page whose header columns are not found keeps the row unverified with the reason; swapped-columns fixture trap. R6 band rule stays.
History: R6 approved A (D6) before this reopening.

### O2: Stable row identity (reopens R2 overlap dedup and R3 row key)
Finding: outside 2, High, confidence 8/10, spec section 4 step 3 ("Rows repeated in window overlaps are dropped by claim number + page") with R4 (several lines per claim) and the R3 key (nth occurrence); reviewer: Codex. Claim number + page drops legitimate feature lines; nth occurrence shifts when a re-read finds an earlier missed line.
Comparison grid: O2 current = dedup by claim number + page; key = document + claim key + nth occurrence. A = row identity is the source location (PDF: document + page + the claim number's printed line on that page; sheet: document + sheet + source row); overlap dedup drops a row only when the same location was read twice; decisions keyed by location + claim key; when a re-read moves a decided row, match by claim key + amounts, else reopen. B = keep. C = investigate. D = defer.
Question D8 (header "Row identity"): Should rows be identified by where they are printed, not by claim number and count?
Recommendation: A because only a printed location tells a feature line from a duplicate read, and it does not shift on re-read.
State: approved
Actual answer: A) Apply (D8, owner 2026-11-04)
Accepted scope: row identity = source location (PDF: document + page + the claim number's printed line on that page; sheet: document + sheet + source row); overlap dedup drops a row only when the same location was read twice; claim_decision keyed by location + claim key (replaces the R3 nth-occurrence key); when a re-read moves a decided row, match by claim key + amounts, else reopen.
History: R2 A (D3) and R3 A (D4) approved before this reopening.

### O3: Printed totals need their own records
Finding: outside 3, High, confidence 8/10, spec section 3 ("each printed total with its scope (e.g. lr:total_incurred[2024])") with `backend/pipeline/extract.py:427` (`normalize_output`: "Scalar facts: exactly one per scalar field"); reviewer: Codex. Scalar facts keep one value per declared key; years, policy numbers and page-break subtotals are found in the document, not declared.
Comparison grid: O3 current = lr: scalar facts. A = a `printed_total` table: metric, scope kind (all, policy year, policy number, carrier, page subtotal), scope value, amount in cents, citation (page + quote or sheet cell), verify state; PDF totals returned by the row-window call, sheet totals read in code; page subtotals reconciled against the rows printed above them on that page. B = keep lr: facts. C = investigate. D = defer.
Question D9 (header "Printed totals"): Should printed totals be their own records with an explicit scope?
Recommendation: A because reconciliation (L6) needs every printed total, and the fact path keeps one per key.
State: approved
Actual answer: A) Apply (D9, owner 2026-11-04)
Accepted scope: a printed_total table: metric, scope kind (all, policy year, policy number, carrier, page subtotal), scope value, amount in cents, citation (page + quote or sheet cell), verify state; PDF totals returned by the row-window call, sheet totals read in code; page subtotals reconciled against the rows printed above them on that page. Replaces lr: total facts (other lr: document facts stay).
History: none

### O4: Table filters must use the ledger's effective values
Finding: outside 4, High, confidence 8/10, R1 (SQL filters) with R3 (edits in claim_decision) and R4 (claim = sum of lines); reviewer: Codex. Filtering raw extracted amounts leaves an edited $120K claim out of "Over $100K", and an $80K + $40K multi-line claim too.
Comparison grid: O4 current = filters on claim_row raw amounts. A = build_ledger writes effective line amounts (after edits) and per-claim totals as derived columns on every build; the paged API sorts and filters only on those; quick filters are claim-level. B = keep. C = investigate. D = defer.
Question D10 (header "Filter values"): Should sorting and filters use the amounts after edits and per-claim sums?
Recommendation: A because the table must agree with the totals strip the broker sees.
State: approved
Actual answer: A) Apply (D10, owner 2026-11-04)
Accepted scope: build_ledger writes effective line amounts (after edits) and per-claim totals as derived columns on every build; the paged API sorts and filters only on those; quick filters are claim-level.
History: none

### O5: Retry after a budget stop
Finding: outside 5, High, confidence 8/10, spec section 4 step 8 ("Stopped at page 180: budget reached" with Retry) with `backend/pipeline/runner.py:676` (`retry`: "Re-run the documents that did not finish") and `backend/pipeline/runner.py:939` (`budget_limit=max(1, self._budget_limit - spent)`); reviewer: Codex. Retry re-reads the whole document from page 1 with less budget, so it can stop again, earlier.
Comparison grid: O5 current = whole-document retry, budget minus spent. A = window completion saved per document; Retry reads only unfinished or failed windows; a budget-stop Retry gets a fresh per-document allowance (the loss-run document cap) and the screen says which pages it will read. B = keep. C = investigate. D = defer.
Question D11 (header "Budget retry"): Should Retry resume from the unread pages with a fresh document allowance?
Recommendation: A because otherwise Retry on a long PDF may never finish.
State: approved
Actual answer: A) Apply (D11, owner 2026-11-04)
Accepted scope: window completion saved per document; Retry reads only unfinished or failed windows; a budget-stop Retry gets a fresh per-document allowance (the loss-run document cap) and the screen says which pages it will read.
History: none

### O6: Cache reuse must carry claim rows
Finding: outside 6, High, confidence 9/10, R8 (cache key) with `backend/pipeline/cache.py:127` (`copy_read`: "Copy the source's facts (and, for another Document, its pages and cited passages)") and `backend/pipeline/runner.py:986` (`_reuse_read` returns before extraction); reviewer: Codex. A reused loss-run read would copy facts but no claim rows: a review with no claims.
Comparison grid: O6 current = copy facts, pages, passages. A = for loss-run reads copy_read also copies extracted claim_rows and printed totals (citations remapped to the target document) and the sheet caches; never broker decisions or added rows; roles recomputed by build_ledger; test that a reused read gives identical rows. B = keep. C = investigate. D = defer.
Question D12 (header "Cache rows"): Should a reused read copy the claim rows and printed totals too?
Recommendation: A because the spec promises cached documents skip the read, and the copy must be complete.
State: approved
Actual answer: A) Apply (D12, owner 2026-11-04)
Accepted scope: for loss-run reads copy_read also copies extracted claim_rows and printed totals (citations remapped to the target document) and the sheet caches; never broker decisions or added rows; roles recomputed by build_ledger; test that a reused read gives identical rows.
History: none

### O7: Citations for spreadsheet rows and printed totals
Finding: outside 7, Medium, confidence 7/10, R5 ("a spreadsheet document has no Page or Passage rows") with `backend/views.py:84` (`chips`: "Chip label and PDF location for every passage the content cites"); reviewer: Codex. Report chips come only from Passage ids; nothing turns a sheet citation or a printed total into a chip.
Comparison grid: O7 current = chips from Passage ids only. A = loss-run chips come from claim_row and printed_total evidence (document, page or sheet page, highlight box), built by the loss-run route; PDF loss-run citations still create Passage rows so the PDF viewer path is unchanged; sheet citations open sheet page n with the row box. B = keep. C = investigate. D = defer.
Question D13 (header "Sheet chips"): Should loss-run chips be built from the row and total evidence, so sheet citations open?
Recommendation: A because a reconciliation check must cite its printed line, including on a spreadsheet.
State: approved
Actual answer: A) Apply (D13, owner 2026-11-04)
Accepted scope: loss-run chips come from claim_row and printed_total evidence (document, page or sheet page, highlight box), built by the loss-run route; PDF loss-run citations still create Passage rows so the PDF viewer path is unchanged; sheet citations open sheet page n with the row box.
History: none

Approval readiness: PASS. Checked R1 (D2 A), R2 (D3 A), R3 (D4 A, key replaced by O2), R4 (D5 A), R5 (L8 carried),
R6 (D6 A, extended by O1), R7 (L7/L8 carried), R8 (CLAUDE.md rule carried), O1 (D7 A), O2 (D8 A), O3 (D9 A),
O4 (D10 A), O5 (D11 A), O6 (D12 A), O7 (D13 A). Structure D1 B. No pending choice.

## Review sections

Scope Challenge: scope accepted as-is (D1 picked the smaller arrangement, same features). 8+ files and 2+ new
services tripped the complexity gate; no feature cut proposed (L1 already cut slice 5).

Reality rows:
- REALITY: premise-table pass 7 premises, 0 unverified (openpyxl/reportlab `backend/pyproject.toml:19`; `.orig` sidecar `backend/ams.py:23`, but AMS renders at upload, not on demand; discrepancy_state scoped items `backend/models.py:388`; 15/2 windows `backend/pipeline/windows.py:66`; no-migration schema check `backend/db.py:91`; cache key `backend/pipeline/cache.py:69`; slug source `backend/slugs.py`)
- REALITY: already-done pass no loss-run code on main (`git log -- backend/loss_runs` empty; HEAD f861093) `backend/slugs.py:1`
- REALITY: surface-check finding `/documents/{id}/file` serves one PDF per document, no sheet pages (R5, O7) `backend/routes/documents.py:174`
- REALITY: binding-decisions pass L1 to L9 honoured; slice 4 D1/D10 discrepancy_state pattern reused `docs/specs/2026-11-03-loss-runs-design.md:15`
- REALITY: numbers finding row-window token math is [estimated]; EXTRACT output cap 8,000 is [measured: config] `backend/providers/config.py:40`; loss-run budget [re-measure before gate]
- REALITY: deferred-asks pass no linked issue `docs/specs/2026-11-03-loss-runs-design.md:35`
- REALITY: pr-count pass no ship-policy.json, lanes merge to local main one at a time (one wave) `HANDOFF.md:1`

### 1. Architecture (findings 1 to 5 above as R1 to R5; plus)
- [INFORMATIONAL] (confidence: 8/10) `backend/work_queue.py:144`, `:179`, `:254` — `if run.workflow_slug == POLICY_CHECK_SLUG:` chains: the queue knows each workflow by name in three places (hand-synced list, red flag 8). loss_run needs a branch in each (client from the wizard, "Review losses" action). Task T12 adds them and a test that every workflow slug has a queue label. Disposition: necessary implementation of section 2 step 5.
- Design twice: shape A (spec) = per-step modules normalize / roles / reconcile / decisions; shape B (picked, D1) =
  one pure `build_ledger(rows, documents, totals, decisions) -> Ledger` hiding normalization, in-file grouping (R4),
  cross-file roles (L5), effective values (O4), reconciliation (O3) and totals behind one call. Deeper: one
  interface, every number has one owner. Red flags screened: split ownership (fixed by R3/O2: decisions only in
  claim_decision and discrepancy_state, roles only from build_ledger); two ways to cite (fixed by O7, loss-run chips
  one builder); hand-synced queue list (T12); importable internals (ledger.py exports only build_ledger and its types).

```
upload ─► sheets.py (xlsx/csv: caps R7, header map headers.py, rows + printed totals, parsed cache)
       └► parse (PDF) ─► pdf_rows.py (2-page windows R2, split on cut-off, rows + printed totals O3,
                                       verify: band R6 + column O1, location identity O2)
                ▼
     claim_row (extracted, replaced per document)  printed_total  claim_decision  discrepancy_state
                └──────────────────────────┬──────────────────────────┘
                                           ▼
                build_ledger (pure): in-file lines (R4) ─► cross-file roles (L5) ─► effective values (O4)
                                         ─► reconcile (L6, O3) ─► totals, checks, notices
                         ▼                                        ▼
        derived columns on claim_row (role, effective,      report artifact: totals strip, checks,
        claim totals) ─► paged API (R1, O4)                 notices, counts (no claims block, R1)
                         ▼
        chips from row/total evidence (O7) ─► PDF page | sheet page n (R5)
```

### 2. Code quality
- [INFORMATIONAL] (confidence: 7/10) `backend/ams.py:13` — `KEY_TABLE maps a normalized field name to a canonical fact key`: reuse its name normalizer and screening (control/bidi characters, value caps) for sheet headers and cells instead of a second copy. Disposition: necessary implementation (section 7 "free text escaped and bounded").
- Money parsing, dates, formulas: R7. No other findings.

### 3. Tests
Framework: pytest (backend), Playwright e2e + axe (frontend), offline eval with recorded replies, live `-k loss_run`.

```
CODE PATHS                                              USER FLOWS
[+] sheets.py                                           [+] New loss review (wizard)
  ├── [GAP] header row find, synonyms, unknown→model      ├── [GAP] [→E2E] Who → Files → Read → Review page
  ├── [GAP] totals/subtotal rows → printed_total          ├── [GAP] [→E2E] upload unique name+bytes (handoff rule)
  ├── [GAP] caps: rows, sheets, zip size (R7)             └── [GAP] [→E2E] unreadable file: reason + Retry, rest builds
  ├── [GAP] formula w/o value → unverified (R7)         [+] Checks
  └── [GAP] sheet page render + cache + highlight (R5)    ├── [GAP] [→E2E] mismatch: filter rows, fix amount, recompute
[+] pdf_rows.py                                           ├── [GAP] [→E2E] add missed claim; accept gap needs note
  ├── [GAP] 2-page windows, split on cut-off, 1-page      ├── [GAP] [→E2E] conflict: pick one, other "Not used"
  │         notice (R2)                                   └── [GAP] [→E2E] unverified: confirm w/ reason, leave out, Undo
  ├── [GAP] band + column verify, scans unverified (R6,O1)[+] Claims table
  ├── [GAP] location identity, overlap dedup (O2)         ├── [GAP] [→E2E] paging, sort, 4 quick filters on effective values
  └── [GAP] [→EVAL] row prompt recall/amounts             └── [GAP] [→E2E] chip opens PDF page / sheet page n
[+] ledger.py (pure)                                    [+] Queue + Activity
  ├── [GAP] status map, claim key, cents                  └── [GAP] [→E2E] Needs review → Ready → Done
  ├── [GAP] in-file lines summed, repeated_line (R4)    [+] Retry after budget stop (O5)
  ├── [GAP] latest valuation, superseded, conflict,       └── [GAP] [→E2E] resumes at unread pages
  │         no-date file, same-date same-amount (L5)
  ├── [GAP] reconcile scopes, $1 tolerance, page
  │         subtotal, no-totals notice (L6, O3)
  └── [GAP] effective values after edits (O4)
[+] decisions + cache
  ├── [GAP] decision survives re-read; moved row matched
  │         or reopened (R3, O2)
  └── [GAP] reused read copies rows/totals, not decisions (O6)
COVERAGE: 0/27 paths (new code)  |  GAPS: 27 (12 E2E, 1 eval)
```

All paths are new code, so every gap is a planned test, recorded in the test plan artifact
(`~/.gstack/projects/aakas/aakas-main-eng-review-test-plan-20261104-loss-runs.md`). Regression risk (IRON RULE):
copy_read (O6) and retry (O5) are shared with Compare, Policy Check and Certificates; regression contract = existing
cache and retry tests stay green unchanged, plus one test each that a non-loss-run reuse and retry behave as before
(carried as necessary proof of D11/D12). Eval: offline gate with recorded replies in the normal suite; live
`-k loss_run` only with owner approval each run; hard fails and thresholds as section 8, plus swapped columns (O1)
and a 3-feature-line claim (R4) as traps.

### 4. Performance
- [INFORMATIONAL] (confidence: 6/10) build_ledger reruns over every row of the review after each broker action; at 200,000 rows the Python pass plus derived-column write is [estimated] seconds, unknown until measured. Medium confidence, verify this is actually an issue. Task T13: a perf test on a generated 200,000-row review records the time per action; index claim_row on (run_id, role), (run_id, claim_key) and the derived sort columns. Disposition: measurement only, no new bound approved.
- Sheet rendering: parsed-rows cache + per-page render cache (R5) keep a citation open off the workbook. No other findings.

## NOT in scope
- Insights, summary, export, loss ratios (5b, L1); DOCX (L4); agency-wide view (L3); several claims tables on one sheet (section 7, first table only); a column picker beyond show/hide (UI lane decides).

## What already exists
- AMS sidecar + screening (`backend/ams.py`), reused for `.orig` and cell screening; windows (`backend/pipeline/windows.py`), reused with loss-run config (R2); word boxes on Page (`backend/models.py:143`) for R6/O1; discrepancy_state with fingerprints (`backend/models.py:388`) for checks and conflicts; extraction cache (`backend/pipeline/cache.py`) extended (R8, O6); retry (`backend/pipeline/runner.py:676`) extended (O5); openpyxl and reportlab already installed.

## Failure modes
| Path | Realistic failure | Covered by | User sees |
|---|---|---|---|
| sheet parse | zip bomb / huge sheet | R7 caps, test | refused with reason |
| sheet parse | formula never calculated | R7, test | row unverified "formula not calculated" |
| PDF rows | reply cut off | R2 split, test | rows complete or "page not fully read" + Retry |
| PDF rows | swapped amount | O1, test + eval trap | row unverified |
| ledger | multi-line claim | R4, test + trap | counted, reconciles |
| reconcile | subtotal on page break | O3, test | check per subtotal |
| decisions | re-read shifts rows | O2, test | decision kept or "Changed since you resolved it" |
| cache | reused read | O6, test | same rows as first read |
| retry | budget stop | O5, test | resumes at unread pages |
| viewer | sheet chip | O7/R5, test | sheet page with row box |
No failure mode is untested, unhandled and silent: 0 critical gaps.

## Worktree parallelization strategy
| Step | Modules touched | Depends on |
|------|----------------|------------|
| A1 models + schema check (claim_row, claim_decision, printed_total, workflow row) | backend/models, db | — |
| A2 sheets + headers + sheet-page route | backend/loss_runs, routes/documents | A1 |
| A3 pdf_rows (windows, verify, identity) | backend/loss_runs, pipeline | A1 |
| A4 ledger + decisions + paged API + queue | backend/loss_runs, routes, work_queue | A1 |
| A5 cache copy + retry resume | backend/pipeline (cache, runner) | A2, A3 |
| B fixtures + truth + offline gate | tests/fixtures/loss_runs, tests/eval | A1 types only |
| C UI (design stack) | frontend/src | A4 API shape |
| D docs | docs, API.md, HANDOFF | A, B, C |
Lane A: A1 → A2 → A3 → A4 → A5 (shared backend modules, sequential, Opus). Lane B: fixtures in parallel from A1's types (Opus). Launch A + B. Merge A, then run B's gate on main. Then lane C (Opus, design stack, Playwright MCP, max 3 rounds). Then D (Sonnet). Conflict flag: A5 touches cache.py and runner.py, shared with every workflow; run the full backend suite before merge.

## Implementation Tasks
- [ ] **T1 (P1, human: ~4h / CC: ~20min)** — models — claim_row (extracted values, location identity, derived role/effective/claim-total columns), claim_decision, printed_total, loss_run workflow row, check_schema gaps
  - Surfaced by: R1, R3, O2, O3, O4
  - Files: backend/models.py, backend/db.py, backend/slugs.py
  - Verify: pytest backend/tests/test_db*.py
- [ ] **T2 (P1, human: ~1d / CC: ~45min)** — sheets — xlsx/csv parse (R7 rules), header synonyms + model for unknown headers, printed totals in code, parsed-rows cache, sheet-page render route with highlight
  - Surfaced by: L7, L8, R5, R7, O3
  - Files: backend/loss_runs/headers.py, backend/loss_runs/sheets.py, backend/routes/documents.py
  - Verify: pytest backend/tests/test_loss_sheets.py
- [ ] **T3 (P1, human: ~1d / CC: ~45min)** — pdf rows — 2-page windows with split on cut-off, rows + printed totals per call, band + column verify, location identity and overlap dedup
  - Surfaced by: R2, R6, O1, O2, O3
  - Files: backend/loss_runs/pdf_rows.py, backend/pipeline/windows.py
  - Verify: pytest backend/tests/test_loss_pdf_rows.py
- [ ] **T4 (P1, human: ~1d / CC: ~45min)** — ledger — pure build_ledger: normalize, in-file lines, cross-file roles, effective values, reconciliation incl. page subtotals, totals, notices
  - Surfaced by: D1, R4, L5, L6, O3, O4
  - Files: backend/loss_runs/ledger.py
  - Verify: pytest backend/tests/test_loss_ledger.py
- [ ] **T5 (P1, human: ~1d / CC: ~45min)** — routes — run wiring, decisions (edit, add, confirm, leave out, pick, accept gap, Undo) with re-read reopen, paged claims API on effective values, chips from evidence, report artifact without claims block
  - Surfaced by: R1, R3, O2, O4, O7
  - Files: backend/routes/loss_runs.py, backend/pipeline/runner.py, backend/schemas.py
  - Verify: pytest backend/tests/test_loss_routes.py
- [ ] **T6 (P1, human: ~4h / CC: ~30min)** — cache + retry — copy claim rows and printed totals on reuse (never decisions); window progress and resume Retry with fresh document allowance; regression tests for other workflows
  - Surfaced by: O5, O6, R8
  - Files: backend/pipeline/cache.py, backend/pipeline/runner.py
  - Verify: full backend pytest
- [ ] **T7 (P1, human: ~2d / CC: ~1h)** — eval — synthetic fixtures (section 8 + swapped columns + 3-line claim), truth JSON, offline gate, recorded replies; live `-k loss_run` with owner approval, budget set from measured tokens
  - Surfaced by: section 8, O1, R4, L8
  - Files: tests/fixtures/loss_runs, tests/eval
  - Verify: pytest tests/eval -k loss_run (offline)
- [ ] **T8 (P2, human: ~3d / CC: ~2h)** — frontend — Analytics page + rail, wizard, review page (totals strip, checks, paged claims table, chips), queue row; design stack, Playwright MCP, max 3 rounds
  - Surfaced by: sections 2, 5, 6; R1, O7
  - Files: frontend/src
  - Verify: e2e + axe at 1440/1024/390
- [ ] **T9 (P2, human: ~2h / CC: ~15min)** — dev seed — one synthetic loss review (4 files, one mismatch, one conflict), cache off
  - Surfaced by: section 8
  - Files: backend/dev_seed.py
  - Verify: pytest backend/tests/test_dev_seed.py
- [ ] **T10 (P2, human: ~2h / CC: ~15min)** — docs — API.md, spec amendments, slice map, DESIGN.md section, HANDOFF, EXTRACTION_VERSION bump
  - Surfaced by: R8, CLAUDE.md rule
  - Files: backend/API.md, docs, backend/pipeline/cache.py
  - Verify: read-through
- [ ] **T12 (P2, human: ~1h / CC: ~10min)** — queue — loss_run branches in work_queue (client, action label) + test that every workflow slug has a queue label
  - Surfaced by: Architecture, work_queue.py:144
  - Files: backend/work_queue.py
  - Verify: pytest backend/tests/test_work_queue*.py
- [ ] **T13 (P2, human: ~2h / CC: ~15min)** — perf — 200,000-row generated review: time per broker action recorded; indexes on claim_row
  - Surfaced by: Performance
  - Files: backend/tests/test_loss_perf.py, backend/models.py
  - Verify: pytest -k loss_perf
- [ ] **T11 (P3, carried)** — diff — Reuse verify value helpers (deferred from slice 3)

## Completion summary
- Step 0: Scope Challenge — scope accepted as-is (structure B, smaller arrangement)
- Reality rows: 7/7 applicable rows emitted (PLAN_REALITY below), 0 unverified premises
- Architecture Review: 6 issues found (R1 to R5, queue list)
- Code Quality Review: 2 issues found (R7, AMS screening reuse)
- Test Review: diagram produced, 27 gaps identified (all new code)
- Performance Review: 1 issue found (ledger rebuild time, measure)
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 0 items proposed to user
- Failure modes: 0 critical gaps flagged
- Unresolved decisions: 0 in this review
- Outside voice: Codex (gpt-6-astra), completed, 7 findings, all applied (D7 to D13)
- Parallelization: 4 lanes, 2 parallel (A, B) / 2 sequential (C, D)
- Lake Score: 12/12 answers picked the 10/10 option

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | Codex (gpt-6-astra) via `/plan-eng-review` | Independent 2nd opinion | 1 | completed | 7 findings, all applied (D7 to D13) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES OPEN (mapped to T1 to T13) | 36 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** Codex, plan-review phase, completed, 7 findings (6 High, 1 Medium), all verified in code and applied.
- **CROSS-MODEL:** Codex found what the native review missed in the remedies themselves (column swap past R6, row identity vs R4, printed totals vs the fact path, effective values vs R1, retry budget, cache contents, sheet chips); no disagreement left.
- **VERDICT:** Eng review complete, all findings decided and mapped to tasks; ready for lanes A + B. UI lane C runs the design stack.

NO UNRESOLVED DECISIONS
