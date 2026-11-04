"""Slice 5a T4: the pure build_ledger (D1) owns every number: lines of one claim (R4), cross-file roles (L5),
effective values (O4), reconciliation (L6, O3), checks and notices."""

from __future__ import annotations

import itertools

from backend.loss_runs.ledger import (
    CheckState,
    Decisions,
    LedgerDocument,
    LedgerRow,
    LedgerTotal,
    RowDecision,
    build_ledger,
    conflict_id,
    total_id,
)

_ids = itertools.count(1)
A = LedgerDocument(1, 0, "Travelers 2024.xlsx", carrier="Travelers", valuation_date="2025-06-30", letter="A")
B = LedgerDocument(2, 1, "Travelers 2025.pdf", carrier="Travelers", valuation_date="2025-12-31", letter="B")
C = LedgerDocument(3, 2, "Undated.pdf", carrier="Travelers", valuation_date=None, letter="C")


def row(doc, claim, paid=None, reserve=None, incurred=None, *, loc=None, carrier="travelers", **kw):
    i = next(_ids)
    return LedgerRow(id=i, document_id=doc.document_id if doc else None, location=loc or f"s0:r{i}",
                     claim_number=claim, claim_key=claim.upper().replace(" ", ""), carrier_key=carrier,
                     paid=paid, reserve=reserve, incurred=incurred, **kw)


def total(doc, metric, amount, scope="all", value=None, detail=None, **kw):
    i = next(_ids)
    return LedgerTotal(id=i, document_id=doc.document_id, metric=metric, scope_kind=scope, amount=amount,
                       location=f"s0:r{1000 + i}:{metric}", scope_value=value, scope_detail=detail or {}, **kw)


def build(rows, docs=(A,), totals=(), decisions=Decisions()):
    return build_ledger(rows, list(docs), list(totals), decisions)


# --------------------------------------------------------------------- lines of one claim (R4)


def test_three_feature_lines_are_one_claim_counted_fully_and_reconciled():
    lines = [row(A, "GL-1", 1000, 0, 1000, status="open"), row(A, "GL-1", 200, 50, 250), row(A, "GL-1", 50, 0, 50)]
    led = build(lines, totals=[total(A, "incurred", 1300)])
    assert [led.rows[r.id].role for r in lines] == ["counted"] * 3
    assert led.totals.claims == 1 and led.totals.incurred.cents == 1300 and led.totals.incurred.from_claims == 1
    assert all(led.rows[r.id].claim_incurred == 1300 and led.rows[r.id].claim_open for r in lines)
    assert [c.verdict for c in led.checks] == ["matches"]


def test_identical_lines_get_repeated_line_and_still_count():
    a, b = row(A, "GL-1", 100, 0, 100), row(A, "GL-1", 100, 0, 100)
    led = build([a, b])
    assert "repeated_line" in led.rows[a.id].flags and "repeated_line" in led.rows[b.id].flags
    assert led.totals.incurred.cents == 200


# --------------------------------------------------------------------- across files (L5)


def test_latest_valuation_wins_older_is_superseded_and_folds_under_it():
    old, new = row(A, "GL-1", 100, 900, 1000), row(B, "GL-1", 600, 200, 800, loc="p1:y100")
    led = build([old, new], docs=(A, B))
    assert led.rows[old.id].role == "superseded" and led.rows[new.id].role == "counted"
    assert "Superseded by Travelers 2025.pdf" in led.rows[old.id].role_reason
    assert led.rows[old.id].group_key == led.rows[new.id].group_key
    assert led.totals.claims == 1 and led.totals.incurred.cents == 800


def test_a_file_with_no_valuation_date_never_supersedes_and_loses_a_tie():
    dated, undated = row(A, "GL-1", 1, 0, 1), row(C, "GL-1", 9, 0, 9, loc="p1:y1")
    led = build([dated, undated], docs=(A, C))
    assert led.rows[undated.id].role == "superseded" and led.rows[dated.id].role == "counted"
    assert any("No valuation date found in Undated.pdf" in n for n in led.notices)


def test_same_date_same_amounts_one_counted_the_other_superseded():
    A2 = LedgerDocument(4, 3, "Copy.xlsx", valuation_date="2025-06-30", letter="D")
    a, b = row(A, "GL-1", 5, 5, 10), row(A2, "GL-1", 5, 5, 10)
    led = build([a, b], docs=(A, A2))
    assert led.rows[a.id].role == "counted" and led.rows[b.id].role == "superseded"
    assert led.rows[b.id].role_reason.startswith("Same claim, same valuation")
    assert not [c for c in led.checks if c.kind == "conflict"]


