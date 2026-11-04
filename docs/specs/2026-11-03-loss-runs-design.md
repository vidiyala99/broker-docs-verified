# Slice 5a: Loss runs (checked claims table)

Date: 2026-11-03. Status: owner-approved design (brainstorm sections 1 to 5), spec awaiting owner review.
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
