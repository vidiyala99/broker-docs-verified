r"""The loss review ledger (eng review D1): one pure function owns every number of a loss review.

    build_ledger(rows, documents, totals, decisions) -> Ledger

No database, no model, no clock. The caller (loss_runs.report) loads the inputs, writes `Ledger.rows` back to
claim_row as derived columns (O4/D10) and stores `Ledger.totals` / `checks` / `notices` as the report (D2).

    1. effective values (O4)   extracted amounts, then the broker's edit (claim_decision) unless the extracted
                                values changed since it was made (stale: not applied, "Changed since you resolved
                                it"); a decision whose row moved on a re-read follows it by claim key + amounts (O2)
                                incurred missing -> paid + reserve, marked computed; incurred != paid + reserve by
                                more than 1 cent -> warning, not an exclusion
    2. lines of one claim (R4)  inside one document, lines sharing carrier + claim key are lines of one claim; the
                                claim's amounts are their sum; identical lines get `repeated_line` and still count
    3. across files (L5)        per carrier + claim key: the latest valuation date wins (counted), older ones are
                                superseded; a file with no valuation date never supersedes and loses a tie; two
                                files on the same date: same sums -> one counted, the other superseded; different
                                sums -> conflicting (out of totals) until the broker picks one (discrepancy_state)
    4. counted                  role counted AND (verified, or confirmed / edited by the broker, or added)
                                left_out is only a broker choice
    5. reconcile (L6, O3)       every verified printed total of a fully read document against THAT document's own
                                rows (superseded and conflicting included), in its scope; |printed - computed| <=
                                $1.00 -> Matches, else a check (accept the gap with a note, or fix rows)
    6. checks                   printed totals, conflicting claims (broker-decided, fingerprinted), and one check per
                                document for its undecided unverified rows (decided row by row)
    7. totals strip             over counted lines: claims = distinct claims, not lines (D5)

Claims of two carriers never merge (carrier is part of the key); a row with no known carrier is keyed by its
document, so it never merges with another file's claim (lane choice: never risk two claims merged).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.loss_runs import normalize
from backend.schemas import (
    LossCheckBlock,
    LossCheckOption,
    LossDocumentSummary,
    LossEvidence,
    LossMetricTotal,
    LossTotalsBlock,
)

MONEY = ("paid", "reserve", "incurred", "recovery", "deductible")
TOLERANCE_CENTS = 100  # L6: a difference of $1.00 or less matches
METRIC_LABELS = {"paid": "paid", "reserve": "reserve", "incurred": "incurred", "recovery": "recoveries",
                 "deductible": "deductible", "claim_count": "claim count"}

# --------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class LedgerDocument:
    document_id: int
    position: int
    label: str
    kind: str = "quote"
    carrier: str | None = None
    valuation_date: str | None = None  # ISO
    status: str = "done"  # the run document's status
    fully_read: bool = True
    unread_pages: tuple[int, ...] = ()
    error: str | None = None
    letter: str = "A"
    notices: tuple[str, ...] = ()


@dataclass(frozen=True)
class LedgerRow:
    id: int
    document_id: int | None
    location: str
    claim_number: str
    claim_key: str
    carrier_key: str = ""
    source: str = "extracted"
    status: str = "unknown"
    date_of_loss: str | None = None
    policy_year: int | None = None
    policy_number: str | None = None
    claimant: str | None = None
    verify_state: str = "verified"
    paid: int | None = None
    reserve: int | None = None
    incurred: int | None = None
    recovery: int | None = None
    deductible: int | None = None
    page_no: int | None = None
    sheet_name: str | None = None
    sheet_row: int | None = None
    line_y: float | None = None
    rects: tuple = ()

    @property
    def decision_key(self) -> str:
        return f"{self.document_id}:{self.location}|{self.claim_key}" if self.source == "extracted" \
            else f"{self.location}|{self.claim_key}"

    def amounts(self) -> dict[str, int | None]:
        return {m: getattr(self, m) for m in MONEY}

    def basis(self) -> dict[str, Any]:
        """What a broker decision was made on: the extracted values that matter for totals."""
        return {"claim_number": self.claim_number, "status": self.status, **self.amounts()}

    @property
    def sheet_index(self) -> int | None:
        if self.location.startswith("s") and ":r" in self.location:
            try:
                return int(self.location[1:self.location.index(":")])
            except ValueError:
                return None
        return None


@dataclass(frozen=True)
class LedgerTotal:
    id: int
    document_id: int
    metric: str
    scope_kind: str
    amount: int
    location: str
    scope_value: str | None = None
    scope_detail: Mapping[str, Any] = field(default_factory=dict)
    label: str = ""
    verify_state: str = "verified"
    page_no: int | None = None
    sheet_name: str | None = None
    sheet_row: int | None = None
    quote: str | None = None
    rects: tuple = ()


@dataclass(frozen=True)
class RowDecision:
    id: int
    key: str
    kind: str  # edit | confirm | leave_out
    amounts: Mapping[str, int | None] = field(default_factory=dict)
    basis: Mapping[str, Any] = field(default_factory=dict)
    note: str | None = None


@dataclass(frozen=True)
class CheckState:
    """A broker-decided check's discrepancy_state row (same attributes as models.DiscrepancyState)."""

    item_id: str
    status: str = "open"
    fingerprint: str = ""
    resolved_fingerprint: str | None = None
    changed_since_resolved: bool = False
    reset_from: str | None = None
    note: str | None = None
    pick_document_id: int | None = None


