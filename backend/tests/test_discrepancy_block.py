"""Slice 3 T5 (D7 A, outside finding 6, D12 A): the DiscrepancyBlock in the artifact.

A Policy Check discrepancy is one block in the ordinary ArtifactContent, so
versions, chips, stale flags, chat and export keep working. These tests pin:
the block's shape and its closed role and severity sets; that passage_ids()
sees the citations inside the slots (current and stored versions); that every
artifact version stored before the union grew still validates unchanged; and
that the DOCX export and chat handle the block.
"""

from __future__ import annotations

import io

import pytest
from docx import Document
from pydantic import ValidationError
from sqlalchemy import select

from api_support import DOC_A, api_factory, api_responder, context_payload  # noqa: F401
from backend import chat, db, edits, export, export_docx, models
from backend.schemas import (
    ArtifactContent,
    Cell,
    ConflictValue,
    DiscrepancyBlock,
    DiscrepancyItemState,
    EvidenceState,
    PolicyRole,
    Section,
    Segment,
    Severity,
)
from test_export_docx import CHIPS, footnotes, slice1_content

V = EvidenceState.VERIFIED


def coverage_a() -> DiscrepancyBlock:
    return DiscrepancyBlock(
        id="disc-coverage_a_limit", key="coverage_a_limit", title="Coverage A limit decreased",
        severity=Severity.CRITICAL, lob="Homeowners",
        prose=[Segment(text="Coverage A drops from $350,000 to $300,000 at renewal.", passage_ids=[1, 2])],
        slots={
            PolicyRole.EXPIRING: Cell(value="$350,000", passage_ids=[1], state=V),
            PolicyRole.RENEWAL: Cell(value="$300,000", passage_ids=[2], state=V),
            PolicyRole.QUOTE: Cell(value="$300,000", passage_ids=[5], state=EvidenceState.SCAN_READ),
            # no AMS slot: the role is N/A
        })


def deductible_needs_review() -> DiscrepancyBlock:
    return DiscrepancyBlock(
        id="disc-deductible_aop", key="deductible_aop", title="All-perils deductible needs review",
        severity=Severity.LOW, item_state=DiscrepancyItemState.NEEDS_REVIEW,
        prose=[Segment(text="The renewal states two deductibles.", passage_ids=[])],
        slots={
            PolicyRole.EXPIRING: Cell(value="$1,000", passage_ids=[1], state=V),
            PolicyRole.RENEWAL: Cell(value=None, passage_ids=[3, 4], state=EvidenceState.CONFLICTING,
                                     conflicts=[ConflictValue(value="$5,000", page_no=20, passage_id=3),
                                                ConflictValue(value="$7,500", page_no=90, passage_id=4)]),
            PolicyRole.QUOTE: Cell(value=None, state=EvidenceState.FAILED),  # D11: Not read
            PolicyRole.AMS: Cell(value=None, state=EvidenceState.ABSENT),  # N/A
        })


def policy_check_content() -> ArtifactContent:
    return ArtifactContent(sections=[Section(id="discrepancies", number="1", title="Discrepancies",
                                             blocks=[coverage_a(), deductible_needs_review()])])


# --------------------------------------------------------------------- schema


def test_discrepancy_block_validates_in_the_block_union_and_round_trips():
    raw = policy_check_content().model_dump(mode="json")
    block = raw["sections"][0]["blocks"][0]
    assert block["type"] == "discrepancy"
    assert set(block["slots"]) == {"expiring", "renewal", "quote"}
    assert block["severity"] == "critical" and block["item_state"] == "discrepancy"
    back = ArtifactContent.model_validate(raw)
    assert isinstance(back.sections[0].blocks[0], DiscrepancyBlock)
    assert back == policy_check_content()
    assert back.model_dump(mode="json") == raw


def test_discrepancy_defaults():
    d = DiscrepancyBlock(id="d", key="k", title="t", severity="high")
    assert d.item_state == DiscrepancyItemState.DISCREPANCY
    assert d.lob is None and d.prose == [] and d.slots == {}
    assert d.slot(PolicyRole.AMS) is None


