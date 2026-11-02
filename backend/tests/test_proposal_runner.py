"""Slice 2 T2: one shared plan helper (S2) for the run, retry and regenerate.

`runner.compose_plan(run)` reads the proposal outline from run.parameters for the
proposal-builder workflow and the static section plan otherwise; both the run
(`_begin`) and `compose_input_for_run` (retry recompose, regenerate) use it.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from sqlalchemy import select

from backend import db, models
from backend.pipeline import runner
from backend.providers import CallRole, FakeProvider
from backend.providers.fake import FakeRefusal
from backend.schemas import RunStatus, TableBlock
from pipeline_fakes import smart_responder
from test_runner import Harness

STANDARD = next(t for t in db.PROPOSAL_TEMPLATES if t["slug"] == "standard")
DOC_A = ["Carrier: Harborline Specialty\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $26,391.14\n"
         "Key Endorsements: Blanket Additional Insured",
         "Each Occurrence Limit: $1,000,000"]
DOC_B = ["Carrier: Juniper Peak\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $14,490.00"]


def stored_parameters(*, blocks=None, no_ai_prose=False, **over) -> dict:
    blocks = STANDARD["blocks"] if blocks is None else blocks
    return {"template_id": 1, "broker_name": "Dana Ruiz", "brokerage_name": "Ruiz and Partners",
            "client_name": "Cedar Hollow LLC", "additional_context": "", "no_ai_prose": no_ai_prose,
            "outline": blocks,
            "template": {"id": 1, "slug": "standard", "name": "Standard", "blocks": STANDARD["blocks"],
                         "theme": STANDARD["theme"], "variables": STANDARD["variables"]}, **over}


def proposal_compose(req):
    text = "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts())
    payload = json.loads(re.search(r"<<<FACTS (\w+)\n(.*)\nFACTS \1>>>", text, re.S).group(2))
    out = []
    for sec in payload["sections"]:
        if sec["facts"]:
            f = sec["facts"][0]
            out.append({"section_id": sec["section_id"], "paragraphs": [{"segments": [
                {"text": f"{f['document']} states {f['label']} {f['value']}.", "passage_ids": [f["passage_id"]]}]}]})
    return {"sections": out}


def responder(req):
    if req.role is CallRole.COMPOSE:
        return proposal_compose(req)
    return smart_responder()(req)


@pytest.fixture
def h(pdf_factory):
    harness = Harness(pdf_factory)
    with harness.factory() as s:
        harness.proposal_id = s.scalar(select(models.Workflow.id).where(
            models.Workflow.slug == db.PROPOSAL_BUILDER_SLUG))
    yield harness
    harness.engine.dispose()


def go(coro):
    return asyncio.run(coro)


def run_proposal(h, fake, params, docs):
    async def main():
        m = h.manager(fake)
        run_id, _ = m.submit(h.proposal_id, docs, params)
        await m.wait(run_id)
        await m.shutdown()
        return run_id
    return go(main())


def test_compare_quotes_ignores_an_outline_in_its_parameters(h):
    a = h.add_doc(DOC_A)
    with db.session_scope(h.factory) as s:
        run = models.Run(workspace_id=1, workflow_id=h.workflow_id, parameters={"outline": STANDARD["blocks"]},
                         status=RunStatus.QUEUED)
        run.documents.append(models.RunDocument(document_id=a, position=0, status="queued", option_count=1))
        s.add(run)
        s.flush()
        plan = runner.compose_plan(run)
        assert plan.outline == () and plan.brief is None
        assert [x.id for x in plan.sections] == [x["id"] for x in db.COMPARE_QUOTES["section_plan"]]
        assert plan.title == "Compare Quotes"


def test_proposal_plan_reads_the_outline_and_brief(h):
    a = h.add_doc(DOC_A)
    with db.session_scope(h.factory) as s:
        run = models.Run(workspace_id=1, workflow_id=h.proposal_id, parameters=stored_parameters(),
                         status=RunStatus.QUEUED)
        run.documents.append(models.RunDocument(document_id=a, position=0, status="queued", option_count=1))
        s.add(run)
        s.flush()
        plan = runner.compose_plan(run)
        assert [b.id for b in plan.outline] == [b["id"] for b in STANDARD["blocks"]]
        assert [x.id for x in plan.sections][:2] == ["cover", "toc"]
        assert plan.title == "Insurance Proposal for Cedar Hollow LLC"
        assert plan.brief.broker_name == "Dana Ruiz" and plan.brief.no_ai_prose is False
        assert {x.id for x in plan.fact_sections} >= {"cyber", "eo"}
        # the run and compose_input_for_run (retry, regenerate) share the plan
        inp = runner.compose_input_for_run(run)
        assert inp.outline == plan.outline and inp.brief == plan.brief and inp.title == plan.title


def test_proposal_plan_rejects_a_tampered_outline(h):
    blocks = [b for b in STANDARD["blocks"] if b["id"] != "cover"]
    with db.session_scope(h.factory) as s:
        run = models.Run(workspace_id=1, workflow_id=h.proposal_id, parameters=stored_parameters(blocks=blocks),
                         status=RunStatus.QUEUED)
        s.add(run)
        s.flush()
        with pytest.raises(ValueError):
            runner.compose_plan(run)


def test_proposal_run_end_to_end_drops_unowned_facts(h):
    a, b = h.add_doc(DOC_A, name="a.pdf"), h.add_doc(DOC_B, name="b.pdf")
    fake = FakeProvider(responder)
    run_id = run_proposal(h, fake, stored_parameters(), [a, b])
    s, run = h.run_row(run_id)
    assert run.status == RunStatus.DONE
    assert run.artifact.title == "Insurance Proposal for Cedar Hollow LLC"
    content = run.artifact.latest_content()
    assert [x.id for x in content.sections] == [b["id"] for b in STANDARD["blocks"]]
    rows = {r.key for _, blk in content.iter_blocks() if isinstance(blk, TableBlock) for r in blk.rows}
    assert "total_premium" in rows and "key_endorsements" not in rows
    compose_req = next(r for r in fake.requests if r.role is CallRole.COMPOSE)
    text = "\n".join(p.text for m in compose_req.messages for p in m.parts())
    assert "Blanket Additional Insured" not in text and "<<<BRIEF" in text
    s.close()


def test_proposal_retry_recomposes_from_the_same_outline(h):
    a, b = h.add_doc(DOC_A, name="a.pdf"), h.add_doc(DOC_B, name="b.pdf")
    calls = {"n": 0}

    def flaky(req):
        if req.role is CallRole.EXTRACT and "Juniper" in "".join(
                p.text for m in req.messages for p in m.parts()) and calls["n"] == 0:
            calls["n"] += 1
            return FakeRefusal("no")
        return responder(req)

    fake = FakeProvider(flaky)
    run_id = run_proposal(h, fake, stored_parameters(), [a, b])
    with h.factory() as s:
        assert s.get(models.Run, run_id).status == RunStatus.PARTIAL

    async def again():
        m = h.manager(fake)
        assert m.retry(run_id)
        await m.wait(run_id)
        await m.shutdown()
    go(again())
    s, run = h.run_row(run_id)
    assert run.status == RunStatus.DONE and run.artifact.current_version == 2
    content = run.artifact.latest_content()
    assert [x.id for x in content.sections][:2] == ["cover", "toc"]
    s.close()