def test_same_date_different_amounts_conflict_until_the_broker_picks():
    A2 = LedgerDocument(4, 3, "Other.xlsx", valuation_date="2025-06-30", letter="D")
    a, b = row(A, "GL-1", 5, 5, 10), row(A2, "GL-1", 7, 5, 12)
    led = build([a, b], docs=(A, A2))
    assert {led.rows[a.id].role, led.rows[b.id].role} == {"conflicting"}
    assert led.totals.incurred.cents == 0 and led.totals.unreconciled
    check = next(c for c in led.checks if c.kind == "conflict")
    assert check.needs_action and [o.incurred for o in check.options] == [10, 12]
    state = CheckState(check.id, status="resolved", fingerprint=check.fingerprint,
                       resolved_fingerprint=check.fingerprint, pick_document_id=4)
    led2 = build([a, b], docs=(A, A2), decisions=Decisions(checks={check.id: state}))
    assert led2.rows[b.id].role == "counted" and led2.rows[a.id].role == "superseded"
    assert led2.rows[a.id].role_reason == "Not used (you picked Other.xlsx)"
    assert led2.totals.incurred.cents == 12 and not led2.totals.unreconciled


def test_same_claim_number_at_two_carriers_and_leading_zeros_are_separate_claims():
    rows = [row(A, "GL-0017", 1, 0, 1), row(A, "GL-17", 1, 0, 1), row(B, "GL-0017", 1, 0, 1, carrier="hartford")]
    led = build(rows, docs=(A, B))
    assert led.totals.claims == 3 and all(led.rows[r.id].role == "counted" for r in rows)


def test_rows_with_no_carrier_never_merge_across_files():
    a, b = row(A, "GL-1", 1, 0, 1, carrier=""), row(B, "GL-1", 2, 0, 2, carrier="")
    led = build([a, b], docs=(A, B))
    assert led.totals.claims == 2


# --------------------------------------------------------------------- unverified, decisions, effective values (O4)


def test_unverified_rows_count_only_once_confirmed():
    r = row(A, "GL-1", 100, 0, 100, verify_state="unverified")
    led = build([r])
    assert led.rows[r.id].role == "counted" and not led.rows[r.id].counted and led.totals.incurred.cents == 0
    check = next(c for c in led.checks if c.kind == "unverified")
    assert check.title == "1 row in Travelers 2024.xlsx not verified" and check.needs_action
    d = RowDecision(1, r.decision_key, "confirm", basis=r.basis(), note="seen on the carrier portal")
    led2 = build([r], decisions=Decisions(rows=[d]))
    assert led2.rows[r.id].counted and led2.rows[r.id].decision == "confirmed" and led2.totals.incurred.cents == 100
    assert next(c for c in led2.checks if c.kind == "unverified").status == "resolved"


def test_edit_changes_effective_values_claim_totals_and_reconciliation():
    r = row(A, "GL-1", 80_000_00, 0, 80_000_00)
    other = row(A, "GL-1", 0, 40_000_00, 40_000_00)
    t = total(A, "incurred", 132_400_00)
    led = build([r, other], totals=[t])
    check = led.checks[0]
    assert check.verdict == "off" and check.difference == 12_400_00 and "off by $12,400.00" in check.title
    assert led.rows[r.id].claim_incurred == 120_000_00  # claim level (two lines): Over $100K
    d = RowDecision(1, r.decision_key, "edit", amounts={"paid": 92_400_00, "incurred": 92_400_00}, basis=r.basis())
    led2 = build([r, other], totals=[t], decisions=Decisions(rows=[d]))
    assert led2.rows[r.id].eff_incurred == 92_400_00 and led2.rows[r.id].decision == "edited"
    assert led2.checks[0].verdict == "matches" and led2.rows[r.id].claim_incurred == 132_400_00


def test_leave_out_is_only_a_broker_choice():
    r = row(A, "GL-1", 1, 0, 1)
    d = RowDecision(1, r.decision_key, "leave_out", basis=r.basis(), note="duplicate print")
    led = build([r], decisions=Decisions(rows=[d]))
    assert led.rows[r.id].role == "left_out" and led.totals.claims == 0


def test_a_re_read_that_changes_the_values_reopens_the_decision():
    r = row(A, "GL-1", 100, 0, 100, verify_state="unverified")
    d = RowDecision(1, r.decision_key, "confirm", basis={**r.basis(), "paid": 90})
    led = build([r], decisions=Decisions(rows=[d]))
    assert led.rows[r.id].decision_stale and led.rows[r.id].decision is None and not led.rows[r.id].counted