@pytest.mark.parametrize("bad", [
    {"slots": {"broker": {"value": "x"}}},  # roles are a closed set
    {"severity": "urgent"},  # severity tiers are a closed set
    {"severity": "Critical"},  # stored lowercase only
    {"item_state": "resolved"},  # resolve state lives in discrepancy_state, not the artifact
])
def test_discrepancy_rejects_values_outside_the_closed_sets(bad):
    raw = {"type": "discrepancy", "id": "d", "key": "k", "title": "t", "severity": "low", **bad}
    with pytest.raises(ValidationError):
        ArtifactContent.model_validate({"sections": [{"id": "s", "number": "1", "title": "S", "blocks": [raw]}]})


def test_roles_and_severities_are_ordered_for_display():
    assert [r.value for r in PolicyRole] == ["expiring", "quote", "renewal", "ams"]
    assert [s.value for s in Severity] == ["critical", "high", "medium", "low"]
    assert [s.label for s in Severity] == ["Critical", "High", "Medium", "Low"]
    assert [r.label for r in PolicyRole] == ["Expiring", "Quote", "Renewal", "AMS"]


def test_passage_ids_sees_citations_inside_slots_and_prose():
    content = policy_check_content()
    assert content.passage_ids() == {1, 2, 3, 4, 5}
    # chips come from all_passage_ids (views.chips), so slot citations get chips too
    assert edits.all_passage_ids(content) == {1, 2, 3, 4, 5}
    only_slots = DiscrepancyBlock(id="d", key="k", title="t", severity="low",
                                  slots={PolicyRole.AMS: Cell(value="$1", passage_ids=[9], state=V)})
    assert only_slots.passage_ids() == {9}


def test_discrepancy_slots_are_not_editable_table_cells():
    content = policy_check_content()
    assert list(edits.iter_cells(content)) == []
    with pytest.raises(KeyError):
        content.edit_cell("disc-coverage_a_limit", "coverage_a_limit", "renewal", "$1")


# --------------------------------------------------------------------- stored versions (D12 A)


OLD_VERSION = {  # a slice 1 / slice 2 version as stored before the union grew: every old block type
    "sections": [
        {"id": "cover", "number": "", "title": "Cover Page", "level": 1, "stale": False, "blocks": [
            {"type": "heading", "id": "cover-title", "level": 1, "text": "Proposal"}]},
        {"id": "s1", "number": "1", "title": "Summary", "level": 1, "stale": True, "blocks": [
            {"type": "paragraph", "id": "p", "segments": [{"text": "A costs $100.", "passage_ids": [1]}]},
            {"type": "list", "id": "l", "title": "Key points",
             "items": [[{"text": "Limit doubled.", "passage_ids": [2]}]]},
            {"type": "issue", "id": "i", "code": "ISSUE-001", "title": "Gap", "severity": "High",
             "category": "Coverage", "impact": [{"text": "x", "passage_ids": [3]}],
             "affected_layers": ["primary"], "follow_form": None}]},
        {"id": "s2", "number": "2", "title": "Table", "level": 1, "stale": False, "blocks": [
            {"type": "table", "id": "t", "title": None, "columns": [
                {"key": "A", "label": "Harborline", "document_id": 1, "warning": None}],
             "rows": [{"key": "premium", "label": "Premium", "cells": {"A": {
                 "value": "$200", "passage_ids": [], "fact_ids": [7], "state": "verified", "edited": True,
                 "original_value": "$100", "original_passage_ids": [4], "notice": None, "conflicts": []}}}]}]},
    ],
    "notices": [{"kind": "info", "message": "m", "retryable": False, "dismissible": False}],
}


def test_versions_stored_before_the_union_grew_still_validate_unchanged():
    content = ArtifactContent.model_validate(OLD_VERSION)
    assert content.model_dump(mode="json") == OLD_VERSION
    assert content.passage_ids() == {1, 2, 3}
    # the minimal stored shape (defaults omitted) of slice 1 also still loads
    minimal = {"sections": [{"id": "s", "number": "1", "title": "S", "blocks": [
        {"type": "paragraph", "id": "p", "segments": [{"text": "t"}]}]}]}
    assert ArtifactContent.model_validate(minimal).sections[0].blocks[0].segments[0].passage_ids == []
    # the content the existing export tests build still round-trips
    old = slice1_content()
    assert ArtifactContent.model_validate(old.model_dump(mode="json")) == old


