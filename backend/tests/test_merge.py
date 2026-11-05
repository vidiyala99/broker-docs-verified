"""T14 merge: one fact per field from verified window candidates; compose shows extras and conflicts."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.db import COMPARE_QUOTES
from backend.pipeline import compose, merge, parse, verify
from backend.pipeline.compose import ComposeColumn, ComposeFact, ComposeInput
from backend.pipeline.verify import CandidateFact, VerifyResult
from backend.schemas import EvidenceState, FactField, SectionPlanItem

V, S, NF = EvidenceState.VERIFIED, EvidenceState.SCAN_READ, EvidenceState.NOT_FOUND
C = EvidenceState.CONFLICTING
FIELDS = [FactField.model_validate(f) for f in COMPARE_QUOTES["fact_schema"]]
SECTIONS = [SectionPlanItem.model_validate(s) for s in COMPARE_QUOTES["section_plan"]]
FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "quotes" / "conflicting_endorsement.pdf"


def vr(value, page, state=V, quote=None, field="retention") -> VerifyResult:
    quote = quote or f"Deductible: {value}"
    cited = state in (V, S)
    return VerifyResult(field=field, value=value, state=state, cited_page=page, page_no=page if cited else None,
                        quote=quote, matched_text=quote if cited else None, method="exact" if cited else None,
                        score=1.0 if cited else 0.0, rects=[[1, 2, 3, 4]] if state == V else [],
                        reason=None if cited else "quote_not_found")


# --------------------------------------------------------------------- tiers and repeats


def test_one_verified_candidate_is_used_as_is():
    m = merge.merge_field("retention", [vr("$5,000", 7)])
    assert (m.value, m.page_no, m.state, m.resolution) == ("$5,000", 7, V, "single")
    assert m.extras == () and m.conflicts == () and m.rects == [[1, 2, 3, 4]]


def test_repeated_value_keeps_the_earliest_page_and_attaches_the_others():
    m = merge.merge_field("retention", [vr("$5,000", 40), vr("5000", 3), vr("$5,000", 40), vr("$5,000.00", 90)])
    assert (m.value, m.page_no, m.resolution) == ("5000", 3, "same_value")
    assert [e.page_no for e in m.extras] == [40, 90]  # same page twice gives one passage
    assert m.conflicts == () and m.candidates == 4


def test_unverified_candidates_are_dropped_and_scan_read_is_a_lower_tier():
    m = merge.merge_field("retention", [vr("$9,999", 2, NF), vr("$5,000", 30, S), vr("$5,000", 50)])
    assert (m.value, m.page_no, m.state) == ("$5,000", 50, V)
    assert m.extras == ()  # the scan-read copy is a lower tier, not an extra chip
    s = merge.merge_field("retention", [vr("$9,999", 2, NF), vr("$5,000", 30, S)])
    assert (s.state, s.page_no) == (S, 30)


def test_no_cited_candidate_means_couldnt_verify_or_not_specified():
    nf = merge.merge_field("retention", [vr("$9,999", 2, NF)])
    assert nf.state == NF and nf.value == "$9,999" and nf.page_no is None and nf.resolution == "not_found"
    ab = merge.merge_field("retention", [])
    assert ab.state == EvidenceState.ABSENT and ab.value is None and ab.resolution == "absent"


def test_merge_document_returns_one_fact_per_field_in_field_order():
    out = merge.merge_document(FIELDS, [vr("Harborline", 1, field="carrier_name"), vr("$5,000", 2)])
    # slice 6: the exclusions field merges to its schedule heading (and any exclusion:<title> keys) in its place
    assert [m.field for m in out] == [f.key if f.kind == "scalar" else "exclusions_schedule" for f in FIELDS]
    by = {m.field: m for m in out}
    assert by["carrier_name"].value == "Harborline" and by["retention"].value == "$5,000"
    assert by["named_insured"].state == EvidenceState.ABSENT


# --------------------------------------------------------------------- conflicts


@pytest.fixture(scope="module")
def endorsement_pages():
    return parse.parse_pdf(FIXTURE.read_bytes()).pages


@pytest.mark.parametrize("reverse", [False, True])
def test_later_endorsement_replaces_the_declarations_value(endorsement_pages, reverse):
    """tests/fixtures/quotes/conflicting_endorsement: truth is the amended $10,000 on page 3."""
    cands = [CandidateFact("retention", "$5,000", 2, "Deductible (per Declarations): $5,000 per occurrence"),
             CandidateFact("retention", "$10,000", 3, "Amended Deductible: $10,000 per occurrence")]
    if reverse:
        cands.reverse()
    checked = verify.verify_facts(cands, endorsement_pages)
    assert [c.state for c in checked] == [V, V]
    text = {p.page_no: p.text for p in endorsement_pages}
    m = merge.merge_field("retention", checked, text)
    assert (m.value, m.page_no, m.state, m.resolution) == ("$10,000", 3, V, "endorsement")
    assert m.conflicts == () and [s.value for s in m.superseded] == ["$5,000"]


def test_endorsement_found_through_the_page_text_when_the_quote_has_no_wording():
    cands = [vr("$5,000", 2), vr("$10,000", 9, quote="Deductible: $10,000")]
    pages = {2: "Declarations\nDeductible: $5,000", 9: "Endorsement No. 4\nDeductible: $10,000"}
    m = merge.merge_field("retention", cands, pages)
    assert (m.value, m.resolution) == ("$10,000", "endorsement")


def test_endorsement_wording_far_from_the_matched_line_does_not_amend():
    # the page mentions an endorsement, but not on or just above the line that states the value
    body = "\n".join(f"Policy wording line {k}." for k in range(12))
    cands = [vr("$5,000", 2), vr("$10,000", 9, quote="Deductible: $10,000")]
    pages = {2: "Declarations\nDeductible: $5,000", 9: f"Deductible: $10,000\n{body}\nSee Endorsement No. 4"}
    m = merge.merge_field("retention", cands, pages)
    assert m.state == C and m.resolution == "ambiguous"


LONG_120 = FIXTURE.parent / "long_120.pdf"
LONG_FORM = "CG 00 01 04 13 Commercial General Liability Coverage Form (Occurrence)"
LONG_HEADER = "Commercial General Liability Policy"


@pytest.fixture(scope="module")
def long_120_pages():
    return parse.parse_pdf(LONG_120.read_bytes()).pages


def _long_120_form_candidates(pages):
    """Live diagnostic, run 3: window 1-15 found the real form on page 2; windows 40-54 and
    92-106 returned the running header from their first page (page 92 carries an
    'Endorsement No. 5' heading right under the header line)."""
    cands = [CandidateFact("policy_form", LONG_FORM, 2, f"Policy Form: {LONG_FORM}"),
             CandidateFact("policy_form", LONG_HEADER, 40, LONG_HEADER),
             CandidateFact("policy_form", LONG_HEADER, 92, LONG_HEADER)]
    checked = verify.verify_facts(cands, pages)
    assert [c.state for c in checked] == [V, V, V]
    return checked


def test_long_120_running_header_never_wins_as_an_endorsement(long_120_pages):
    checked = _long_120_form_candidates(long_120_pages)
    text = {p.page_no: p.text for p in long_120_pages}
    m = merge.merge_field(_field("policy_form"), checked, text)
    assert m.resolution != "endorsement"
    assert not (m.value == LONG_HEADER and m.state == V)
    assert (m.value, m.page_no, m.state) == (LONG_FORM, 2, V)
    assert {d.value for d in m.dropped} == {LONG_HEADER}


def test_long_120_header_without_the_header_detector_is_a_conflict(long_120_pages):
    # only the two header pages are known, so the header is not seen on 3 pages: the
    # amendment fix alone must leave a conflict, not an endorsement pick
    checked = _long_120_form_candidates(long_120_pages)
    text = {p.page_no: p.text for p in long_120_pages if p.page_no in (40, 92)}
    assert merge.running_header_pages(LONG_HEADER, text) == [40, 92]
    assert any(merge.AMEND_RE.search(t) for t in text.values())  # page 92: "Endorsement No. 5"
    m = merge.merge_field(_field("policy_form"), checked, text)
    assert (m.state, m.resolution) == (C, "ambiguous")
    assert [(c.value, c.page_no) for c in m.conflicts] == [(LONG_FORM, 2), (LONG_HEADER, 40)]


def test_long_120_amended_deductible_still_wins(long_120_pages):
    cands = [CandidateFact("retention", "$5,000", 3, "Deductible (per Declarations): $5,000 per occurrence"),
             CandidateFact("retention", "$10,000", 104, "Amended Deductible: $10,000 per occurrence")]
    checked = verify.verify_facts(cands, long_120_pages)
    assert [c.state for c in checked] == [V, V]
    text = {p.page_no: p.text for p in long_120_pages}
    m = merge.merge_field(_field("retention"), checked, text)
    assert (m.value, m.page_no, m.state, m.resolution) == ("$10,000", 104, V, "endorsement")
    assert [s.value for s in m.superseded] == ["$5,000"]


LONG_300 = FIXTURE.parent / "long_300.pdf"
LONG_300_FORM = "CG 00 02 04 13 Commercial General Liability Coverage Form (Claims-Made)"
COVERAGE_PART = "Commercial General Liability Coverage Part"
APPLIES_LINE = f"This endorsement modifies insurance provided under the {COVERAGE_PART}."


@pytest.fixture(scope="module")
def long_300_pages():
    return parse.parse_pdf(LONG_300.read_bytes()).pages


@pytest.mark.parametrize("reverse", [False, True])
def test_long_300_endorsement_applicability_line_never_wins(long_300_pages, reverse):
    """Live eval 2026-11-02 11:37 hard fail: a window returned the coverage part named on every
    endorsement page ("This endorsement modifies insurance provided under the ...") as the
    policy form; its quote holds the word "endorsement" and it sits on a later page, so the
    endorsement rule showed it as verified over the real form on page 2."""
    cands = [CandidateFact("policy_form", LONG_300_FORM, 2, f"Policy Form: {LONG_300_FORM}"),
             CandidateFact("policy_form", COVERAGE_PART, 248, APPLIES_LINE)]
    if reverse:
        cands.reverse()
    checked = verify.verify_facts(cands, long_300_pages)
    assert [c.state for c in checked] == [V, V]  # both really are on their pages
    text = {p.page_no: p.text for p in long_300_pages}
    part = next(c for c in checked if c.value == COVERAGE_PART)
    assert len(merge.repeated_line_pages(part, text)) >= 3
    m = merge.merge_field(_field("policy_form"), checked, text)
    assert m.resolution != "endorsement"
    assert (m.value, m.page_no, m.state) == (LONG_300_FORM, 2, V)
    assert [d.value for d in m.dropped] == [COVERAGE_PART]


def test_boilerplate_line_does_not_amend_even_outside_the_header_fields():
    # retention is not a running-header field, so nothing is dropped, but a line printed on every
    # endorsement page is not a one-time amendment: the cell is a conflict, never an endorsement pick
    line = "This endorsement applies to the Deductible: $10,000"
    pages = {2: "Declarations\nDeductible: $5,000", **{p: f"Endorsement No. {p}\n{line}\nBody." for p in (9, 10, 11)}}
    cands = [vr("$5,000", 2), vr("$10,000", 9, quote=line)]
    m = merge.merge_field(_field("retention"), cands, pages)
    assert (m.state, m.resolution) == (C, "ambiguous")
    assert m.dropped == ()
    # the same amendment printed once still wins
    once = {2: pages[2], 9: pages[9]}
    m1 = merge.merge_field(_field("retention"), cands, once)
    assert (m1.value, m1.state, m1.resolution) == ("$10,000", V, "endorsement")


def test_repeated_line_is_kept_when_it_is_the_only_value():
    pages = {p: f"Header\n{APPLIES_LINE}\nBody {p}." for p in (5, 6, 7)}
    only = vr(COVERAGE_PART, 5, field="policy_form", quote=APPLIES_LINE)
    m = merge.merge_field(_field("policy_form"), [only], pages)
    assert (m.value, m.state, m.dropped) == (COVERAGE_PART, V, ())


def test_long_300_page_footer_is_not_a_policy_term(long_300_pages):
    """Live eval 2026-11-02 11:37: long_300.policy_term was 'Conflicting values' between the real
    period on page 1 and the page header's 'Page 27 of 300'."""
    term = "January 1, 2027 to January 1, 2028"
    cands = [CandidateFact("policy_term", term, 1, f"Policy Period: {term}"),
             CandidateFact("policy_term", "Page 27 of 300", 27, "Commercial General Liability Policy Page 27 of 300")]
    checked = verify.verify_facts(cands, long_300_pages)
    assert [c.state for c in checked] == [V, V]
    m = merge.merge_field(_field("policy_term"), checked, {p.page_no: p.text for p in long_300_pages})
    assert (m.value, m.page_no, m.state, m.resolution) == (term, 1, V, "single")
    assert [d.value for d in m.dropped] == ["Page 27 of 300"]