@dataclass(frozen=True)
class Decisions:
    rows: Sequence[RowDecision] = ()
    checks: Mapping[str, CheckState] = field(default_factory=dict)


# --------------------------------------------------------------------- outputs


@dataclass
class RowResult:
    role: str = "counted"
    counted: bool = False
    group_key: str = ""
    eff_paid: int | None = None
    eff_reserve: int | None = None
    eff_incurred: int | None = None
    eff_recovery: int | None = None
    eff_deductible: int | None = None
    claim_paid: int | None = None
    claim_reserve: int | None = None
    claim_incurred: int | None = None
    claim_open: bool = False
    claim_date: str | None = None
    decision: str | None = None  # edited | confirmed | left_out
    decision_id: int | None = None
    decision_stale: bool = False
    role_reason: str | None = None
    flags: list[str] = field(default_factory=list)

    def eff(self, metric: str) -> int | None:
        return getattr(self, f"eff_{metric}")

    def derived(self) -> dict[str, Any]:
        """The claim_row derived columns."""
        return {k: getattr(self, k) for k in DERIVED_COLUMNS}


DERIVED_COLUMNS = ("role", "counted", "group_key", "eff_paid", "eff_reserve", "eff_incurred", "eff_recovery",
                   "eff_deductible", "claim_paid", "claim_reserve", "claim_incurred", "claim_open", "claim_date",
                   "decision", "decision_id", "decision_stale", "role_reason", "flags")


@dataclass
class Ledger:
    rows: dict[int, RowResult]
    totals: LossTotalsBlock
    checks: list[LossCheckBlock]
    notices: list[str]
    applied_decisions: dict[int, int]  # decision id -> row id it applies to
    orphan_decisions: list[int]


# --------------------------------------------------------------------- helpers


