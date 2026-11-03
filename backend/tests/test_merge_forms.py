"""Slice 3 T3 (D13 A): merge_document is given the dynamic form keys; form rules."""

from __future__ import annotations

from backend.pipeline import merge
from backend.pipeline.verify import VerifyResult
from backend.schemas import EvidenceState, FactField

FORMS = FactField(key="forms", label="Forms and Endorsements", kind="forms")
FIELDS = [FactField(key="policy_number", label="Policy Number"), FORMS,
          FactField(key="total_premium", label="Total Premium")]


def cand(field, value, page, state=EvidenceState.VERIFIED, quote=None):
    return VerifyResult(field=field, value=value, state=state, cited_page=page,
                        page_no=page if state in (EvidenceState.VERIFIED, EvidenceState.SCAN_READ) else None,
                        quote=quote or value, matched_text=quote or value, method="exact", score=1.0)


def test_forms_field_expands_to_the_form_keys_found_sorted_in_its_place():
    cands = [cand("form:HO0490", "HO 04 90 05 11", 3), cand("policy_number", "HO-1", 1),
             cand("form:HO0003", "HO 00 03 05 11", 2), cand("form:HO0490", "HO 04 90 05 11", 9)]
    out = merge.merge_document(FIELDS, cands)
    assert [m.field for m in out] == ["policy_number", "form:HO0003", "form:HO0490", "total_premium"]
    h = out[2]
    assert h.state == EvidenceState.VERIFIED and h.page_no == 3 and [e.page_no for e in h.extras] == [9]
    assert out[3].state == EvidenceState.ABSENT


def test_explicit_dynamic_keys_are_merged_even_without_candidates():
    out = merge.merge_document(FIELDS, [cand("form:HO0003", "HO 00 03 05 11", 2)],
                               dynamic_keys=["form:HO0490", "form:HO0003"])
    assert [m.field for m in out] == ["policy_number", "form:HO0003", "form:HO0490", "total_premium"]
    assert out[2].state == EvidenceState.ABSENT and out[2].value is None


def test_without_a_forms_field_form_candidates_are_ignored_as_before():
    plain = [f for f in FIELDS if f.kind == "scalar"]
    out = merge.merge_document(plain, [cand("form:HO0003", "HO 00 03 05 11", 2), cand("policy_number", "HO-1", 1)])
    assert [m.field for m in out] == ["policy_number", "total_premium"]


def test_a_form_without_its_edition_loses_to_the_same_form_with_one():
    cands = [cand("form:HO0003", "HO 00 03", 1), cand("form:HO0003", "HO 00 03 05 11", 7)]
    m = merge.merge_field("form:HO0003", cands)
    assert (m.value, m.page_no, m.state) == ("HO 00 03 05 11", 7, EvidenceState.VERIFIED)
    assert [d.value for d in m.dropped] == ["HO 00 03"]


def test_a_form_only_ever_listed_without_an_edition_keeps_that_value():
    m = merge.merge_field("form:HO0003", [cand("form:HO0003", "HO 00 03", 1)])
    assert (m.value, m.state) == ("HO 00 03", EvidenceState.VERIFIED)


def test_two_editions_of_one_form_conflict_and_nothing_is_picked():
    cands = [cand("form:HO0003", "HO 00 03 05 11", 2), cand("form:HO0003", "HO 00 03 04 91", 30)]
    m = merge.merge_field("form:HO0003", cands)
    assert m.state == EvidenceState.CONFLICTING and m.value is None
    assert [c.value for c in m.conflicts] == ["HO 00 03 05 11", "HO 00 03 04 91"]


def test_form_edition_written_two_ways_is_one_value():
    cands = [cand("form:HO0003", "HO 00 03 05 11", 2), cand("form:HO0003", "HO 00 03 (05/11)", 8),
             cand("form:HO0003", "HO 00 03 05/11", 9)]
    m = merge.merge_field("form:HO0003", cands)
    assert m.state == EvidenceState.VERIFIED and m.value == "HO 00 03 05 11"
    assert [e.page_no for e in m.extras] == [8, 9] and m.resolution == "same_value"


def test_form_edition_helper():
    assert merge.form_edition("form:HO0003", "HO 00 03 05 11") == "05/11"
    assert merge.form_edition("form:HO0003", "HO 00 03 (05/11)") == "05/11"
    assert merge.form_edition("form:HO0003", "HO 00 03 05 2011") == "05/11"
    assert merge.form_edition("form:HO0003", "HO0003 5/11") == "05/11"
    assert merge.form_edition("form:HO0003", "HO 00 03") is None
    assert merge.form_edition("form:HO0003", "Homeowners 3 Special Form") is None  # number not first