def test_long_300_definition_body_is_not_a_policy_term(long_300_pages):
    """Fix-lane live run 1 (2026-11-02): policy_term conflicted between page 1 and the body of the
    '"Policy period" means ...' definition on page 171."""
    term = "January 1, 2027 to January 1, 2028"
    body = "period of time stated in the Declarations unless it is ended earlier by cancellation."
    cands = [CandidateFact("policy_term", term, 1, f"Policy Period: {term}"),
             CandidateFact("policy_term", body, 171, f'"Policy period" means the {body}')]
    checked = verify.verify_facts(cands, long_300_pages)
    assert [c.state for c in checked] == [V, V]
    text = {p.page_no: p.text for p in long_300_pages}
    assert merge.definition_body(checked[1], text) and not merge.definition_body(checked[0], text)
    m = merge.merge_field(_field("policy_term"), checked, text)
    assert (m.value, m.page_no, m.state) == (term, 1, V)
    assert [d.value for d in m.dropped] == [body]


LONG_300_TERM = "January 1, 2027 to January 1, 2028"
POLICY_NO = "Policy No. LG-773595"


@pytest.mark.parametrize("reverse", [False, True])
def test_long_300_policy_number_header_is_not_a_policy_term(long_300_pages, reverse):
    """Live eval 2026-11-02 (compare_eval2): long_300.policy_term was 'Conflicting values' between the
    real period on page 1 and 'Policy No. LG-773595' (page 170), the first line of every page. The
    running-header rule only covers policy_form, so the field-type rule must drop it: not a date."""
    cands = [CandidateFact("policy_term", LONG_300_TERM, 1, f"Policy Period: {LONG_300_TERM}"),
             CandidateFact("policy_term", POLICY_NO, 170, POLICY_NO)]
    if reverse:
        cands.reverse()
    checked = verify.verify_facts(cands, long_300_pages)
    assert [c.state for c in checked] == [V, V]  # both really are printed on their pages
    text = {p.page_no: p.text for p in long_300_pages}
    assert text[170].splitlines()[0] == POLICY_NO
    m = merge.merge_field(_field("policy_term"), checked, text)
    assert (m.value, m.page_no, m.state, m.resolution) == (LONG_300_TERM, 1, V, "single")
    assert m.conflicts == () and [d.value for d in m.dropped] == [POLICY_NO]


