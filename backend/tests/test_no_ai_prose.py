"""Slice 2 T4 (D5): no-AI-prose mode is its own deterministic renderer.

Plain per-kind text from verified facts, no compose_fallback notice, no compose
model call, byte-identical for the same facts and outline, and preserved across
retry and regenerate (both build their input through runner.compose_plan).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from backend import models
from backend.pipeline import compose, runner
from backend.pipeline.compose import ComposeColumn, ComposeEdit, ComposeFact, ProposalBrief
from backend.providers import CallRole, FakeProvider, RunBudget
from backend.schemas import EvidenceState, ListBlock, ParagraphBlock, RunStatus, TableBlock
from test_proposal_compose import BRIEF, COLUMNS, FACTS, make_input, outline, section
from test_proposal_runner import DOC_A, DOC_B, h, responder, run_proposal, stored_parameters  # noqa: F401

PLAIN = replace(BRIEF, no_ai_prose=True)


def run(coro):
    return asyncio.run(coro)


def plain_input(**kw):
    return make_input(brief=PLAIN, **kw)


def prose_as_draft(content):
    """The renderer's prose in the model's draft shape, so the validator can judge it."""
    sections = []
    for s in content.sections:
        paras = []
        for b in s.blocks:
            if isinstance(b, ParagraphBlock):
                paras.append({"segments": [seg.model_dump() for seg in b.segments]})
            elif isinstance(b, ListBlock):
                paras.extend({"segments": [seg.model_dump() for seg in item]} for item in b.items)
        if paras and s.id not in ("cover", "toc"):
            sections.append({"section_id": s.id, "paragraphs": paras})
    return {"sections": sections}


def text_of(content, sid):
    out = []
    for b in section(content, sid).blocks:
        if isinstance(b, ParagraphBlock):
            out.append("".join(s.text for s in b.segments))
        elif isinstance(b, ListBlock):
            out.extend("".join(s.text for s in item) for item in b.items)
    return "\n".join(out)


def test_no_model_call_and_no_fallback_notice():
    fake = FakeProvider([])
    res = run(compose.compose_report(fake, RunBudget(), plain_input()))
    assert fake.requests == [] and res.attempts == 0 and not res.fallback and res.errors == []
    assert not any(n.kind == "compose_fallback" for n in res.content.notices)
    assert [s.id for s in res.content.sections][:2] == ["cover", "toc"]


def test_byte_identical_for_the_same_facts_and_outline():
    a = run(compose.compose_report(FakeProvider([]), RunBudget(), plain_input())).content
    b = compose.render_plain(plain_input())
    assert a.model_dump_json() == b.model_dump_json()


def test_plain_text_per_kind_from_verified_facts_only():
    content = compose.render_plain(plain_input())
    summary = text_of(content, "executive_summary")
    assert summary.startswith("This proposal presents 2 quotes: A (Harborline), B (Juniper Peak).")
    assert "Total Premium $26,391.14" in summary and "Named Insured Cedar Hollow LLC" in summary
    assert "$14,490.00 (read from scan, unverified)" in summary
    assert "$2,500" not in summary  # a value that did not verify is never shown
    assert "Premium terms as stated in each quote" in text_of(content, "premium")
    assert [b.id for b in section(content, "premium").blocks if isinstance(b, TableBlock)] == \
        ["rate-table", "premium-table"]
    # dropped facts (no block owns cyber or endorsements) appear nowhere
    blob = content.model_dump_json()
    assert "2,000,000" not in blob and "Blanket Additional Insured" not in blob


def test_rendered_prose_passes_the_validator():
    inp = plain_input()
    content = compose.render_plain(inp)
    assert compose.validate_draft(prose_as_draft(content), inp) == []
    by_pid = {pid for s in content.sections for b in s.blocks if not isinstance(b, TableBlock)
              for seg in (b.segments if isinstance(b, ParagraphBlock) else
                          [x for item in getattr(b, "items", []) for x in item]) for pid in seg.passage_ids}
    assert by_pid and by_pid <= inp.allowed_passage_ids()


def test_edits_override_and_failed_columns_are_named():
    cols = [*COLUMNS, ComposeColumn(key="C", label="c.pdf", document_id=3, filename="c.pdf", failed=True,
                                    failure_message="This PDF is password-protected.")]
    inp = plain_input(edits_=[ComposeEdit("A", "total_premium", "$30,000.00", "$26,391.14")])
    inp = replace(inp, columns=cols)
    content = compose.render_plain(inp)
    summary = text_of(content, "executive_summary")
    assert "Total Premium $30,000.00 (edited by the user)" in summary and "26,391.14" not in summary
    assert "C (c.pdf): Extraction failed: This PDF is password-protected." in summary
    assert compose.validate_draft(prose_as_draft(content), inp) == []


def test_conflicting_values_are_listed_not_picked():
    facts = [*FACTS, ComposeFact("B", "named_insured", None, EvidenceState.CONFLICTING, fact_id=9,
                                 conflicts=(("Cedar Hollow LLC", 1, 31), ("Cedar Hollow Inc", 4, 32)))]
    inp = plain_input(facts=facts)
    summary = text_of(compose.render_plain(inp), "executive_summary")
    assert "Named Insured: conflicting values Cedar Hollow LLC (page 1) and Cedar Hollow Inc (page 4)" in summary
    assert compose.validate_draft(prose_as_draft(compose.render_plain(inp)), inp) == []


def test_preserved_across_retry_and_regenerate_input(h):  # noqa: F811
    a, b = h.add_doc(DOC_A, name="a.pdf"), h.add_doc(DOC_B, name="b.pdf")
    fake = FakeProvider(responder)
    run_id = run_proposal(h, fake, stored_parameters(no_ai_prose=True), [a, b])
    assert not any(r.role is CallRole.COMPOSE for r in fake.requests)
    s, run_row = h.run_row(run_id)
    assert run_row.status == RunStatus.DONE
    first = run_row.artifact.latest_content()
    assert not any(n.kind == "compose_fallback" for n in first.notices)
    # Regenerate and retry both compose from compose_input_for_run: still the plain renderer
    inp = runner.compose_input_for_run(run_row)
    assert inp.brief.no_ai_prose
    again = run(compose.compose_report(FakeProvider([]), RunBudget(), inp))
    assert again.content.model_dump_json() == first.model_dump_json()
    assert isinstance(run_row, models.Run)
    s.close()


def test_without_broker_fields_or_facts_still_renders():
    inp = make_input(brief=ProposalBrief(title="Insurance Proposal", no_ai_prose=True), facts=[])
    content = compose.render_plain(inp)
    assert "no verified values" in text_of(content, "client_details") or \
        "Client and carrier details" in text_of(content, "client_details")
    assert compose.validate_draft(prose_as_draft(content), inp) == []
