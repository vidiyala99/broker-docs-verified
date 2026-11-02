# TODOS

## Chunked extraction for long documents
- **What:** split documents over the 80-page cap into page windows, extract per window, merge and de-duplicate facts.
- **Why:** policies (slice 2) are long; slice 1 hard caps (D3) reject them.
- **Pros:** removes the page cap. **Cons:** merge conflicts between windows, more pipeline code.
- **Context:** D3 in docs/specs/2026-11-01-brokerdocs-core-design.md chose hard caps for slice 1.
- **Depends on:** slice 1 pipeline.

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