def test_a_decided_row_that_moved_on_a_re_read_follows_by_claim_key_and_amounts():
    r = row(A, "GL-1", 100, 0, 100, verify_state="unverified", loc="p2:y300")
    d = RowDecision(7, f"{A.document_id}:p2:y288|GL-1", "confirm", basis=r.basis())
    led = build([r], decisions=Decisions(rows=[d]))
    assert led.rows[r.id].decision == "confirmed" and led.applied_decisions == {7: r.id}
    gone = RowDecision(8, f"{A.document_id}:p9:y1|GL-9", "confirm", basis={"claim_number": "GL-9"})
    led2 = build([r], decisions=Decisions(rows=[gone]))
    assert led2.orphan_decisions == [8] and any("no longer match" in n for n in led2.notices)


def test_incurred_missing_is_computed_and_a_mismatch_is_a_warning_not_an_exclusion():
    a, b = row(A, "GL-1", 100, 50, None), row(A, "GL-2", 100, 50, 175)
    led = build([a, b])
    assert led.rows[a.id].eff_incurred == 150 and "incurred_computed" in led.rows[a.id].flags
    assert "incurred_not_paid_plus_reserve" in led.rows[b.id].flags and led.rows[b.id].counted


def test_negative_paid_amounts_are_kept_as_printed():
    r = row(A, "GL-1", -500, 0, -500)
    assert build([r]).totals.paid.cents == -500


# --------------------------------------------------------------------- reconciliation (L6, O3)


def test_tolerance_scopes_and_page_subtotals():
    rows = [row(A, "GL-1", 100_00, 0, 100_00, policy_year=2024, page_no=1, line_y=100, loc="p1:y100"),
            row(A, "GL-2", 200_00, 0, 200_00, policy_year=2025, page_no=1, line_y=200, loc="p1:y200"),
            row(A, "GL-3", 300_00, 0, 300_00, policy_year=2025, page_no=2, line_y=100, loc="p2:y100")]
    totals = [total(A, "incurred", 100_99, "policy_year", "2024"),  # within $1.00
              total(A, "incurred", 500_00, "policy_year", "2025"),
              total(A, "incurred", 302_00, "page_subtotal", "1", {"page": 1, "max_y": 250.0}),
              total(A, "claim_count", 3, "all")]
    led = build(rows, totals=totals)
    verdicts = [(c.scope_kind, c.verdict, c.difference) for c in led.checks]
    assert verdicts == [("policy_year", "matches", 99), ("policy_year", "matches", 0),
                        ("page_subtotal", "off", 200), ("all", "matches", 0)]


def test_accepting_a_gap_resolves_it_and_a_change_reopens_it():
    r = row(A, "GL-1", 100_00, 0, 100_00)
    t = total(A, "incurred", 150_00)
    first = build([r], totals=[t]).checks[0]
    state = CheckState(first.id, status="resolved", fingerprint=first.fingerprint,
                       resolved_fingerprint=first.fingerprint, note="carrier counts a closed claim")
    assert first.id == total_id(t)
    led = build([r], totals=[t], decisions=Decisions(checks={first.id: state}))
    assert led.checks[0].status == "resolved" and not led.checks[0].needs_action and not led.totals.unreconciled
    r2 = LedgerRow(**{**r.__dict__, "paid": 120_00, "incurred": 120_00})
    led2 = build([r2], totals=[t], decisions=Decisions(checks={first.id: state}))
    assert led2.checks[0].status == "open" and led2.checks[0].changed_since_resolved


def test_superseded_rows_still_reconcile_their_own_document():
    old, new = row(A, "GL-1", 100, 0, 100), row(B, "GL-1", 300, 0, 300, loc="p1:y1")
    led = build([old, new], docs=(A, B), totals=[total(A, "incurred", 100)])
    assert led.rows[old.id].role == "superseded" and led.checks[0].verdict == "matches"


def test_notices_no_totals_partial_and_unverified_printed_total():
    P = LedgerDocument(5, 4, "Long.pdf", valuation_date="2025-01-01", fully_read=False, unread_pages=(181, 182, 183),
                       status="failed", letter="E")
    rows = [row(A, "GL-1", 1, 0, 1), row(P, "GL-2", 1, 0, 1, loc="p1:y1")]
    led = build(rows, docs=(A, P), totals=[total(P, "incurred", 5)])
    assert "Travelers 2024.xlsx prints no totals: nothing to reconcile." in led.notices
    assert "Long.pdf was not fully read (pages 181 to 183). Retry reads the rest." in led.notices
    assert any("not fully read, so its printed totals are not reconciled" in n for n in led.notices)
    led2 = build([rows[0]], totals=[total(A, "incurred", 5, verify_state="unverified", label="Grand Total")])
    assert led2.checks == [] and any("could not be verified" in n for n in led2.notices)


def test_conflict_check_id_is_stable_and_bounded():
    assert conflict_id("travelers|GL-1") == conflict_id("travelers|GL-1") and len(conflict_id("x" * 500)) < 40
