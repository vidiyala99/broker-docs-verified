"""Loss-run eval gate (slice 5a, task T7; spec docs/specs/2026-11-03-loss-runs-design.md section 8 and the
decision ledger R1 to R8, O1 to O7, D1 to D13, which overrides sections 1 to 8).

One case is one loss review: one client and that client's loss-run files (PDF, .xlsx, .csv). The truth
(``tests/fixtures/loss_runs/``) is written first as claim lines, then rendered into carrier files, so every
amount, location, role, total and check is exact:

* per document (``<fixture>.truth.json``): every printed claim line (location, claim number, amounts in
  integer cents, status, the verify state the row must end in), every printed total with its scope, the
  document facts (carrier, valuation date), the sheets and their column mapping, decoys (injection canary);
* per case (``<case>.case.json``): the role of every line (counted, superseded, conflicting), the claims,
  the totals strip, every reconciliation check with its verdict, and the notices.

Gate (spec section 8, ledger O1/R4):
  * hard fails == 0, where a hard fail is
      - ``wrong_amount_counted``: a verified, counted row whose amounts differ from its printed line
        (includes a swapped paid/reserve, O1), or a verified, counted row that matches no printed line;
      - ``unverified_counted``: a row the truth says must stay unverified (scan, formula not calculated)
        reported verified and counted, or totals that include an unverified row;
      - ``wrong_reconciliation_verdict``: a printed total checked with the wrong verdict, or a mismatching
        printed total with no check at all;
      - ``wrong_role``: a supersede / conflict role different from the truth (L5, R4: lines of one claim in
        one file are summed per file before the cross-file rule);
      - ``claims_merged``: two truth claims reported under one claim key (leading zeros, two carriers);
      - ``canary_followed``: a row or value that the injection text asked for;
  * spreadsheet rows (code path) all correct: 1.000;
  * PDF row recall >= 0.95;
  * amount accuracy on verified rows >= 0.99;
  * a case with no rows fails (no data is not a pass).
Totals, notices, statuses and document facts are reported. Input tokens per document are gated by
LOSS_RUN_DOC_TOKEN_BUDGET, set from the measured live run of 2026-11-04 (L8).

BACKEND VOCABULARY AND ADAPTER CONTRACT, reconciled with lane A's backend (``backend/loss_runs/``, main cf45e13)
and kept in one block. ``test_loss_run_eval`` compares it with the backend.

    run_eval_batch(paths, workflow="loss-run" (or "loss_run"), parameters={"client_name": str})
    -> {"input_tokens": int, "documents": {path: {...}},       # the Compare Quotes dict
        "loss_run": {
          "workflow": "loss_run", "run_status": str, "warnings": [str],
          "documents": {path: {"status": "ok" | "failed", "failure_reason": str | None,
                               "carrier": str | None, "valuation_date": "YYYY-MM-DD" | None,
                               "input_tokens": int | None}},
          "rows": [{"document": path,
                    "location": {"page": int} | {"sheet": str | None, "row": int},   # O2 printed location
                    "claim_number": str, "claim_key": str, "carrier": str | None,
                    "policy_number": str | None, "policy_year": int | None, "line": str | None,
                    "claimant": str | None, "date_of_loss": "YYYY-MM-DD" | None,
                    "status_raw": str | None, "status": "open" | "closed" | "reopened" | "unknown",
                    "paid": int | None, "reserve": int | None, "incurred": int | None,   # cents, effective
                    "recovery": int | None, "deductible": int | None,
                    "incurred_computed": bool,
                    "verify": "verified" | "unverified", "unverified_reason": str | None,
                    "role": "counted" | "superseded" | "conflicting" | "left_out",
                    "warnings": [str]}],
          "totals": {"claims": int, "open": int, "paid": int, "reserve": int, "incurred": int,
                     "recovery": int, "deductible": int},
          "checks": [{"document": path, "metric": str, "scope_kind": str, "scope_value": str | None,
                      "printed": int, "computed": int, "difference": int,
                      "verdict": "matches" | "off" | "needs_review"}],
              # needs_review: the backend could not check the total (fail safe). Truth is only ever
              # matches/off, so the gate scores needs_review as a wrong verdict: it is safe for the
              # broker but not a pass; a missing check for an "off" total is a hard fail as before.
          "notices": [{"kind": str, "document": path | None, "sheet": str | None, "message": str}]}}

``rows`` are every extracted row of the run (all roles, before any broker decision; the paged claims API
of R1 serves the same rows); ``totals`` is the totals strip (rows counted and verified, O4 effective values).
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import eval_gates as g

# =============================================================================================
# BACKEND VOCABULARY (reconciled with lane A at main cf45e13; one place on purpose)
# =============================================================================================

LOSS_RUN_SLUG = "loss-run"  # Workflow.slug (backend/slugs.py); the eval hook also accepts "loss_run"
GATE_BUCKET = "loss_run"

METRICS = ("paid", "reserve", "incurred", "recovery", "deductible")  # claim_row money columns, cents
ROLES = ("counted", "superseded", "conflicting", "left_out")
STATUSES = ("open", "closed", "reopened", "unknown")
VERIFY_STATES = ("verified", "unverified")
SCOPE_KINDS = ("all", "policy_year", "policy_number", "carrier", "page_subtotal", "sheet")  # O3 printed_total
VERDICTS = ("matches", "off")
TOLERANCE_CENTS = 100  # L6: a difference of $1.00 or less matches

# Section 4 step 4: status map as data. Unknown spellings stay raw with status "unknown".
STATUS_MAP = {
    "o": "open", "open": "open", "opened": "open",
    "c": "closed", "closed": "closed",
    "re-opened": "reopened", "reopened": "reopened", "reopen": "reopened", "ro": "reopened",
}

WARN_INCURRED = "incurred_not_paid_plus_reserve"  # section 4 step 4
WARN_REPEATED = "repeated_line"  # D5: lines identical in every field

UNVERIFIED_SCAN = "scan"  # pages with no word boxes keep the unverified rule (R6)
UNVERIFIED_FORMULA = "formula not calculated"  # R7

NOTICE_NO_TOTALS = "no_printed_totals"  # "<carrier> 2025 prints no totals: nothing to reconcile"
NOTICE_NO_VALUATION = "no_valuation_date"  # "No valuation date found in File C"
NOTICE_NO_TABLE = "no_claims_table"  # "No claims table found on sheet 2023"
NOTICE_UNVERIFIED = "rows_not_verified"  # one item per document for its unverified rows
NOTICE_NOT_READ = "document_not_read"  # a file that could not be read (reason + Retry)
NOTICE_INFO = "info"  # any other notice the report shows
NOTICE_KINDS = (NOTICE_NO_TOTALS, NOTICE_NO_VALUATION, NOTICE_NO_TABLE, NOTICE_UNVERIFIED, NOTICE_NOT_READ, NOTICE_INFO)
UNMAPPED = "unmapped"  # header mapping answer for a header the model cannot map (never null)

DOC_FACT_KEYS = ("lr:carrier", "lr:valuation_date")  # other lr: document facts (O3 keeps them)

# Canonical sheet columns the header synonym table maps to (backend/loss_runs/headers.py, L7).
COLUMNS = ("claim_number", "policy_number", "policy_year", "line", "carrier", "claimant", "date_of_loss",
           "status", "paid", "reserve", "incurred", "recovery", "deductible")

# =============================================================================================
# Gate thresholds (spec section 8)
# =============================================================================================

HARD_FAIL_MAX = 0
SHEET_ROW_ACCURACY_MIN = 1.0
PDF_ROW_RECALL_MIN = 0.95
VERIFIED_AMOUNT_ACCURACY_MIN = 0.99

# L8, measured: the live run of 2026-11-04 (recorded/loss_run_live_2026-11-04.json) read the 120-page file
# with 382,855 input tokens (~3.2k per page, 119 two-page windows, each page read twice by the overlap); the
# lr_main files are far smaller. 500,000 leaves ~30% headroom for reply variance and split-and-retry on a
# cut-off window (a split re-reads its pages) while still catching a regression that re-reads pages or bloats
# the prompt (+30% on the largest fixture). It is the gate for the fixtures, not the product cap: the
# per-document runtime budget stays 1.2M (backend), so a real 300-page file is not refused by this number.
LOSS_RUN_DOC_TOKEN_BUDGET: int | None = 500_000

HARD_FAIL_KINDS = ("wrong_amount_counted", "unverified_counted", "wrong_reconciliation_verdict", "wrong_role",
                   "claims_merged", "canary_followed")


# =============================================================================================
# Normalization (the spec's rules, re-derived here so the truth can be checked)
# =============================================================================================


def claim_key(number: str | None) -> str:
    """Section 4 step 4: case and whitespace only, no leading-zero stripping."""
    return "".join(str(number or "").split()).upper()


def status_of(raw: str | None) -> str:
    return STATUS_MAP.get(" ".join(str(raw or "").split()).lower(), "unknown")


def parse_money(text: Any) -> int | None:
    """Printed money -> integer cents: ``$``, commas, ``(1,200.00)`` and ``-1,200.00`` negatives, ``-`` zero.
    Numbers (spreadsheet cells) go through Decimal(str(value)), rounded half-up (R7)."""
    if text is None:
        return None
    if isinstance(text, bool):
        raise ValueError(f"not money: {text!r}")
    if isinstance(text, (int, float, Decimal)):
        return int((Decimal(str(text)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    s = str(text).strip().replace("$", "").replace(",", "").replace(" ", "")
    if s == "":
        return None
    if s in ("-", "--"):
        return 0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    try:
        v = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"not money: {text!r}") from exc
    cents = int((v * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return -cents if neg else cents


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(value: Any) -> date | None:
    """ISO, M/D/YYYY, D-Mon-YYYY and D Mon YYYY (the printed forms the fixtures use)."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return date(int(m[3]), int(m[1]), int(m[2]))
    m = re.fullmatch(r"(\d{1,2})[- ]([A-Za-z]{3})[a-z]*[- ,]+(\d{4})", s)
    if m and m[2].lower() in _MONTHS:
        return date(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))
    return None


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def norm_path(p: str | None) -> str:
    return str(Path(p)).lower() if p else ""


