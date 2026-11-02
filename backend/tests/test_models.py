"""T1: data model, evidence states, run states, passages, artifact versions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend import db, models
from backend.schemas import (
    ArtifactContent,
    EvidenceState,
    RunDocumentStatus,
    RunStatus,
)


# ---------------------------------------------------------------- helpers


def _document(session, name="carrier-a.pdf", pages=3):
    doc = models.Document(
        workspace_id=db.DEFAULT_WORKSPACE_ID,
        filename=name,
        stored_name=f"{name}.uuid",
        size_bytes=1000,
        sha256="0" * 64,
        page_count=pages,
    )
    session.add(doc)
    session.flush()
    for n in range(1, pages + 1):
        session.add(models.Page(document_id=doc.id, page_no=n, text=f"page {n}", words=[]))
    session.flush()
    return doc


def _run(session, docs=()):
    wf = session.scalar(select(models.Workflow).where(models.Workflow.slug == "compare-quotes"))
    run = models.Run(workspace_id=db.DEFAULT_WORKSPACE_ID, workflow_id=wf.id, parameters={})
    session.add(run)
    session.flush()
    for pos, doc in enumerate(docs):
        session.add(models.RunDocument(run_id=run.id, document_id=doc.id, position=pos))
    session.flush()
    return run


def _content(value="Acme LLC", passage_ids=(1,)):
    return ArtifactContent.model_validate(
        {
            "sections": [
                {
                    "id": "s1",
                    "number": "1",
                    "title": "Executive Summary",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "id": "p1",
                            "segments": [{"text": "Two quotes compared.", "passage_ids": [1]}],
                        }
                    ],
                },
                {
                    "id": "s2",
                    "number": "2",
                    "title": "Common Declarations",
                    "blocks": [
                        {
                            "type": "table",
                            "id": "t1",
                            "columns": [{"key": "A", "label": "Carrier A", "document_id": 1}],
                            "rows": [
                                {
                                    "key": "named_insured",
                                    "label": "Named Insured",
                                    "cells": {
                                        "A": {
                                            "value": value,
                                            "passage_ids": list(passage_ids),
                                            "fact_ids": [7],
                                            "state": "verified",
                                        }
                                    },
                                }
                            ],
                        }
                    ],
                },
            ]
        }
    )


# ---------------------------------------------------------------- schema / seed


def test_init_db_seeds_workspace_and_compare_quotes_idempotently():
    engine = db.make_engine("sqlite://")
    db.init_db(engine)
    db.init_db(engine)  # second call must not duplicate seed rows
    factory = db.make_session_factory(engine)
    with factory() as s:
        workflows = s.scalars(select(models.Workflow)).all()
        assert [w.slug for w in workflows] == ["compare-quotes"]
        wf = workflows[0]
        assert wf.name == "Compare Quotes"
        keys = [f["key"] for f in wf.fact_schema]
        for required in (
            "named_insured",
            "address",
            "admitted_status",
            "am_best_rating",
            "policy_term",
            "territory",
            "producer",
            "policy_form",
        ):
            assert required in keys
        titles = [s_["title"] for s_ in wf.section_plan]
        assert titles[0] == "Executive Summary"
        assert "Cross-Quote Consistency Flags" in titles
        assert s.get(models.Workspace, db.DEFAULT_WORKSPACE_ID) is not None


def test_sqlite_enforces_foreign_keys(session):
    session.add(models.Page(document_id=9999, page_no=1, text="x", words=[]))
    with pytest.raises(IntegrityError):
        session.flush()


def test_page_unique_per_document_and_page_no(session):
    doc = _document(session, pages=1)
    session.add(models.Page(document_id=doc.id, page_no=1, text="dup", words=[]))
    with pytest.raises(IntegrityError):
        session.flush()


def test_run_document_letter_and_unique_position(session):
    a, b = _document(session, "a.pdf"), _document(session, "b.pdf")
    run = _run(session, [a, b])
    letters = sorted(rd.letter for rd in run.documents)
    assert letters == ["A", "B"]
    assert models.document_letter(7) == "H"
    with pytest.raises(ValueError):
        models.document_letter(8)


# ---------------------------------------------------------------- evidence states


def test_fact_defaults_to_pending_and_moves_to_terminal_state(session):
    doc = _document(session)
    run = _run(session, [doc])
    fact = models.Fact(run_id=run.id, document_id=doc.id, field="named_insured", value="Acme")
    session.add(fact)
    session.flush()
    assert fact.state == EvidenceState.PENDING
    models.transition_fact(fact, EvidenceState.VERIFIED)
    assert fact.state == EvidenceState.VERIFIED


@pytest.mark.parametrize(
    "target",
    [EvidenceState.VERIFIED, EvidenceState.SCAN_READ, EvidenceState.NOT_FOUND,
     EvidenceState.ABSENT, EvidenceState.FAILED],
)
def test_every_terminal_state_reachable_from_pending(target):
    fact = models.Fact(field="f", state=EvidenceState.PENDING)
    models.transition_fact(fact, target)
    assert fact.state == target


@pytest.mark.parametrize(
    "start,target",
    [
        (EvidenceState.VERIFIED, EvidenceState.NOT_FOUND),
        (EvidenceState.NOT_FOUND, EvidenceState.VERIFIED),
        (EvidenceState.SCAN_READ, EvidenceState.VERIFIED),
        (EvidenceState.ABSENT, EvidenceState.VERIFIED),
        (EvidenceState.FAILED, EvidenceState.VERIFIED),
        (EvidenceState.PENDING, EvidenceState.PENDING),
    ],
)
def test_illegal_fact_transitions_raise(start, target):
    fact = models.Fact(field="f", state=start)
    with pytest.raises(models.InvalidTransition):
        models.transition_fact(fact, target)
    assert fact.state == start


def test_failed_fact_can_be_retried():
    fact = models.Fact(field="f", state=EvidenceState.FAILED)
    models.transition_fact(fact, EvidenceState.PENDING)
    models.transition_fact(fact, EvidenceState.VERIFIED)
    assert fact.state == EvidenceState.VERIFIED


def test_display_text_per_evidence_state():
    assert models.display_value(EvidenceState.VERIFIED, "Acme") == "Acme"
    assert models.display_value(EvidenceState.SCAN_READ, "Acme") == "Acme"
    assert models.display_value(EvidenceState.ABSENT, None) == "Not specified"
    assert models.display_value(EvidenceState.NOT_FOUND, "Acme") == "Couldn't verify"
    assert models.display_value(EvidenceState.FAILED, None) == "Extraction failed"
    assert models.display_value(EvidenceState.PENDING, "Acme") == "Not specified"


# ---------------------------------------------------------------- run states


def test_run_happy_path_and_retry(session):
    run = _run(session)
    assert run.status == RunStatus.QUEUED
    for s in (RunStatus.RUNNING, RunStatus.COMPOSING, RunStatus.PARTIAL):
        models.transition_run(run, s)
    models.transition_run(run, RunStatus.QUEUED)  # retry failed documents
    assert run.status == RunStatus.QUEUED


@pytest.mark.parametrize(
    "start,target",
    [
        (RunStatus.QUEUED, RunStatus.DONE),
        (RunStatus.DONE, RunStatus.RUNNING),
        (RunStatus.RUNNING, RunStatus.QUEUED),
    ],
)
def test_illegal_run_transitions_raise(start, target):
    run = models.Run(status=start)
    with pytest.raises(models.InvalidTransition):
        models.transition_run(run, target)


def test_run_document_status_flow():
    rd = models.RunDocument(status=RunDocumentStatus.QUEUED)
    for s in (RunDocumentStatus.READING, RunDocumentStatus.EXTRACTING,
              RunDocumentStatus.VERIFYING, RunDocumentStatus.DONE):
        models.transition_run_document(rd, s)
    with pytest.raises(models.InvalidTransition):
        models.transition_run_document(rd, RunDocumentStatus.EXTRACTING)
    rd2 = models.RunDocument(status=RunDocumentStatus.EXTRACTING)
    models.transition_run_document(rd2, RunDocumentStatus.FAILED)
    models.transition_run_document(rd2, RunDocumentStatus.QUEUED)


def test_record_model_call_accumulates_on_run(session):
    run = _run(session)
    models.record_model_call(session, run, purpose="extract", model="m", tokens_in=100,
                             tokens_out=20, cost_usd=0.01)
    models.record_model_call(session, run, purpose="compose", model="m", tokens_in=50,
                             tokens_out=10, cost_usd=0.005)
    session.flush()
    assert (run.tokens_in, run.tokens_out) == (150, 30)
    assert run.cost_usd == pytest.approx(0.015)
    assert len(run.model_calls) == 2


# ---------------------------------------------------------------- passages and chips


def test_passage_upsert_is_keyed_by_document_and_page(session):
    doc = _document(session)
    p1 = models.upsert_passage(session, doc.id, 2, verified=False, rects=[])
    p2 = models.upsert_passage(session, doc.id, 2, verified=True, rects=[[1, 2, 3, 4]])
    p3 = models.upsert_passage(session, doc.id, 2, verified=False, rects=[[5, 6, 7, 8]])
    assert p1.id == p2.id == p3.id
    assert p3.verified is True  # once digitally verified, stays verified
    assert p3.rects == [[1, 2, 3, 4], [5, 6, 7, 8]]
    other = models.upsert_passage(session, doc.id, 3, verified=True, rects=[])
    assert other.id != p1.id


def test_passage_unique_constraint(session):
    doc = _document(session)
    session.add(models.Passage(document_id=doc.id, page_no=1, verified=True, rects=[]))
    session.add(models.Passage(document_id=doc.id, page_no=1, verified=True, rects=[]))
    with pytest.raises(IntegrityError):
        session.flush()


def test_chip_labels_number_pages_per_document():
    labels = models.assign_chip_labels(
        [
            (11, "A", 4),
            (10, "A", 2),
            (12, "B", 7),
            (13, "B", 1),
            (10, "A", 2),  # duplicate reference to the same passage
        ]
    )
    assert labels == {10: "A1", 11: "A2", 13: "B1", 12: "B2"}


# ---------------------------------------------------------------- artifacts


def test_artifact_versions_snapshot_on_every_change(session):
    run = _run(session)
    art = models.create_artifact(session, run, title="Compare Quotes", content=_content())
    assert art.current_version == 1
    first = art.latest_content()
    edited = first.edit_cell("t1", "named_insured", "A", "Acme Holdings LLC")
    models.snapshot_version(session, art, edited, note="edit named_insured/A")
    session.flush()
    assert art.current_version == 2
    versions = sorted(art.versions, key=lambda v: v.version)
    assert [v.version for v in versions] == [1, 2]
    # version 1 is untouched by the edit
    v1 = ArtifactContent.model_validate(versions[0].content)
    assert v1.find_cell("t1", "named_insured", "A").value == "Acme LLC"
    assert art.latest_content().find_cell("t1", "named_insured", "A").value == "Acme Holdings LLC"


def test_restore_version_creates_new_snapshot(session):
    run = _run(session)
    art = models.create_artifact(session, run, title="CQ", content=_content())
    models.snapshot_version(
        session, art, art.latest_content().edit_cell("t1", "named_insured", "A", "X"), note="e"
    )
    models.restore_version(session, art, 1)
    session.flush()
    assert art.current_version == 3
    assert art.latest_content().find_cell("t1", "named_insured", "A").value == "Acme LLC"
    with pytest.raises(LookupError):
        models.restore_version(session, art, 99)


def test_version_numbers_unique_per_artifact(session):
    run = _run(session)
    art = models.create_artifact(session, run, title="CQ", content=_content())
    session.add(models.ArtifactVersion(artifact_id=art.id, version=1, content={"sections": []}))
    with pytest.raises(IntegrityError):
        session.flush()


def test_feedback_is_stored_against_a_version(session):
    run = _run(session)
    art = models.create_artifact(session, run, title="CQ", content=_content())
    session.add(models.Feedback(artifact_id=art.id, version=1, rating=1))
    session.flush()
    assert art.feedback[0].rating == 1
    session.add(models.Feedback(artifact_id=art.id, version=1, rating=5))
    with pytest.raises(IntegrityError):
        session.flush()


# ---------------------------------------------------------------- artifact content


def test_edit_cell_marks_edited_and_folds_chips_into_original_source():
    content = _content(passage_ids=(3, 4))
    once = content.edit_cell("t1", "named_insured", "A", "Acme Holdings")
    cell = once.find_cell("t1", "named_insured", "A")
    assert cell.edited is True
    assert cell.value == "Acme Holdings"
    assert cell.passage_ids == []
    assert cell.original_passage_ids == [3, 4]
    assert cell.original_value == "Acme LLC"
    twice = once.edit_cell("t1", "named_insured", "A", "Acme Group")
    cell2 = twice.find_cell("t1", "named_insured", "A")
    assert cell2.original_value == "Acme LLC"  # original stays the extracted one
    assert cell2.original_passage_ids == [3, 4]
    # the source content is not mutated
    assert content.find_cell("t1", "named_insured", "A").edited is False


def test_edit_cell_unknown_target_raises():
    with pytest.raises(KeyError):
        _content().edit_cell("t1", "nope", "A", "v")
    with pytest.raises(KeyError):
        _content().edit_cell("missing", "named_insured", "A", "v")


def test_artifact_content_rejects_unknown_block_type():
    with pytest.raises(ValidationError):
        ArtifactContent.model_validate(
            {"sections": [{"id": "s", "number": "1", "title": "t",
                           "blocks": [{"type": "video", "id": "v"}]}]}
        )


def test_artifact_content_collects_cited_passages():
    content = _content(passage_ids=(3,))
    assert content.passage_ids() == {1, 3}
