"""Offline unit tests for the eval scorer and R6 gates. No model, no network."""

from __future__ import annotations

import copy

import pytest

import eval_gates as g
from eval_gates import DocResult, PredictedFact as F


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("$12,500.00", "$12,500"),
    ("$12,500", "12500"),
    ("$1,000,000", "$1M"),
    ("$1,000,000", "1 million"),
    ("$2,000,000", "$2mm"),
    ("$12,500", "$12.5k"),
    ("$5.466 per $1,000 of gross sales", "5.466 per 1,000 gross sales"),
    ("01/01/2027 to 01/01/2028", "January 1, 2027 - January 1, 2028"),
    ("2027-01-01 to 2028-01-01", "1/1/2027 through 1/1/2028"),
    ("01/01/2027 to 01/01/2028", "01/01/2027 \u2013 01/01/2028"),
    ("December 15, 2026", "12/15/2026"),
    ("15 Dec 2026", "2026-12-15"),
    ("Ashford Kitchen Group LLC", "Ashford Kitchen Group, L.L.C."),
    ("Brightwater Logistics Inc.", "BRIGHTWATER LOGISTICS INC"),
    ("Harborline Specialty Insurance Company", "Harborline Specialty Insurance Co."),
    ("1420 Larkspur Avenue, Suite 210, Columbus, OH 43215",
     "1420 Larkspur Ave Ste 210 Columbus OH 43215"),
    ("A (Excellent), XII", "A Excellent XII"),
    ("Non-Admitted", "non admitted"),
    ("Smith & Sons", "Smith and Sons"),
])
def test_values_match_equivalent_forms(a, b):
    assert g.values_match(b, a), (g.normalize_value(a), g.normalize_value(b))


@pytest.mark.parametrize("a,b", [
    ("$12,500", "$12,550"),
    ("$1,000,000", "$2,000,000"),
    ("A+ (Superior), XIV", "A (Superior), XIV"),
    ("A- (Excellent), IX", "A (Excellent), IX"),
    ("A++ (Superior), XV", "A+ (Superior), XV"),
    ("Non-Admitted", "Admitted"),
    ("01/01/2027 to 01/01/2028", "01/01/2027 to 01/02/2028"),
    ("Occurrence", "Claims-Made"),
    ("$5,000", "$50,000"),
])
def test_values_differ(a, b):
    assert not g.values_match(b, a)


def test_null_handling():
    assert g.values_match(None, None)
    assert not g.values_match("x", None)
    assert not g.values_match(None, "x")
    assert not g.values_match("   ", "x")
    assert g.normalize_value(None) == ()


def test_accepted_aliases():
    assert g.values_match("Surplus Lines", "Non-Admitted", ["Surplus Lines"])
    assert not g.values_match("Surplus Lines", "Non-Admitted")


@pytest.mark.parametrize("pred,truth", [
    ("$10,000 per occurrence", "$10,000"),
    ("$1,000,000 each occurrence", "$1,000,000"),
    ("$2,000,000 general aggregate", "$2,000,000"),
    ("10000 per claim", "$10,000.00"),
])
def test_trailing_unit_after_equal_amount_matches(pred, truth):
    assert g.values_match(pred, truth)


@pytest.mark.parametrize("pred,truth", [
    ("$20,000 per occurrence", "$10,000"),
    ("$10,000 per occurrence, $20,000 aggregate", "$10,000"),
    ("per occurrence $10,000", "$10,000"),
    ("25%", "25"),
    ("25", "25%"),
    ("25 percent", "25%"),
    ("25% per claim", "25"),
    ("$10,000 per occurrence", "$10,000 per claim"),
])
def test_unit_suffix_rule_is_strict(pred, truth):
    assert not g.values_match(pred, truth)


def test_unit_suffix_applies_to_accepted_aliases():
    assert g.values_match("$7,500 per claim", "$10,000", ["$7,500"])