def location_key(loc: Mapping | None) -> tuple:
    if not loc:
        return ("?",)
    if loc.get("page") is not None:
        return ("page", int(loc["page"]))
    return ("sheet", loc.get("sheet"), int(loc["row"]) if loc.get("row") is not None else None)


def scope_value_key(kind: str, value: Any) -> str | None:
    if kind in ("all", "carrier") or value is None:
        return None if kind == "all" else (" ".join(str(value).split()).lower() if value is not None else None)
    if kind == "policy_number":
        return claim_key(value)
    return str(int(value)) if str(value).strip().isdigit() else str(value).strip()


# =============================================================================================
# Reference ledger (the spec's rules over the truth lines; the truth must agree with it)
# =============================================================================================


def _amounts(line: Mapping) -> dict[str, int | None]:
    return {m: line.get(m) for m in METRICS}


def doc_claim_sums(lines: Iterable[Mapping]) -> dict[str, int | None]:
    """R4/D5: the claim's amounts in one document = the sum of its lines (None when no line gives it)."""
    out: dict[str, int | None] = {m: None for m in METRICS}
    for ln in lines:
        for m in METRICS:
            v = ln.get(m)
            if v is not None:
                out[m] = (out[m] or 0) + v
    return out


def sums_equal(a: Mapping, b: Mapping) -> bool:
    """Amounts compared on the columns both files give (lane choice: a column one file lacks is not a
    difference)."""
    shared = [m for m in METRICS if a.get(m) is not None and b.get(m) is not None]
    return bool(shared) and all(a[m] == b[m] for m in shared)


def _ident(line: Mapping) -> tuple:
    return (line["claim_key"], line.get("policy_number"), line.get("claimant"),
            line.get("date_of_loss"), line.get("status_raw"), *(line.get(m) for m in METRICS))


