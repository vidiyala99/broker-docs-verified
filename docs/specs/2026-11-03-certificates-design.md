# Slice 4: Checked certificates (COI)

Date: 2026-11-03. Status: owner-approved design (brainstorm sections 1 to 4), spec awaiting owner review.
Next: `/plan-eng-review`, then lanes. Rail item: Certificates (the last disabled item this slice makes live).
Evidence: `docs/reference/brokerdocs-workflows-evidence.md` section 3; `docs/slice-map.md`; PRODUCT.md.

## 1. Purpose and decisions

A broker issues a certificate of insurance when a client's counterparty (landlord, general contractor, lender)
asks for proof of coverage. The certificate is where brokers carry E&O exposure: it is easy to state coverage the
policies do not give (additional insured with no endorsement, a policy that expired). This slice proves the
engine's strength, verification, on that risk: every field cited, every claim checked before issue.

Owner decisions (2026-11-03, do not re-ask):
- C1 Purpose: a **checked** certificate (fill with citations, and check each claim against the policies before
  issue). Not fill-only; holder contract requirements (gap list) are out of scope.
- C2 Holder: typed on the certificate, remembered per client and offered as picks next time. No holder directory page.
- C3 Policies: upload, or pick documents already in the app for that client; already-read documents are reused.
- C4 Failed check: Issue is blocked until each failing claim is fixed (add document), removed from the certificate,
  or overridden with a typed reason saved on the issued record.
- C5 Approach: a new workflow on the existing run engine; code fills fields and runs checks (no model call after
  extraction); layout is a data field map so ACORD 25 can later be a second map.
- C6 Own one-page layout first (ACORD blanks are copyrighted; no licensed blank).
- C7 One declarations document = one line of coverage this slice; package policies are flagged unsupported.
- C8 Extraction cache applies to every workflow, but only for the same file (sha256) and the same schema version.

Out of scope: email sending, batch certificates for many holders, expiry tracking/reminders, ACORD 25 or other
ACORD forms, holder contract requirement checks, package policies, CRM/client entities.

## 2. Flow and states

1. **Start** from the Certificates rail item or a client's queue row menu ("Issue certificate", prefills client).
   Inputs: client (typed, or from the row); holder name and address (the client's past holders first, or new);
   requested wording checkboxes: additional insured, waiver of subrogation, primary and non-contributory;
   optional description of operations (free text); certificate date (default today).
2. **Policies**: upload declarations and endorsement PDFs, or pick the client's documents (proposal wizard picker,
   deduped by sha256). Cached documents skip the read; new ones are read with the reading mark.
3. **Draft**: certificate layout with a citation chip on every filled field; beside it the **check list**, one row
   per claim: Supported (cited page) or Not supported (reason). Rows: each requested wording item; automatic
   checks (each shown policy in force on the certificate date; named insured consistent across policies).
4. **Resolve** each Not supported row: Add document (read, then re-check), Remove from certificate, or Override
   with a required reason.
5. **Issue** is enabled only when every row is Supported, Removed or Overridden; the button shows the remaining
   count when disabled ("2 checks to resolve"). Issuing freezes values, override reasons, holder and PDF, and
   saves the holder to the client's list. Issued certificates are read-only; "New version" starts an edited copy.
6. **Queue**: draft with unresolved rows = Needs attention; draft with all rows handled = Ready ("Ready to issue");
   issued = Done recently. Certificate items appear on the Certificates page queue only; the main work queue
   (`#/`, Personal lines) does not list them, so certificate volume does not bury comparisons and checks
   [assumed in the spec, owner may veto]. Activity lists them like any run.

## 3. Data model and extraction

**Workflow** `certificate` (`Workflow.input_slots`): one slot `policies`, 1 to 8 PDFs. A document is a policy's
declarations or an endorsement. Endorsements join their policy in code by policy number.

**Extraction schema** (`cert:` keys, one combined pass like Policy Check's `form:<n>`; existing windows, verify,
merge pipeline):
- Per policy document: named insured, insured address, carrier, NAIC, policy number, line (general liability,
  auto, umbrella, workers comp, property, other), effective date, expiration date.
- Limits by line: GL each occurrence, general aggregate, products-completed operations aggregate, personal and
  advertising injury, damage to rented premises, medical expense; auto combined single limit; umbrella each
  occurrence and aggregate; WC statutory, employer's liability each accident, disease each employee, disease
  policy limit.
- Endorsements (per form): form number, type (additional insured blanket / scheduled with named party; waiver of
  subrogation; primary and non-contributory), policy number it modifies.
- A document stating several lines (package policy) gets a document-level notice and contributes no facts.
- A value without a verified citation is unverified, conflicting or missing, never guessed (PRODUCT.md).

**Extraction cache** (closes TODO 2c): before reading a document for a run, find the latest finished run whose
facts came from the same `document.sha256` with the same schema fingerprint (field set + prompt version). If
found, copy those facts (with state, quotes, rects, passages) into the new run and mark the run document
`reused_from_run_id`; otherwise read normally. Applies to every workflow; schema mismatch is always a miss.