def test_long_300_only_the_policy_number_means_couldnt_verify(long_300_pages):
    checked = verify.verify_facts([CandidateFact("policy_term", POLICY_NO, 170, POLICY_NO)], long_300_pages)
    m = merge.merge_field(_field("policy_term"), checked, {p.page_no: p.text for p in long_300_pages})
    assert (m.state, m.reason, m.page_no) == (NF, "implausible_value", None)


def test_policy_number_is_still_a_value_for_fields_without_a_type():
    # the type rule is per field: an identifier is a plausible value for an untyped field
    assert merge.implausible_reason(_field("named_insured"), POLICY_NO) is None
    assert merge.implausible_reason(_field("policy_form"), POLICY_NO) is None


def _term(value, page, quote=None):
    return vr(value, page, field="policy_term", quote=quote or f"Policy Period: {value}")


def test_two_different_date_ranges_still_conflict():
    a, b = "01/01/2027 to 01/01/2028", "03/01/2027 to 03/01/2028"
    pages = {1: f"Declarations\nPolicy Period: {a}", 40: f"Schedule\nPolicy Period: {b}"}
    m = merge.merge_field(_field("policy_term"), [_term(a, 1), _term(b, 40), _term(POLICY_NO, 12, POLICY_NO)],
                          pages)
    assert (m.state, m.resolution) == (C, "ambiguous")
    assert [(c.value, c.page_no) for c in m.conflicts] == [(a, 1), (b, 40)]
    assert [d.value for d in m.dropped] == [POLICY_NO]


