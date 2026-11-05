"""Work queue: the pure rules in backend/work_queue.py (spec section 1, ledger D1 to D10, M7).

Every row of the classification diagram, its first-match order, the 14-day window on
max(finished_at, queue_changed_at), reasons copy and client resolution, with the clock passed in.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from backend import work_queue as wq

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
OPEN3 = {"critical": 1, "high": 2, "medium": 0, "low": 0}
NONE_OPEN = {"critical": 0, "high": 0, "medium": 0, "low": 0}


def run(slug="compare-quotes", **kw) -> wq.QueueRun:
    base = dict(run_id=1, workflow_slug=slug, workflow_name={"compare-quotes": "Compare Quotes",
                                                             "policy-check": "Policy Check",
                                                             "proposal-builder": "Proposal Builder"}[slug],
                line="commercial", status="done", active=False, queue_state="open",
                created_at=NOW - timedelta(days=1), finished_at=NOW - timedelta(hours=1), queue_changed_at=None,
                document_count=3, has_report=True)
    base.update(kw)
    return wq.QueueRun(**base)


def check(**kw) -> wq.QueueRun:
    return run("policy-check", **kw)


def proposal(status="done", active=False) -> wq.ProposalLink:
    return wq.ProposalLink(run_id=99, status=status, active=active)


def group(r, counts=None, prop=None, now=NOW):
    out = wq.classify(r, counts, prop, now)
    return None if out is None else out[0]


def item(r, counts=None, prop=None, now=NOW):
    return wq.classify(r, counts, prop, now)[1]


# --------------------------------------------------------------------- each row of the diagram


def test_proposal_builder_runs_are_never_in_the_queue():
    for status in ("queued", "running", "done", "failed", "cancelled"):
        assert group(run("proposal-builder", status=status, active=status in ("queued", "running"))) is None


@pytest.mark.parametrize("slug", ["compare-quotes", "policy-check"])
def test_an_active_run_is_in_progress_whatever_its_queue_state(slug):
    for state in ("open", "done", "dismissed"):
        r = run(slug, status="running", active=True, queue_state=state, finished_at=None)
        assert group(r, OPEN3) == "in_progress"


def test_a_comparison_whose_proposal_runs_is_in_progress_building_proposal():
    r = run(proposal_run_id=99)
    assert group(r, prop=proposal("running", active=True)) == "in_progress"
    assert item(r, prop=proposal("running", active=True))["summary"] == "Building proposal"
    # even after it was marked done: the proposal is the work in progress
    assert group(replace(r, queue_state="done"), prop=proposal("queued", active=True)) == "in_progress"


@pytest.mark.parametrize("status", ["failed", "partial", "cancelled"])
@pytest.mark.parametrize("slug", ["compare-quotes", "policy-check"])
def test_failed_partial_or_cancelled_and_open_needs_attention(slug, status):
    assert group(run(slug, status=status, retryable=True), OPEN3) == "needs_attention"


def test_cancelled_reads_stopped_before_finishing():
    """D10: cancelled is needs_attention with its own reason, not Done recently."""
    r = run(status="cancelled", failed_codes=("cancelled", "cancelled"))
    it = item(r)
    assert it["reasons"] == ["Stopped before finishing"]


def test_a_partial_check_with_open_items_appears_once_under_needs_attention_with_counts():
    r = check(status="partial", failed_codes=("corrupt",))
    out = wq.classify(r, OPEN3, None, NOW)
    assert out[0] == "needs_attention"
    assert out[1]["open_counts"] == OPEN3 and out[1]["reasons"] == ["1 document not read"]


def test_a_done_check_with_open_items_needs_review():
    it = item(check(), OPEN3)
    assert group(check(), OPEN3) == "needs_review"
    assert it["open_counts"] == OPEN3 and it["reasons"] is None and it["summary"] == "Renewal check"


def test_a_check_without_a_report_does_not_need_review():
    assert group(check(has_report=False), None) == "done_recently"


def test_a_fully_resolved_check_is_done_recently_in_the_window():
    assert group(check(), NONE_OPEN) == "done_recently"
    assert item(check(), NONE_OPEN)["open_counts"] == NONE_OPEN


def test_a_done_comparison_without_a_proposal_is_ready():
    it = item(run())
    assert group(run()) == "ready" and it["reasons"] is None and it["summary"] == "3 carriers"
    assert item(run(document_count=1))["summary"] == "1 carrier"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_a_comparison_whose_proposal_did_not_finish_needs_attention(status):
    """D3 (amended by the owner 2026-11-04): a failed or cancelled proposal never loses the comparison, and it is
    a failure like any other, so the comparison sits in Needs attention, not Ready."""
    r = run(proposal_run_id=99)
    assert group(r, prop=proposal(status)) == "needs_attention"
    it = item(r, prop=proposal(status))
    assert it["reasons"] == ["Proposal didn't finish"]
    assert it["proposal_retryable"] is True and it["retryable"] is False  # "Retry proposal", not Retry
    assert it["action"] == "Compare quotes"


def test_a_comparison_whose_proposal_is_gone_is_ready():
    """A proposal_run_id naming a deleted run is no proposal: Ready, no reasons."""
    r = run(proposal_run_id=99)
    assert group(r, prop=None) == "ready" and item(r, prop=None)["reasons"] is None


@pytest.mark.parametrize("status", ["done", "partial"])
def test_a_comparison_whose_proposal_was_built_is_done_recently(status):
    r = run(proposal_run_id=99, queue_changed_at=NOW - timedelta(minutes=5))
    assert group(r, prop=proposal(status)) == "done_recently"


def test_a_proposal_link_to_a_missing_run_counts_as_no_proposal():
    assert group(run(proposal_run_id=99), prop=None) == "ready"


@pytest.mark.parametrize("state", ["done", "dismissed"])
def test_marked_done_or_dismissed_is_done_recently(state):
    for r in (run(status="failed"), check(), run()):
        assert group(replace(r, queue_state=state, queue_changed_at=NOW), OPEN3) == "done_recently"


# --------------------------------------------------------------------- window (D2)


def test_outside_the_window_is_omitted():
    old = NOW - timedelta(days=15)
    assert group(check(finished_at=old), NONE_OPEN) is None
    assert group(run(queue_state="dismissed", finished_at=old)) is None


def test_the_window_counts_from_the_later_of_finished_at_and_queue_changed_at():
    """D2: marking done a run finished a month ago lands it in Done recently first."""
    old = NOW - timedelta(days=30)
    r = run(status="failed", finished_at=old, queue_state="done", queue_changed_at=NOW - timedelta(days=1))
    assert group(r) == "done_recently"
    assert group(replace(r, queue_changed_at=NOW - timedelta(days=14, seconds=1))) is None
    assert group(replace(r, queue_changed_at=NOW - timedelta(days=14))) == "done_recently"  # inclusive edge


def test_open_actionable_work_is_never_dropped_by_the_window():
    old = NOW - timedelta(days=90)
    assert group(run(finished_at=old)) == "ready"
    assert group(check(finished_at=old), OPEN3) == "needs_review"
    assert group(run(status="failed", finished_at=old)) == "needs_attention"


def test_naive_datetimes_from_sqlite_are_read_as_utc():
    r = run(queue_state="done", finished_at=(NOW - timedelta(days=2)).replace(tzinfo=None))
    assert group(r) == "done_recently"


# --------------------------------------------------------------------- first match wins


def test_order_active_beats_everything():
    r = check(status="running", active=True, queue_state="dismissed")
    assert group(r, OPEN3) == "in_progress"


def test_order_needs_attention_beats_needs_review_and_ready():
    assert group(check(status="partial"), OPEN3) == "needs_attention"
    assert group(run(status="partial")) == "needs_attention"


def test_order_queue_state_beats_status_rules():
    assert group(run(status="failed", queue_state="dismissed", queue_changed_at=NOW)) == "done_recently"
    assert group(check(queue_state="done", queue_changed_at=NOW), OPEN3) == "done_recently"


# --------------------------------------------------------------------- reasons copy


@pytest.mark.parametrize("r, expected", [
    (run(status="failed", failed_codes=("corrupt", "encrypted")), ["2 documents not read"]),
    (run(status="partial", failed_codes=("budget_exhausted",)), ["Extraction stopped: budget reached"]),
    (run(status="partial", failed_codes=("document_budget_exhausted", "corrupt")),
     ["Extraction stopped: budget reached", "1 document not read"]),
    (run(status="partial", failed_codes=()), ["Extraction stopped: budget reached"]),
    (run(status="cancelled", failed_codes=("cancelled",)), ["Stopped before finishing"]),
    (run(status="cancelled", failed_codes=("cancelled", "corrupt")), ["Stopped before finishing", "1 document not read"]),
    (run(status="failed", failed_codes=("interrupted", "interrupted")), ["Interrupted by a restart"]),
    (run(status="failed", failed_codes=()), ["Stopped with an error"]),
])
def test_reasons_copy(r, expected):
    assert item(r)["reasons"] == expected


def test_reasons_only_on_needs_attention_and_ready():
    assert item(check(), OPEN3)["reasons"] is None
    assert item(run(queue_state="done", queue_changed_at=NOW, status="failed", failed_codes=("corrupt",)))["reasons"] is None


# --------------------------------------------------------------------- item shape


def test_item_carries_the_spec_fields():
    it = item(run(client="Acme LLC", client_source="broker", proposal_run_id=None, retryable=False))
    assert {"run_id", "workflow_slug", "workflow_name", "client", "client_source", "summary", "open_counts",
            "reasons", "finished_at", "queue_state"} <= set(it)
    assert it["client"] == "Acme LLC" and it["client_source"] == "broker" and it["open_counts"] is None
    assert it["finished_at"] == (NOW - timedelta(hours=1)).isoformat()


def test_groups_are_sorted_newest_change_first():
    a = run(run_id=1, finished_at=NOW - timedelta(days=3))
    b = run(run_id=2, finished_at=NOW - timedelta(days=5), queue_changed_at=NOW - timedelta(hours=1),
            queue_state="done")
    c = run(run_id=3, finished_at=NOW - timedelta(days=1))
    d = run(run_id=4, finished_at=NOW - timedelta(days=1))
    groups = wq.group_items((r, wq.classify(r, None, None, NOW)) for r in (a, b, c, d))
    assert [i["run_id"] for i in groups["ready"]] == [4, 3, 1]
    assert [i["run_id"] for i in groups["done_recently"]] == [2]
    assert set(groups) == set(wq.GROUPS)


# --------------------------------------------------------------------- client resolution (W4, D1, M7)


def test_broker_name_beats_the_extracted_one():
    assert wq.resolve_client("Acme LLC", "Cedar Hollow LLC") == ("Acme LLC", "broker")
    assert wq.resolve_client(None, "Cedar Hollow LLC") == ("Cedar Hollow LLC", "extracted")
    assert wq.resolve_client(None, None) == (None, None)


def test_policy_check_takes_renewal_then_expiring_then_quote():
    facts = [("quote", 2, "Quote Name"), ("expiring", 0, "Expiring Name"), ("renewal", 1, "Renewal Name")]
    assert wq.extracted_client("policy-check", facts) == "Renewal Name"
    assert wq.extracted_client("policy-check", facts[:2]) == "Expiring Name"
    assert wq.extracted_client("policy-check", facts[:1]) == "Quote Name"
    assert wq.extracted_client("policy-check", [("ams", 3, "AMS Name")]) is None
    assert wq.extracted_client("policy-check", []) is None


def test_compare_takes_the_majority_name():
    facts = [("quotes", 0, "Cedar Hollow"), ("quotes", 1, "Cedar Hollow LLC"), ("quotes", 2, "Cedar  Hollow llc")]
    assert wq.extracted_client("compare-quotes", facts) == "Cedar Hollow LLC"  # case and spacing ignored


def test_compare_tie_goes_to_the_leftmost_quote():
    facts = [("quotes", 2, "Zeta Corp"), ("quotes", 1, "Alpha Inc"), ("quotes", 3, "Zeta Corp"),
             ("quotes", 0, "Alpha Inc")]
    assert wq.extracted_client("compare-quotes", facts) == "Alpha Inc"
    assert wq.extracted_client("compare-quotes", list(reversed(facts))) == "Alpha Inc"  # input order is irrelevant
    assert wq.extracted_client("compare-quotes", [("quotes", 1, "B"), ("quotes", 0, "A")]) == "A"


def test_blank_extracted_values_are_ignored():
    assert wq.extracted_client("compare-quotes", [("quotes", 0, "  ")]) is None
