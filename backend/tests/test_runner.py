"""T6 runner and T13 cancel: asyncio runs, SSE events, reconcile, budget, idempotent Run."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

import pytest
from sqlalchemy import select

from backend import db, models
from backend.pipeline import extract, runner
from backend.pipeline.runner import RunManager, format_sse, reconcile_interrupted_runs
from backend.providers import CallRole, FakeProvider, ProviderConfig
from backend.providers.fake import FakeRefusal
from backend.schemas import EvidenceState, FactField, RunDocumentStatus, RunStatus
from pipeline_fakes import GatedProvider, smart_responder

DOC_A = ["Carrier: Harborline Specialty\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $26,391.14",
         "Each Occurrence Limit: $1,000,000"]
DOC_B = ["Carrier: Juniper Peak\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $14,490.00"]


class Harness:
    def __init__(self, pdf_factory):
        self.engine = db.make_engine("sqlite://")
        db.init_db(self.engine)
        self.factory = db.make_session_factory(self.engine)
        self.blobs: dict[int, bytes] = {}
        self.make_pdf = pdf_factory
        with self.factory() as s:
            self.workflow_id = s.scalar(select(models.Workflow.id).where(
                models.Workflow.slug == db.COMPARE_QUOTES_SLUG))

    def add_doc(self, pages=None, *, data: bytes | None = None, name="q.pdf") -> int:
        data = data if data is not None else self.make_pdf(pages)
        with db.session_scope(self.factory) as s:
            doc = models.Document(workspace_id=db.DEFAULT_WORKSPACE_ID, filename=name,
                                  stored_name=uuid.uuid4().hex, size_bytes=len(data),
                                  sha256=hashlib.sha256(data).hexdigest())
            s.add(doc)
            s.flush()
            doc_id = doc.id
        self.blobs[doc_id] = data
        return doc_id

    def manager(self, provider, **kw) -> RunManager:
        return RunManager(self.factory, provider, load_bytes=lambda d: self.blobs[d.id], **kw)

    def run_row(self, run_id):
        s = self.factory()
        return s, s.get(models.Run, run_id)


@pytest.fixture
def h(pdf_factory):
    harness = Harness(pdf_factory)
    yield harness
    harness.engine.dispose()


def go(coro):
    return asyncio.run(coro)


async def _run_to_end(m: RunManager, workflow_id, doc_ids):
    run_id, created = m.submit(workflow_id, doc_ids)
    assert created
    await m.wait(run_id)
    return run_id


# --------------------------------------------------------------------- happy path


def test_run_completes_with_verified_facts_artifact_events_and_call_log(h):
    a, b = h.add_doc(DOC_A, name="a.pdf"), h.add_doc(DOC_B, name="b.pdf")
    fake = FakeProvider(smart_responder())

    async def main():
        m = h.manager(fake)
        run_id = await _run_to_end(m, h.workflow_id, [a, b])
        events = [e async for e in m.events(run_id)]
        await m.shutdown()
        return run_id, events

    run_id, events = go(main())
    s, run = h.run_row(run_id)
    assert run.status == RunStatus.DONE and run.finished_at is not None
    assert [rd.status for rd in run.documents] == [RunDocumentStatus.DONE] * 2
    facts = {(f.document_id, f.field): f for f in run.facts}
    prem = facts[(a, "total_premium")]
    assert prem.state == EvidenceState.VERIFIED and prem.passage_id is not None and prem.page_no == 1
    occ = facts[(a, "each_occurrence_limit")]
    assert occ.page_no == 2 and occ.passage.rects
    assert facts[(b, "each_occurrence_limit")].state == EvidenceState.ABSENT
    # one fact per field per document (M5: absent fields come back null)
    assert len(run.facts) == 2 * len(db.COMPARE_QUOTES["fact_schema"])
    art = run.artifact
    assert art is not None and art.current_version == 1
    content = art.latest_content()
    assert content.passage_ids() and not content.notices
    table = content.find_table("common_declarations-table")
    assert [c.label for c in table.columns] == ["Harborline Specialty", "Juniper Peak"]
    # every model call logged with tokens; extract calls carry their document
    purposes = sorted(c.purpose for c in run.model_calls)
    assert purposes == ["compose", "extract", "extract"]
    assert {c.document_id for c in run.model_calls if c.purpose == "extract"} == {a, b}
    assert run.tokens_in > 0 and run.tokens_in == sum(c.tokens_in for c in run.model_calls)
    # SSE: replay ends with run.end; per-document progress in order
    types = [e["type"] for e in events]
    assert types[-1] == "run.end" and events[-1]["data"]["status"] == "done"
    assert "artifact.ready" in types and "run.usage" in types
    doc_a = [e["data"]["status"] for e in events if e["type"] == "document.status"
             and e["data"]["document_id"] == a]
    assert doc_a == ["reading", "reading", "extracting", "verifying", "done"]
    run_statuses = [e["data"]["status"] for e in events if e["type"] == "run.status"]
    assert run_statuses == ["running", "composing", "done"]
    ids = [e["id"] for e in events]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    s.close()


def test_format_sse_shape():
    event = {"id": 7, "run_id": 1, "type": "run.status", "data": {"status": "running"}}
    text = format_sse(event)
    assert text.startswith("id: 7\nevent: run.status\ndata: ")
    assert text.endswith("\n\n")
    assert json.loads(text.split("data: ", 1)[1]) == event


# --------------------------------------------------------------------- failures


def test_password_document_fails_only_its_column(h):
    a = h.add_doc(DOC_A, name="a.pdf")
    locked = h.add_doc(data=h.make_pdf(DOC_B, password="pw"), name="locked.pdf")

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        rid = await _run_to_end(m, h.workflow_id, [a, locked])
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    assert run.status == RunStatus.PARTIAL
    rd = run.documents[1]
    assert rd.status == RunDocumentStatus.FAILED and rd.error_code == "encrypted"
    assert "password" in rd.error.lower()
    content = run.artifact.latest_content()
    assert [n.kind for n in content.notices] == ["documents_failed"]
    table = content.find_table("common_declarations-table")
    assert table.columns[1].warning and "password" in table.columns[1].warning.lower()
    s.close()


def test_all_documents_failed_fails_run_without_compose(h):
    bad = h.add_doc(data=b"not a pdf at all", name="x.pdf")
    fake = FakeProvider(smart_responder())

    async def main():
        m = h.manager(fake)
        rid = await _run_to_end(m, h.workflow_id, [bad])
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    assert run.status == RunStatus.FAILED and run.artifact is None
    assert run.documents[0].error_code == "not_pdf"
    assert fake.requests == []
    s.close()


def test_refusal_fails_the_document(h):
    a, b = h.add_doc(DOC_A), h.add_doc(DOC_B)
    smart = smart_responder()

    def respond(req):
        if req.role is CallRole.EXTRACT and "Juniper" in "\n".join(str(m.content) for m in req.messages):
            return FakeRefusal("no")
        return smart(req)

    async def main():
        m = h.manager(FakeProvider(respond))
        rid = await _run_to_end(m, h.workflow_id, [a, b])
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    assert run.status == RunStatus.PARTIAL
    assert run.documents[1].error_code == "refusal"
    s.close()


def test_multi_option_document_gets_a_column_warning(h):
    a = h.add_doc(DOC_A)
    smart = smart_responder()

    def respond(req):
        out = smart(req)
        if req.role is CallRole.EXTRACT:
            out = {**out, "options_detected": 2, "option_compared": "Option 1 (Primary Program)"}
        return out

    async def main():
        m = h.manager(FakeProvider(respond))
        rid = await _run_to_end(m, h.workflow_id, [a])
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    rd = run.documents[0]
    assert rd.option_count == 2 and rd.option_label == "Option 1 (Primary Program)"
    col = run.artifact.latest_content().find_table("common_declarations-table").columns[0]
    assert "option 1 of 2" in col.warning and "Primary Program" in col.warning
    s.close()


# --------------------------------------------------------------------- OCR (D5, M3)


def test_scanned_page_goes_through_vision_and_cites_as_scan_read(h):
    scan = h.add_doc([None], name="scan.pdf")
    fake = FakeProvider(smart_responder({1: "Carrier: Scanline Mutual\nTotal Premium: $9,000"}))

    async def main():
        m = h.manager(fake)
        rid = await _run_to_end(m, h.workflow_id, [scan])
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    assert run.status == RunStatus.DONE
    page = s.scalar(select(models.Page).where(models.Page.document_id == scan))
    assert page.ocr and page.ocr_text.startswith("Carrier")
    fact = next(f for f in run.facts if f.field == "total_premium")
    assert fact.state == EvidenceState.SCAN_READ and fact.passage_id is not None
    assert fact.passage.verified is False
    assert "vision" in {c.purpose for c in run.model_calls}
    s.close()


def test_scanned_page_with_no_text_after_ocr_fails_empty(h):
    scan = h.add_doc([None])

    async def main():
        m = h.manager(FakeProvider(smart_responder({})))
        rid = await _run_to_end(m, h.workflow_id, [scan])
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    assert run.status == RunStatus.FAILED and run.documents[0].error_code == "empty"
    s.close()


# --------------------------------------------------------------------- budget (D3, M4)

SMALL = ProviderConfig(max_output_tokens={CallRole.EXTRACT: 400, CallRole.VISION: 400,
                                          CallRole.COMPOSE: 1500, CallRole.CHAT: 400,
                                          CallRole.SUGGEST: 100})
FIELDS = [FactField.model_validate(f) for f in db.COMPARE_QUOTES["fact_schema"]]


def _extract_reserve(provider, pages_text):
    class P:
        def __init__(self, n, t):
            self.page_no, self.text, self.ocr_text = n, t, None

    msgs = extract.build_extraction_messages(FIELDS, [P(i + 1, t) for i, t in enumerate(pages_text)],
                                             document_name="a.pdf",
                                             instructions=db.COMPARE_QUOTES["instructions"])
    return provider.reserved_tokens(CallRole.EXTRACT, msgs, extract.build_extraction_schema(FIELDS))


@pytest.mark.parametrize("headroom, compose_ok", [(6000, True), (0, False)])
def test_budget_exhaustion_gives_partial_run_and_headroom_protects_compose(h, headroom, compose_ok):
    big_pages = [("Filler clause text for the policy wording. " * 60)] * 12
    a, big = h.add_doc(DOC_A, name="a.pdf"), h.add_doc(big_pages, name="big.pdf")
    fake = FakeProvider(smart_responder(), config=SMALL)
    a_res = _extract_reserve(fake, DOC_A)
    assert _extract_reserve(fake, big_pages) > a_res * 3
    limit = headroom + a_res + 200

    async def main():
        m = h.manager(fake, budget_limit=limit, compose_headroom=headroom)
        rid = await _run_to_end(m, h.workflow_id, [a, big])
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    assert run.status == RunStatus.PARTIAL
    assert run.documents[0].status == RunDocumentStatus.DONE
    assert run.documents[1].error_code == "budget_exhausted"
    assert "budget" in run.documents[1].error.lower()
    kinds = [n.kind for n in run.artifact.latest_content().notices]
    assert "budget_exhausted" in kinds
    assert ("compose_fallback" not in kinds) is compose_ok
    assert run.tokens_in + run.tokens_out <= limit
    s.close()


# --------------------------------------------------------------------- idempotent Run


def test_double_click_run_is_idempotent(h):
    a = h.add_doc(DOC_A)
    fake = FakeProvider(smart_responder())

    async def main():
        m = h.manager(fake)
        first, created1 = m.submit(h.workflow_id, [a])
        second, created2 = m.submit(h.workflow_id, [a])
        again = m.start(first)
        await m.wait(first)
        await m.shutdown()
        return first, created1, second, created2, again

    first, c1, second, c2, again = go(main())
    assert first == second and c1 and not c2 and again is False
    with h.factory() as s:
        assert s.scalar(select(models.Run.id).where(models.Run.id != first)) is None
    assert sum(1 for r in fake.requests if r.role is CallRole.EXTRACT) == 1


def test_submit_after_finish_creates_a_new_run(h):
    a = h.add_doc(DOC_A)

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        r1 = await _run_to_end(m, h.workflow_id, [a])
        r2, created = m.submit(h.workflow_id, [a])
        await m.wait(r2)
        await m.shutdown()
        return r1, r2, created

    r1, r2, created = go(main())
    assert r1 != r2 and created


def test_submit_validates_documents(h):
    a = h.add_doc(DOC_A)

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        with pytest.raises(ValueError):
            m.submit(h.workflow_id, [])
        with pytest.raises(ValueError):
            m.submit(h.workflow_id, [a, a])
        with pytest.raises(LookupError):
            m.submit(h.workflow_id, [a, 999])
        with pytest.raises(ValueError):
            m.submit(h.workflow_id, [a] + [h.add_doc(DOC_B) for _ in range(8)])
        await m.shutdown()

    go(main())


# --------------------------------------------------------------------- cancel (T13)


def test_cancel_mid_run_leaves_cancelled_run_and_no_orphaned_tasks(h):
    a, b = h.add_doc(DOC_A), h.add_doc(DOC_B)
    gated = GatedProvider(smart_responder())

    async def main():
        m = h.manager(gated)
        run_id, _ = m.submit(h.workflow_id, [a, b])
        while gated.waiting < 2:
            await asyncio.sleep(0.01)
        before = len(asyncio.all_tasks())
        assert before > 1
        assert await m.cancel(run_id) is True
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        events = [e async for e in m.events(run_id)]
        assert await m.cancel(run_id) is False  # nothing left to cancel
        await m.shutdown()
        return run_id, leftover, events

    run_id, leftover, events = go(main())
    assert leftover == []
    s, run = h.run_row(run_id)
    assert run.status == RunStatus.CANCELLED and run.artifact is None
    assert all(rd.status == RunDocumentStatus.FAILED and rd.error_code == "cancelled" for rd in run.documents)
    assert events[-1]["type"] == "run.end" and events[-1]["data"]["status"] == "cancelled"
    s.close()


def test_cancel_queued_run_without_task(h):
    a = h.add_doc(DOC_A)
    with db.session_scope(h.factory) as s:
        run = models.Run(workspace_id=1, workflow_id=h.workflow_id)
        run.documents.append(models.RunDocument(document_id=a, position=0))
        s.add(run)
        s.flush()
        rid = run.id

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        ok = await m.cancel(rid)
        await m.shutdown()
        return ok

    assert go(main()) is True
    s, run = h.run_row(rid)
    assert run.status == RunStatus.CANCELLED
    s.close()


# --------------------------------------------------------------------- reconcile and retry (M6)


def test_reconcile_marks_interrupted_runs_failed_and_retry_finishes_them(h):
    a, b = h.add_doc(DOC_A), h.add_doc(DOC_B)
    with db.session_scope(h.factory) as s:
        run = models.Run(workspace_id=1, workflow_id=h.workflow_id, status=RunStatus.RUNNING)
        run.documents.append(models.RunDocument(document_id=a, position=0, status=RunDocumentStatus.EXTRACTING))
        run.documents.append(models.RunDocument(document_id=b, position=1, status=RunDocumentStatus.QUEUED))
        s.add(run)
        done = models.Run(workspace_id=1, workflow_id=h.workflow_id, status=RunStatus.DONE)
        s.add(done)
        s.flush()
        rid, done_id = run.id, done.id

    assert reconcile_interrupted_runs(h.factory) == [rid]
    s, run = h.run_row(rid)
    assert run.status == RunStatus.FAILED and run.retryable and "restart" in run.error.lower()
    assert all(rd.error_code == "interrupted" for rd in run.documents)
    assert s.get(models.Run, done_id).status == RunStatus.DONE
    s.close()
    assert reconcile_interrupted_runs(h.factory) == []  # idempotent

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        assert m.retry(rid) is True
        assert m.retry(rid) is False  # already running
        await m.wait(rid)
        await m.shutdown()

    go(main())
    s, run = h.run_row(rid)
    assert run.status == RunStatus.DONE and run.error is None
    assert all(rd.status == RunDocumentStatus.DONE for rd in run.documents)
    s.close()


def test_retry_reprocesses_only_failed_documents_and_snapshots_new_version(h):
    a, b = h.add_doc(DOC_A), h.add_doc(DOC_B)
    smart = smart_responder()
    refuse = {"on": True}

    def respond(req):
        if refuse["on"] and req.role is CallRole.EXTRACT and "Juniper" in str(req.messages[-1].content):
            return FakeRefusal("no")
        return smart(req)

    fake = FakeProvider(respond)

    async def main():
        m = h.manager(fake)
        rid = await _run_to_end(m, h.workflow_id, [a, b])
        refuse["on"] = False
        n_before = sum(1 for r in fake.requests if r.role is CallRole.EXTRACT)
        assert m.retry(rid)
        await m.wait(rid)
        n_after = sum(1 for r in fake.requests if r.role is CallRole.EXTRACT)
        await m.shutdown()
        return rid, n_after - n_before

    rid, extra_extracts = go(main())
    assert extra_extracts == 1
    s, run = h.run_row(rid)
    assert run.status == RunStatus.DONE and run.artifact.current_version == 2
    assert len(run.facts) == 2 * len(db.COMPARE_QUOTES["fact_schema"])
    s.close()


# --------------------------------------------------------------------- events API


def test_events_for_unknown_or_idle_run_yield_a_snapshot_only(h):
    a = h.add_doc(DOC_A)

    async def main():
        m = h.manager(FakeProvider(smart_responder()))
        rid = await _run_to_end(m, h.workflow_id, [a])
        fresh = h.manager(FakeProvider(smart_responder()))  # e.g. after a restart: no history
        events = [e async for e in fresh.events(rid)]
        after = [e async for e in m.events(rid, last_event_id=10_000)]
        await m.shutdown()
        await fresh.shutdown()
        return events, after

    events, after = go(main())
    assert [e["type"] for e in events] == ["run.snapshot"]
    snap = events[0]["data"]
    assert snap["run"]["status"] == "done" and len(snap["documents"]) == 1
    assert snap["artifact"]["version"] == 1
    assert [e["type"] for e in after] == ["run.snapshot"]


def test_live_subscriber_receives_events_until_run_end(h):
    a = h.add_doc(DOC_A)
    gated = GatedProvider(smart_responder())

    async def main():
        m = h.manager(gated)
        rid, _ = m.submit(h.workflow_id, [a])
        received = []

        async def listen():
            async for e in m.events(rid):
                received.append(e)

        listener = asyncio.create_task(listen())
        while gated.waiting < 1:
            await asyncio.sleep(0.01)
        gated.gate.set()
        await m.wait(rid)
        await asyncio.wait_for(listener, 2)
        await m.shutdown()
        return received

    received = go(main())
    assert received[-1]["type"] == "run.end"
    assert "artifact.ready" in [e["type"] for e in received]


def test_runner_module_exposes_reason_map():
    assert runner.failure_message("budget_exhausted").lower().startswith("budget exhausted")
    assert runner.failure_message("unknown_code")
