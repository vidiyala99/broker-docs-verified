# TODOS

## Chunked extraction for long documents
- **What:** split documents over the 80-page cap into page windows, extract per window, merge and de-duplicate facts.
- **Why:** policies (slice 2) are long; slice 1 hard caps (D3) reject them.
- **Pros:** removes the page cap. **Cons:** merge conflicts between windows, more pipeline code.
- **Context:** D3 in docs/specs/2026-11-01-brokerdocs-core-design.md chose hard caps for slice 1.
- **Depends on:** slice 1 pipeline.
- **Status:** done in T14 (backend/pipeline/windows.py, merge.py): 15-page windows with 2 pages of overlap, caps raised to 300 pages and 50 MB, 1.5M run budget with a 400k per-document ceiling. Open follow-up: the page-scoring skip pass (off; extension point `windows.select_windows`).

## Claim-level validation of report prose
- **What:** check each prose claim (for example "Carrier A is cheaper than B") against the facts it cites.
- **Why:** the number check cannot catch reversed meanings or numbers reassigned between carriers (outside voice finding 1).
- **Pros:** closes the largest remaining prose-trust gap. **Cons:** needs a second model or rules plus an eval set.
- **Context:** M2 and D2 cover quotes and numbers only.
- **Depends on:** slice 1 compose step.

## Local OCR for independent scan verification
- **What:** Tesseract-based OCR with word boxes so scanned citations are verified independently of the model.
- **Why:** D5 left scanned chips labeled unverified.
- **Pros:** scanned citations reach digital-PDF trust. **Cons:** needs a Tesseract install; lower accuracy on messy scans.
- **Context:** parse returns optional word boxes, so this slots in behind the same seam.
- **Depends on:** slice 1 parse module.

## Cross-reference long fixture and targeted second pass
- **What:** add a long fixture where a value is defined by reference (page 12 says "see Schedule C", Schedule C is on page 250) and measure it; if it fails, add a bounded second pass for null or pointer fields (keyword search on the field label and synonyms, one more extract call over the top 10 to 15 pages, verification unchanged).
- **Why:** windows are read independently, so references across windows are not followed (known gap, not yet measured).
- **Context:** needed before Policy Check and Coverage Gap workflows. Not retrieval for everything, only a fallback for fields that need it.
- **Depends on:** slice 1 pipeline (windows.py, merge.py).

## Coverage identity for multi-location and multi-schedule policies
- **What:** give scalar coverage keys (`coverage_a_limit`, `deductible_wind_hail`, ...) a location or schedule scope, so a policy with several insured locations or coverage schedules aligns correctly across expiring and renewal.
- **Why:** slice 3 Policy Check (D17 of the eng review) supports single-location policies only; with several locations the diff cannot tell which Coverage A limit is which.
- **Pros:** correct comparisons on real commercial and multi-property policies. **Cons:** extraction must emit a scope per fact, and alignment gets a second dimension.
- **Context:** `docs/specs/2026-11-02-policy-check-design.md` section 8 and the Engineering review section. Related: the cross-reference TODO above (long policies).
- **Depends on:** slice 3 Policy Check diff engine (`pipeline/diff.py`).

## Policy Check residuals (slice 3 backend, 2026-11-02)
- **What:** known gaps left after the slice 3 backend merge (HANDOFF.md, 2026-11-02 19:50 entry).
- **reading_text limits:** `parse.reading_text` rejoins label and value columns row by row, but it does not rejoin word-only value columns (a value that is words, not a number or date), wrapped labels, or tables with three or more columns. A schedule laid out that way can still be read values-then-labels.
- **Duplicate form editions:** when one extraction call states the same form number twice with different editions, the diff keeps the first.
- **long_300 ceiling:** the long_300 fixture plans 467k tokens of the 500k per-document ceiling once the forms schema is added; a longer policy would hit the ceiling.
- **Scanned retention miss:** `carrier_c_scanned.retention` came back `not_found` once in the 19:53 live eval (Compare Quotes, scanned bucket, otherwise 0.983). A one-off so far; watch it in the next live run before acting.
- **Context:** `docs/specs/2026-11-02-policy-check-design.md`, "Amendments after build". Multi-location policies are the separate "Coverage identity" entry above.
- **Depends on:** slice 3 Policy Check backend.

## Run-list paging past 100 runs (slice 3b, D5/D11)
- **What:** add `offset` paging to `GET /api/runs` and a "Load more" control on Personal lines, Workflows' recent runs and the task run picker.
- **Why:** slice 3b shows the newest 100 runs only (`routes/runs.py:76` caps `limit` at 100; eng review D5). Older runs drop off those lists silently.
- **Pros:** every run stays reachable. **Cons:** one API parameter, three UI controls and their tests, for a case the demo does not reach yet.
- **Context:** `docs/specs/2026-11-02-rail-completion-design.md`, decision ledger SC6/D5. Upgrade when a workspace passes 100 runs.
- **Depends on:** slice 3b merged.