def test_amended_policy_term_still_wins_by_endorsement():
    a, b = "January 1, 2027 to January 1, 2028", "January 1, 2027 to July 1, 2028"
    pages = {1: f"Declarations\nPolicy Period: {a}", 90: f"Endorsement No. 7 - Policy Period Extension\nPolicy Period: {b}"}
    m = merge.merge_field(_field("policy_term"), [_term(a, 1), _term(b, 90), _term(POLICY_NO, 50, POLICY_NO)], pages)
    assert (m.value, m.page_no, m.state, m.resolution) == (b, 90, V, "endorsement")
    assert [s.value for s in m.superseded] == [a]


@pytest.mark.parametrize("field, value", [
    ("policy_term", POLICY_NO),
    ("policy_term", "LG-773595"),
    ("policy_term", "Policy No. LG-2027"),  # a year glued into an identifier is not a year
    ("policy_term", "Commercial General Liability Policy"),
    ("policy_term", "the period of time stated in the Declarations"),
    ("quote_expiration", POLICY_NO),
    ("quote_expiration", "Ironvale Specialty Insurance Company"),
])
def test_temporal_field_values_without_a_date_are_implausible(field, value):
    assert merge.implausible_reason(_field(field), value) == "not_a_date"


@pytest.mark.parametrize("field, value", [
    *(("policy_term", v) for v in ("01/01/2027 to 01/01/2028", "January 1, 2027 to January 1, 2028",
                                   "2027-01-01 to 2028-01-01", "01/01/2027 - 01/01/2028")),
    *(("quote_expiration", v) for v in ("12/15/2026", "December 15, 2026", "2026-12-15")),
    ("policy_term", "Jan. 1, 2027 - Jan. 1, 2028"),
    ("policy_term", "1st January 2027 to 1st January 2028"),
    ("policy_term", "12 months"),
    ("policy_term", "twelve months"),
    ("policy_term", "One-year"),
    ("policy_term", "18 mos."),
    ("policy_term", "Annual"),
    ("policy_term", "12"),
    ("policy_term", "2027 policy year"),
    ("quote_expiration", "30 days from the date of this quotation"),
    ("quote_expiration", "thirty (30) days"),
])
def test_temporal_field_values_with_a_date_or_duration_pass(field, value):
    assert merge.implausible_reason(_field(field), value) is None


QUOTE_DATE = "December 15, 2026"
NOTICE_45 = "45 days of the end of the policy period"


def _expiry(value, page, quote=None):
    return vr(value, page, field="quote_expiration", quote=quote or f"Quote Valid Until: {value}")


@pytest.mark.parametrize("value", [
    NOTICE_45,
    "within 45 days of the end of the policy period",
    "30 days after the expiration of the policy period",
    "sixty (60) days from the effective date of the policy period",
    "10 days before the end of the policy period",
])
def test_quote_expiration_duration_anchored_to_the_policy_period_is_implausible(value):
    # a notice clause measured from the policy period, not when the quote lapses
    assert merge.implausible_reason(_field("quote_expiration"), value) == "policy_period_duration"


def test_policy_term_durations_are_unchanged_by_the_quote_expiration_rule():
    assert merge.implausible_reason(_field("policy_term"), NOTICE_45) is None


def test_long_300_notice_duration_does_not_conflict_with_the_quote_date():
    """Live eval 2026-11-02: long_300.quote_expiration conflicted between '45 days of the end of the
    policy period' (page 6, a notice clause) and 'December 15, 2026' (page 298)."""
    line = "workload conditions. Notice under this provision must be given within 45 days of the end of the"
    pages = {6: f"{line}\npolicy period.", 298: f"Binding Terms\nQuote Valid Until: {QUOTE_DATE}"}
    m = merge.merge_field(_field("quote_expiration"), [_expiry(NOTICE_45, 6, f"{NOTICE_45}."),
                                                       _expiry(QUOTE_DATE, 298)], pages)
    assert (m.value, m.page_no, m.state, m.resolution) == (QUOTE_DATE, 298, V, "single")
    assert m.conflicts == () and [d.value for d in m.dropped] == [NOTICE_45]


def test_undated_quote_expiration_does_not_conflict_with_a_stated_date():
    rel = "30 days from the date of this quotation"
    pages = {2: f"Quote valid for {rel}.", 90: f"Quote Valid Until: {QUOTE_DATE}"}
    m = merge.merge_field(_field("quote_expiration"), [_expiry(rel, 2, f"Quote valid for {rel}."),
                                                       _expiry(QUOTE_DATE, 90)], pages)
    assert (m.value, m.page_no, m.state) == (QUOTE_DATE, 90, V)
    assert m.conflicts == () and [d.value for d in m.dropped] == [rel]
    # with no dated value, the stated duration is still the value
    only = merge.merge_field(_field("quote_expiration"), [_expiry(rel, 2, f"Quote valid for {rel}.")], pages)
    assert (only.value, only.state, only.dropped) == (rel, V, ())


def test_two_different_quote_expiration_dates_still_conflict():
    a, b = "12/15/2026", "January 15, 2027"
    pages = {3: f"Quote Valid Until: {a}", 90: f"Binding Terms\nQuote Valid Until: {b}"}
    m = merge.merge_field(_field("quote_expiration"), [_expiry(a, 3), _expiry(b, 90),
                                                       _expiry(NOTICE_45, 6, f"{NOTICE_45}.")], pages)
    assert (m.state, m.resolution) == (C, "ambiguous")
    assert [(c.value, c.page_no) for c in m.conflicts] == [(a, 3), (b, 90)]
    assert [d.value for d in m.dropped] == [NOTICE_45]


@pytest.mark.parametrize("value, dated", [
    ("12/15/2026", True), ("December 15, 2026", True), ("2026-12-15", True), ("15 December 2026", True),
    ("Dec. 15, 2026", True), ("30 days from the date of this quotation", False), ("2026", False),
    ("December 2026", False), (NOTICE_45, False),
])
def test_states_date_needs_month_day_and_year(value, dated):
    assert merge.states_date(value) is dated


