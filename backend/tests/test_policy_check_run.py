"""Slice 3 integration: a Policy Check run end to end through the run pipeline.

Seeded fact schema = schemas.POLICY_CHECK_FIELDS (T3). PDF roles take the normal
extraction; an AMS document takes ams.facts_from_original (D5: no model call) and
verifies against its rendered PDF. The diff (T4) runs on role inputs built from
slot_states and report cells (RoleFact.from_cell), and its items become
DiscrepancyBlocks (T5) with deterministic text (D14) in the run artifact.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import select

from backend import ams, db, models
from backend.pipeline import diff, policy_check, runner
from backend.pipeline.runner import RunManager, SlotError
from backend.providers import CallRole, FakeProvider
from backend.schemas import (
    POLICY_CHECK_FIELDS,
    DiscrepancyBlock,
    DiscrepancyItemState,
    EvidenceState,
    PolicyRole,
    RunStatus,
    Severity,
    scalar_fields,
)
from pipeline_fakes import policy_responder
from test_runner import Harness, go

EXPIRING = ["Policy Number: HO-4471-2026\nPolicy Term: 01/01/2026 to 01/01/2027\n"
            "Named Insured: Cedar Hollow LLC\nCoverage A (Dwelling): $350,000\n"
            "Coverage B (Other Structures): $35,000\nAll Other Perils Deductible: $1,000\n"
            "Total Premium: $2,481.50\nMulti-Policy Discount: 10%\n"
            "HO 00 03 05 11 Homeowners 3 Special Form"]
RENEWAL = ["Policy Number: HO-4471-2026\nPolicy Term: 01/01/2027 to 01/01/2028\n"
           "Named Insured: Cedar Hollow LLC\nCoverage A (Dwelling): $300,000\n"
           "Coverage B (Other Structures): $35,000\nAll Other Perils Deductible: $2,500\n"
           "Total Premium: $2,481.50\n"
           "HO 00 03 10 20 Homeowners 3 Special Form"]
QUOTE = ["Policy Number: HO-4471-2026\nNamed Insured: Cedar Hollow LLC\nCoverage A (Dwelling): $300,000\n"
         "Total Premium: $2,481.50"]
AMS = {"Policy Number": "HO-4471-2026", "Cov A": "$350,000", "Total Premium": "$2,600.00",
       "Agent Notes": "Called about the roof"}

EXPECTED_ORDER = [
    ("coverage_a_limit", Severity.CRITICAL), ("deductible_all_peril", Severity.CRITICAL),
    ("discount_multi_policy", Severity.HIGH), ("total_premium", Severity.HIGH),
    ("form:HO0003", Severity.MEDIUM),
]


class PCHarness(Harness):
    def __init__(self, pdf_factory):
        super().__init__(pdf_factory)
        self.originals: dict[int, bytes] = {}
        with self.factory() as s:
            self.pc_id = s.scalar(select(models.Workflow.id).where(models.Workflow.slug == db.POLICY_CHECK_SLUG))

    def add_ams(self, obj, name="ams.json") -> int:
        data = json.dumps(obj).encode()
        rendered = ams.render_pdf(ams.parse_ams(data))
        with db.session_scope(self.factory) as s:
            doc = models.Document(workspace_id=db.DEFAULT_WORKSPACE_ID, filename=name, stored_name=uuid.uuid4().hex,
                                  size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), kind="ams",
                                  page_count=rendered.page_count)
            s.add(doc)
            s.flush()
            doc_id = doc.id
        self.blobs[doc_id] = rendered.pdf
        self.originals[doc_id] = data
        return doc_id

    def manager(self, provider, **kw) -> RunManager:
        return RunManager(self.factory, provider, load_bytes=lambda d: self.blobs[d.id],
                          load_original=lambda d: self.originals[d.id], **kw)


@pytest.fixture
def h(pdf_factory):
    harness = PCHarness(pdf_factory)
    yield harness
    harness.engine.dispose()


def _run(h, provider, docs: dict[str, int]):
    async def main():
        m = h.manager(provider)
        ids = list(docs.values())
        rid, created = m.submit(h.pc_id, ids, slots={v: k for k, v in docs.items()})
        assert created
        await m.wait(rid)
        await m.shutdown()
        return rid
    return go(main())


def _blocks(run) -> list[DiscrepancyBlock]:
    return [b for _, b in run.artifact.latest_content().iter_blocks() if isinstance(b, DiscrepancyBlock)]


# --------------------------------------------------------------------- seed and key table


def test_seeded_policy_check_fact_schema_is_the_combined_extraction_schema(h):
    with h.factory() as s:
        wf = s.get(models.Workflow, h.pc_id)
        assert wf.fact_schema == [f.model_dump() for f in POLICY_CHECK_FIELDS]
    assert db.POLICY_CHECK["fact_schema"] == [f.model_dump() for f in POLICY_CHECK_FIELDS]


def test_ams_canonical_keys_are_the_policy_check_scalar_keys():
    scalar = [f.key for f in scalar_fields(POLICY_CHECK_FIELDS)]
    assert list(ams.CANONICAL_KEYS) == scalar
    assert set(ams.KEY_TABLE.values()) <= set(scalar)
    assert ams.canonical_key("Insured Address") == "insured_address"
    assert ams.canonical_key("Mailing Address") == "insured_address"
    assert ams.canonical_key("Multi Policy Discount") == "discount_multi_policy"
    assert ams.canonical_key("Claims Free Discount") == "discount_claims_free"
    assert ams.canonical_key("Hurricane Deductible") == "deductible_hurricane"
    # keys the policy-check schema does not have are unmapped (named in a notice), never invented
    for name in ("Effective Date", "Expiration Date", "Carrier"):
        assert ams.canonical_key(name) is None


# --------------------------------------------------------------------- the run


def test_policy_check_run_builds_a_discrepancy_artifact_without_compose(h):
    exp, ren, amsd = h.add_doc(EXPIRING, name="exp.pdf"), h.add_doc(RENEWAL, name="ren.pdf"), h.add_ams(AMS)
    fake = FakeProvider(policy_responder())
    rid = _run(h, fake, {"expiring": exp, "renewal": ren, "ams": amsd})

    assert [r.role for r in fake.requests] == [CallRole.EXTRACT, CallRole.EXTRACT]  # PDFs only, no compose
    s, run = h.run_row(rid)
    assert run.status == RunStatus.DONE
    content = run.artifact.latest_content()
    assert [sec.id for sec in content.sections] == ["discrepancies"]
    blocks = _blocks(run)
    assert [(b.key, b.severity) for b in blocks] == EXPECTED_ORDER
    assert [b.id for b in blocks] == [diff.item_id(k) for k, _ in EXPECTED_ORDER]
    assert all(b.item_state == DiscrepancyItemState.DISCREPANCY for b in blocks)
    by_key = {b.key: b for b in blocks}

    cov_a = by_key["coverage_a_limit"]
    assert cov_a.title == "Coverage A (Dwelling) decreased by 14.3%"
    exp_cell, ren_cell, ams_cell = (cov_a.slot(PolicyRole.EXPIRING), cov_a.slot(PolicyRole.RENEWAL),
                                    cov_a.slot(PolicyRole.AMS))
    assert (exp_cell.value, exp_cell.state) == ("$350,000", EvidenceState.VERIFIED) and exp_cell.passage_ids
    assert (ren_cell.value, ren_cell.state) == ("$300,000", EvidenceState.VERIFIED) and ren_cell.passage_ids
    assert (ams_cell.value, ams_cell.state) == ("$350,000", EvidenceState.VERIFIED) and ams_cell.passage_ids
    assert cov_a.slot(PolicyRole.QUOTE) is None  # no quote document: N/A
    assert "AMS states $350,000" in cov_a.prose[0].text

    removed = by_key["discount_multi_policy"]
    assert removed.slot(PolicyRole.RENEWAL).state == EvidenceState.ABSENT  # N/A, not stated
    assert by_key["total_premium"].title == "Total Premium differs from AMS"
    assert "edition" in by_key["form:HO0003"].title

    # every value shown in a slot is a verified quote with its passage (spec section 1)
    for b in blocks:
        for cell in b.slots.values():
            if cell.value is not None:
                assert cell.state == EvidenceState.VERIFIED and cell.passage_ids
    s.close()


def test_ams_facts_come_from_the_key_table_and_verify_against_the_rendered_pdf(h):
    exp, ren, amsd = h.add_doc(EXPIRING), h.add_doc(RENEWAL), h.add_ams(AMS)
    fake = FakeProvider(policy_responder())
    rid = _run(h, fake, {"expiring": exp, "renewal": ren, "ams": amsd})
    s, run = h.run_row(rid)
    facts = {f.field: f for f in run.facts if f.document_id == amsd}
    assert set(facts) == {"policy_number", "coverage_a_limit", "total_premium"}
    assert all(f.state == EvidenceState.VERIFIED and f.passage_id for f in facts.values())
    assert facts["coverage_a_limit"].quote == "Cov A: $350,000"
    assert not any(c.document_id == amsd for c in run.model_calls)
    ams_rd = next(rd for rd in run.documents if rd.slot == "ams")
    assert ams_rd.status == "done"
    s.close()


def test_unmapped_ams_fields_are_named_in_a_run_notice(h):
    exp, ren, amsd = h.add_doc(EXPIRING), h.add_doc(RENEWAL), h.add_ams(AMS)
    rid = _run(h, FakeProvider(policy_responder()), {"expiring": exp, "renewal": ren, "ams": amsd})
    s, run = h.run_row(rid)
    notices = run.artifact.latest_content().notices
    assert any(n.kind == "info" and "Agent Notes" in n.message for n in notices)
    s.close()


def test_conflicting_ams_fields_show_needs_review_in_the_slot_and_never_count(h):
    exp, ren = h.add_doc(EXPIRING), h.add_doc(RENEWAL)
    amsd = h.add_ams({"Cov B": "$35,000", "Other Structures": "$40,000"})
    rid = _run(h, FakeProvider(policy_responder()), {"expiring": exp, "renewal": ren, "ams": amsd})
    s, run = h.run_row(rid)
    fact = next(f for f in run.facts if f.document_id == amsd and f.field == "coverage_b_limit")
    assert fact.state == EvidenceState.CONFLICTING and len(fact.conflicts) == 2
    # coverage B agrees between expiring and renewal; the uncertain AMS value is not a mismatch (D15)
    assert "coverage_b_limit" not in {b.key for b in _blocks(run)}
    s.close()


def test_unverified_renewal_value_is_a_needs_review_item_never_removed(h):
    exp, ren = h.add_doc(EXPIRING), h.add_doc(RENEWAL)

    def tamper(text, reply):
        if "$2,500" in text:  # the renewal: claim a deductible the page does not print
            for f in reply["facts"]:
                if f["field"] == "deductible_all_peril":
                    f["value"], f["quote"] = "$9,999", "All Other Perils Deductible: $9,999"
        return reply

    rid = _run(h, FakeProvider(policy_responder(tamper)), {"expiring": exp, "renewal": ren})
    s, run = h.run_row(rid)
    item = next(b for b in _blocks(run) if b.key == "deductible_all_peril")
    assert item.item_state == DiscrepancyItemState.NEEDS_REVIEW
    assert item.severity == Severity.LOW
    assert item.slot(PolicyRole.RENEWAL).state == EvidenceState.NOT_FOUND
    assert item.slot(PolicyRole.RENEWAL).value is None
    assert "needs review" in item.title
    s.close()


def test_failed_optional_quote_reads_not_read_in_every_slot(h):
    exp, ren = h.add_doc(EXPIRING), h.add_doc(RENEWAL)
    bad = h.add_doc(data=b"not a pdf at all", name="quote.pdf")
    rid = _run(h, FakeProvider(policy_responder()), {"expiring": exp, "renewal": ren, "quote": bad})
    s, run = h.run_row(rid)
    assert run.status == RunStatus.PARTIAL and run.retryable
    blocks = _blocks(run)
    assert blocks and all(b.slot(PolicyRole.QUOTE).state == EvidenceState.FAILED for b in blocks)
    assert all(b.slot(PolicyRole.QUOTE).value is None for b in blocks)
    assert any(n.kind == "documents_failed" and n.retryable for n in run.artifact.latest_content().notices)
    s.close()


def test_missing_ams_original_fails_only_the_ams_column(h):
    exp, ren, amsd = h.add_doc(EXPIRING), h.add_doc(RENEWAL), h.add_ams(AMS)
    del h.originals[amsd]
    rid = _run(h, FakeProvider(policy_responder()), {"expiring": exp, "renewal": ren, "ams": amsd})
    s, run = h.run_row(rid)
    assert run.status == RunStatus.PARTIAL
    assert runner.slot_states(run)["ams"] == "not_read"
    assert all(b.slot(PolicyRole.AMS).state == EvidenceState.FAILED for b in _blocks(run))
    s.close()


def test_quote_document_is_compared_with_the_renewal(h):
    exp, ren, quo = h.add_doc(EXPIRING), h.add_doc(RENEWAL), h.add_doc(QUOTE)
    rid = _run(h, FakeProvider(policy_responder()), {"expiring": exp, "renewal": ren, "quote": quo})
    s, run = h.run_row(rid)
    cov_a = next(b for b in _blocks(run) if b.key == "coverage_a_limit")
    assert cov_a.slot(PolicyRole.QUOTE).value == "$300,000"  # agrees with renewal: no mismatch text
    assert "quote states" not in cov_a.prose[0].text
    # a key the quote does not state is N/A in its slot (absent in quote never counts, D10)
    disc = next(b for b in _blocks(run) if b.key == "discount_multi_policy")
    assert disc.slot(PolicyRole.QUOTE).state == EvidenceState.ABSENT
    s.close()


def test_content_is_deterministic_and_rebuilds_byte_identical(h):
    exp, ren, amsd = h.add_doc(EXPIRING), h.add_doc(RENEWAL), h.add_ams(AMS)
    fake = FakeProvider(policy_responder())
    rid = _run(h, fake, {"expiring": exp, "renewal": ren, "ams": amsd})
    m = h.manager(fake)
    with h.factory() as s:
        run = s.get(models.Run, rid)
        stored = run.artifact.latest_content().model_dump(mode="json")
        again = m.policy_check_content(run).model_dump(mode="json")
        third = m.policy_check_content(run).model_dump(mode="json")
    assert json.dumps(stored, sort_keys=True) == json.dumps(again, sort_keys=True) == json.dumps(third, sort_keys=True)
    assert len(fake.requests) == 2  # rebuilding makes no model call


def test_no_discrepancies_says_so_in_a_paragraph(h):
    exp, ren = h.add_doc(RENEWAL), h.add_doc(RENEWAL)
    rid = _run(h, FakeProvider(policy_responder()), {"expiring": exp, "renewal": ren})
    s, run = h.run_row(rid)
    content = run.artifact.latest_content()
    assert _blocks(run) == []
    text = " ".join(seg.text for _, b in content.iter_blocks() for seg in getattr(b, "segments", []))
    assert "No discrepancies" in text
    s.close()


# --------------------------------------------------------------------- slot accepts


def test_slot_accepts_keeps_pdfs_out_of_the_ams_slot_and_ams_out_of_pdf_slots(h):
    exp, ren, quo, amsd = h.add_doc(EXPIRING), h.add_doc(RENEWAL), h.add_doc(QUOTE), h.add_ams(AMS)
    m = h.manager(FakeProvider(policy_responder()))
    with pytest.raises(SlotError, match="AMS"):
        m.submit(h.pc_id, [exp, ren, quo], slots={exp: "expiring", ren: "renewal", quo: "ams"})
    with pytest.raises(SlotError, match="AMS"):
        m.submit(h.pc_id, [amsd, ren], slots={amsd: "expiring", ren: "renewal"})


# --------------------------------------------------------------------- mapping rules (pure)


def test_slot_to_cell_rules():
    S = diff.SlotState
    assert policy_check.slot_cell(diff.Slot("quote", S.NO_DOCUMENT)) is None
    assert policy_check.slot_cell(diff.Slot("quote", S.NOT_STATED)).state == EvidenceState.ABSENT
    assert policy_check.slot_cell(diff.Slot("quote", S.NOT_READ)).state == EvidenceState.FAILED
    pending = policy_check.slot_cell(diff.Slot("quote", S.NEEDS_REVIEW, evidence_state=EvidenceState.PENDING))
    assert pending.state == EvidenceState.NOT_FOUND and pending.value is None
    v = policy_check.slot_cell(diff.Slot("renewal", S.VALUE, value="$1", evidence_state=EvidenceState.VERIFIED,
                                         passage_ids=(4,), fact_ids=(9,)))
    assert (v.value, v.state, v.passage_ids, v.fact_ids) == ("$1", EvidenceState.VERIFIED, [4], [9])
