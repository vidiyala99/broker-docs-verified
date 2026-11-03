"""Slice 3 T2: document slots on run creation (D8) and the per-slot failure rule (D11).

D8 A: POST /api/runs takes `slots`, a map document_id -> slot key, stored in
RunDocument.slot and validated against the workflow's input_slots (min and max per
slot); active-run dedupe includes the slot. D11 A: a required slot (min >= 1) left
with fewer readable documents than its min fails the run (Retry, no compose call);
a failed optional document lets the run complete PARTIAL and its slot reads
"not_read", never absent. D12 A: runs without slots still land in slot "quotes"
and dedupe exactly as before.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from api_support import DOC_A, DOC_B, api, api_factory  # noqa: F401
from backend import db, models
from backend.pipeline import runner
from backend.pipeline.runner import SlotError
from backend.providers import CallRole, FakeProvider
from backend.providers.fake import FakeRefusal
from backend.schemas import DiscrepancyBlock, EvidenceState, PolicyRole, RunDocumentStatus, RunStatus
from pipeline_fakes import GatedProvider, smart_responder
from test_runner import Harness, go

EXPIRING = ["Carrier: Harborline Specialty\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $20,000.00"]
RENEWAL = ["Carrier: Harborline Specialty\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $21,500.00"]
QUOTE = ["Carrier: Juniper Peak\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $19,900.00"]


def _usable_policy_check(factory) -> int:
    """Swap the seeded policy-check fact schema for the Compare Quotes one, so the slot tests
    can keep smart_responder (Compare Quotes labels). The real combined schema is covered by
    test_policy_check_run.py."""
    with db.session_scope(factory) as s:
        wf = s.scalar(select(models.Workflow).where(models.Workflow.slug == db.POLICY_CHECK_SLUG))
        wf.fact_schema = db.COMPARE_QUOTES["fact_schema"]
        wf.section_plan = db.COMPARE_QUOTES["section_plan"]
        return wf.id


@pytest.fixture
def h(pdf_factory):
    harness = Harness(pdf_factory)
    harness.policy_check_id = _usable_policy_check(harness.factory)
    yield harness
    harness.engine.dispose()


async def _finish(m, workflow_id, ids, slots=None):
    run_id, created = m.submit(workflow_id, ids, slots=slots)
    assert created
    await m.wait(run_id)
    return run_id


# --------------------------------------------------------------------- seed


def test_policy_check_is_seeded_with_its_four_slots_idempotently():
    engine = db.make_engine("sqlite://")
    db.init_db(engine)
    db.init_db(engine)
    factory = db.make_session_factory(engine)
    with factory() as s:
        rows = s.scalars(select(models.Workflow).where(models.Workflow.slug == db.POLICY_CHECK_SLUG)).all()
        assert len(rows) == 1
        wf = rows[0]
        assert wf.name == "Policy Check"
        slots = {x["key"]: (x["min"], x["max"]) for x in wf.input_slots}
        assert slots == {"expiring": (1, 1), "renewal": (1, 1), "quote": (0, 1), "ams": (0, 1)}
        assert [x["key"] for x in wf.input_slots] == ["expiring", "renewal", "quote", "ams"]
        assert all(x["label"] and x["accepts"] for x in wf.input_slots)
    engine.dispose()


# --------------------------------------------------------------------- D12 regression: no slots


def test_run_without_slots_lands_in_quotes_and_dedupes_as_before(h):
    a, b = h.add_doc(DOC_A), h.add_doc(DOC_B)
    gated = GatedProvider(smart_responder())

    async def main():
        m = h.manager(gated)
        first, created = m.submit(h.workflow_id, [a, b])
        again, created_again = m.submit(h.workflow_id, [a, b])
        explicit, created_explicit = m.submit(h.workflow_id, [a, b], slots={a: "quotes", b: "quotes"})
        other_order, created_other = m.submit(h.workflow_id, [b, a])
        gated.gate.set()
        for rid in {first, other_order}:
            await m.wait(rid)
        await m.shutdown()
        return first, created, again, created_again, explicit, created_explicit, other_order, created_other

    first, created, again, created_again, explicit, created_explicit, other, created_other = go(main())
    assert created and not created_again and again == first
    assert explicit == first and not created_explicit  # explicit "quotes" is the same run
    assert created_other and other != first  # document order still matters, as before
    s, run = h.run_row(first)
    assert [rd.slot for rd in run.documents] == ["quotes", "quotes"]
    assert run.status == RunStatus.DONE
    s.close()


def test_single_slot_workflow_rejects_an_unknown_slot_key(h):
    a = h.add_doc(DOC_A)
    m = h.manager(FakeProvider(smart_responder()))
    with pytest.raises(SlotError, match="renewal"):
        m.submit(h.workflow_id, [a], slots={a: "renewal"})


# --------------------------------------------------------------------- D8 slots on policy-check


def test_policy_check_run_stores_each_documents_slot_in_document_order(h):
    exp, ren, quo = h.add_doc(EXPIRING), h.add_doc(RENEWAL), h.add_doc(QUOTE)

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        rid = await _finish(m, h.policy_check_id, [ren, exp, quo],
                            slots={exp: "expiring", ren: "renewal", quo: "quote"})
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    assert [(rd.document_id, rd.slot, rd.letter) for rd in run.documents] == [
        (ren, "renewal", "A"), (exp, "expiring", "B"), (quo, "quote", "C")]
    assert run.status == RunStatus.DONE
    assert runner.slot_states(run) == {"expiring": "read", "renewal": "read", "quote": "read", "ams": "absent"}
    s.close()


@pytest.mark.parametrize("case,expect", [
    ("no_slots", "slot"),
    ("missing_renewal", "Renewal"),
    ("two_expiring", "Expiring"),
    ("two_quotes", "Quote"),
    ("unknown_key", "nope"),
    ("unassigned_document", "every document"),
    ("extra_document", "not in this run"),
])
def test_policy_check_slot_validation(h, case, expect):
    exp, ren, quo, extra = (h.add_doc(EXPIRING), h.add_doc(RENEWAL), h.add_doc(QUOTE), h.add_doc(QUOTE))
    ids = [exp, ren]
    slots = {exp: "expiring", ren: "renewal"}
    if case == "no_slots":
        slots = None
    elif case == "missing_renewal":
        ids, slots = [exp], {exp: "expiring"}
    elif case == "two_expiring":
        ids, slots = [exp, ren, quo], {exp: "expiring", ren: "renewal", quo: "expiring"}
    elif case == "two_quotes":
        ids, slots = [exp, ren, quo, extra], {exp: "expiring", ren: "renewal", quo: "quote", extra: "quote"}
    elif case == "unknown_key":
        slots = {exp: "expiring", ren: "nope"}
    elif case == "unassigned_document":
        ids = [exp, ren, quo]
    elif case == "extra_document":
        slots = {**slots, extra: "quote"}
    m = h.manager(FakeProvider(smart_responder()))
    with pytest.raises(SlotError, match=expect):
        m.submit(h.policy_check_id, ids, slots=slots)
    with h.factory() as s:
        assert s.scalars(select(models.Run)).all() == []  # nothing created


def test_slot_error_is_a_value_error_with_a_user_safe_message():
    assert issubclass(SlotError, ValueError)


def test_active_run_dedupe_includes_the_slot(h):
    exp, ren = h.add_doc(EXPIRING), h.add_doc(RENEWAL)
    gated = GatedProvider(smart_responder())

    async def main():
        m = h.manager(gated)
        r1, c1 = m.submit(h.policy_check_id, [exp, ren], slots={exp: "expiring", ren: "renewal"})
        r2, c2 = m.submit(h.policy_check_id, [exp, ren], slots={ren: "renewal", exp: "expiring"})
        r3, c3 = m.submit(h.policy_check_id, [exp, ren], slots={exp: "renewal", ren: "expiring"})
        gated.gate.set()
        for rid in {r1, r3}:
            await m.wait(rid)
        await m.shutdown()
        return (r1, c1), (r2, c2), (r3, c3)

    (r1, c1), (r2, c2), (r3, c3) = go(main())
    assert c1 and not c2 and r2 == r1
    assert c3 and r3 != r1  # same documents, swapped roles: a different run


# --------------------------------------------------------------------- D11 failure rules


def test_failed_required_document_fails_the_run_without_compose(h):
    exp = h.add_doc(EXPIRING)
    ren = h.add_doc(data=b"not a pdf at all", name="renewal.pdf")
    fake = FakeProvider(smart_responder())

    async def main():
        m = h.manager(fake)
        rid = await _finish(m, h.policy_check_id, [exp, ren], slots={exp: "expiring", ren: "renewal"})
        events = [e async for e in m.events(rid)]
        await m.shutdown()
        return rid, events

    rid, events = go(main())
    s, run = h.run_row(rid)
    assert run.status == RunStatus.FAILED and run.retryable and run.artifact is None
    assert "renewal" in run.error.lower() and "retry" in run.error.lower()
    assert [r.role for r in fake.requests] == [CallRole.EXTRACT]  # the expiring read; no compose
    assert runner.slot_states(run)["renewal"] == "not_read"
    assert events[-1]["type"] == "run.end" and events[-1]["data"]["status"] == "failed"
    status = [e["data"] for e in events if e["type"] == "run.status"][-1]
    assert status["retryable"] is True
    s.close()


def test_required_failure_by_refusal_also_fails_and_retry_completes_it(h):
    exp, ren = h.add_doc(EXPIRING), h.add_doc(RENEWAL)
    smart = smart_responder()
    refuse = {"on": True}

    def respond(req):
        if refuse["on"] and req.role is CallRole.EXTRACT and "21,500" in str(req.messages[-1].content):
            return FakeRefusal("no")
        return smart(req)

    async def main():
        m = h.manager(FakeProvider(respond))
        rid = await _finish(m, h.policy_check_id, [exp, ren], slots={exp: "expiring", ren: "renewal"})
        with h.factory() as s:
            assert s.get(models.Run, rid).status == RunStatus.FAILED
        refuse["on"] = False
        assert m.retry(rid)
        await m.wait(rid)
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    assert run.status == RunStatus.DONE and run.error is None and run.artifact is not None
    assert [rd.slot for rd in run.documents] == ["expiring", "renewal"]  # retry keeps the slots
    s.close()


def test_failed_optional_document_completes_partial_and_reads_not_read(h):
    exp, ren = h.add_doc(EXPIRING), h.add_doc(RENEWAL)
    bad_quote = h.add_doc(data=b"not a pdf at all", name="quote.pdf")

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        rid = await _finish(m, h.policy_check_id, [exp, ren, bad_quote],
                            slots={exp: "expiring", ren: "renewal", bad_quote: "quote"})
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    assert run.status == RunStatus.PARTIAL and run.retryable and run.artifact is not None
    states = runner.slot_states(run)
    assert states == {"expiring": "read", "renewal": "read", "quote": "not_read", "ams": "absent"}
    content = run.artifact.latest_content()
    assert any(n.kind == "documents_failed" and n.retryable for n in content.notices)
    # the Policy Check report is the discrepancy list: the failed quote reads Not read in every slot
    blocks = [b for _, b in content.iter_blocks() if isinstance(b, DiscrepancyBlock)]
    assert blocks and all(b.slot(PolicyRole.QUOTE).state == EvidenceState.FAILED for b in blocks)
    s.close()


def test_budget_exhausted_optional_document_is_not_read_never_absent(h):
    exp, ren, quo = h.add_doc(EXPIRING), h.add_doc(RENEWAL), h.add_doc(QUOTE)

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        rid = await _finish(m, h.policy_check_id, [exp, ren, quo],
                            slots={exp: "expiring", ren: "renewal", quo: "quote"})
        await m.shutdown()
        return rid

    rid = go(main())
    with db.session_scope(h.factory) as s:  # simulate the quote running out of budget
        rd = s.get(models.Run, rid).documents[2]
        rd.status, rd.error_code = RunDocumentStatus.FAILED, "budget_exhausted"
    s, run = h.run_row(rid)
    assert runner.slot_states(run)["quote"] == "not_read"
    s.close()


def test_compose_columns_carry_their_slot(h):
    exp, ren = h.add_doc(EXPIRING), h.add_doc(RENEWAL)

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        rid = await _finish(m, h.policy_check_id, [exp, ren], slots={exp: "expiring", ren: "renewal"})
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    inp = runner.compose_input_for_run(run)
    assert [(c.key, c.slot) for c in inp.columns] == [("A", "expiring"), ("B", "renewal")]
    s.close()


def test_compare_quotes_failure_behavior_is_unchanged(h):
    # D12: one failed quote of two is still PARTIAL; all failed is still the old FAILED message.
    a = h.add_doc(DOC_A)
    bad = h.add_doc(data=b"not a pdf at all", name="x.pdf")

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        partial = await _finish(m, h.workflow_id, [a, bad])
        failed = await _finish(m, h.workflow_id, [bad])
        await m.shutdown()
        return partial, failed

    partial, failed = go(main())
    s, run = h.run_row(partial)
    assert run.status == RunStatus.PARTIAL
    s.close()
    s, run = h.run_row(failed)
    assert run.status == RunStatus.FAILED and run.error.startswith("No document could be read.")
    s.close()


# --------------------------------------------------------------------- API


def _pc_api(api):
    return _usable_policy_check(api.ctx.factory)


def test_api_policy_check_run_with_slots(api):
    _pc_api(api)
    exp, ren = api.upload_pages(EXPIRING, "exp.pdf"), api.upload_pages(RENEWAL, "ren.pdf")
    r = api.client.post("/api/runs", json={"document_ids": [exp, ren], "workflow": "policy-check",
                                           "slots": {str(exp): "expiring", str(ren): "renewal"}})
    assert r.status_code == 201, r.text
    docs = r.json()["run"]["documents"]
    assert [(d["document_id"], d["slot"]) for d in docs] == [(exp, "expiring"), (ren, "renewal")]
    run_id = r.json()["run"]["id"]
    events = api.events(run_id)
    doc_event = next(e for e in events if e["type"] == "document.status")
    assert set(doc_event["data"]) == set(docs[0]) and "slot" in doc_event["data"]
    assert api.client.get(f"/api/runs/{run_id}").json()["status"] == "done"


@pytest.mark.parametrize("body", [
    {"workflow": "policy-check"},  # no slots
    {"workflow": "policy-check", "slots": "both-expiring"},
    {"workflow": "policy-check", "slots": "renewal-only"},
    {"workflow": "compare-quotes", "slots": "bad-key"},
])
def test_api_invalid_slots_is_400_invalid_run(api, body):
    _pc_api(api)
    exp, ren = api.upload_pages(EXPIRING, "exp.pdf"), api.upload_pages(RENEWAL, "ren.pdf")
    ids = [exp, ren]
    slots = {"both-expiring": {str(exp): "expiring", str(ren): "expiring"},
             "renewal-only": {str(ren): "renewal"},
             "bad-key": {str(exp): "quotes", str(ren): "expiring"}}.get(body.get("slots"))
    if body.get("slots") == "renewal-only":
        ids = [ren]
    payload = {"document_ids": ids, "workflow": body["workflow"]}
    if slots is not None:
        payload["slots"] = slots
    r = api.client.post("/api/runs", json=payload)
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_run" and err["message"]
    assert api.client.get("/api/runs").json()["runs"] == []


def test_api_run_without_slots_keeps_quotes_and_old_errors(api):
    a = api.upload_pages(DOC_A, "a.pdf")
    run, _ = api.run([a])
    assert [d["slot"] for d in run["documents"]] == ["quotes"]
    r = api.client.post("/api/runs", json={"document_ids": []})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_run"  # unchanged
    r = api.client.post("/api/runs", json={"document_ids": [a], "slots": {"x": "quotes"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_api_failed_renewal_is_failed_and_retryable(api_factory):
    a = api_factory()
    _pc_api(a)
    exp = a.upload_pages(EXPIRING, "exp.pdf")
    ren = a.upload_pages(RENEWAL, "ren.pdf")
    with db.session_scope(a.ctx.factory) as s:  # make the renewal unreadable after upload
        stored = s.get(models.Document, ren).stored_name
    (a.data_dir / "files" / stored).write_bytes(b"not a pdf at all")
    with db.session_scope(a.ctx.factory) as s:
        s.get(models.Document, ren).pages = []
        s.get(models.Document, ren).page_count = None
    r = a.client.post("/api/runs", json={"document_ids": [exp, ren], "workflow": "policy-check",
                                         "slots": {str(exp): "expiring", str(ren): "renewal"}})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    a.events(run_id)
    end = time.monotonic() + 5
    run = a.client.get(f"/api/runs/{run_id}").json()
    while run["active"] and time.monotonic() < end:
        time.sleep(0.01)
        run = a.client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed" and run["retryable"] is True
    assert "renewal" in run["error"].lower()
