"""T14 end to end through RunManager: 120 and 300 page PDFs, caps, per-document budget, cancel."""

from __future__ import annotations

import asyncio
import re

import fitz
import pytest

from backend import db
from backend.pipeline import parse
from backend.providers import CallRole, FakeProvider
from backend.providers.types import TextPart
from backend.schemas import EvidenceState, RunDocumentStatus, RunStatus
from pipeline_fakes import GatedProvider, smart_responder
from test_runner import DOC_A, Harness, _extract_reserve, _run_to_end, go

FACT_KEYS = [f["key"] for f in db.COMPARE_QUOTES["fact_schema"]]


def long_pdf(n: int, facts: dict[int, str] | None = None) -> bytes:
    """n small pages, each with a line of filler and optional fact lines (fast to build and parse)."""
    facts = facts or {}
    doc = fitz.open()
    for i in range(1, n + 1):
        page = doc.new_page(width=300, height=200)
        text = f"Policy wording page {i}. General conditions apply.\n{facts.get(i, '')}".strip()
        page.insert_textbox(fitz.Rect(10, 10, 290, 190), text, fontsize=7, fontname="helv")
    data = doc.tobytes(garbage=0, deflate=False)
    doc.close()
    return data


@pytest.fixture
def h(pdf_factory):
    harness = Harness(pdf_factory)
    yield harness
    harness.engine.dispose()


def extract_requests(fake):
    return [r for r in fake.requests if r.role is CallRole.EXTRACT]


def progress(events, doc_id):
    return [e["data"]["progress"] for e in events if e["type"] == "document.status"
            and e["data"]["document_id"] == doc_id]


async def _run_with_events(m, workflow_id, ids):
    rid = await _run_to_end(m, workflow_id, ids)
    events = [e async for e in m.events(rid)]
    await m.shutdown()
    return rid, events


# --------------------------------------------------------------------- 120 pages


LONG_120 = {
    1: "Carrier: Harborline Specialty\nNamed Insured: Cedar Hollow LLC",
    20: "Deductible / Retention: $5,000",
    50: "Total Premium: $26,391.14",
    60: "Carrier: Harborline Specialty",
    90: "Deductible / Retention: $7,500",
}


def test_120_page_document_is_extracted_in_windows_verified_and_merged(h):
    long_id = h.add_doc(data=long_pdf(120, LONG_120), name="policy.pdf")
    fake = FakeProvider(smart_responder())

    rid, events = go(_run_with_events(h.manager(fake), h.workflow_id, [long_id]))
    s, run = h.run_row(rid)
    assert run.status == RunStatus.DONE
    assert len(extract_requests(fake)) == 10
    # progress: Extracting, then window k/10 as each window finishes, then Verifying
    prog = progress(events, long_id)
    windows_seen = [p for p in prog if p and p.startswith("Extracting window")]
    assert windows_seen == [f"Extracting window {k}/10" for k in range(1, 11)]
    assert prog.index("Extracting") < prog.index("Extracting window 1/10") < prog.index("Verifying")
    statuses = [e["data"]["status"] for e in events if e["type"] == "document.status"]
    assert statuses[-1] == "done" and "verifying" in statuses

    facts = {f.field: f for f in run.facts}
    assert sorted(facts) == sorted(FACT_KEYS)  # one fact per field
    carrier = facts["carrier_name"]
    assert carrier.state == EvidenceState.VERIFIED and carrier.page_no == 1
    assert len(carrier.extra_passage_ids) == 1 and carrier.extra_passage_ids[0] != carrier.passage_id
    prem = facts["total_premium"]
    assert prem.page_no == 50 and prem.value == "$26,391.14" and prem.extra_passage_ids == []
    ret = facts["retention"]
    assert (ret.value, ret.page_no) == ("$5,000", 20)
    assert [(c["value"], c["page_no"]) for c in ret.conflicts] == [("$7,500", 90)]
    assert ret.conflicts[0]["passage_id"] is not None

    content = run.artifact.latest_content()
    cells = {r.key: r.cells["A"] for _, b in content.iter_blocks() if b.type == "table" for r in b.rows}
    assert cells["carrier_name"].passage_ids == [carrier.passage_id, carrier.extra_passage_ids[0]]
    notice = cells["retention"].notice
    assert notice is not None and notice.kind == "conflicting_values"
    assert "$5,000 (page 20)" in notice.message and "$7,500 (page 90)" in notice.message
    assert cells["retention"].value == "$5,000" and cells["retention"].state == EvidenceState.VERIFIED
    assert all(c.notice is None for k, c in cells.items() if k != "retention")
    s.close()


# --------------------------------------------------------------------- 300 / 301 pages and caps