def test_policy_term_duration_still_conflicts_with_a_date_range():
    # unchanged: the dated-value preference is for quote_expiration only
    a, b = "01/01/2027 to 01/01/2028", "18 months"
    pages = {1: f"Policy Period: {a}", 40: f"Policy Term: {b}"}
    m = merge.merge_field(_field("policy_term"), [_term(a, 1), _term(b, 40, f"Policy Term: {b}")], pages)
    assert (m.state, m.resolution) == (C, "ambiguous")


def test_definition_body_is_kept_when_it_is_the_only_value():
    pages = {3: 'Definitions\n"Coverage territory" means the United States of America.'}
    only = vr("the United States of America", 3, field="territory",
              quote='"Coverage territory" means the United States of America.')
    m = merge.merge_field(_field("territory"), [only], pages)
    assert (m.value, m.state, m.dropped) == ("the United States of America", V, ())
    # a value stated by label beside it wins, the definition body is dropped
    pages[1] = "Coverage territory: Worldwide"
    m2 = merge.merge_field(_field("territory"), [only, vr("Worldwide", 1, field="territory",
                                                          quote="Coverage territory: Worldwide")], pages)
    assert (m2.value, m2.state, [d.value for d in m2.dropped]) == ("Worldwide", V, ["the United States of America"])


def _header_pages(n=6, header=LONG_HEADER):
    return {i: f"Policy No. LG-1\n{header}    Page {i} of {n}\nPolicy wording page {i}." for i in range(1, n + 1)}


def test_running_header_value_is_dropped_when_a_real_value_exists():
    pages = _header_pages()
    pages[2] += f"\nPolicy Form: {LONG_FORM}"
    real = vr(LONG_FORM, 2, field="policy_form", quote=f"Policy Form: {LONG_FORM}")
    header = vr(LONG_HEADER, 4, field="policy_form", quote=LONG_HEADER)
    m = merge.merge_field(_field("policy_form"), [header, real], pages)
    assert (m.value, m.page_no, m.state, m.resolution) == (LONG_FORM, 2, V, "single")
    assert [d.value for d in m.dropped] == [LONG_HEADER]
    assert merge.running_header_pages(LONG_HEADER, pages) == [1, 2, 3, 4, 5, 6]


def test_running_header_is_kept_when_it_is_the_only_value_or_on_fewer_than_3_pages():
    only = merge.merge_field(_field("policy_form"), [vr(LONG_HEADER, 4, field="policy_form", quote=LONG_HEADER)],
                             _header_pages())
    assert (only.value, only.state) == (LONG_HEADER, V)
    two = _header_pages(2)
    two[2] += f"\nPolicy Form: {LONG_FORM}"
    m = merge.merge_field(_field("policy_form"), [vr(LONG_FORM, 2, field="policy_form"),
                                                  vr(LONG_HEADER, 1, field="policy_form", quote=LONG_HEADER)], two)
    assert m.state == C


def test_running_header_rule_skips_fields_that_legitimately_repeat():
    # a carrier name in every page header is the real value, not a decoy
    pages = _header_pages(header="Harborline Specialty Insurance Company")
    cands = [vr("Harborline Specialty Insurance Company", 1, field="carrier_name"),
             vr("Harborline Specialty", 5, field="carrier_name")]
    m = merge.merge_field(_field("carrier_name"), cands, pages)
    assert m.dropped == () and m.state == C


@pytest.mark.parametrize("cands, pages", [
    # no endorsement wording anywhere
    ([vr("$5,000", 20), vr("$7,500", 90)], {}),
    # the endorsement value comes BEFORE the other value
    ([vr("$5,000", 20, quote="Amended Deductible: $5,000"), vr("$7,500", 90)], {}),
    # two different endorsement values
    ([vr("$1,000", 5), vr("$5,000", 20, quote="Endorsement 2 Deductible: $5,000"),
      vr("$7,500", 90, quote="Endorsement 3 Deductible: $7,500")], {}),
])
def test_ambiguous_conflict_is_shown_as_a_conflict_not_a_verified_pick(cands, pages):
    # Owner decision (2026-11-02): no silent earliest pick. The cell is not verified and
    # carries every value with its page, earliest first.
    m = merge.merge_field("retention", cands, pages)
    assert m.state == C and m.state not in (V, S) and not m.cited
    assert (m.value, m.page_no, m.resolution) == (None, None, "ambiguous")
    assert [(c.value, c.page_no) for c in m.conflicts] == sorted(
        ((c.value, c.page_no) for c in cands), key=lambda vp: vp[1])
    assert all(c.state == V for c in m.conflicts)  # each value keeps its own verified evidence
    assert m.reason == "conflicting_values" and m.rects == []


def test_two_values_that_differ_only_in_format_are_not_a_conflict():
    m = merge.merge_field("retention", [vr("$5,000", 20), vr("5,000", 90)])
    assert m.state == V and m.resolution == "same_value" and m.conflicts == ()


def test_conflict_message_names_every_value_and_page():
    msg = merge.conflict_message("Deductible / Retention", ("$5,000", 20), [("$7,500", 90), ("$9,000", None)])
    assert msg == ("This document states different values for Deductible / Retention: $5,000 (page 20), "
                   "$7,500 (page 90) and $9,000. The earliest is shown; check which one applies.")
    none = merge.conflict_message("Deductible / Retention", ("$5,000", 20), [("$7,500", 90)], none_shown=True)
    assert none == ("This document states different values for Deductible / Retention: $5,000 (page 20) "
                    "and $7,500 (page 90). No value is shown as verified; check which one applies.")


# --------------------------------------------------------------------- plausibility filter


