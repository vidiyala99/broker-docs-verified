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
    assert [m.field for m in out] == [f.key for f in FIELDS]
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