@pytest.mark.parametrize("pred,truth", [
    ("Valid until 12/15/2026", "12/15/2026"),  # live run 3: carrier_a_multicol.quote_expiration
    ("Valid until December 15, 2026", "December 15, 2026"),  # live run 3: carrier_b_multicol
    ("Valid until December 15, 2026", "12/15/2026"),  # dates compared by value, not by string
    ("Expires on 2026-12-15", "December 15, 2026"),
    ("Effective 01/01/2027", "January 1, 2027"),
    ("Good through 12/15/2026", "12/15/2026"),
    ("Quote valid until: 12/15/2026", "12/15/2026"),
    ("Bind by 12/15/2026", "12/15/2026"),
])
def test_short_qualifier_before_an_equal_date_matches(pred, truth):
    assert g.values_match(pred, truth), (g.normalize_value(pred), g.normalize_value(truth))


@pytest.mark.parametrize("pred,truth", [
    ("Valid until 12/16/2026", "12/15/2026"),  # a different date
    ("Valid until December 16, 2026", "December 15, 2026"),
    ("Valid 30 days until 12/15/2026", "12/15/2026"),  # extra number in the qualifier
    ("Valid until 12/15/2026 11:59 PM", "12/15/2026"),  # extra numbers after the date
    ("Valid until 12/15/2026 or 30 days", "12/15/2026"),
    ("12/01/2026 until 12/15/2026", "12/15/2026"),  # a second date
    ("This quote remains valid until 12/15/2026", "12/15/2026"),  # more than 3 leading words
    ("Must be bound by 12/15/2026", "12/15/2026"),  # 4 leading words
    ("Harborline 12/15/2026", "12/15/2026"),  # leading word that is not a qualifier
    ("Valid until 12/15/2026", "Valid until 12/16/2026"),  # truth is not a plain date
    ("Limit $10,000", "$10,000"),  # leading words never tolerated for non-dates
    ("Valid until $10,000", "$10,000"),
    ("Deductible of $10,000", "$10,000"),
    ("Insured Ashford Kitchen Group LLC", "Ashford Kitchen Group LLC"),
    ("Effective Harborline Specialty", "Harborline Specialty"),
    ("Valid until 25%", "25%"),
])
def test_date_qualifier_rule_is_strict(pred, truth):
    assert not g.values_match(pred, truth)


def test_date_qualifier_applies_to_accepted_aliases():
    assert g.values_match("Valid until 12/20/2026", "12/15/2026", ["12/20/2026"])


def test_percent_values_still_match_each_other():
    assert g.values_match("25 %", "25%")


def test_predicted_fields_outside_scored_fields_are_ignored():
    r = DocResult("t1", [
        F("carrier_name", "Harborline Specialty Insurance Company", 1, "q", "verified"),
        F("subjectivities", "Signed application", 3, "q", "verified"),
        F("key_exclusions", "Pollution", 3, "q", "scan_read"),
        F("key_endorsements", "CG 20 10", 3, "q", "not_found"),
        F("minimum_earned_premium", "25%", 2, "q", "verified"),
    ])
    s = g.score_document(_truth(), r)
    assert s.hard_fails == 0 and s.absent_violations == 0
    assert s.displayed == 1 and s.displayed_wrong == 0
    assert (s.verified, s.rejected, s.scan_read) == (1, 0, 0)
    assert {o.field for o in s.outcomes} == {"total_premium", "carrier_name", "territory"}


def test_date_tokens_are_not_reparsed_as_numbers():
    toks = g.normalize_value("01/01/2027")
    assert toks == ("@d20270101",)


def test_unknown_state_rejected():
    with pytest.raises(ValueError):
        F("total_premium", "$1", 1, None, "maybe")


# ---------------------------------------------------------------------------
# Per-document scoring
# ---------------------------------------------------------------------------

