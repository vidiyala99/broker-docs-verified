"""T14 windows: window math and overlap, per-window strict extract, progress, budget pre-check, cancel."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import pytest

from backend.db import COMPARE_QUOTES
from backend.pipeline import extract, verify, windows
from backend.pipeline.windows import Window, WindowConfig, plan_windows
from backend.providers import (
    BudgetExhausted,
    CallRole,
    DocumentBudgetExhausted,
    FakeProvider,
    ProviderConfig,
    RefusalError,
    RunBudget,
)
from backend.providers.fake import FakeRefusal
from backend.providers.types import TextPart
from backend.schemas import EvidenceState, FactField
from pipeline_fakes import extraction_reply, smart_responder

FIELDS = [FactField.model_validate(f) for f in COMPARE_QUOTES["fact_schema"]]


@dataclass
class P:
    """A page as extract and verify see it (no word boxes: verify uses the text layer)."""

    page_no: int
    text: str
    ocr_text: str | None = None
    words: list | None = None


def doc(n: int, facts: dict[int, str] | None = None) -> list[P]:
    facts = facts or {}
    return [P(i, f"Policy wording page {i}. General conditions apply.\n{facts.get(i, '')}".strip())
            for i in range(1, n + 1)]


def user_text(req) -> str:
    return "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts() if isinstance(p, TextPart))


def page_tags(req) -> list[int]:
    return [int(x) for x in re.findall(r"^\[page (\d+)\]$", user_text(req), re.M)]


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------- window math


def test_short_documents_take_one_window():
    assert plan_windows(0) == []
    assert plan_windows(1) == [Window(1, 1, 1)]
    assert plan_windows(15) == [Window(1, 1, 15)]
    assert not windows.needs_windows(15) and windows.needs_windows(16)


def test_window_layout_for_16_120_and_300_pages():
    assert plan_windows(16) == [Window(1, 1, 15), Window(2, 14, 16)]
    w120 = plan_windows(120)
    assert len(w120) == 10
    assert (w120[0].first, w120[0].last) == (1, 15) and (w120[1].first, w120[1].last) == (14, 28)
    assert w120[-1].last == 120
    assert len(plan_windows(300)) == 23 and plan_windows(300)[-1].last == 300


@pytest.mark.parametrize("n", range(1, 320))
def test_windows_cover_every_page_with_two_pages_of_overlap(n):
    ws = plan_windows(n)
    covered = set()
    for w in ws:
        assert 1 <= w.first <= w.last <= n and len(w.pages) <= 15
        covered.update(w.pages)
    assert covered == set(range(1, n + 1))
    assert [w.index for w in ws] == list(range(1, len(ws) + 1))
    for a, b in zip(ws, ws[1:]):
        assert a.last - b.first + 1 == 2  # exactly two shared pages
        assert b.last > a.last  # no window is swallowed by the one before


def test_window_config_is_validated_and_configurable():
    with pytest.raises(ValueError):
        WindowConfig(size=10, overlap=10)
    with pytest.raises(ValueError):
        WindowConfig(size=0)
    assert plan_windows(20, WindowConfig(size=10, overlap=0)) == [Window(1, 1, 10), Window(2, 11, 20)]


# --------------------------------------------------------------------- per-window extraction


def test_each_window_is_a_strict_call_with_absolute_pages_and_its_own_boundary():
    pages = doc(40, {1: "Carrier: Harborline Specialty", 33: "Total Premium: $26,391.14"})
    fake = FakeProvider(smart_responder())
    result = run(windows.extract_windows(fake, RunBudget(), FIELDS, pages, document_name="long.pdf"))
    reqs = [r for r in fake.requests if r.role is CallRole.EXTRACT]
    assert [page_tags(r) for r in reqs] == [list(range(1, 16)), list(range(14, 29)), list(range(27, 41))]
    boundaries = set()
    for r in reqs:
        system = r.messages[0].parts()[0].text
        b = re.search(r"<<<DOCUMENT (\w+) ", system).group(1)
        assert system == extract.SYSTEM_PROMPT.format(b=b)  # R3 wording, unchanged
        assert f"<<<DOCUMENT {b}\n" in user_text(r) and r.schema is not None
        boundaries.add(b)
    assert len(boundaries) == 3
    cands = result.candidates()
    assert ("carrier_name", "Harborline Specialty", 1) in [(c.field, c.value, c.page) for c in cands]
    assert ("total_premium", "$26,391.14", 33) in [(c.field, c.value, c.page) for c in cands]


def test_progress_reports_each_finished_window_in_order():
    seen = []
    run(windows.extract_windows(FakeProvider(smart_responder()), RunBudget(), FIELDS, doc(120),
                                document_name="x.pdf", on_progress=lambda k, n: seen.append((k, n))))
    assert seen == [(k, 10) for k in range(1, 11)]


def _needs_header(req):
    """Reads Total Premium only when the "Premium Summary" header is in the same window above it."""
    text = user_text(req)
    reply = extraction_reply(text)
    header = text.find("Premium Summary")
    reply["facts"] = [f for f in reply["facts"]
                      if f["field"] != "total_premium" or (0 <= header < text.find("Total Premium:"))]
    return reply


@pytest.mark.parametrize("overlap, found", [(2, True), (0, False)])
def test_fact_straddling_a_window_boundary_is_found_through_the_overlap(overlap, found):
    # The header is on page 14 and its value on page 16: only a window holding both reads it.
    pages = doc(30, {14: "Premium Summary", 16: "Total Premium: $14,490.00"})
    cfg = WindowConfig(size=15, overlap=overlap)
    ex = run(windows.extract_windows(FakeProvider(_needs_header), RunBudget(), FIELDS, pages,
                                     document_name="x.pdf", config=cfg))
    merged = {m.field: m for m in windows.verify_document(ex, pages)}
    prem = merged["total_premium"]
    if found:
        assert prem.state == EvidenceState.VERIFIED and prem.page_no == 16 and prem.value == "$14,490.00"
    else:
        assert prem.state == EvidenceState.ABSENT


def test_model_page_outside_its_window_becomes_the_window_hint_and_verify_searches_the_whole_document():
    pages = doc(30, {25: "Carrier: Harborline Specialty"})

    def responder(req):
        reply = extraction_reply(user_text(req))
        for f in reply["facts"]:
            f["page"] = 2  # wrong page, outside the second window
        return reply

    ex = run(windows.extract_windows(FakeProvider(responder), RunBudget(), FIELDS, pages, document_name="x.pdf"))
    carrier = [c for c in ex.candidates() if c.field == "carrier_name"]
    assert [c.page for c in carrier] == [14]  # first page of window 2 (14-28)
    merged = {m.field: m for m in windows.verify_document(ex, pages)}
    assert merged["carrier_name"].page_no == 25 and merged["carrier_name"].state == EvidenceState.VERIFIED


def test_option_count_is_the_largest_any_window_reports():
    def responder(req):
        text = user_text(req)
        if "[page 20]" in text:
            return extraction_reply(text, options=2, option="Option 1 (Primary)")
        return extraction_reply(text)

    ex = run(windows.extract_windows(FakeProvider(responder), RunBudget(), FIELDS, doc(30), document_name="x.pdf"))
    assert (ex.option_count, ex.option_label) == (2, "Option 1 (Primary)")


# --------------------------------------------------------------------- headings and labels as values

HEADER = "Policy No. LG-1 Commercial General Liability Policy"
REAL_FORM = "CG 00 02 04 13 Commercial General Liability Coverage Form (Claims-Made)"
REAL_PRODUCER = "Lantern Insurance Brokerage Inc."


def decoy_doc(n: int = 40) -> list[P]:
    """Real values on page 2 only; every page has a running header, later pages have headings and labels."""
    body = {2: f"Producer: {REAL_PRODUCER}\nPolicy Form: {REAL_FORM}",
            20: "76. Policy Territory Definitions\nThe Producer of record must send the Carrier copies.",
            35: "The Producer of record shall cooperate with the Carrier.",
            40: "Coverage territory: all locations within the State of Ohio and goods in transit."}
    return [P(i, f"{HEADER}\nPolicy wording page {i}.\n{body.get(i, '')}".strip()) for i in range(1, n + 1)]


def _lazy_model(req):
    """Like the live model on long documents: fills fields from nearby headings and labels."""
    text = user_text(req)
    reply = extraction_reply(text)
    have = {f["field"] for f in reply["facts"]}
    first = page_tags(req)[0]
    if "producer" not in have and "Producer of record" in text:
        page = 20 if 20 in page_tags(req) else 35
        reply["facts"].append({"field": "producer", "value": "Producer of record", "page": page,
                               "quote": "The Producer of record"})
    if "territory" not in have and "Policy Territory Definitions" in text:
        reply["facts"].append({"field": "territory", "value": "Policy Territory Definitions", "page": 20,
                               "quote": "76. Policy Territory Definitions"})
    if "policy_form" not in have:  # the running header, on the window's first page
        reply["facts"].append({"field": "policy_form", "value": "Commercial General Liability Policy",
                               "page": first, "quote": HEADER})
    return reply


def _careful_model(req):
    """Follows the null-if-absent rule: a window without the value returns nothing for the field."""
    return extraction_reply(user_text(req))


def test_window_calls_carry_the_null_if_absent_note_and_single_calls_do_not():
    fake = FakeProvider(smart_responder())
    run(windows.extract_windows(fake, RunBudget(), FIELDS, decoy_doc(), document_name="x.pdf"))
    reqs = [r for r in fake.requests if r.role is CallRole.EXTRACT]
    assert len(reqs) == 3
    for k, r in enumerate(reqs, start=1):
        u = user_text(r)
        assert u.startswith(f"IMPORTANT: this is part {k} of 3")
        assert "return value, page and quote as null" in u[:u.index("<<<DOCUMENT")]
        assert "actual stated value" in r.messages[0].parts()[0].text
    single = FakeProvider(smart_responder())
    run(windows.extract_document(single, RunBudget(), FIELDS, decoy_doc(15), document_name="q.pdf"))
    assert "IMPORTANT: this is part" not in user_text(single.requests[0])


def test_label_values_from_other_windows_lose_to_the_one_real_value():
    pages = decoy_doc()
    ex = run(windows.extract_windows(FakeProvider(_lazy_model), RunBudget(), FIELDS, pages, document_name="x.pdf"))
    assert sum(1 for c in ex.candidates() if c.value == "Producer of record") >= 2
    merged = {m.field: m for m in windows.verify_document(ex, pages)}
    prod = merged["producer"]
    assert (prod.value, prod.page_no, prod.state, prod.resolution) == (REAL_PRODUCER, 2, EvidenceState.VERIFIED,
                                                                       "single")
    assert {d.value for d in prod.dropped} == {"Producer of record"}
    # a heading is dropped; with no real value left the cell is "Couldn't verify", never verified
    terr = merged["territory"]
    assert terr.state == EvidenceState.NOT_FOUND and terr.reason == "implausible_value" and terr.page_no is None
    # the running header (windows 2 and 3) is stated on a line printed on every page: dropped as
    # boilerplate (merge.repeated_line_pages), so the real form (window 1) is the one value left
    form = merged["policy_form"]
    assert (form.value, form.page_no, form.state) == (REAL_FORM, 2, EvidenceState.VERIFIED)
    assert {d.value for d in form.dropped} == {"Commercial General Liability Policy"}
    assert all(d.reason is None and d.cited for d in form.dropped)  # dropped by merge, not by verify


def test_prompt_compliant_null_windows_leave_the_real_value_alone():
    pages = decoy_doc()
    ex = run(windows.extract_windows(FakeProvider(_careful_model), RunBudget(), FIELDS, pages, document_name="x.pdf"))
    merged = {m.field: m for m in windows.verify_document(ex, pages)}
    assert (merged["producer"].value, merged["producer"].state) == (REAL_PRODUCER, EvidenceState.VERIFIED)
    assert (merged["policy_form"].value, merged["policy_form"].state) == (REAL_FORM, EvidenceState.VERIFIED)
    assert merged["policy_form"].resolution == "single" and merged["producer"].dropped == ()


# --------------------------------------------------------------------- single call unchanged


def test_documents_of_15_pages_or_fewer_take_the_unchanged_single_call():
    pages = doc(15, {3: "Carrier: Harborline Specialty"})
    fake = FakeProvider(smart_responder())
    seen = []
    res = run(windows.extract_document(fake, RunBudget(), FIELDS, pages, document_name="q.pdf",
                                       on_progress=lambda k, n: seen.append((k, n))))
    assert isinstance(res, extract.ExtractionResult) and seen == []
    assert len(fake.requests) == 1 and page_tags(fake.requests[0]) == list(range(1, 16))
    assert windows.verify_document(res, pages) == verify.verify_facts(res.facts, pages)


# --------------------------------------------------------------------- concurrency, failure, budget


class CountingProvider(FakeProvider):
    def __init__(self, responder, **kw):
        super().__init__(responder, **kw)
        self.active = self.peak = 0

    async def _send(self, request):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.005)
            return await super()._send(request)
        finally:
            self.active -= 1


def test_windows_run_concurrently_within_the_providers_four_call_limit():
    fake = CountingProvider(smart_responder())
    budget = RunBudget()
    peak_reserved = 0
    orig = budget.reserve

    def reserve(tokens, **kw):
        nonlocal peak_reserved
        r = orig(tokens, **kw)
        peak_reserved = max(peak_reserved, budget.reserved)
        return r

    budget.reserve = reserve
    run(windows.extract_windows(fake, budget, FIELDS, doc(120), document_name="x.pdf"))
    assert 1 < fake.peak <= 4
    one = fake.reserved_tokens(CallRole.EXTRACT, fake.requests[0].messages, fake.requests[0].schema)
    assert peak_reserved <= 4 * one * 1.2  # at most four windows hold a reservation at a time


def test_a_failing_window_cancels_its_siblings_and_raises():
    def responder(req):
        if "[page 30]" in user_text(req):
            return FakeRefusal()
        return smart_responder()(req)

    async def main():
        with pytest.raises(RefusalError):
            await windows.extract_windows(FakeProvider(responder), RunBudget(), FIELDS, doc(120),
                                          document_name="x.pdf")
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert run(main()) == []


def test_cancel_mid_windows_leaves_no_tasks():
    from pipeline_fakes import GatedProvider

    gated = GatedProvider(smart_responder())

    async def main():
        task = asyncio.create_task(windows.extract_windows(gated, RunBudget(), FIELDS, doc(120),
                                                           document_name="x.pdf"))
        while gated.waiting < 4:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)
        assert gated.waiting == 4  # the rest wait for a slot, not for the model
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert run(main()) == []


def test_budget_precheck_refuses_before_any_call():
    pages = doc(120)
    fake = FakeProvider(smart_responder())
    with pytest.raises(DocumentBudgetExhausted):
        run(windows.extract_windows(fake, RunBudget().child(20_000), FIELDS, pages, document_name="x.pdf"))
    assert fake.requests == []
    small_run = RunBudget(limit=20_000)
    with pytest.raises(BudgetExhausted) as exc:
        run(windows.extract_windows(fake, small_run.child(400_000), FIELDS, pages, document_name="x.pdf"))
    assert not isinstance(exc.value, DocumentBudgetExhausted) and small_run.exhausted
    assert fake.requests == []


def test_window_calls_charge_the_document_and_the_run():
    run_budget = RunBudget()
    child = run_budget.child(400_000)
    fake = FakeProvider(smart_responder(), config=ProviderConfig())
    run(windows.extract_windows(fake, child, FIELDS, doc(40), document_name="x.pdf"))
    assert child.used == run_budget.used > 0 and child.reserved == 0 == run_budget.reserved
    assert len(run_budget.records) == 3