def fingerprint(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def effective_status(state: CheckState | None, fp: str) -> tuple[str, bool]:
    """(status, changed since resolved) a check gets for fingerprint `fp` (models.reconciled_status rules)."""
    if state is None:
        return "open", False
    if state.fingerprint == fp:
        return state.status, bool(state.changed_since_resolved)
    if state.status in ("resolved", "removed") and fp != state.resolved_fingerprint:
        return "open", True
    if state.status == "open" and state.changed_since_resolved and fp == state.resolved_fingerprint:
        return state.reset_from or "resolved", False
    return state.status, bool(state.changed_since_resolved)


def _sum(values: Iterable[int | None]) -> int | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def _short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _label_date(doc: LedgerDocument) -> str:
    return f"{doc.label} (valued {doc.valuation_date})" if doc.valuation_date else doc.label


def _scope_phrase(kind: str, value: str | None) -> str:
    return {"all": "All claims", "policy_year": f"{value} policy year", "policy_number": f"Policy {value}",
            "carrier": f"Carrier {value}", "page_subtotal": f"Subtotal ({value})" if value else "Subtotal",
            "sheet": f"Sheet {value}"}.get(kind, kind)


# --------------------------------------------------------------------- build


def build_ledger(rows: Sequence[LedgerRow], documents: Sequence[LedgerDocument], totals: Sequence[LedgerTotal],
                 decisions: Decisions) -> Ledger:
    docs = {d.document_id: d for d in documents}
    results: dict[int, RowResult] = {r.id: RowResult() for r in rows}

    # ---- 1. decisions and effective values
    by_key = {d.key: d for d in decisions.rows}
    applied: dict[int, RowDecision] = {}  # row id -> decision
    stale: set[int] = set()
    used: set[int] = set()
    for r in rows:
        d = by_key.get(r.decision_key)
        if d is None:
            continue
        used.add(d.id)
        if r.source == "extracted" and dict(d.basis) != r.basis():
            stale.add(r.id)
        else:
            applied[r.id] = d
    orphans: list[int] = []
    unmatched = [d for d in decisions.rows if d.id not in used]
    if unmatched:
        free: dict[tuple[int | None, str], list[LedgerRow]] = defaultdict(list)
        for r in rows:
            if r.id not in applied and r.id not in stale and r.source == "extracted":
                free[(r.document_id, r.claim_key)].append(r)
        for d in unmatched:
            doc_part, _, claim = d.key.partition("|")
            doc_id = doc_part.split(":", 1)[0]
            pool = free.get((int(doc_id) if doc_id.isdigit() else None, claim), [])
            hit = next((r for r in pool if r.basis() == dict(d.basis)), None)
            if hit is None:
                orphans.append(d.id)
                continue
            pool.remove(hit)
            applied[hit.id] = d  # O2: the decided line moved on a re-read; same claim, same amounts

    eff: dict[int, dict[str, int | None]] = {}
    for r in rows:
        res = results[r.id]
        amounts = r.amounts()
        d = applied.get(r.id)
        if d is not None:
            res.decision_id = d.id
            res.decision = {"edit": "edited", "confirm": "confirmed", "leave_out": "left_out"}[d.kind]
            if d.kind == "edit":
                for m, v in d.amounts.items():
                    if m in MONEY:
                        amounts[m] = v
        res.decision_stale = r.id in stale
        if amounts["incurred"] is None and (amounts["paid"] is not None or amounts["reserve"] is not None):
            amounts["incurred"] = (amounts["paid"] or 0) + (amounts["reserve"] or 0)
            res.flags.append("incurred_computed")
        elif None not in (amounts["incurred"], amounts["paid"], amounts["reserve"]) \
                and abs(amounts["incurred"] - (amounts["paid"] + amounts["reserve"])) > 1:
            res.flags.append("incurred_not_paid_plus_reserve")
        if r.verify_state != "verified" and r.source == "extracted" and res.decision is None:
            res.flags.append("unverified")
        eff[r.id] = amounts
        for m in MONEY:
            setattr(res, f"eff_{m}", amounts[m])

    def left_out(r: LedgerRow) -> bool:
        return results[r.id].decision == "left_out"

    def trusted(r: LedgerRow) -> bool:
        """The row's amounts may count: verified, or the broker confirmed / edited / added it."""
        return r.source == "added" or r.verify_state == "verified" or results[r.id].decision in ("confirmed", "edited")

    # ---- 2. lines of one claim inside one document (R4)
    def group_of(r: LedgerRow) -> str:
        if r.document_id is None:
            return f"added:{r.id}"
        carrier = r.carrier_key or f"doc{r.document_id}"
        return f"{carrier}|{r.claim_key}"

    doc_claims: dict[str, dict[int, list[LedgerRow]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        results[r.id].group_key = group_of(r)
        if not left_out(r):
            doc_claims[results[r.id].group_key][r.document_id or 0].append(r)
    for r in rows:
        if left_out(r):
            results[r.id].role = "left_out"
    for group, per_doc in doc_claims.items():
        for lines in per_doc.values():
            sums = {m: _sum(eff[x.id][m] for x in lines) for m in MONEY}
            is_open = any(x.status in normalize.OPEN_STATUSES for x in lines)
            dates = sorted(x.date_of_loss for x in lines if x.date_of_loss)
            seen: dict[tuple, int] = {}
            for x in lines:
                res = results[x.id]
                res.claim_paid, res.claim_reserve, res.claim_incurred = sums["paid"], sums["reserve"], sums["incurred"]
                res.claim_open, res.claim_date = is_open, dates[0] if dates else None
                sig = (x.claim_key, x.claimant, x.date_of_loss, x.status, x.policy_number,
                       *(eff[x.id][m] for m in MONEY))
                if sig in seen:
                    res.flags.append("repeated_line")
                    if "repeated_line" not in results[seen[sig]].flags:
                        results[seen[sig]].flags.append("repeated_line")
                else:
                    seen[sig] = x.id

    # ---- 3. across files (L5)
    def sums_of(lines: list[LedgerRow]) -> tuple:
        return tuple(_sum(eff[x.id][m] for x in lines) for m in MONEY)

    conflicts: list[tuple[str, list[int]]] = []  # (group, top document ids)
    for group, per_doc in doc_claims.items():
        if len(per_doc) == 1:
            continue
        ids = list(per_doc)
        dated = [d for d in ids if docs.get(d) and docs[d].valuation_date]
        latest = max((docs[d].valuation_date for d in dated), default=None)
        tops = [d for d in ids if latest is not None and docs.get(d) and docs[d].valuation_date == latest] \
            if latest is not None else ids
        tops.sort(key=lambda d: docs[d].position if d in docs else 99)
        top_label = docs[tops[0]].label if tops and tops[0] in docs else "another file"
        for d in ids:
            if d in tops:
                continue
            for x in per_doc[d]:
                results[x.id].role = "superseded"
                results[x.id].role_reason = (f"Superseded by {top_label}, valued {latest}" if latest
                                             else f"Superseded by {top_label}")
        if len(tops) < 2:
            continue
        if len({sums_of(per_doc[d]) for d in tops}) == 1:
            for d in tops[1:]:
                for x in per_doc[d]:
                    results[x.id].role = "superseded"
                    results[x.id].role_reason = f"Same claim, same valuation, also in {top_label}"
            continue
        conflicts.append((group, tops))
        state = decisions.checks.get(conflict_id(group))
        options_fp = conflict_fingerprint(group, [(d, sums_of(per_doc[d])) for d in tops])
        status, _ = effective_status(state, options_fp)
        pick = state.pick_document_id if state is not None and status == "resolved" else None
        for d in tops:
            for x in per_doc[d]:
                if pick in tops and d != pick:
                    results[x.id].role = "superseded"
                    results[x.id].role_reason = f"Not used (you picked {docs[pick].label if pick in docs else 'another file'})"
                elif pick not in tops:
                    results[x.id].role = "conflicting"
                    results[x.id].role_reason = "Conflicting values on the same valuation date"

    # ---- 4. counted
    for r in rows:
        res = results[r.id]
        res.counted = res.role == "counted" and trusted(r)

    # ---- checks
    checks: list[LossCheckBlock] = []
    notices: list[str] = []
    rows_by_doc: dict[int, list[LedgerRow]] = defaultdict(list)
    for r in rows:
        if r.document_id is not None:
            rows_by_doc[r.document_id].append(r)

    # 5. reconcile printed totals
    totals_by_doc: dict[int, list[LedgerTotal]] = defaultdict(list)
    for t in totals:
        totals_by_doc[t.document_id].append(t)
    for doc in sorted(documents, key=lambda d: d.position):
        doc_totals = totals_by_doc.get(doc.document_id, [])
        own = [r for r in rows_by_doc.get(doc.document_id, []) if not left_out(r) and trusted(r)]
        if not doc.fully_read:
            if doc_totals:
                notices.append(f"{doc.label} was not fully read, so its printed totals are not reconciled.")
            continue
        if doc.status not in ("done",):
            continue
        if not doc_totals:
            if rows_by_doc.get(doc.document_id):
                notices.append(f"{doc.label} prints no totals: nothing to reconcile.")
            continue
        for t in doc_totals:
            if t.verify_state != "verified":
                notices.append(f"A printed total in {doc.label} ({t.label or METRIC_LABELS.get(t.metric, t.metric)}) "
                               "could not be verified, so it is not reconciled.")
                continue
            checks.append(_total_check(t, doc, [r for r in own if in_scope(r, t)], eff, decisions))

    # conflicts
    for group, tops in conflicts:
        per_doc = doc_claims[group]
        first = per_doc[tops[0]][0]
        options = []
        for d in tops:
            s = dict(zip(MONEY, sums_of(per_doc[d]), strict=True))
            doc = docs.get(d)
            options.append(LossCheckOption(document_id=d, label=doc.label if doc else str(d),
                                           valuation_date=doc.valuation_date if doc else None,
                                           claim_row_ids=[x.id for x in per_doc[d]], **s))
        fp = conflict_fingerprint(group, [(d, sums_of(per_doc[d])) for d in tops])
        state = decisions.checks.get(conflict_id(group))
        status, changed = effective_status(state, fp)
        pick = state.pick_document_id if state is not None and status == "resolved" else None
        names = " and ".join(o.label for o in options)
        checks.append(LossCheckBlock(
            id=conflict_id(group), kind="conflict", verdict="conflicting",
            title=f"Claim {first.claim_number}: different amounts on {docs[tops[0]].valuation_date or 'the same date'}",
            message=(f"{names} value claim {first.claim_number} on the same date with different amounts. Pick the one "
                     "to count; the other is kept as history."),
            claim_key=first.claim_key, carrier_key=first.carrier_key,
            filter={"claim_key": first.claim_key, "include_superseded": True},
            evidence=[_row_evidence(x) for d in tops for x in per_doc[d]][:20], options=options,
            picked_document_id=pick if pick in tops else None, fingerprint=fp,
            status="resolved" if status == "resolved" and pick in tops else "open",
            needs_action=not (status == "resolved" and pick in tops), changed_since_resolved=changed))

    # unverified rows, one check per document (decided row by row)
    for doc in sorted(documents, key=lambda d: d.position):
        unverified = [r for r in rows_by_doc.get(doc.document_id, [])
                      if r.source == "extracted" and r.verify_state != "verified"]
        if not unverified:
            continue
        undecided = [r for r in unverified if results[r.id].decision is None]
        n, total_n = len(undecided), len(unverified)
        checks.append(LossCheckBlock(
            id=f"lr-unverified:{doc.document_id}", kind="unverified", decided_by="rows",
            verdict="unverified" if n else "handled",
            title=(f"{n} row{'s' if n != 1 else ''} in {doc.label} not verified" if n
                   else f"{total_n} unverified row{'s' if total_n != 1 else ''} in {doc.label} handled"),
            message=("Kept out of the totals until you confirm each one with a reason, fix it, or leave it out."
                     if n else "Every unverified row is confirmed, fixed or left out."),
            document_id=doc.document_id, rows=n,
            filter={"document_id": doc.document_id, "state": "unverified"},
            evidence=[_row_evidence(r) for r in undecided[:20]],
            fingerprint=fingerprint([doc.document_id, sorted(r.id for r in undecided)]),
            status="open" if n else "resolved", needs_action=bool(n)))

    # ---- notices
    undecided_total = sum(c.rows or 0 for c in checks if c.kind == "unverified")
    if undecided_total:
        notices.insert(0, f"{undecided_total} row{'s' if undecided_total != 1 else ''} not verified "
                          f"{'are' if undecided_total != 1 else 'is'} left out of the totals until you confirm them.")
    for doc in sorted(documents, key=lambda d: d.position):
        if doc.status == "failed" and doc.fully_read:
            notices.append(f"{doc.label} was not read{': ' + doc.error if doc.error else '.'}")
        elif not doc.fully_read:
            pages = _ranges(doc.unread_pages)
            notices.append(f"{doc.label} was not fully read{' (pages ' + pages + ')' if pages else ''}. "
                           "Retry reads the rest.")
        if doc.status == "done" and not doc.valuation_date and rows_by_doc.get(doc.document_id):
            notices.append(f"No valuation date found in {doc.label}: its claims count, but never replace another "
                           "file's.")
        notices.extend(doc.notices)
    if orphans:
        notices.append(f"{len(orphans)} of your decision{'s' if len(orphans) != 1 else ''} no longer match a row "
                       "after the file was read again.")

    totals_block = _totals_block(rows, results, documents, totals, checks)
    return Ledger(rows=results, totals=totals_block, checks=checks, notices=notices,
                  applied_decisions={d.id: rid for rid, d in applied.items()}, orphan_decisions=orphans)


# --------------------------------------------------------------------- reconciliation


def in_scope(r: LedgerRow, t: LedgerTotal) -> bool:
    kind, value, detail = t.scope_kind, t.scope_value, dict(t.scope_detail or {})
    if kind == "all":
        return True
    if kind == "policy_year":
        year = normalize.policy_year_of(value)
        return year is not None and r.policy_year == year
    if kind == "policy_number":
        return bool(r.policy_number) and normalize.claim_key(r.policy_number) == normalize.claim_key(value)
    if kind == "carrier":
        return r.carrier_key == normalize.carrier_key(value)
    if kind == "sheet":
        if "sheet" in detail:
            return r.sheet_index == detail["sheet"]
        return r.sheet_name == value
    if kind == "page_subtotal":
        if "page" in detail:
            return r.page_no == detail["page"] and r.line_y is not None and r.line_y < float(detail.get("max_y", 1e9))
        if "sheet" in detail:
            return (r.sheet_index == detail["sheet"] and r.sheet_row is not None
                    and int(detail.get("from", 0)) <= r.sheet_row <= int(detail.get("to", 0)))
    return False


def computed_amount(rows: Sequence[LedgerRow], metric: str, eff: Mapping[int, Mapping[str, int | None]]) -> int:
    if metric == "claim_count":
        return len({r.claim_key for r in rows})
    return sum(eff[r.id][metric] or 0 for r in rows)


def total_id(t: LedgerTotal) -> str:
    return f"lr-total:{t.document_id}:{t.location}"[:200]


def conflict_id(group: str) -> str:
    return f"lr-conflict:{_short(group)}"


def conflict_fingerprint(group: str, options: Sequence[tuple[int, tuple]]) -> str:
    return fingerprint([group, sorted([d, list(s)] for d, s in options)])


def _total_check(t: LedgerTotal, doc: LedgerDocument, scoped: Sequence[LedgerRow],
                 eff: Mapping[int, Mapping[str, int | None]], decisions: Decisions) -> LossCheckBlock:
    computed = computed_amount(scoped, t.metric, eff)
    diff = t.amount - computed
    tol = 0 if t.metric == "claim_count" else TOLERANCE_CENTS
    matches = abs(diff) <= tol
    fp = fingerprint([t.document_id, t.location, t.metric, t.amount, computed])
    state = decisions.checks.get(total_id(t))
    status, changed = effective_status(state, fp)
    what = METRIC_LABELS.get(t.metric, t.metric)
    scope = _scope_phrase(t.scope_kind, t.scope_value)

    def fmt(v: int) -> str:
        return f"{v:,}" if t.metric == "claim_count" else normalize.format_cents(v)

    if matches:
        title = f"{scope}: printed {what} {fmt(t.amount)} matches"
        message = f"{doc.label} prints {fmt(t.amount)}; its claims add up to {fmt(computed)}."
        st, needs = "match", False
    else:
        off = fmt(abs(diff))
        title = f"{scope}: printed {what} {fmt(t.amount)}, claims add up to {fmt(computed)}, off by {off}"
        message = (f"{doc.label} prints {what} {fmt(t.amount)} for {scope.lower()}; its claims add up to "
                   f"{fmt(computed)}. Fix or add rows, or accept the gap with a note.")
        st = "resolved" if status == "resolved" else "open"
        needs = st == "open"
    filt: dict[str, Any] = {"document_id": t.document_id, "scope_kind": t.scope_kind}
    if t.scope_value is not None:
        filt["scope_value"] = t.scope_value
    if t.scope_detail:
        filt["scope_detail"] = dict(t.scope_detail)
    return LossCheckBlock(
        id=total_id(t), kind="total", verdict="matches" if matches else "off", title=title, message=message,
        document_id=t.document_id, metric=t.metric, scope_kind=t.scope_kind, scope_value=t.scope_value,
        printed=t.amount, computed=computed, difference=diff, filter=filt,
        evidence=[LossEvidence(document_id=t.document_id, page_no=t.page_no, sheet_name=t.sheet_name,
                               sheet_row=t.sheet_row, quote=t.quote, rects=[list(x) for x in t.rects],
                               printed_total_id=t.id)],
        fingerprint=fp, status=st, needs_action=needs, changed_since_resolved=changed and not matches)


def _row_evidence(r: LedgerRow) -> LossEvidence:
    return LossEvidence(document_id=r.document_id or 0, page_no=r.page_no, sheet_name=r.sheet_name,
                        sheet_row=r.sheet_row, rects=[list(x) for x in r.rects], claim_row_id=r.id)


def _ranges(pages: Iterable[int]) -> str:
    ps = sorted(set(pages))
    out, start = [], None
    for i, p in enumerate(ps):
        if start is None:
            start = p
        if i + 1 == len(ps) or ps[i + 1] != p + 1:
            out.append(f"{start}" if start == p else f"{start} to {p}")
            start = None
    return ", ".join(out)


def _totals_block(rows: Sequence[LedgerRow], results: Mapping[int, RowResult], documents: Sequence[LedgerDocument],
                  totals: Sequence[LedgerTotal], checks: Sequence[LossCheckBlock]) -> LossTotalsBlock:
    counted = [r for r in rows if results[r.id].counted]
    groups = {results[r.id].group_key for r in counted}
    open_groups = {results[r.id].group_key for r in counted if r.status in normalize.OPEN_STATUSES}
    metrics = {}
    for m in MONEY:
        vals = [(results[r.id].group_key, results[r.id].eff(m)) for r in counted]
        metrics[m] = LossMetricTotal(cents=sum(v for _, v in vals if v is not None),
                                     from_claims=len({g for g, v in vals if v is not None}))
    roles = defaultdict(int)
    for r in rows:
        roles[results[r.id].role] += 1
    counts = {"rows": len(rows), "counted_rows": len(counted), **{k: roles.get(k, 0) for k in
                                                                  ("counted", "superseded", "conflicting", "left_out")},
              "unverified": sum(1 for r in rows if "unverified" in results[r.id].flags),
              "edited": sum(1 for r in rows if results[r.id].decision == "edited"),
              "confirmed": sum(1 for r in rows if results[r.id].decision == "confirmed"),
              "added": sum(1 for r in rows if r.source == "added"),
              "warnings": sum(1 for r in rows if any(f in ("incurred_not_paid_plus_reserve", "repeated_line")
                                                     for f in results[r.id].flags)),
              "checks_open": sum(1 for c in checks if c.needs_action),
              "checks_total": len(checks)}
    as_of = max((d.valuation_date for d in documents if d.valuation_date), default=None)
    per_doc_rows: dict[int, list[LedgerRow]] = defaultdict(list)
    for r in rows:
        if r.document_id is not None:
            per_doc_rows[r.document_id].append(r)
    totals_n = defaultdict(int)
    for t in totals:
        totals_n[t.document_id] += 1
    summaries = [LossDocumentSummary(
        document_id=d.document_id, letter=d.letter, filename=d.label, kind=d.kind, carrier=d.carrier,
        valuation_date=d.valuation_date, status=d.status if d.fully_read else "partial",
        rows=len(per_doc_rows[d.document_id]),
        counted_rows=sum(1 for r in per_doc_rows[d.document_id] if results[r.id].counted),
        unverified_rows=sum(1 for r in per_doc_rows[d.document_id] if "unverified" in results[r.id].flags),
        printed_totals=totals_n[d.document_id], unread_pages=list(d.unread_pages))
        for d in sorted(documents, key=lambda d: d.position)]
    return LossTotalsBlock(claims=len(groups), open_claims=len(open_groups), unreconciled=counts["checks_open"] > 0,
                           as_of=as_of, counts=counts, documents=summaries, **metrics)


__all__ = ["CheckState", "DERIVED_COLUMNS", "Decisions", "Ledger", "LedgerDocument", "LedgerRow", "LedgerTotal",
           "RowDecision", "RowResult", "TOLERANCE_CENTS", "build_ledger", "computed_amount", "conflict_fingerprint",
           "conflict_id", "effective_status", "fingerprint", "in_scope", "total_id"]