@pytest.mark.parametrize("field, value", [
    ("producer", "Producer"),  # the field's own label
    ("producer", "Producer of record"),  # label plus generic label words
    ("producer", "producer:"),
    ("territory", "Policy Territory Definitions"),  # definition heading
    ("territory", "Coverage Territory"),  # heading made of the label and generic words
    ("territory", "Policy Territory (continued)"),
    ("key_exclusions", "Exclusions ........ 14"),  # table-of-contents entry
    ("named_insured", "Ab"),  # two letters, not a name
    ("named_insured", "of"),
    ("policy_term", "Page 27 of 300"),  # pagination from a page header or footer
    ("policy_term", "page 4"),
    ("quote_expiration", "27 / 300"),
    ("policy_term", "Pg. 3 of 12"),
])
def test_label_and_heading_values_are_implausible(field, value):
    assert merge.implausible_reason(_field(field), value) is not None


@pytest.mark.parametrize("field, value", [
    ("producer", "Lantern Insurance Brokerage Inc."),
    ("producer", "Producers Alliance Group"),  # a name that only starts with the label word
    ("territory", "Worldwide"),
    ("territory", "USA"),  # short, but an upper-case abbreviation
    ("admitted_status", "Admitted"),  # one of the label's words is a real value
    ("am_best_rating", "A-"),
    ("am_best_rating", "A++"),
    ("retention", "$5,000"),
    ("rate", "1.25"),
    ("policy_form", "CG 00 02 04 13 Commercial General Liability Coverage Form (Claims-Made)"),
    ("policy_form", "Claims-Made"),
    ("key_endorsements", "CG 20 10 Additional Insured"),
    ("policy_term", "12"),  # a bare number is not pagination
    ("policy_term", "12 months"),
    ("policy_term", "January 1, 2027 to January 1, 2028"),
    ("quote_expiration", "12/15/2026"),
])
def test_real_values_pass_the_plausibility_filter(field, value):
    assert merge.implausible_reason(_field(field), value) is None


def _field(key):
    return next(f for f in FIELDS if f.key == key)


def test_label_equal_candidate_is_dropped_before_conflict_resolution():
    real = vr("Lantern Insurance Brokerage Inc.", 2, field="producer",
              quote="Producer: Lantern Insurance Brokerage Inc.")
    decoys = [vr("Producer of record", 30, field="producer", quote="The Producer of record must send"),
              vr("Producer of record", 81, field="producer", quote="The Producer of record shall cooperate")]
    m = merge.merge_field(_field("producer"), [*decoys, real])
    assert (m.value, m.page_no, m.state, m.resolution) == ("Lantern Insurance Brokerage Inc.", 2, V, "single")
    assert m.conflicts == () and m.candidates == 3
    assert [d.value for d in m.dropped] == ["Producer of record", "Producer of record"]


def test_only_implausible_candidates_means_couldnt_verify_without_a_passage():
    m = merge.merge_field(_field("territory"), [vr("Policy Territory Definitions", 89, field="territory",
                                                    quote="76. Policy Territory Definitions")])
    assert m.state == NF and m.resolution == "not_found" and m.reason == "implausible_value"
    assert m.page_no is None and m.rects == [] and not m.cited
    assert m.value == "Policy Territory Definitions" and m.cited_page == 89


def test_endorsement_winner_still_applies_after_the_filter():
    cands = [vr("$5,000", 2), vr("$10,000", 9, quote="Deductible: $10,000"),
             vr("Deductible / Retention", 40, quote="Deductible / Retention")]
    pages = {2: "Declarations\nDeductible: $5,000", 9: "Endorsement No. 4\nDeductible: $10,000"}
    m = merge.merge_field(_field("retention"), cands, pages)
    assert (m.value, m.state, m.resolution) == ("$10,000", V, "endorsement")
    assert [s.value for s in m.superseded] == ["$5,000"]


# --------------------------------------------------------------------- contained (less specific) mentions

FORM_SHORT = "Commercial General Liability Coverage Form"


def _pf(value, page, quote=None):
    return vr(value, page, field="policy_form", quote=quote or f"Policy Form: {value}")


@pytest.mark.parametrize("order", ["long_first", "short_first"])
def test_shorter_contained_form_merges_into_the_longer_value_as_extras(order):
    """Live eval (3 runs), long_120 / long_300 policy_form: the real form line on page 2 and a
    later boilerplate mention that is a substring of it. Same value, not a conflict."""
    cands = [_pf(LONG_FORM, 2), _pf(FORM_SHORT, 30, quote=f"{FORM_SHORT}, Section I"), _pf(FORM_SHORT, 77)]
    if order == "short_first":
        cands.reverse()
    m = merge.merge_field(_field("policy_form"), cands)
    assert (m.value, m.page_no, m.state, m.resolution) == (LONG_FORM, 2, V, "contained")
    assert [(e.value, e.page_no) for e in m.extras] == [(FORM_SHORT, 30), (FORM_SHORT, 77)]
    assert m.conflicts == () and m.superseded == () and m.candidates == 3


def test_longer_value_keeps_its_earliest_page_even_when_the_shorter_comes_first():
    m = merge.merge_field(_field("policy_form"), [_pf(FORM_SHORT, 1), _pf(LONG_FORM, 9), _pf(LONG_FORM, 4)])
    assert (m.value, m.page_no, m.resolution) == (LONG_FORM, 4, "contained")
    assert [e.page_no for e in m.extras] == [9, 1]  # the longer value's own repeats, then the mentions


@pytest.mark.parametrize("name, form", [("long_120", LONG_FORM),
                                        ("long_300", "CG 00 02 04 13 Commercial General Liability Coverage Form "
                                                     "(Claims-Made)")])