**New tables** (fail-fast schema check extended per D10):
- `holder`: id, workspace_id, client_key (normalized client name), name, address, created_at, last_used_at.
- `certificate_claim`: run_id, claim_key, status (supported, not_supported, removed, overridden), reason,
  fingerprint, resolved_fingerprint, updated_at. A changed fingerprint reopens a removed/overridden claim
  (DiscrepancyState rule).
- `certificate_issue`: run_id, artifact_id, version, issued_at, holder snapshot (JSON), claims snapshot with
  override reasons (JSON), pdf path. Unique (artifact_id, version).

Draft certificate = an artifact version (`CertificateBlock` in the `Block` union), so edit/undo/versions reuse the
artifact machinery. Field map (data): `{field, source fact, format}` rows for the own layout.

**Checks** (`pipeline/coi_checks.py`, deterministic, no model call):
- Requested additional insured / waiver / primary and non-contributory: needs a matching endorsement type joined
  to a shown policy, with a verified citation. Scheduled AI must name the holder (normalized compare); blanket
  passes with its form cited.
- In force: each shown policy's effective <= certificate date <= expiration, both dates verified.
- Insured consistency: named insured matches across shown policies (case/space-insensitive, as the queue's rule).
- Limits print only when verified; otherwise the field prints "Not stated" (not a claim row, a visible gap).

## 4. Screens (look via the design stack; this section is content and behaviour only)

- **Certificates page** (`#/certificates`): certificate queue (Needs attention, Ready to issue, Done recently) with
  the work-queue row pattern (serif client, one next action), and "New certificate".
- **Wizard** (3 steps, like Proposal/Policy Check): Who (client, holder picks, wording, date, description);
  Policies (upload or pick; "already read" marker on cached ones); Read (progress, then the draft).
- **Draft view** (run page): certificate with chips (open the cited page side by side); check list with per-row
  actions (Add document, Remove from certificate, Override... dialog with required reason); header with client,
  holder, and Issue certificate (disabled with count).
- **Issued view**: read-only, "Issued <date>", override reasons on the record (not printed), Download PDF,
  New version.
- **Hooks**: queue row overflow "Issue certificate"; Activity lists certificate runs.
- **PDF**: own one-page layout in the Paper trail voice: producer, insured, insurers A to F with NAIC, coverage
  table, description of operations, holder, cancellation clause; missing values print "Not stated". Generated in
  code from the frozen values.

## 5. Errors and edge cases

- Document fails to read: "Not read" + Retry; dependent claims are Not supported.
- Package policy: document notice "This document covers several lines; not supported yet"; no facts used.
- Endorsement whose policy number matches no shown policy: notice naming the form and the number.
- Conflicting values (e.g. two expiration dates): dependent claim Not supported until resolved or overridden.
- Adding a document or editing a value re-runs checks; changed fingerprints reopen removed/overridden rows.
- Issue is atomic (PDF + record in one transaction, PDF written to a temp path then moved); a second Issue on the
  same version returns the existing issue (idempotent), never a duplicate.
- Cached document rows say "read on <date>".

## 6. Testing and eval

- Backend unit: each check type (pass, fail, conflicting, unverified); endorsement join (match, no match);
  scheduled vs blanket AI; in-force boundaries (effective day, expiration day); insured mismatch; cache hit,
  schema-mismatch miss, verified state copied; claim fingerprint reopen; issue freeze + idempotency; holder save
  and reuse per client_key.
- e2e: wizard to draft, resolve by add/remove/override, issue, download, new version; past-holder pick; cached
  document marker; queue row "Issue certificate"; axe 0 at 1440, 1024, 390.
- Fixtures (synthetic generator, `tests/fixtures/certificates/`): GL, auto, umbrella, WC declarations; AI scheduled
  and blanket, waiver, primary non-contributory endorsements; traps: expired policy, AI endorsement for another
  policy number, one scanned document, one package policy.
- Eval gate (live, `-k certificate`): every claim verdict vs truth; 0 hard fails (hard fail = Supported when the
  truth is not supported); recall on not-supported claims >= 0.90; field accuracy reported per field.

## 7. Build order (model policy: Opus for pipeline, migrations, checks, design)

1. Backend lane (Opus): workflow + slot, `cert:` schema, extraction cache, tables + schema check, checks module,
   claim state API, issue + PDF, holder API, queue classification, API.md.
2. Fixtures + eval lane (Opus): generator, truth sets, offline tests, live gate.
3. UI lane (Opus, full design stack, Playwright MCP, max 3 rounds).
4. Docs (Sonnet): DESIGN.md, slice map, HANDOFF.

## 8. Related, not in this slice

"Clicks to outcome" benchmark (owner question 2026-11-03): scripted shortest paths for core tasks with click,
keystroke and screen counts against a written manual baseline. Offered as its own item after this spec.