def _run(session):
    wf = session.scalar(select(models.Workflow).where(models.Workflow.slug == "compare-quotes"))
    run = models.Run(workspace_id=db.DEFAULT_WORKSPACE_ID, workflow_id=wf.id, parameters={})
    session.add(run)
    session.flush()
    return run


def test_old_and_new_versions_load_from_the_database(session):
    run = _run(session)
    art = models.Artifact(run_id=run.id, title="Report", current_version=1)
    session.add(art)
    session.flush()
    session.add(models.ArtifactVersion(artifact_id=art.id, version=1, content=OLD_VERSION))
    session.flush()
    assert art.latest_content().model_dump(mode="json") == OLD_VERSION
    models.snapshot_version(session, art, policy_check_content(), note="policy check")
    session.flush()
    session.expire_all()
    assert art.current_version == 2
    latest = art.latest_content()
    assert latest == policy_check_content() and latest.passage_ids() == {1, 2, 3, 4, 5}
    v1 = ArtifactContent.model_validate(art.version_row(1).content)
    assert v1.model_dump(mode="json") == OLD_VERSION
    models.restore_version(session, art, 1)
    session.flush()
    assert art.latest_content().model_dump(mode="json") == OLD_VERSION


# --------------------------------------------------------------------- DOCX export


def _policy_data(content: ArtifactContent) -> export.ExportData:
    return export.ExportData(title="Policy Check", run_id=9, version=1, content=content, chips=CHIPS)


def _text(blob: bytes) -> str:
    doc = Document(io.BytesIO(blob))
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            parts.extend(c.text for c in row.cells)
    return "\n".join(parts)


def test_slot_text_states():
    assert export_docx.slot_text(None) == "N/A"
    assert export_docx.slot_text(Cell(value=None, state=EvidenceState.ABSENT)) == "N/A"
    assert export_docx.slot_text(Cell(value="$1", state=EvidenceState.FAILED)) == "Not read"
    assert export_docx.slot_text(Cell(value="$300,000", passage_ids=[2], state=V)) == "$300,000"
    assert export_docx.slot_text(Cell(value="$9", state=EvidenceState.SCAN_READ)) == \
        "$9 (Read from scan, unverified)"
    assert export_docx.slot_text(Cell(value="$9", state=EvidenceState.NOT_FOUND)) == \
        "Needs review: couldn't verify"
    conflicting = deductible_needs_review().slot(PolicyRole.RENEWAL)
    assert export_docx.slot_text(conflicting) == "Needs review: $5,000 (page 20); $7,500 (page 90)"


def test_docx_export_writes_discrepancies_with_slots_and_footnotes():
    blob = export_docx.to_docx(_policy_data(policy_check_content()))
    text = _text(blob)
    assert "Coverage A limit decreased" in text and "Critical" in text and "Homeowners" in text
    assert "All-perils deductible needs review" in text and "Needs review" in text
    for label in ("Expiring", "Quote", "Renewal", "AMS"):
        assert label in text
    assert "$350,000" in text and "$300,000 (Read from scan, unverified)" in text
    assert "Not read" in text and "N/A" in text
    assert "Needs review: $5,000 (page 20); $7,500 (page 90)" in text
    notes = list(footnotes(blob).values())
    assert any("A1: a.pdf page 1" in n for n in notes)  # expiring slot chip
    assert any("A2: a.pdf page 3" in n for n in notes)  # renewal slot chip
    assert any("Read from scan, unverified" in n and "B1: b.pdf page 2" in n for n in notes)
    assert any("Conflicting values" in n and "A3: a.pdf page 20" in n for n in notes)
    # deterministic, like every other export
    assert export_docx.to_docx(_policy_data(policy_check_content())) == blob


def test_docx_export_of_a_mixed_report_keeps_the_existing_blocks():
    mixed = slice1_content()
    mixed.sections.append(policy_check_content().sections[0])
    text = _text(export_docx.to_docx(_policy_data(mixed)))
    assert "Harborline costs $26,391.14." in text and "Coverage A limit decreased" in text