def _truth(**over):
    t = {
        "fixture_id": "t1",
        "gate_bucket": "clean",
        "expect": {"parse": "ok", "failure_reason": None, "ocr_pages": [], "option_count": 1},
        "facts": {
            "total_premium": {"value": "$12,500.00", "page": 2, "pages": [2], "quote": "Annual Premium: $12,500.00",
                        "source": "text", "accepted_values": []},
            "carrier_name": {"value": "Harborline Specialty Insurance Company", "page": 1, "pages": [1, 2, 3],
                        "quote": "Carrier: Harborline Specialty Insurance Company", "source": "text",
                        "accepted_values": []},
            "territory": None,
        },
        "decoys": [{"field": "total_premium", "value": "$19,860.00", "kind": "other_option"},
                   {"field": "total_premium", "value": "$1.00", "kind": "injection_canary"}],
    }
    t.update(over)
    return t


def test_perfect_output_scores_full():
    s = g.score_document(_truth(), g.oracle_result(_truth()))
    assert (s.present, s.correct, s.absent, s.absent_violations, s.hard_fails) == (2, 2, 1, 0, 0)
    assert s.verified == 2 and s.verified_page_ok == 2
    assert s.parse_ok is True and s.option_count_ok is True and s.ocr_pages_ok is True


def test_page_listed_in_pages_is_ok():
    r = DocResult("t1", [F("carrier_name", "Harborline Specialty Insurance Co.", 3, "q", "verified")])
    s = g.score_document(_truth(), r)
    assert s.hard_fails == 0 and s.correct == 1


def test_verified_wrong_value_is_hard_fail():
    r = DocResult("t1", [F("total_premium", "$12,000", 2, "q", "verified")])
    s = g.score_document(_truth(), r)
    assert s.hard_fails == 1 and s.correct == 0 and s.displayed_wrong == 1
    assert s.outcomes[0].hard_fail == "verified value differs from the source"


def test_verified_wrong_page_is_hard_fail():
    r = DocResult("t1", [F("total_premium", "$12,500", 1, "q", "verified")])
    s = g.score_document(_truth(), r)
    assert s.hard_fails == 1
    assert s.correct == 1  # value right, citation wrong: accuracy counts it, gate fails it


def test_verified_value_for_absent_field_is_hard_fail_and_violation():
    r = DocResult("t1", [F("territory", "Worldwide", 2, "q", "verified")])
    s = g.score_document(_truth(), r)
    assert s.hard_fails == 1 and s.absent_violations == 1
    assert s.outcomes[-1].outcome == "absent_violation"


def test_field_not_in_truth_schema_is_ignored():
    r = DocResult("t1", [F("brand_new_field", "x", 1, "q", "verified")])
    s = g.score_document(_truth(), r)
    assert s.hard_fails == 0 and s.absent_violations == 0 and s.displayed == 0


def test_scan_read_wrong_value_is_not_hard_fail_but_counts_against_accuracy():
    r = DocResult("t1", [F("total_premium", "$12,000", 2, "q", "scan_read")])
    s = g.score_document(_truth(), r)
    assert s.hard_fails == 0 and s.correct == 0 and s.displayed_wrong == 1 and s.scan_read == 1


def test_rejected_value_is_missing_not_hallucinated():
    r = DocResult("t1", [F("total_premium", "$12,500", 2, "q", "not_found")])
    s = g.score_document(_truth(), r)
    assert s.rejected == 1 and s.displayed == 0 and s.correct == 0 and s.hard_fails == 0
    assert s.outcomes[0].outcome == "missing"


def test_absent_field_returning_null_is_ok():
    r = DocResult("t1", [F("territory", None, None, None, "not_found")])
    s = g.score_document(_truth(), r)
    assert s.absent_violations == 0 and s.rejected == 0


def test_decoy_and_canary_hits():
    s = g.score_document(_truth(), DocResult("t1", [F("total_premium", "$19,860", 2, "q", "verified")]))
    assert s.decoy_hits == {"other_option": 1} and s.hard_fails == 1 and s.canary_hits == 0
    s = g.score_document(_truth(), DocResult("t1", [F("total_premium", "$1.00", 2, "q", "scan_read")]))
    assert s.canary_hits == 1 and s.hard_fails == 0


def test_duplicates_prefer_displayed_fact():
    r = DocResult("t1", [F("total_premium", "$12,500", 2, "q", "not_found"),
                         F("total_premium", "$12,500", 2, "q", "verified")])
    s = g.score_document(_truth(), r)
    assert s.correct == 1 and s.verified == 1 and s.rejected == 0


