# BrokerDocs, Slice 3: Policy Check

Date: 2026-11-02. Status: draft for owner review. Builds on
`2026-11-01-brokerdocs-core-design.md` (slice 1), `2026-11-02-proposal-builder-design.md`
(slice 2) and the evidence in `docs/reference/brokerdocs-workflows-evidence.md` section 2.

## 1. Intent

Outcome: a broker uploads an expiring policy, a renewal, optionally a quote and an AMS export,
and gets a list of discrepancies. Every value shown in a comparison slot is a verified quote
with a citation, and every severity comes from a fixed rule table.

Audience: the owner, as a showcase of the verified-citation engine on a second real workflow.
Single-user local app. Not a pixel match of the original.

Aim chosen by the owner: citation-engine showcase (not visual fidelity, not workflow breadth).

Success criteria:
- Every cited value in every slot passes verification; N/A is shown when a role has no value, never a guess.
- Diff and severity are deterministic code. A model writes only title and explanation prose.
- Live eval gate: 0 hard fails (section 7).

Decisions already made (do not re-ask):
- Four roles: `expiring`, `quote`, `renewal`, `ams`. `expiring` and `renewal` required.
- AMS is an uploaded JSON or CSV file, cited by field name.
- Severity is a rule table in code, tiers Critical, High, Medium, Low.
- Approach A: a seeded `policy-check` workflow through the existing run pipeline, per-role facts, code-first diff.
  Not a standalone subsystem, not derived from Compare Quotes.

Out of scope (not observable in the evidence): Summary tab, Document Verification tab, Email Carrier,
Update AMS write-back, "Accept AI recommendations".

## 2. Roles and inputs

`POST /api/runs` with `workflow: "policy-check"` and `parameters.roles`, a map `document_id -> role`.
- `expiring` and `renewal` required; `quote` and `ams` optional; at most one document per role.
- Otherwise `400 invalid_run`. A missing optional role renders as N/A in every slot.
- AMS file (JSON flat key-value, or CSV with a header row) is parsed to pseudo-pages: one passage per
  field, text `field_name: value`. Existing size caps apply. Verify runs unchanged against these
  passages; a citation chip reads "AMS · field_name" instead of a page number.
- The AMS file is untrusted input: field names and values are capped in length and count, and are
  treated like document text (injection screening applies).

## 3. Facts and diff

Every document goes through the same `policy-check` fact schema: policy number and term, named insured
and address, coverage limits (A, B, C, D, ...), deductibles, premium, discounts, forms and endorsements
(form number, edition date), special provisions. Each fact carries a verified quote and page.
Merge runs inside a document only (windows logic unchanged). No merge across roles.

`pipeline/diff.py` (pure code):
1. Align facts across roles by canonical key (`coverage_a_limit`, `deductible_wind_hail`, ...). Forms align by
   form number, so an edition change is a change, not an add plus a remove.
2. Normalize values (money, percent, date, text). Raise a discrepancy when two present values differ, or a
   value is present in one role and absent in another.
3. Each discrepancy carries its slots (value or N/A, plus citations to verified quotes).
4. Severity table, keyed by fact type and change size. Starting values, to be tuned in review:
   - Critical: coverage limit down more than 10%, deductible increase.
   - High: coverage limit change under 10%, discount dropped, coverage removed.
   - Medium: form edition change, special-provision text change.
   - Low: formatting-only or non-material differences, or suppressed when equal after normalization.
5. Output ordered by severity, then key. Same input gives byte-identical output.

Compose writes title and explanation for each item from its slots only. The existing validator rejects any
number or passage id not in the item's facts. No-AI-prose mode uses template text and needs no model call.

## 4. Storage

- Run artifact: discrepancy list as JSON, items `{id, key, title, severity, lob, prose, slots, citations}`.
  Item id is derived from the key so it is stable across regeneration.
- New table `discrepancy_state(run_id, item_id, status, note, updated_at)`, status `open | resolved | accepted`.
  Separate from the artifact so a regenerate never wipes decisions. If an item's slot values change after a
  regenerate, it resets to `open` and is flagged "changed since resolved".
- No migration framework: the table is created at startup; delete old local DBs.

## 5. API (to be added to `backend/API.md`)

- `POST /api/runs` as in section 2.
- `GET /api/runs/{id}/discrepancies` with `?severity=` and `?hide_resolved=`; returns items and counts per severity.
- `PATCH /api/runs/{id}/discrepancies/{item_id}` sets status and note.
- `POST /api/runs/{id}/discrepancies/bulk` sets a status for a severity filter ("Apply to filtered", "Clear all").

## 6. UI scope (built through the design stack, not decided here)

- Wizard step to assign roles to uploaded documents, including the AMS file.
- Discrepancy list: severity chips with counts, Hide Resolved toggle, cards with the four-slot comparison row
  and citation chips, resolve control.
- One-at-a-time review modal (Previous/Next, "Discrepancy 3 of 14") opening the cited source side by side:
  PDF at the page, AMS citation highlighting the field.
- Design gate applies: DESIGN.md and tokens.css are the contract; max 3 review rounds.

## 7. Eval and tests

Fixtures: an expiring/quote/renewal policy set built with the existing fixture generator, plus an AMS JSON,
with a seeded discrepancy ground truth.

Hard fails (any one fails the gate):
- a cited value that fails verification;
- a discrepancy with a missing or wrong-page citation;
- a severity that violates the table;
- an injection canary verified.

Soft metrics: precision and recall of discrepancies against ground truth; accuracy of the four slots.
Offline tests: diff engine, severity table, AMS parser (including malformed and oversized input), state table,
role validation, regenerate keeps resolve state.

## 8. Risks and open questions

- Canonical key alignment is the hardest part: the same coverage can be worded differently across carriers and
  roles. Mitigation: extraction emits the canonical key from a closed list; unknown keys go to a "Other" bucket
  with Low severity and are never dropped silently.
- Severity thresholds are starting values with no external evidence; tune against the fixtures.
- The citation shape for non-PDF sources was never observed in the original; ours is defined here (field name).
- Fixture realism: synthetic policies may under-test real-world layout variation. Reuse the multi-column and
  scanned fixtures where possible.