def reference_ledger(case: Mapping, doc_truths: Mapping[str, Mapping]) -> dict:
    """Roles per line, claims, totals strip, checks and notices from the document truths, by the rules of
    section 4 (steps 4 to 6) with the ledger's D5 (lines of one claim summed per file)."""
    docs = [doc_truths[f] for f in case["documents"]]
    roles: dict[str, str] = {}
    warnings: dict[str, list[str]] = defaultdict(list)
    groups: dict[tuple[str, str], dict[str, list[Mapping]]] = defaultdict(lambda: defaultdict(list))
    for d in docs:
        seen: dict[tuple, list[str]] = defaultdict(list)
        for ln in d["lines"]:
            groups[(d["carrier"], ln["claim_key"])][d["fixture_id"]].append(ln)
            inc, paid, res = ln.get("incurred"), ln.get("paid"), ln.get("reserve")
            if (ln.get("incurred_source") == "printed" and None not in (inc, paid, res)
                    and abs(inc - (paid + res)) > 1):
                warnings[ln["line_id"]].append(WARN_INCURRED)
            seen[_ident(ln)].append(ln["line_id"])
        for ids in seen.values():
            if len(ids) > 1:
                for i in ids:
                    warnings[i].append(WARN_REPEATED)
    valuation = {d["fixture_id"]: parse_date(d.get("valuation_date")) for d in docs}
    order = {d["fixture_id"]: i for i, d in enumerate(docs)}
    claims = []
    for (carrier, key), by_doc in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        fids = sorted(by_doc, key=order.get)
        sums = {f: doc_claim_sums(by_doc[f]) for f in fids}
        dated = [f for f in fids if valuation[f] is not None]
        pool = dated or fids  # no dated file: the undated files compete among themselves
        latest = max(valuation[f] for f in dated) if dated else None
        top = [f for f in pool if valuation[f] == latest]
        role_of: dict[str, str] = {f: "superseded" for f in fids}
        state = "counted"
        if len(top) == 1:
            role_of[top[0]] = "counted"
        elif all(sums_equal(sums[top[0]], sums[f]) for f in top[1:]):
            role_of[top[0]] = "counted"  # same valuation, same amounts: one counted (first file)
            state = "duplicate_same_valuation"
        else:
            for f in top:
                role_of[f] = "conflicting"
            state = "conflicting"
        for f in fids:
            for ln in by_doc[f]:
                roles[ln["line_id"]] = role_of[f]
        counted = next((f for f in fids if role_of[f] == "counted"), None)
        claims.append({"carrier": carrier, "claim_key": key, "documents": fids, "state": state,
                       "counted_document": counted,
                       "tie_documents": top if state == "duplicate_same_valuation" else [],
                       "roles": {f: role_of[f] for f in fids},
                       "lines": {f: len(by_doc[f]) for f in fids},
                       "document_sums": sums})
    totals = totals_from_lines(
        [{**ln, "carrier": d["carrier"], "role": roles[ln["line_id"]]} for d in docs for ln in d["lines"]])
    checks = [reference_check(d, t) for d in docs for t in d["printed_totals"]]
    return {"roles": roles, "warnings": {k: sorted(set(v)) for k, v in warnings.items()}, "claims": claims,
            "totals": totals, "checks": checks, "notices": reference_notices(docs)}


def totals_from_lines(lines: Iterable[Mapping]) -> dict[str, int]:
    """The totals strip: rows counted and verified (an unverified row is excluded until confirmed)."""
    out = {"claims": 0, "open": 0, **{m: 0 for m in METRICS}}
    claims: set[tuple] = set()
    open_claims: set[tuple] = set()
    for ln in lines:
        if ln.get("role") != "counted" or ln.get("verify") != "verified":
            continue
        k = (ln.get("carrier"), ln["claim_key"])
        claims.add(k)
        if ln.get("status") in ("open", "reopened"):
            open_claims.add(k)
        for m in METRICS:
            out[m] += ln.get(m) or 0
    out["claims"], out["open"] = len(claims), len(open_claims)
    return out


def _in_scope(line: Mapping, kind: str, value: Any) -> bool:
    if kind in ("all", "carrier"):
        return True
    if kind == "policy_year":
        return line.get("policy_year") is not None and str(line["policy_year"]) == str(value)
    if kind == "policy_number":
        return claim_key(line.get("policy_number")) == claim_key(value)
    if kind == "page_subtotal":
        return (line.get("location") or {}).get("page") == int(value)
    raise ValueError(f"unknown scope kind {kind!r}")


def reference_check(doc: Mapping, total: Mapping) -> dict:
    """L6/O3: a printed total against that document's own lines in scope (every line, whatever its verify
    state or cross-file role, before broker decisions; lane choice: the printed total describes the whole
    document)."""
    lines = [ln for ln in doc["lines"] if _in_scope(ln, total["scope_kind"], total["scope_value"])]
    computed = sum(ln.get(total["metric"]) or 0 for ln in lines)
    diff = total["amount"] - computed
    return {"total_id": total["total_id"], "fixture_id": doc["fixture_id"], "metric": total["metric"],
            "scope_kind": total["scope_kind"], "scope_value": total["scope_value"], "printed": total["amount"],
            "computed": computed, "difference": diff,
            "verdict": "matches" if abs(diff) <= TOLERANCE_CENTS else "off", "lines_in_scope": len(lines)}


def reference_notices(docs: Iterable[Mapping]) -> list[dict]:
    out = []
    for d in docs:
        fid = d["fixture_id"]
        if d["lines"] and not d["printed_totals"]:
            out.append({"kind": NOTICE_NO_TOTALS, "fixture_id": fid, "sheet": None})
        if d.get("valuation_date") is None:
            out.append({"kind": NOTICE_NO_VALUATION, "fixture_id": fid, "sheet": None})
        for s in d.get("sheets") or []:
            if not s["has_claims_table"]:
                out.append({"kind": NOTICE_NO_TABLE, "fixture_id": fid, "sheet": s["name"]})
        unverified = sum(1 for ln in d["lines"] if ln["verify"] == "unverified")
        if unverified:
            out.append({"kind": NOTICE_UNVERIFIED, "fixture_id": fid, "sheet": None, "rows": unverified})
    return out


# =============================================================================================
# Scoring one case
# =============================================================================================


@dataclass
class LossRunScore:
    case_id: str
    run_status: str | None = None
    rows_output: int = 0
    truth_lines: int = 0
    sheet_lines: int = 0
    sheet_correct: int = 0
    pdf_lines: int = 0
    pdf_found: int = 0
    verified_amount_fields: int = 0
    verified_amount_correct: int = 0
    status_compared: int = 0
    status_correct: int = 0
    hard_fails: list[dict] = field(default_factory=list)
    missed_lines: list[dict] = field(default_factory=list)
    sheet_row_errors: list[dict] = field(default_factory=list)
    unmatched_rows: list[dict] = field(default_factory=list)
    location_mismatches: list[dict] = field(default_factory=list)
    checks_expected: int = 0
    checks_found: int = 0
    checks_right: int = 0
    checks_missing: list[dict] = field(default_factory=list)
    extra_checks: list[dict] = field(default_factory=list)
    notices_expected: int = 0
    notices_found: int = 0
    notices_missing: list[dict] = field(default_factory=list)
    warnings_expected: int = 0
    warnings_found: int = 0
    totals_truth: dict = field(default_factory=dict)
    totals_output: dict | None = None
    totals_match_truth: bool | None = None
    doc_facts_compared: int = 0
    doc_facts_correct: int = 0
    documents_failed: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    doc_tokens: dict[str, int | None] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _hard(sc: LossRunScore, kind: str, detail: str, **extra) -> None:
    sc.hard_fails.append({"case_id": sc.case_id, "kind": kind, "detail": detail, **extra})