def test_300_pages_accepted_with_endorsement_rule_and_301_rejected_naming_the_limit(h):
    facts = {2: "Carrier: Stonegate Assurance\nDeclarations\nDeductible / Retention: $5,000",
             280: "Endorsement No. 3: Amendment of Deductible\nDeductible / Retention: $10,000"}
    ok = h.add_doc(data=long_pdf(300, facts), name="p300.pdf")
    over = h.add_doc(data=long_pdf(301), name="p301.pdf")
    fake = FakeProvider(smart_responder())

    rid, _ = go(_run_with_events(h.manager(fake), h.workflow_id, [ok, over]))
    s, run = h.run_row(rid)
    assert run.status == RunStatus.PARTIAL
    good, bad = run.documents
    assert good.status == RunDocumentStatus.DONE and good.document.page_count == 300
    assert bad.status == RunDocumentStatus.FAILED and bad.error_code == "too_many_pages"
    assert "300 pages" in bad.error and "301 pages" in bad.error
    assert len(extract_requests(fake)) == 23  # 300-page windows only; the 301 never reaches the model
    ret = next(f for f in run.facts if f.document_id == ok and f.field == "retention")
    assert (ret.value, ret.page_no, ret.conflicts) == ("$10,000", 280, [])
    s.close()


def test_default_caps_are_300_pages_and_50_mb():
    assert parse.DEFAULT_LIMITS.max_pages == 300
    assert parse.DEFAULT_LIMITS.max_bytes == 50 * 1024 * 1024
    assert parse.DEFAULT_LIMITS.max_documents_per_run == 8


# --------------------------------------------------------------------- single call unchanged


def test_documents_of_15_pages_or_fewer_keep_the_single_call_path(h):
    short = h.add_doc(data=long_pdf(15, {1: "Carrier: Harborline Specialty", 15: "Total Premium: $1,000.00"}),
                      name="short.pdf")
    fake = FakeProvider(smart_responder())
    rid, events = go(_run_with_events(h.manager(fake), h.workflow_id, [short]))
    s, run = h.run_row(rid)
    assert run.status == RunStatus.DONE
    reqs = extract_requests(fake)
    assert len(reqs) == 1
    text = "\n".join(p.text for m in reqs[0].messages if m.role == "user" for p in m.parts()
                     if isinstance(p, TextPart))
    assert re.findall(r"^\[page (\d+)\]$", text, re.M) == [str(i) for i in range(1, 16)]
    assert not any(p and "window" in p for p in progress(events, short))
    assert all(f.extra_passage_ids == [] and f.conflicts == [] for f in run.facts)
    s.close()


# --------------------------------------------------------------------- budget per document


def test_one_long_document_cannot_starve_the_others(h):
    a = h.add_doc(DOC_A, name="a.pdf")
    long_id = h.add_doc(data=long_pdf(120, LONG_120), name="policy.pdf")
    fake = FakeProvider(smart_responder())
    ceiling = _extract_reserve(fake, DOC_A) + 1_000  # the short quote fits, ten windows do not

    async def main():
        m = h.manager(fake, document_budget_limit=ceiling)
        return await _run_with_events(m, h.workflow_id, [a, long_id])

    rid, _ = go(main())
    s, run = h.run_row(rid)
    assert run.status == RunStatus.PARTIAL
    short, long_rd = run.documents
    assert short.status == RunDocumentStatus.DONE
    assert long_rd.status == RunDocumentStatus.FAILED and long_rd.error_code == "document_budget_exhausted"
    assert "tokens" in long_rd.error
    # the pre-check refused before any of the long document's windows was sent
    assert len(extract_requests(fake)) == 1
    assert run.artifact is not None
    s.close()


def test_document_ceiling_counts_every_window_and_defaults_to_400k(h):
    long_id = h.add_doc(data=long_pdf(40), name="policy.pdf")
    fake = FakeProvider(smart_responder())
    m = h.manager(fake)
    assert m._document_budget_limit == 400_000 == fake.config.document_token_budget
    assert fake.config.run_token_budget == 1_500_000

    async def main():
        rid = await _run_to_end(m, h.workflow_id, [long_id])
        await m.shutdown()
        return rid

    s, run = h.run_row(go(main()))
    extract_tokens = sum(c.tokens_in + c.tokens_out for c in run.model_calls if c.purpose == "extract")
    assert run.status == RunStatus.DONE and len([c for c in run.model_calls if c.purpose == "extract"]) == 3
    assert 0 < extract_tokens <= 400_000
    s.close()


# --------------------------------------------------------------------- cancel


def test_cancel_mid_windows_leaves_a_cancelled_run_and_no_tasks(h):
    long_id = h.add_doc(data=long_pdf(120, LONG_120), name="policy.pdf")
    gated = GatedProvider(smart_responder())

    async def main():
        m = h.manager(gated)
        run_id, _ = m.submit(h.workflow_id, [long_id])
        while gated.waiting < 4:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)
        assert gated.waiting == 4  # windows wait for a slot instead of piling up on the model
        assert await m.cancel(run_id) is True
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await m.shutdown()
        return run_id, leftover

    run_id, leftover = go(main())
    assert leftover == []
    s, run = h.run_row(run_id)
    assert run.status == RunStatus.CANCELLED and run.artifact is None
    assert run.documents[0].error_code == "cancelled" and run.facts == []
    s.close()