def test_long_fixture_boilerplate_mention_does_not_make_a_conflict(name, form):
    pages = parse.parse_pdf((FIXTURE.parent / f"{name}.pdf").read_bytes()).pages
    cands = [CandidateFact("policy_form", form, 2, f"Policy Form: {form}"),
             CandidateFact("policy_form", FORM_SHORT, 5, f"{FORM_SHORT}, Section I: Coverages")]
    checked = verify.verify_facts(cands, pages)
    assert [c.state for c in checked] == [V, V]
    text = {p.page_no: p.text for p in pages}
    m = merge.merge_field(_field("policy_form"), checked, text)
    # the mention is a section heading line printed on many pages: dropped as boilerplate
    # (repeated_line_pages) before containment would fold it; the real form is shown either way
    assert (m.value, m.page_no, m.state, m.resolution) == (form, 2, V, "single")
    assert [d.value for d in m.dropped] == [FORM_SHORT]
    # without page text (no repetition known) containment still folds it
    c = merge.merge_field(_field("policy_form"), checked)
    assert (c.value, c.resolution, [e.page_no for e in c.extras]) == (form, "contained", [5])


@pytest.mark.parametrize("field, short, long", [
    ("admitted_status", "Admitted", "Non-Admitted"),  # single token, and not a token-boundary match
    ("admitted_status", "Admitted", "Non Admitted"),  # single token
    ("admitted_status", "Admitted Carrier In California", "Not Admitted Carrier In California"),  # negation
    ("admitted_status", "Admitted Carrier In California", "Non Admitted Carrier In California"),
    ("admitted_status", "Admitted Carrier In California", "Admitted Carrier In California: No"),
    ("carrier_name", "Harborline Specialty", "Harborline Specialty Insurance Company"),  # under 3 tokens
    ("policy_form", "Liability Coverage Form", "Commercial General Liability Coverage Forms"),  # not on a token edge
    ("retention", "$1,000,000", "$2,000,000"),
    ("retention", "$5,000", "$5,000 per occurrence"),  # numbers and limits keep their behavior
    ("retention", "$5,000 per occurrence", "$5,000 per occurrence each claim"),
    ("retention", "Each Occurrence $1,000,000", "Each Occurrence $1,000,000 Aggregate"),
    ("named_insured", "Northwind Logistics Group", "Harbor Freight Partners LLC"),  # unrelated
])
def test_values_that_are_not_a_safe_containment_still_conflict(field, short, long):
    for cands in ([vr(short, 2, field=field), vr(long, 40, field=field)],
                  [vr(long, 2, field=field), vr(short, 40, field=field)]):
        m = merge.merge_field(_field(field), cands)
        assert (m.state, m.resolution) == (C, "ambiguous")
        assert sorted(c.value for c in m.conflicts) == sorted([short, long])


def test_amendment_winner_is_decided_before_containment():
    amended = "CG 00 02 04 13 Commercial General Liability Coverage Form (Claims-Made)"
    cands = [_pf(LONG_FORM, 2), _pf(FORM_SHORT, 30),
             _pf(amended, 90, quote=f"Amendatory Endorsement: Policy Form replaced with {amended}")]
    m = merge.merge_field(_field("policy_form"), cands)
    assert (m.value, m.page_no, m.state, m.resolution) == (amended, 90, V, "endorsement")
    assert [(s.value, s.page_no) for s in m.superseded] == [(LONG_FORM, 2), (FORM_SHORT, 30)]


def test_mention_contained_in_two_unrelated_values_stays_a_conflict():
    claims = "CG 00 02 04 13 Commercial General Liability Coverage Form (Claims-Made)"
    m = merge.merge_field(_field("policy_form"), [_pf(LONG_FORM, 2), _pf(claims, 3), _pf(FORM_SHORT, 30)])
    assert (m.state, m.resolution) == (C, "ambiguous")
    assert [c.page_no for c in m.conflicts] == [2, 3, 30]


def test_containment_folds_the_mention_but_a_real_conflict_remains():
    m = merge.merge_field(_field("policy_form"), [_pf(LONG_FORM, 2), _pf(FORM_SHORT, 30),
                                                  _pf("Businessowners Coverage Form BP 00 03", 50)])
    assert (m.state, m.resolution) == (C, "ambiguous")
    assert [(c.value, c.page_no) for c in m.conflicts] == [(LONG_FORM, 2),
                                                           ("Businessowners Coverage Form BP 00 03", 50)]


def test_contained_value_helper_guards():
    k = merge.value_key
    assert merge.contained_in(k(FORM_SHORT), k(LONG_FORM))
    assert merge.contained_in(k("general liability coverage form"), k(LONG_FORM))  # case-insensitive
    assert not merge.contained_in(k(LONG_FORM), k(FORM_SHORT))  # proper containment only
    assert not merge.contained_in(k(LONG_FORM), k(LONG_FORM))
    assert not merge.contained_in(k("Coverage Form"), k(LONG_FORM))  # under 3 tokens
    assert not merge.contained_in(k("Admitted Carrier Status"), k("Not Admitted Carrier Status"))
    assert not merge.contained_in(k("Each Occurrence $1,000,000"), k("Each Occurrence $1,000,000 Aggregate"))


def test_value_key_compares_numbers_by_value_but_keeps_words():
    assert merge.value_key("$10,000") == merge.value_key("10000") == merge.value_key("$10,000.00")
    assert merge.value_key("$10,000") != merge.value_key("$10,000 per occurrence")
    assert merge.value_key("10%") != merge.value_key("10")


# --------------------------------------------------------------------- compose shows them


def _input(facts):
    cols = [ComposeColumn(key="A", label="Harborline", document_id=1, filename="a.pdf")]
    return ComposeInput(title="Compare Quotes", fields=FIELDS, sections=SECTIONS, columns=cols, facts=facts)