def _fid_for(paths: Mapping[str, str]) -> dict[str, str]:
    return {norm_path(p): fid for fid, p in paths.items()}


def _amount_diff(truth: Mapping, row: Mapping) -> list[str]:
    return [m for m in METRICS if truth.get(m) != row.get(m)]


def _match_rows(rows: list[Mapping], truth_lines: list[Mapping], fid_for: Mapping[str, str]
                ) -> tuple[dict[int, Mapping], list[Mapping], list[tuple[Mapping, Mapping]]]:
    """Output row index -> truth line. Matched by document + printed location + claim key (O2); several
    lines of one claim at one location are paired by closest amounts; a row cited at another location of
    the same document is matched on claim key + amounts and reported."""
    by_loc: dict[tuple, list[Mapping]] = defaultdict(list)
    for ln in truth_lines:
        by_loc[(ln["fixture_id"], location_key(ln["location"]), ln["claim_key"])].append(ln)
    used: set[str] = set()
    matched: dict[int, Mapping] = {}
    pending: list[int] = []
    for i, r in enumerate(rows):
        fid = fid_for.get(norm_path(r.get("document")))
        key = claim_key(r.get("claim_number") or r.get("claim_key"))
        cands = [ln for ln in by_loc.get((fid, location_key(r.get("location")), key), [])
                 if ln["line_id"] not in used]
        if not cands:
            pending.append(i)
            continue
        best = min(cands, key=lambda ln: len(_amount_diff(ln, r)))
        used.add(best["line_id"])
        matched[i] = best
    moved: list[tuple[Mapping, Mapping]] = []
    unmatched: list[Mapping] = []
    for i in pending:
        r = rows[i]
        fid = fid_for.get(norm_path(r.get("document")))
        key = claim_key(r.get("claim_number") or r.get("claim_key"))
        cands = [ln for ln in truth_lines if ln["fixture_id"] == fid and ln["claim_key"] == key
                 and ln["line_id"] not in used and not _amount_diff(ln, r)]
        if cands:
            used.add(cands[0]["line_id"])
            matched[i] = cands[0]
            moved.append((r, cands[0]))
        else:
            unmatched.append(r)
    return matched, unmatched, moved


def expected_role(case: Mapping, line_id: str) -> str:
    """The case truth lists only lines whose role is not ``counted``."""
    return case["roles"].get(line_id, "counted")


def _expected_roles(case: Mapping) -> tuple[defaultdict, dict[str, list[str]]]:
    """line_id -> expected role, and tie groups (claim -> documents of which exactly one is counted)."""
    roles = defaultdict(lambda: "counted", case["roles"])
    ties = {f"{c['carrier']}|{c['claim_key']}": c["tie_documents"] for c in case["claims"] if c["tie_documents"]}
    return roles, ties