def test_parse_expectations():
    t = _truth(expect={"parse": "failed", "failure_reason": "password_protected"}, facts={})
    ok = g.score_document(t, DocResult("t1", status="failed", failure_reason="password_protected"))
    bad = g.score_document(t, DocResult("t1", status="failed", failure_reason="corrupt"))
    worse = g.score_document(t, DocResult("t1", status="ok"))
    assert ok.parse_ok is True and bad.parse_ok is False and worse.parse_ok is False


def test_ocr_and_option_count_checks():
    t = _truth(expect={"parse": "ok", "ocr_pages": [2, 3], "option_count": 2})
    s = g.score_document(t, DocResult("t1", ocr_pages=[3, 2], option_count=2))
    assert s.ocr_pages_ok is True and s.option_count_ok is True
    s = g.score_document(t, DocResult("t1", ocr_pages=[2], option_count=1))
    assert s.ocr_pages_ok is False and s.option_count_ok is False


def test_from_dict_round_trip():
    r = DocResult.from_dict({"fixture_id": "t1", "facts": [
        {"field": "total_premium", "value": "$12,500", "page": 2, "quote": "q", "state": "verified"}]})
    assert r.facts[0].displayed and r.status == "ok"


# ---------------------------------------------------------------------------
# Aggregation and gates
# ---------------------------------------------------------------------------

def _doc(bucket: str, present: int, correct: int, fid: str = "d") -> g.DocScore:
    return g.DocScore(fid, bucket, present=present, correct=correct)


def test_gate_bars_are_the_r6_numbers():
    assert g.HARD_FAIL_MAX == 0
    assert g.FIELD_ACCURACY_CLEAN_MIN == 0.90
    assert g.FIELD_ACCURACY_MULTI_COLUMN_MIN == 0.80
    assert g.RUN_INPUT_TOKEN_BUDGET == 1_500_000


@pytest.mark.parametrize("clean,multi,passed", [
    ((90, 100), (80, 100), True),    # exactly on both bars
    ((89, 100), (80, 100), False),   # clean just under
    ((90, 100), (79, 100), False),   # multi-column just under
    ((100, 100), (100, 100), True),
])
def test_accuracy_gate_boundaries(clean, multi, passed):
    m = g.aggregate([_doc("clean", clean[1], clean[0]), _doc("multi_column", multi[1], multi[0])])
    assert g.evaluate_gates(m).passed is passed


def test_scanned_is_reported_not_gated():
    m = g.aggregate([_doc("clean", 10, 10), _doc("multi_column", 10, 10), _doc("scanned", 10, 0)])
    rep = g.evaluate_gates(m)
    assert rep.passed
    scanned = [r for r in rep.results if r.name == "field_accuracy[scanned]"][0]
    assert scanned.gated is False and scanned.value == 0.0


def test_any_hard_fail_fails_suite_even_with_perfect_accuracy():
    d = _doc("clean", 10, 10)
    d.hard_fails = 1
    m = g.aggregate([d, _doc("multi_column", 10, 10)])
    rep = g.evaluate_gates(m)
    assert not rep.passed
    assert rep.failures[0].name.startswith("hard_fails")


def test_hard_fail_in_report_only_bucket_still_fails():
    d = _doc("report_only", 10, 10)
    d.hard_fails = 1
    m = g.aggregate([_doc("clean", 10, 10), _doc("multi_column", 10, 10), d])
    assert not g.evaluate_gates(m).passed


def test_missing_gated_bucket_is_not_a_pass():
    m = g.aggregate([_doc("clean", 10, 10)])
    rep = g.evaluate_gates(m)
    assert not rep.passed
    assert any(r.note == "no data" and not r.passed for r in rep.results)
    assert g.evaluate_gates(m, require_buckets=["clean"]).passed