# --------------------------------------------------------------------- chat


def test_chat_section_prose_includes_discrepancies():
    prose = chat._section_prose(policy_check_content().sections[0])
    assert "Coverage A limit decreased" in prose and "[passages 1, 2]" in prose
    assert "All-perils deductible needs review" in prose


def test_chat_discrepancy_items_carry_slots_and_values():
    items, values = chat.discrepancy_items(policy_check_content())
    first, second = items
    assert first["id"] == "disc-coverage_a_limit" and first["severity"] == "critical"
    assert first["state"] == "discrepancy" and first["lob"] == "Homeowners"
    assert [s["role"] for s in first["slots"]] == ["expiring", "quote", "renewal", "ams"]
    by_role = {s["role"]: s for s in first["slots"]}
    assert by_role["renewal"] == {"role": "renewal", "value": "$300,000", "evidence": "verified",
                                  "passage_ids": [2]}
    assert by_role["ams"] == {"role": "ams", "value": None, "evidence": "not_applicable", "passage_ids": []}
    by_role2 = {s["role"]: s for s in second["slots"]}
    assert second["state"] == "needs_review"
    assert by_role2["quote"]["evidence"] == "not_read"
    assert by_role2["renewal"]["conflicts"] == [{"value": "$5,000", "page": 20, "passage_id": 3},
                                                {"value": "$7,500", "page": 90, "passage_id": 4}]
    assert {"$350,000", "$300,000", "$1,000", "$5,000", "$7,500"} <= set(values)
    assert chat.discrepancy_items(slice1_content()) == ([], [])


def test_chat_outline_names_discrepancies():
    outline = chat._outline(policy_check_content())
    assert outline["discrepancies"] == ["Coverage A limit decreased", "All-perils deductible needs review"]
    assert "discrepancies" not in chat._outline(slice1_content())


def test_chat_sees_discrepancies_and_may_cite_their_slots(api_factory):
    seen = {}

    def answer(req):
        payload = context_payload(req)
        seen["payload"] = payload
        item = payload["report"]["discrepancies"][0]
        slot = next(s for s in item["slots"] if s["role"] == "renewal")
        # $20,000.00 is uncited: it is allowed only because it is a slot value of the report
        return {"answer": [{"text": f"The renewal states {slot['value']}; the expiring policy stated $20,000.00.",
                            "passage_ids": slot["passage_ids"]}], "proposed_edits": []}

    api = api_factory(api_responder(chat=answer))
    run, _ = api.run([api.upload_pages(DOC_A)])
    art = api.artifact(run["id"])
    with db.session_scope(api.ctx.factory) as s:
        row = s.get(models.Artifact, art["id"])
        cited = sorted(row.latest_content().passage_ids())
        # a report whose only citations sit inside discrepancy slots
        only_slots = ArtifactContent(sections=[Section(id="discrepancies", number="1", title="Discrepancies", blocks=[
            DiscrepancyBlock(id="disc-premium", key="total_premium", title="Premium changed", severity="high",
                             slots={PolicyRole.EXPIRING: Cell(value="$20,000.00", state=V),
                                    PolicyRole.RENEWAL: Cell(value="$26,391.14", passage_ids=cited[:1],
                                                             state=V)})])])
        models.snapshot_version(s, row, only_slots, note="policy check")
    # the artifact view gives the slot citation a chip
    view = api.artifact(run["id"])
    assert view["current_version"] == 2 and list(view["chips"]) == [str(cited[0])]
    r = api.client.post(f"/api/runs/{run['id']}/messages", json={"content": "What does the renewal say?"})
    assert r.status_code == 201, r.text
    bot = r.json()["assistant_message"]
    assert bot["passage_ids"] == cited[:1] and "$20,000.00" in bot["content"]
    payload = seen["payload"]
    assert payload["report"]["discrepancies"][0]["title"] == "Premium changed"
    assert payload["report"]["table_cells"] == []
    # the DOCX export endpoint handles the version too
    out = api.client.get(f"/api/artifacts/{art['id']}/export", params={"format": "docx"})
    assert out.status_code == 200, out.text
    assert "Premium changed" in _text(out.content)