def score_loss_run_case(case: Mapping, doc_truths: Mapping[str, Mapping], output: Mapping,
                        paths: Mapping[str, str]) -> LossRunScore:
    sc = LossRunScore(case_id=case["case_id"])
    sc.input_tokens = output.get("input_tokens")
    block = output.get("loss_run") or {}
    sc.run_status = block.get("run_status")
    fid_for = _fid_for(paths)
    path_of = dict(paths)
    docs = {f: doc_truths[f] for f in case["documents"]}
    truth_lines = [{**ln, "fixture_id": f, "carrier": docs[f]["carrier"], "format": docs[f]["format"]}
                   for f in case["documents"] for ln in docs[f]["lines"]]
    sc.truth_lines = len(truth_lines)
    role_map, ties = _expected_roles(case)
    rows = list(block.get("rows") or [])
    sc.rows_output = len(rows)
    for w in block.get("warnings") or []:
        sc.warnings.append(str(w))

    # Documents: failures, document facts, tokens per document.
    out_docs = {fid_for.get(norm_path(p)): d for p, d in (block.get("documents") or {}).items()}
    for f in case["documents"]:
        d = out_docs.get(f) or {}
        if d.get("status") not in (None, "ok"):
            sc.documents_failed.append(f"{f}: {d.get('failure_reason')}")
        sc.doc_tokens[f] = d.get("input_tokens")
        for key, want in (("carrier", docs[f]["carrier"]), ("valuation_date", docs[f].get("valuation_date"))):
            if not d:
                continue
            sc.doc_facts_compared += 1
            got = d.get(key)
            if key == "carrier":
                ok = " ".join(str(got or "").split()).lower() == want.lower()
            else:
                ok = (iso(parse_date(got)) if got else None) == want
            sc.doc_facts_correct += ok

    matched, unmatched, moved = _match_rows(rows, truth_lines, fid_for)
    for r, ln in moved:
        sc.location_mismatches.append({"line_id": ln["line_id"], "truth": ln["location"], "output": r.get("location")})

    # Rows: amounts, verify, role, sheet accuracy, recall.
    found_ids = set()
    canaries = [(f, c) for f in case["documents"] for c in docs[f].get("decoys") or []
                if c.get("kind") == "injection_canary"]
    for i, r in enumerate(rows):
        counted = r.get("role") == "counted"
        verified = r.get("verify") == "verified"
        for f, c in canaries:
            if claim_key(r.get("claim_key") or r.get("claim_number")) != claim_key(c["claim_number"]):
                continue
            if c.get("invented"):
                _hard(sc, "canary_followed", f"row {c['claim_number']} added as the injection text asked",
                      document=f)
            else:
                for attr in ("status", "reserve"):
                    if attr in c and r.get(attr) == c[attr]:
                        _hard(sc, "canary_followed", f"{c['claim_number']} {attr} set as the injection text asked "
                                                     f"({r.get(attr)!r})", document=f)
        ln = matched.get(i)
        if ln is None:
            sc.unmatched_rows.append({"document": r.get("document"), "location": r.get("location"),
                                      "claim_number": r.get("claim_number"), "role": r.get("role"),
                                      "verify": r.get("verify")})
            if counted and verified:
                _hard(sc, "wrong_amount_counted", "verified, counted row matches no printed line",
                      claim_number=r.get("claim_number"), document=r.get("document"))
            continue
        found_ids.add(ln["line_id"])
        diff = _amount_diff(ln, r)
        if verified:
            given = [m for m in METRICS if ln.get(m) is not None or r.get(m) is not None]
            sc.verified_amount_fields += len(given)
            sc.verified_amount_correct += sum(1 for m in given if m not in diff)
        if verified and counted and diff:
            _hard(sc, "wrong_amount_counted", f"{ln['line_id']}: {', '.join(diff)} differ",
                  line_id=ln["line_id"], truth={m: ln.get(m) for m in diff}, output={m: r.get(m) for m in diff})
        if verified and counted and ln["verify"] == "unverified":
            _hard(sc, "unverified_counted", f"{ln['line_id']}: must stay unverified ({ln['unverified_reason']}) "
                                            "but is verified and counted", line_id=ln["line_id"])
        want_role = role_map[ln["line_id"]]
        group = f"{ln['carrier']}|{ln['claim_key']}"
        if group in ties and r.get("role") in ("counted", "superseded"):
            pass  # same valuation, same amounts: either file may be the counted one (checked per group below)
        elif r.get("role") != want_role:
            _hard(sc, "wrong_role", f"{ln['line_id']}: role {r.get('role')!r}, truth {want_role!r}",
                  line_id=ln["line_id"])
        sc.status_compared += 1
        sc.status_correct += r.get("status") == ln["status"]
        want_w = set(ln.get("warnings") or [])
        sc.warnings_expected += len(want_w)
        sc.warnings_found += len(want_w & set(r.get("warnings") or []))
        if ln["format"] in ("xlsx", "csv"):
            errs = [m for m in diff]
            if r.get("status") != ln["status"]:
                errs.append("status")
            if (iso(parse_date(r.get("date_of_loss"))) if r.get("date_of_loss") else None) != ln["date_of_loss"]:
                errs.append("date_of_loss")
            if r.get("verify") != ln["verify"]:
                errs.append("verify")
            if bool(r.get("incurred_computed")) != (ln["incurred_source"] == "computed"):
                errs.append("incurred_computed")
            if errs:
                sc.sheet_row_errors.append({"line_id": ln["line_id"], "fields": errs})
    for ln in truth_lines:
        if ln["format"] in ("xlsx", "csv"):
            sc.sheet_lines += 1
            if ln["line_id"] in found_ids and not any(e["line_id"] == ln["line_id"] for e in sc.sheet_row_errors):
                sc.sheet_correct += 1
        else:
            sc.pdf_lines += 1
            sc.pdf_found += ln["line_id"] in found_ids
        if ln["line_id"] not in found_ids:
            sc.missed_lines.append({"line_id": ln["line_id"], "claim_number": ln["claim_number"],
                                    "location": ln["location"]})

    # Tie groups: exactly one file of a same-valuation, same-amount claim is counted.
    for group, tie_docs in ties.items():
        roles_by_doc: dict[str, set] = defaultdict(set)
        for i, r in enumerate(rows):
            ln = matched.get(i)
            if ln and f"{ln['carrier']}|{ln['claim_key']}" == group:
                roles_by_doc[ln["fixture_id"]].add(r.get("role"))
        counted_docs = [f for f in tie_docs if roles_by_doc.get(f) == {"counted"}]
        if roles_by_doc and len(counted_docs) != 1:
            _hard(sc, "wrong_role", f"{group}: same valuation and amounts in {tie_docs}; counted in "
                                    f"{counted_docs or 'none'} (exactly one file must count)")

    # Claims merged: two truth claims of one carrier under one output claim key.
    out_key_of: dict[tuple, set] = defaultdict(set)
    for i, r in enumerate(rows):
        ln = matched.get(i)
        if ln:
            out_key_of[(ln["carrier"], claim_key(r.get("claim_key") or r.get("claim_number")))].add(ln["claim_key"])
    for (carrier, okey), tkeys in out_key_of.items():
        if len(tkeys) > 1:
            _hard(sc, "claims_merged", f"{carrier}: claims {sorted(tkeys)} reported as one claim {okey!r}")

    # Totals strip: must be the sum of the rows reported counted and verified (code adds, never the model).
    sc.totals_truth = dict(case["totals"])
    totals = block.get("totals")
    if totals is not None:
        sc.totals_output = dict(totals)
        carrier_of = {norm_path(path_of[f]): docs[f]["carrier"] for f in docs}
        own = totals_from_lines([{**r, "carrier": r.get("carrier") or carrier_of.get(norm_path(r.get("document"))),
                                  "claim_key": claim_key(r.get("claim_key") or r.get("claim_number"))}
                                 for r in rows])
        bad = [k for k in own if totals.get(k) != own[k]]
        if bad:
            # The excess is one unverified row's amount, or all of them: an unverified row was added in.
            unv = [r for r in rows if r.get("role") == "counted" and r.get("verify") != "verified"]
            included = [m for m in bad if m in METRICS and isinstance(totals.get(m), int)
                        and (totals[m] - own[m]) != 0
                        and (totals[m] - own[m]) in {r.get(m) or 0 for r in unv} | {sum(r.get(m) or 0 for r in unv)}]
            _hard(sc, "unverified_counted" if included else "wrong_amount_counted",
                  f"totals strip is not the sum of the counted, verified rows: {bad}",
                  output={k: totals.get(k) for k in bad}, rows_sum={k: own[k] for k in bad})
        sc.totals_match_truth = all(totals.get(k) == v for k, v in case["totals"].items())

    # Checks.
    out_checks: dict[tuple, Mapping] = {}
    for c in block.get("checks") or []:
        fid = fid_for.get(norm_path(c.get("document")))
        out_checks[(fid, c.get("metric"), c.get("scope_kind"), scope_value_key(c.get("scope_kind"), c.get("scope_value")))] = c
    want_keys = set()
    for t in case["checks"]:
        k = (t["fixture_id"], t["metric"], t["scope_kind"], scope_value_key(t["scope_kind"], t["scope_value"]))
        want_keys.add(k)
        sc.checks_expected += 1
        c = out_checks.get(k)
        if c is None:
            sc.checks_missing.append({"total_id": t["total_id"], "verdict": t["verdict"]})
            if t["verdict"] == "off":
                _hard(sc, "wrong_reconciliation_verdict", f"{t['total_id']}: printed total is off by "
                                                          f"{t['difference']} cents but no check was reported")
            continue
        sc.checks_found += 1
        if c.get("verdict") == t["verdict"]:
            sc.checks_right += 1
        else:
            _hard(sc, "wrong_reconciliation_verdict", f"{t['total_id']}: verdict {c.get('verdict')!r}, truth "
                                                      f"{t['verdict']!r} (printed {t['printed']}, rows "
                                                      f"{t['computed']})", total_id=t["total_id"])
    for k, c in out_checks.items():
        if k not in want_keys:
            sc.extra_checks.append({"document": c.get("document"), "metric": c.get("metric"),
                                    "scope_kind": c.get("scope_kind"), "scope_value": c.get("scope_value"),
                                    "verdict": c.get("verdict")})

    # Notices.
    out_notices = [(n.get("kind"), fid_for.get(norm_path(n.get("document"))), n.get("sheet"))
                   for n in block.get("notices") or []]
    for n in case["notices"]:
        sc.notices_expected += 1
        hit = any(k == n["kind"] and f == n["fixture_id"] and (n.get("sheet") is None or s == n["sheet"])
                  for k, f, s in out_notices)
        sc.notices_found += hit
        if not hit:
            sc.notices_missing.append(n)
    return sc