def test_cell_carries_extra_passages_and_a_conflicting_values_notice():
    inp = _input([
        ComposeFact("A", "carrier_name", "Harborline", V, passage_id=1, page_no=1, fact_id=1,
                    extra_passage_ids=(7, 9)),
        ComposeFact("A", "retention", "$5,000", V, passage_id=3, page_no=20, fact_id=2,
                    conflicts=(("$7,500", 90),)),
    ])
    tables = compose.build_tables(inp)
    cells = {r.key: r.cells["A"] for t in tables.values() for r in t.rows}
    assert cells["carrier_name"].passage_ids == [1, 7, 9] and cells["carrier_name"].notice is None
    ret = cells["retention"]
    assert ret.value == "$5,000" and ret.state == V and ret.passage_ids == [3]
    assert ret.notice.kind == "conflicting_values"
    assert "$5,000 (page 20)" in ret.notice.message and "$7,500 (page 90)" in ret.notice.message
    assert inp.allowed_passage_ids() == {1, 3, 7, 9}
    # prose may cite an extra passage and name the conflicting value
    good = {"sections": [{"section_id": "executive_summary", "paragraphs": [{"segments": [
        {"text": "A states a deductible of $5,000 and also $7,500.", "passage_ids": [3, 9]}]}]}]}
    assert compose.validate_draft(good, inp) == []
    payload = compose._facts_payload(inp)
    ret_fact = next(f for f in payload["facts"] if f["field"] == "retention")
    assert ret_fact["conflicting_values"] == [{"value": "$7,500", "page": 90}]


def test_conflicting_cell_is_not_verified_and_keeps_a_chip_per_value():
    inp = _input([ComposeFact("A", "retention", None, C, fact_id=2,
                              conflicts=(("$5,000", 20, 3), ("$7,500", 90, 8)))])
    cell = next(r.cells["A"] for t in compose.build_tables(inp).values() for r in t.rows if r.key == "retention")
    assert cell.state == C and cell.value == "Conflicting values"
    assert cell.passage_ids == [3, 8] and cell.fact_ids == [2]
    assert [(c.value, c.page_no, c.passage_id) for c in cell.conflicts] == [("$5,000", 20, 3), ("$7,500", 90, 8)]
    assert cell.notice.kind == "conflicting_values"
    assert "$5,000 (page 20)" in cell.notice.message and "$7,500 (page 90)" in cell.notice.message
    assert "No value is shown as verified" in cell.notice.message
    # prose: the field is listed as conflicting (never as a fact), its passages and values may be cited
    payload = compose._facts_payload(inp)
    assert all(f["field"] != "retention" for f in payload["facts"])
    assert payload["conflicting_fields"] == [{
        "document": "A", "field": "retention", "label": "Deductible / Retention",
        "status": "conflicting values, none verified as the value",
        "conflicting_values": [{"value": "$5,000", "page": 20, "passage_id": 3},
                               {"value": "$7,500", "page": 90, "passage_id": 8}]}]
    assert inp.allowed_passage_ids() == {3, 8}
    good = {"sections": [{"section_id": "executive_summary", "paragraphs": [{"segments": [
        {"text": "A states both $5,000 and $7,500 as the deductible.", "passage_ids": [3, 8]}]}]}]}
    assert compose.validate_draft(good, inp) == []
    # the template report counts it apart from verified and unverified fields
    tpl = compose.template_report(inp)
    facts_list = next(b for _, b in tpl.iter_blocks() if b.type == "list")
    assert "1 with conflicting values" in facts_list.items[0][0].text
    assert "0 fields verified" in facts_list.items[0][0].text


def test_conflicting_state_display_and_counts():
    from backend import models, views
    assert models.display_value(C, None) == "Conflicting values"
    assert models.display_value(C, "$5,000") == "Conflicting values"
    inp = _input([ComposeFact("A", "retention", None, C, fact_id=2, conflicts=(("$5,000", 20, 3), ("$7,500", 90, 8)))])
    content = compose.template_report(inp)
    counts = views.cell_counts(content)
    assert counts["conflicting"] == 1 and counts["verified"] == 0


def test_export_notes_a_conflicting_cell_with_its_values_and_sources():
    from backend import export
    inp = _input([ComposeFact("A", "retention", None, C, fact_id=2, conflicts=(("$5,000", 20, 3), ("$7,500", 90, 8)))])
    content = compose.template_report(inp)
    cell = next(r.cells["A"] for _, b in content.iter_blocks() if b.type == "table" for r in b.rows
                if r.key == "retention")
    chips = {str(i): {"passage_id": i, "label": f"A{n}", "letter": "A", "document_id": 1, "filename": "a.pdf",
                      "page_no": p, "verified": True, "rects": [], "page_width": 612.0, "page_height": 792.0}
             for n, (i, p) in enumerate([(3, 20), (8, 90)], start=1)}
    data = export.ExportData(title="Compare Quotes", run_id=1, version=1, content=content, chips=chips)
    note = export.cell_note(data, cell)
    assert note.startswith("Conflicting values: ") and "$5,000 (page 20)" in note and "$7,500 (page 90)" in note
    assert "Source A1: a.pdf page 20; A2: a.pdf page 90" in note
    assert "conflicting values" in export._cell_markup(data, cell)
    assert export.to_xlsx(data) and export.to_pdf(data)


def test_cells_without_merge_data_are_unchanged():
    inp = _input([ComposeFact("A", "retention", "$5,000", V, passage_id=3, page_no=2, fact_id=2)])
    cell = next(r.cells["A"] for t in compose.build_tables(inp).values() for r in t.rows if r.key == "retention")
    assert cell.passage_ids == [3] and cell.notice is None
    assert "notice" in cell.model_dump() and cell.model_dump()["notice"] is None
