"""T5 compose: passage-id citations, number validator, retry once, template fallback (R1)."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.db import COMPARE_QUOTES
from backend.pipeline import compose
from backend.pipeline.compose import ComposeColumn, ComposeFact, ComposeInput
from backend.providers import CallRole, FakeProvider, RunBudget
from backend.providers.fake import FakeRefusal
from backend.schemas import (
    EvidenceState,
    FactField,
    ListBlock,
    Notice,
    ParagraphBlock,
    SectionPlanItem,
    TableBlock,
)

V, S, NF, AB, FL = (EvidenceState.VERIFIED, EvidenceState.SCAN_READ, EvidenceState.NOT_FOUND,
                    EvidenceState.ABSENT, EvidenceState.FAILED)

FIELDS = [FactField.model_validate(f) for f in COMPARE_QUOTES["fact_schema"]]
SECTIONS = [SectionPlanItem.model_validate(s) for s in COMPARE_QUOTES["section_plan"]]


def run(coro):
    return asyncio.run(coro)


def make_input(**over) -> ComposeInput:
    columns = [
        ComposeColumn(key="A", label="Harborline", document_id=1, filename="a.pdf"),
        ComposeColumn(key="B", label="Juniper Peak", document_id=2, filename="b.pdf",
                      option_count=2, option_label="Option 1 (Primary Program)"),
        ComposeColumn(key="C", label="c.pdf", document_id=3, filename="c.pdf", failed=True,
                      failure_message="This PDF is password-protected."),
    ]
    facts = [
        ComposeFact("A", "total_premium", "$26,391.14", V, passage_id=11, page_no=2, fact_id=1),
        ComposeFact("A", "each_occurrence_limit", "$1,000,000", V, passage_id=11, page_no=2, fact_id=2),
        ComposeFact("A", "quote_expiration", "12/15/2026", V, passage_id=12, page_no=3, fact_id=3),
        ComposeFact("A", "named_insured", "Cedar Hollow LLC", V, passage_id=10, page_no=1, fact_id=4),
        ComposeFact("B", "total_premium", "$14,490.00", S, passage_id=21, page_no=2, fact_id=5),
        ComposeFact("B", "retention", "$2,500", NF, fact_id=6),
        ComposeFact("B", "named_insured", None, AB, fact_id=7),
    ]
    base = dict(title="Compare Quotes", fields=FIELDS, sections=SECTIONS, columns=columns, facts=facts)
    base.update(over)
    return ComposeInput(**base)


def draft(*sections):
    return {"sections": [
        {"section_id": sid, "paragraphs": [{"segments": [{"text": t, "passage_ids": ids} for t, ids in segs]}]}
        for sid, segs in sections
    ]}


GOOD = draft(
    ("executive_summary", [("Harborline totals $26,391.14 ", [11]), ("and Juniper Peak $14,490.00.", [21])]),
    ("coverage", [("Harborline offers a $1 million per occurrence limit.", [11])]),
)


# --------------------------------------------------------------------- number extraction


def test_prose_numbers_classify_money_percent_dates_and_skip_counts():
    toks = compose.prose_numbers(
        "Section 3.1 compares 4 quotes: $1,000,000 limit, 25% minimum earned, "
        "1.5M aggregate, valid until 12/15/2026 or January 5, 2027, in the 2027 term, 2500 retention.")
    kinds = sorted(t.kind for t in toks)
    values = {(t.kind, t.value) for t in toks}
    assert ("number", 1_000_000.0) in values
    assert ("percent", 25.0) in values
    assert ("number", 1_500_000.0) in values
    assert ("date", (2026, 12, 15)) in values
    assert ("date", (2027, 1, 5)) in values
    assert ("number", 2500.0) in values
    # 3.1 (section), 4 (count) and the bare year 2027 are not checked
    assert all(t.value not in (3.1, 4.0, 2027.0) for t in toks if t.kind == "number")
    assert kinds.count("date") == 2


def test_fact_numbers_cover_dates_inside_ranges():
    allowed = compose.allowed_numbers(["01/01/2027 to 01/01/2028", "$5.466 per $1,000 of gross sales"])
    assert ("date", (2027, 1, 1)) in allowed and ("date", (2028, 1, 1)) in allowed
    assert ("number", 5.466) in allowed and ("number", 1000.0) in allowed


# --------------------------------------------------------------------- validator


def test_validator_accepts_good_draft():
    assert compose.validate_draft(GOOD, make_input()) == []


def test_validator_rejects_unknown_passage_id():
    bad = draft(("executive_summary", [("Harborline totals $26,391.14.", [99])]))
    errors = compose.validate_draft(bad, make_input())
    assert any("99" in e and "passage" in e for e in errors)


def test_validator_rejects_passage_of_unverified_fact():
    # passage ids only come from cited facts; a not_found fact has none, so any other id is invalid
    bad = draft(("executive_summary", [("text", [6])]))
    assert compose.validate_draft(bad, make_input())


def test_validator_rejects_invented_number_and_withheld_value():
    invented = draft(("premium", [("The difference is $11,901.14.", [11])]))
    errors = compose.validate_draft(invented, make_input())
    assert any("11,901.14" in e for e in errors)
    # $2,500 is the value of a not_found fact: it was never verified, so prose may not state it
    withheld = draft(("coverage", [("Juniper Peak has a $2,500 deductible.", [21])]))
    assert compose.validate_draft(withheld, make_input())


def test_validator_rejects_invented_percent_and_date():
    errors = compose.validate_draft(draft(("premium", [("25% minimum earned, binds by 01/02/2027.", [11])])),
                                    make_input())
    assert len(errors) == 2


def test_validator_rejects_empty_draft():
    assert compose.validate_draft({"sections": []}, make_input())


# --------------------------------------------------------------------- tables


def test_tables_are_built_from_facts_with_states_and_column_warnings():
    tables = compose.build_tables(make_input())
    decl = tables["common_declarations"]
    assert [c.key for c in decl.columns] == ["A", "B", "C"]
    assert decl.columns[1].warning and "1 of 2" in decl.columns[1].warning
    assert "Option 1 (Primary Program)" in decl.columns[1].warning
    assert decl.columns[2].warning and "password" in decl.columns[2].warning
    row = {r.key: r for r in decl.rows}["named_insured"]
    assert row.cells["A"].value == "Cedar Hollow LLC" and row.cells["A"].passage_ids == [10]
    assert row.cells["A"].state == V and row.cells["A"].fact_ids == [4]
    assert row.cells["B"].value == "Not specified" and row.cells["B"].passage_ids == []
    assert row.cells["C"].value == "Extraction failed" and row.cells["C"].state == FL
    cov = {r.key: r for r in tables["coverage"].rows}
    assert cov["retention"].cells["B"].value == "Couldn't verify"
    assert cov["retention"].cells["B"].passage_ids == []
    prem = {r.key: r for r in tables["premium"].rows}
    assert prem["total_premium"].cells["B"].state == S and prem["total_premium"].cells["B"].passage_ids == [21]
    # a field with no fact at all for a working column reads Not specified
    assert cov["aggregate_limit"].cells["A"].value == "Not specified"
    # every section-plan field appears in exactly one table
    keys = [r.key for t in tables.values() for r in t.rows]
    assert sorted(keys) == sorted(f.key for f in FIELDS)


# --------------------------------------------------------------------- compose_report


def test_compose_success_assembles_sections_with_prose_and_tables():
    fake = FakeProvider([GOOD])
    res = run(compose.compose_report(fake, RunBudget(), make_input()))
    assert not res.fallback and res.attempts == 1 and res.errors == []
    content = res.content
    assert [s.id for s in content.sections] == [s.id for s in SECTIONS]
    by = {s.id: s for s in content.sections}
    assert by["rate"].level == 2 and by["coverage"].level == 1
    assert isinstance(by["executive_summary"].blocks[0], ParagraphBlock)
    assert any(isinstance(b, TableBlock) for b in by["coverage"].blocks)
    assert content.passage_ids() <= {10, 11, 12, 21}
    assert not any(n.kind == "compose_fallback" for n in content.notices)
    req = fake.requests[0]
    assert req.role is CallRole.COMPOSE
    # compose sees only cited values; the not_found value is withheld
    text = "\n".join(str(m.content) for m in req.messages)
    assert "$26,391.14" in text and "$2,500" not in text
    assert "<<<FACTS" in text


def test_compose_retries_once_with_validator_errors_then_succeeds():
    bad = draft(("premium", [("The difference is $11,901.14.", [11])]))
    fake = FakeProvider([bad, GOOD])
    res = run(compose.compose_report(fake, RunBudget(), make_input()))
    assert not res.fallback and res.attempts == 2
    retry_msgs = fake.requests[1].messages
    assert "11,901.14" in str(retry_msgs[-1].content)


def test_two_failed_validations_fall_back_to_template_with_notice():
    bad = draft(("premium", [("The difference is $11,901.14.", [11])]))
    fake = FakeProvider([bad, bad])
    res = run(compose.compose_report(fake, RunBudget(), make_input()))
    assert res.fallback and res.attempts == 2 and res.errors
    notice = [n for n in res.content.notices if n.kind == "compose_fallback"]
    assert len(notice) == 1 and notice[0].retryable
    # chips stay verified: tables still carry passage ids, and no model prose survives
    assert {10, 11, 12, 21} <= res.content.passage_ids()
    for _, block in res.content.iter_blocks():
        if isinstance(block, ParagraphBlock):
            assert "11,901.14" not in " ".join(s.text for s in block.segments)
    summary = res.content.sections[0]
    assert any(isinstance(b, ListBlock) for b in summary.blocks)


@pytest.mark.parametrize("script", [[FakeRefusal("no")], ["not json", "still not json"]])
def test_provider_failure_falls_back(script):
    res = run(compose.compose_report(FakeProvider(script), RunBudget(), make_input()))
    assert res.fallback
    assert any(n.kind == "compose_fallback" for n in res.content.notices)


def test_budget_exhaustion_at_compose_falls_back_without_sending():
    fake = FakeProvider([GOOD])
    res = run(compose.compose_report(fake, RunBudget(limit=50), make_input()))
    assert res.fallback and fake.requests == []


def test_runner_notices_are_kept_and_fallback_comes_first():
    extra = [Notice(kind="budget_exhausted", message="Budget ran out.")]
    res = run(compose.compose_report(FakeProvider([FakeRefusal()]), RunBudget(), make_input(notices=extra)))
    assert [n.kind for n in res.content.notices] == ["compose_fallback", "budget_exhausted"]


def test_template_report_is_deterministic_and_json_safe():
    a = compose.template_report(make_input(), reason="test")
    b = compose.template_report(make_input(), reason="test")
    assert a == b
    json.dumps(a.model_dump(mode="json"))