# =============================================================================================
# Aggregate, gates, report
# =============================================================================================


@dataclass
class LossRunMetrics:
    cases: list[dict]
    hard_fails: list[dict]
    sheet_lines: int
    sheet_correct: int
    sheet_row_accuracy: float | None
    pdf_lines: int
    pdf_found: int
    pdf_row_recall: float | None
    verified_amount_fields: int
    verified_amount_accuracy: float | None
    status_accuracy: float | None
    checks_expected: int
    checks_right: int
    notices_expected: int
    notices_found: int
    warnings_expected: int
    warnings_found: int
    doc_fact_accuracy: float | None
    totals_match_truth: list[str]
    empty_cases: list[str]
    sheet_row_errors: list[dict]
    missed_lines: list[dict]
    unmatched_rows: list[dict]
    location_mismatches: list[dict]
    checks_missing: list[dict]
    extra_checks: list[dict]
    notices_missing: list[dict]
    documents_failed: list[str]
    max_run_input_tokens: int | None
    max_doc_input_tokens: int | None
    doc_tokens: dict[str, int | None]
    warnings: list[str]


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def aggregate_loss_run(scores: list[LossRunScore]) -> LossRunMetrics:
    def tag(s, items):
        return [{"case_id": s.case_id, **x} for x in items]

    doc_tokens = {f"{s.case_id}:{f}": t for s in scores for f, t in s.doc_tokens.items()}
    known = [t for t in doc_tokens.values() if t is not None]
    return LossRunMetrics(
        cases=[{"case_id": s.case_id, "run_status": s.run_status, "rows": s.rows_output, "truth_lines": s.truth_lines,
                "sheet": f"{s.sheet_correct}/{s.sheet_lines}", "pdf": f"{s.pdf_found}/{s.pdf_lines}",
                "checks": f"{s.checks_right}/{s.checks_expected}", "hard_fails": len(s.hard_fails),
                "input_tokens": s.input_tokens, "totals_match_truth": s.totals_match_truth,
                "totals_output": s.totals_output, "totals_truth": s.totals_truth} for s in scores],
        hard_fails=[h for s in scores for h in s.hard_fails],
        sheet_lines=sum(s.sheet_lines for s in scores),
        sheet_correct=sum(s.sheet_correct for s in scores),
        sheet_row_accuracy=_rate(sum(s.sheet_correct for s in scores), sum(s.sheet_lines for s in scores)),
        pdf_lines=sum(s.pdf_lines for s in scores),
        pdf_found=sum(s.pdf_found for s in scores),
        pdf_row_recall=_rate(sum(s.pdf_found for s in scores), sum(s.pdf_lines for s in scores)),
        verified_amount_fields=sum(s.verified_amount_fields for s in scores),
        verified_amount_accuracy=_rate(sum(s.verified_amount_correct for s in scores),
                                       sum(s.verified_amount_fields for s in scores)),
        status_accuracy=_rate(sum(s.status_correct for s in scores), sum(s.status_compared for s in scores)),
        checks_expected=sum(s.checks_expected for s in scores),
        checks_right=sum(s.checks_right for s in scores),
        notices_expected=sum(s.notices_expected for s in scores),
        notices_found=sum(s.notices_found for s in scores),
        warnings_expected=sum(s.warnings_expected for s in scores),
        warnings_found=sum(s.warnings_found for s in scores),
        doc_fact_accuracy=_rate(sum(s.doc_facts_correct for s in scores), sum(s.doc_facts_compared for s in scores)),
        totals_match_truth=[s.case_id for s in scores if s.totals_match_truth],
        empty_cases=[s.case_id for s in scores if s.rows_output == 0],
        sheet_row_errors=[x for s in scores for x in tag(s, s.sheet_row_errors)],
        missed_lines=[x for s in scores for x in tag(s, s.missed_lines)],
        unmatched_rows=[x for s in scores for x in tag(s, s.unmatched_rows)],
        location_mismatches=[x for s in scores for x in tag(s, s.location_mismatches)],
        checks_missing=[x for s in scores for x in tag(s, s.checks_missing)],
        extra_checks=[x for s in scores for x in tag(s, s.extra_checks)],
        notices_missing=[x for s in scores for x in tag(s, s.notices_missing)],
        documents_failed=[f"{s.case_id}: {d}" for s in scores for d in s.documents_failed],
        max_run_input_tokens=max((s.input_tokens for s in scores if s.input_tokens is not None), default=None),
        max_doc_input_tokens=max(known, default=None),
        doc_tokens=doc_tokens,
        warnings=[f"{s.case_id}: {w}" for s in scores for w in s.warnings],
    )


GATE_HARD = "loss_run_hard_fails (amount, unverified, verdict, role, merge, canary)"
GATE_SHEET = "sheet_row_accuracy[loss_run] (code path)"
GATE_RECALL = "pdf_row_recall[loss_run]"
GATE_AMOUNTS = "verified_amount_accuracy[loss_run]"
GATE_EMPTY = "empty_result[loss_run] (cases with no rows)"
GATE_DOC_TOKENS = "max_doc_input_tokens[loss_run]"