def test_token_budget_gate():
    docs = [_doc("clean", 10, 10), _doc("multi_column", 10, 10)]
    assert g.evaluate_gates(g.aggregate(docs, [100_000, 1_500_000])).passed
    assert not g.evaluate_gates(g.aggregate(docs, [1_500_001])).passed
    names = [r.name for r in g.evaluate_gates(g.aggregate(docs)).results]
    assert "max_run_input_tokens" not in names  # only gated when reported


def test_rates():
    a = g.DocScore("a", "clean", present=4, correct=3, verified=3, rejected=1,
                   scan_read=2, displayed=5, displayed_wrong=1)
    m = g.aggregate([a])
    o = m.overall
    assert o.field_accuracy == 0.75
    assert o.citation_verification_rate == 0.75  # 3 / (3 + 1); scan_read excluded
    assert o.hallucinated_value_rate == 0.2


def test_empty_suite_has_no_ratios():
    m = g.aggregate([])
    assert m.overall.field_accuracy is None and m.overall.citation_verification_rate is None
    assert not g.evaluate_gates(m).passed


def test_format_and_dict_report():
    s = g.score_document(_truth(), DocResult("t1", [F("total_premium", "$12,000", 2, "q", "verified")]))
    m = g.aggregate([s, _doc("multi_column", 1, 1)])
    rep = g.evaluate_gates(m)
    text = g.format_report(m, rep)
    assert "HARD FAIL t1.total_premium" in text and "overall: FAIL" in text
    assert "\u2014" not in text
    d = g.report_dict(m, rep)
    assert d["passed"] is False and d["metrics"]["hard_fails"] == 1


# ---------------------------------------------------------------------------
# Scorer against the committed fixtures
# ---------------------------------------------------------------------------

def test_oracle_on_committed_fixtures_passes_every_gate(truths):
    scores = [g.score_document(t, g.oracle_result(t)) for t in truths.values()]
    m = g.aggregate(scores, [1000])
    rep = g.evaluate_gates(m)
    assert rep.passed, g.format_report(m, rep)
    assert m.hard_fails == 0 and m.canary_hits == 0
    assert not m.parse_mismatches and not m.ocr_mismatches and not m.option_count_mismatches
    assert m.buckets["clean"].field_accuracy == 1.0
    assert m.buckets["multi_column"].field_accuracy == 1.0
    assert m.buckets["scanned"].field_accuracy == 1.0
    assert m.buckets["scanned"].verified == 0  # scan_read only, excluded from verification


def test_decoy_picking_pipeline_hard_fails_on_adversarial_fixtures(truths):
    for fid in ("multi_option", "conflicting_endorsement", "injection"):
        t = truths[fid]
        res = g.oracle_result(t)
        by_field = {f.field: f for f in res.facts}
        for d in t["decoys"]:
            by_field[d["field"]] = F(d["field"], d["value"], d["pages"][0], d["value"], "verified")
        s = g.score_document(t, DocResult(fid, list(by_field.values()), option_count=1))
        assert s.hard_fails >= 1, fid
        assert sum(s.decoy_hits.values()) == len({d["field"] for d in t["decoys"]}), fid
    inj = truths["injection"]
    assert {d["field"] for d in inj["decoys"]} == {"total_premium", "am_best_rating", "territory"}


def test_dropping_one_clean_field_per_doc_stays_above_bar(truths):
    """16 scored fields, a few absent: one miss per clean doc keeps accuracy >= 0.90,
    two misses per doc fall below. Shows the bar has teeth on this set."""
    def run(drop: int) -> float:
        scores = []
        for t in truths.values():
            if t["gate_bucket"] != "clean":
                continue
            res = g.oracle_result(t)
            present = [f for f in res.facts if f.value is not None]
            for f in present[:drop]:
                f.state = "not_found"
            scores.append(g.score_document(t, res))
        return g.aggregate(scores).buckets["clean"].field_accuracy

    assert run(1) >= g.FIELD_ACCURACY_CLEAN_MIN
    assert run(2) < g.FIELD_ACCURACY_CLEAN_MIN


def test_truth_is_not_mutated_by_scoring(truths):
    t = truths["carrier_a_clean"]
    before = copy.deepcopy(t)
    g.score_document(t, g.oracle_result(t))
    assert t == before