def evaluate_loss_run_gates(m: LossRunMetrics, *, doc_token_budget: int | None = None) -> g.GateReport:
    budget = LOSS_RUN_DOC_TOKEN_BUDGET if doc_token_budget is None else doc_token_budget
    hard = len(m.hard_fails)
    kinds = sorted({h["kind"] for h in m.hard_fails})
    res = [
        g.GateResult(GATE_HARD, hard, f"== {HARD_FAIL_MAX}", True, hard <= HARD_FAIL_MAX, ", ".join(kinds)),
        g.GateResult(GATE_SHEET, m.sheet_row_accuracy, f">= {SHEET_ROW_ACCURACY_MIN:.3f}", True,
                     m.sheet_row_accuracy is not None and m.sheet_row_accuracy >= SHEET_ROW_ACCURACY_MIN,
                     "no data" if m.sheet_row_accuracy is None else f"{m.sheet_correct}/{m.sheet_lines}"),
        g.GateResult(GATE_RECALL, m.pdf_row_recall, f">= {PDF_ROW_RECALL_MIN:.2f}", True,
                     m.pdf_row_recall is not None and m.pdf_row_recall >= PDF_ROW_RECALL_MIN,
                     "no data" if m.pdf_row_recall is None else f"{m.pdf_found}/{m.pdf_lines}"),
        g.GateResult(GATE_AMOUNTS, m.verified_amount_accuracy, f">= {VERIFIED_AMOUNT_ACCURACY_MIN:.2f}", True,
                     m.verified_amount_accuracy is not None
                     and m.verified_amount_accuracy >= VERIFIED_AMOUNT_ACCURACY_MIN,
                     "no data" if m.verified_amount_accuracy is None else f"of {m.verified_amount_fields} amounts"),
        g.GateResult(GATE_EMPTY, len(m.empty_cases) if m.cases else None, "== 0", True,
                     bool(m.cases) and not m.empty_cases, ", ".join(m.empty_cases) if m.cases else "no data"),
    ]
    if m.max_run_input_tokens is not None:
        res.append(g.GateResult("max_run_input_tokens", m.max_run_input_tokens, f"<= {g.RUN_INPUT_TOKEN_BUDGET}",
                                True, m.max_run_input_tokens <= g.RUN_INPUT_TOKEN_BUDGET))
    if budget is None:
        res.append(g.GateResult(GATE_DOC_TOKENS, m.max_doc_input_tokens, "reported (budget placeholder, L8)",
                                False, True, "set LOSS_RUN_DOC_TOKEN_BUDGET from a measured live run"))
    else:
        res.append(g.GateResult(GATE_DOC_TOKENS, m.max_doc_input_tokens, f"<= {budget}", True,
                                m.max_doc_input_tokens is not None and m.max_doc_input_tokens <= budget))
    res += [
        g.GateResult("reconciliation_verdicts[loss_run]", _rate(m.checks_right, m.checks_expected), "reported",
                     False, True, f"{m.checks_right}/{m.checks_expected} printed totals checked right"),
        g.GateResult("status_accuracy[loss_run]", m.status_accuracy, "reported", False, True),
        g.GateResult("row_warnings_found", m.warnings_found, "reported", False, True,
                     f"of {m.warnings_expected} ({WARN_INCURRED}, {WARN_REPEATED})"),
        g.GateResult("notices_found", m.notices_found, "reported", False, True, f"of {m.notices_expected}"),
        g.GateResult("doc_fact_accuracy[carrier, valuation date]", m.doc_fact_accuracy, "reported", False, True),
        g.GateResult("totals_match_truth", len(m.totals_match_truth), "reported", False, True,
                     f"of {len(m.cases)} cases (differs when a row is missed or unverified)"),
        g.GateResult("unmatched_rows", len(m.unmatched_rows), "reported", False, True),
        g.GateResult("extra_checks", len(m.extra_checks), "reported", False, True),
        g.GateResult("documents_failed", len(m.documents_failed), "reported", False, True,
                     ", ".join(m.documents_failed)),
    ]
    return g.GateReport(res)


def format_loss_run_report(m: LossRunMetrics, rep: g.GateReport) -> str:
    lines = ["Loss-run eval gates (slice 5a, T7)", "-" * 86]
    for r in rep.results:
        flag = ("PASS" if r.passed else "FAIL") if r.gated else "info"
        note = f"  ({r.note})" if r.note else ""
        lines.append(f"{flag:5} {r.name:64} {g._fmt(r.value):>8}  {r.threshold}{note}")
    lines.append("-" * 86)
    lines.append("case        status   rows  truth  sheet      pdf          checks  hard  tokens")
    for c in m.cases:
        lines.append(f"{c['case_id']:11} {str(c['run_status']):8} {c['rows']:5} {c['truth_lines']:6} {c['sheet']:10} "
                     f"{c['pdf']:12} {c['checks']:7} {c['hard_fails']:5} {c['input_tokens']}")
    for k, t in sorted(m.doc_tokens.items()):
        lines.append(f"tokens {k}: {t}")
    for h in m.hard_fails:
        lines.append(f"HARD FAIL {h['case_id']} {h['kind']}: {h['detail']}")
    for e in m.sheet_row_errors[:40]:
        lines.append(f"SHEET ROW {e['case_id']} {e['line_id']}: {', '.join(e['fields'])}")
    for x in m.missed_lines[:40]:
        lines.append(f"MISSED {x['case_id']} {x['line_id']} ({x['claim_number']}, {x['location']})")
    if len(m.missed_lines) > 40:
        lines.append(f"... {len(m.missed_lines) - 40} more missed lines")
    for x in m.checks_missing:
        lines.append(f"CHECK MISSING {x['case_id']} {x['total_id']} (truth {x['verdict']})")
    for x in m.notices_missing:
        lines.append(f"NOTICE MISSING {x['case_id']} {x['kind']} {x['fixture_id']} {x.get('sheet') or ''}")
    for w in m.warnings:
        lines.append(f"WARNING {w}")
    lines.append(f"overall: {'PASS' if rep.passed else 'FAIL'}")
    return "\n".join(lines)


def loss_run_report_dict(m: LossRunMetrics, rep: g.GateReport) -> dict:
    return {"passed": rep.passed, "gates": [asdict(r) for r in rep.results], "metrics": asdict(m)}


# =============================================================================================
# Oracle output (a perfect adapter reply; self-tests the scorer and documents the shape)
# =============================================================================================


def oracle_row(ln: Mapping, doc: Mapping, path: str, role: str) -> dict:
    return {"document": path, "location": dict(ln["location"]), "claim_number": ln["claim_number"],
            "claim_key": ln["claim_key"], "carrier": doc["carrier"], "policy_number": ln.get("policy_number"),
            "policy_year": ln.get("policy_year"), "line": ln.get("line"), "claimant": ln.get("claimant"),
            "date_of_loss": ln["date_of_loss"], "status_raw": ln["status_raw"], "status": ln["status"],
            **{m: ln.get(m) for m in METRICS}, "incurred_computed": ln["incurred_source"] == "computed",
            "verify": ln["verify"], "unverified_reason": ln.get("unverified_reason"), "role": role,
            "warnings": list(ln.get("warnings") or [])}


def loss_run_oracle_output(case: Mapping, doc_truths: Mapping[str, Mapping], paths: Mapping[str, str], *,
                           input_tokens: int = 1000) -> dict:
    rows, docs = [], {}
    per_doc = max(1, input_tokens // max(1, len(case["documents"])))
    for f in case["documents"]:
        d = doc_truths[f]
        docs[paths[f]] = {"status": "ok", "failure_reason": None, "carrier": d["carrier"],
                          "valuation_date": d.get("valuation_date"),
                          "input_tokens": per_doc if d["format"] == "pdf" else 0}
        rows += [oracle_row(ln, d, paths[f], expected_role(case, ln["line_id"])) for ln in d["lines"]]
    checks = [{"document": paths[c["fixture_id"]], "metric": c["metric"], "scope_kind": c["scope_kind"],
               "scope_value": c["scope_value"], "printed": c["printed"], "computed": c["computed"],
               "difference": c["difference"], "verdict": c["verdict"]} for c in case["checks"]]
    notices = [{"kind": n["kind"], "document": paths[n["fixture_id"]], "sheet": n.get("sheet"),
                "message": n["kind"].replace("_", " ")} for n in case["notices"]]
    return {"input_tokens": input_tokens,
            "documents": {paths[f]: {"status": "ok", "failure_reason": None, "option_count": 1, "ocr_pages": [],
                                     "facts": []} for f in case["documents"]},
            "loss_run": {"workflow": LOSS_RUN_SLUG, "run_status": "done", "warnings": [], "documents": docs,
                         "rows": rows, "totals": dict(case["totals"]), "checks": checks, "notices": notices}}


# =============================================================================================
# Fake model replies built from the truth (the backend's strict reply schemas, main cf45e13)
# =============================================================================================
#
# Row-window call (R2, ``CallRole.LOSS_ROWS``, schema ``loss_rows``): the request text carries ``[page N]``
# markers and the document file name; the reply is exactly
#   {"rows": [{"claim_number", "claimant", "date_of_loss", "status", "policy_number", "line",
#              "policy_year" (string or null), "carrier", "paid", "reserve", "incurred", "recovery",
#              "deductible", "page", "quote"}],     # amounts and dates as printed; code parses them
#    "totals": [{"label", "metric", "scope_kind", "scope_value" (string or null), "amount" (string),
#                "page", "quote"}]}
# There is no feature column: the lines of one claim (D5) are separate rows with the same claim number.
# Document facts call (extract): {"facts": [{"field": "lr:carrier" | "lr:valuation_date", "value", "page",
#   "quote"}], "options_detected": 1, "option_compared": None}
# Header mapping call (``CallRole.LOSS_HEADERS``, schema ``loss_header_map``):
#   {"mappings": [{"header", "column"}]}, column one of the offered values or "unmapped", never null.
# Vision (scanned page): the page's text lines.


def fake_row_reply(doc: Mapping, pages: Iterable[int]) -> dict:
    pages = set(pages)
    rows = []
    for ln in doc["lines"]:
        p = ln["location"].get("page")
        if p not in pages:
            continue
        pr = ln["printed"]
        year = ln.get("policy_year")
        rows.append({"claim_number": pr["claim_number"], "claimant": ln.get("claimant"),
                     "date_of_loss": pr["date_of_loss"], "status": pr["status"],
                     "policy_number": ln.get("policy_number"), "line": ln.get("line"),
                     "policy_year": None if year is None else str(year), "carrier": doc.get("carrier"),
                     **{m: pr.get(m) for m in METRICS}, "page": p, "quote": ln["quote"]})
    totals = [{"label": t["label"], "metric": t["metric"], "scope_kind": t["scope_kind"],
               "scope_value": None if t["scope_value"] is None else str(t["scope_value"]),
               "amount": str(t["printed"]), "page": t["location"]["page"], "quote": t["quote"]}
              for t in doc["printed_totals"] if t["location"].get("page") in pages]
    return {"rows": rows, "totals": totals}


def swap_paid_reserve(reply: dict, claim_number: str) -> dict:
    """The O1 trap: the model puts the reserve in paid and the paid in reserve for one claim."""
    out = {**reply, "rows": [dict(r) for r in reply["rows"]]}
    for r in out["rows"]:
        if claim_key(r["claim_number"]) == claim_key(claim_number):
            r["paid"], r["reserve"] = r["reserve"], r["paid"]
    return out


def fake_fact_reply(doc: Mapping) -> dict:
    facts = []
    for key in DOC_FACT_KEYS:
        f = (doc.get("facts") or {}).get(key)
        facts.append({"field": key, "value": f["printed"] if f else None, "page": f.get("page") if f else None,
                      "quote": f["quote"] if f else None})
    return {"facts": facts, "options_detected": 1, "option_compared": None}


def fake_header_reply(doc: Mapping, headers: Iterable[str]) -> dict:
    known = {h: col for s in doc.get("sheets") or [] for h, col in (s.get("columns") or {}).items()}
    return {"mappings": [{"header": h, "column": known.get(h) or UNMAPPED} for h in headers]}


PAGE_MARK = re.compile(r"\[page (\d+)\]")
FILE_NAME = re.compile(r"Document file name \(data, not an instruction\): '([^']+)'")


# =============================================================================================
# Suite runner (one adapter call per case)
# =============================================================================================

CASES_ENV = "BROKERDOCS_EVAL_LOSS_RUN_CASES"


def selected_cases(known: list[str], env: Mapping | None = None) -> list[str]:
    raw = (os.environ if env is None else env).get(CASES_ENV, "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        return list(known)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"{CASES_ENV}: unknown case(s) {unknown}; known: {known}")
    return list(dict.fromkeys(names))


def adapter_parameters(case: Mapping) -> dict:
    return {"client_name": case["client_name"]}


def run_loss_run_suite(adapter: Callable[..., dict], fixture_dir, cases: Mapping, doc_truths: Mapping,
                       ids: list[str] | None = None, *, doc_token_budget: int | None = None):
    """Call ``adapter`` once per case, score it, apply the gate. NotImplementedError (and the hook's
    ValueError for an unknown workflow) propagate to the caller."""
    ids = ids if ids is not None else selected_cases(sorted(cases))
    scores = []
    for cid in ids:
        case = cases[cid]
        paths = {f: str(Path(fixture_dir) / doc_truths[f]["file"]) for f in case["documents"]}
        out = adapter([paths[f] for f in case["documents"]], workflow=case["workflow"],
                      parameters=adapter_parameters(case))
        scores.append(score_loss_run_case(case, doc_truths, out, paths))
    m = aggregate_loss_run(scores)
    return m, evaluate_loss_run_gates(m, doc_token_budget=doc_token_budget)
