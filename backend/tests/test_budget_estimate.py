"""Pre-send token estimate and the windows pre-check (D3), after the first live eval.

The live eval refused the 300-page fixture with document_budget_exhausted: at
3 chars per token the pre-check estimated 480k against a 400k ceiling, while
the real prompt cost is about 300k (OpenAI bills dense policy prose at about
4.7 chars per token). These tests pin the calibrated estimate, the pre-check
arithmetic and the raised per-document ceiling, and that D3 still refuses a
document that truly does not fit before anything is sent.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.db import COMPARE_QUOTES
from backend.pipeline import extract, windows
from backend.providers import (
    BudgetExhausted,
    CallRole,
    DocumentBudgetExhausted,
    FakeProvider,
    ProviderConfig,
    RunBudget,
)
from backend.providers.budget import estimate_tokens
from backend.providers.fake import FakeReply
from backend.schemas import FactField
from pipeline_fakes import extraction_reply, smart_responder

FIELDS = [FactField.model_validate(f) for f in COMPARE_QUOTES["fact_schema"]]
LONG_300 = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "quotes" / "long_300.pdf"

SENTENCES = [
    '"Property damage" means physical injury to tangible property, including all resulting loss of use.',
    "Any insured must send the Carrier copies of any demand, notice, summons or legal paper received.",
    "Notice under this provision must be given within 90 days of receipt of the demand or suit papers.",
    "See Section V, paragraph 9(f) for the conditions that apply to each location shown in the Schedule.",
    "Each insured shall provide access to books and records that relate to this insurance on request.",
]


@dataclass
class P:
    page_no: int
    text: str
    ocr_text: str | None = None
    words: list | None = None


def prose_page(n: int, chars: int) -> str:
    """One page of policy prose, about `chars` characters, broken into lines like a text layer."""
    lines = [f"Policy No. LG-773595    Page {n}"]
    i = n
    while sum(len(x) + 1 for x in lines) < chars:
        lines.append(SENTENCES[i % len(SENTENCES)])
        i += 1
    return "\n".join(lines)


def prose_doc(pages: int, chars_per_page: int) -> list[P]:
    return [P(i, prose_page(i, chars_per_page)) for i in range(1, pages + 1)]


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------- the estimate


def test_estimate_is_four_chars_per_token_by_default():
    assert ProviderConfig().chars_per_token == 4.0
    assert estimate_tokens("x" * 4000) == 1000 == estimate_tokens("x" * 3997)
    assert estimate_tokens("") == 0


def test_schema_and_system_prompt_are_counted_once_per_call():
    fake = FakeProvider([])
    pages = prose_doc(15, 3900)
    msgs = extract.build_extraction_messages(FIELDS, pages, document_name="x.pdf")
    schema = extract.build_extraction_schema(FIELDS)
    cfg = fake.config
    by_hand = (sum(cfg.message_overhead_tokens + sum(estimate_tokens(p.text, 4.0) for p in m.parts())
                   for m in msgs)
               + estimate_tokens(json.dumps(schema.json_schema), 4.0))
    assert fake.estimate_input_tokens(msgs, schema) == by_hand


def test_precheck_counts_every_input_full_output_in_flight_and_expected_output_after():
    fake = FakeProvider([])
    pages = prose_doc(120, 3900)
    ws = windows.plan_windows(120)
    schema = extract.build_extraction_schema(FIELDS)
    inputs = sum(fake.estimate_input_tokens(
        extract.build_extraction_messages(FIELDS, windows._window_pages(pages, w), document_name="x.pdf",
                                          part=windows.window_part(w, ws, 120)), schema)
        for w in ws)
    cfg = fake.config
    expected = (inputs + (len(ws) - cfg.max_concurrency) * cfg.expected_output_tokens[CallRole.EXTRACT]
                + cfg.max_concurrency * cfg.max_output_tokens[CallRole.EXTRACT])
    assert windows.precheck_tokens(fake, FIELDS, pages, ws, document_name="x.pdf") == expected
    # fewer windows than the concurrency limit: every window holds a full reservation
    two = windows.plan_windows(20)
    short = prose_doc(20, 3900)
    in2 = sum(fake.estimate_input_tokens(
        extract.build_extraction_messages(FIELDS, windows._window_pages(short, w), document_name="x.pdf",
                                          part=windows.window_part(w, two, 20)), schema)
        for w in two)
    assert windows.precheck_tokens(fake, FIELDS, short, two, document_name="x.pdf") == (
        in2 + 2 * cfg.max_output_tokens[CallRole.EXTRACT])


# --------------------------------------------------------------------- long documents


def test_a_300_page_document_of_realistic_density_completes_within_default_budgets():
    pages = prose_doc(300, 3900)  # the long_300 fixture's density (3.9k chars per page)
    run_budget = RunBudget()
    child = run_budget.child()
    assert (run_budget.limit, child.limit) == (1_500_000, 500_000)

    def respond(req):  # prompt billed at the estimate (worst case for the cap), 3k output each
        return FakeReply(content=json.dumps(extraction_reply(_user(req))), completion_tokens=3_000)

    fake = FakeProvider(respond)
    out = run(windows.extract_document(fake, child, FIELDS, pages, document_name="long.pdf"))
    assert isinstance(out, windows.WindowedExtraction)
    assert len(fake.requests) == len(out.windows) == 23
    assert not child.exhausted and not run_budget.exhausted
    assert 0 < child.used <= child.limit and child.reserved == 0 == run_budget.reserved


def test_the_long_300_fixture_fits_the_default_ceiling():
    if not LONG_300.exists():
        pytest.skip("long_300 fixture not present")
    from backend.pipeline import parse

    pages = parse.parse_pdf(LONG_300.read_bytes()).pages
    fake = FakeProvider([])
    need = windows.precheck_tokens(fake, FIELDS, pages, windows.plan_windows(len(pages)), document_name="long_300.pdf")
    # measured: about 449k planned (360k estimated input) against the 500k ceiling;
    # the old 3 chars per token estimate planned 488k against 400k and refused it
    assert 400_000 < need < fake.config.document_token_budget


def test_a_document_that_truly_exceeds_the_ceiling_still_fails_before_sending():
    pages = prose_doc(300, 8_000)  # twice the density: about 700k estimated input
    fake = FakeProvider(smart_responder())
    run_budget = RunBudget()
    child = run_budget.child()
    with pytest.raises(DocumentBudgetExhausted) as exc:
        run(windows.extract_document(fake, child, FIELDS, pages, document_name="dense.pdf"))
    assert exc.value.code == "document_budget_exhausted"
    assert exc.value.requested > child.limit
    assert fake.requests == [] and child.exhausted and not run_budget.exhausted
    assert (run_budget.used, run_budget.reserved) == (0, 0)


def test_a_run_too_small_for_the_document_fails_as_the_runs_before_sending():
    pages = prose_doc(300, 3900)
    fake = FakeProvider(smart_responder())
    small = RunBudget(limit=300_000)
    with pytest.raises(BudgetExhausted) as exc:
        run(windows.extract_document(fake, small.child(), FIELDS, pages, document_name="long.pdf"))
    assert not isinstance(exc.value, DocumentBudgetExhausted) and small.exhausted
    assert fake.requests == []


# --------------------------------------------------------------------- single call, accounting


def test_single_call_quote_is_unaffected():
    pages = prose_doc(3, 900)
    fake = FakeProvider(smart_responder())
    msgs = extract.build_extraction_messages(FIELDS, pages, document_name="q.pdf")
    reserve = fake.reserved_tokens(CallRole.EXTRACT, msgs, extract.build_extraction_schema(FIELDS))
    # one call reserving its input estimate plus the full 8k reserved output (M4), no window pre-check
    with pytest.raises(BudgetExhausted):
        run(windows.extract_document(fake, RunBudget(limit=reserve - 1), FIELDS, pages, document_name="q.pdf"))
    assert fake.requests == []
    out = run(windows.extract_document(fake, RunBudget(limit=reserve), FIELDS, pages, document_name="q.pdf"))
    assert isinstance(out, extract.ExtractionResult) and len(fake.requests) == 1
    assert fake.requests[0].max_output_tokens == 8_000


def test_window_accounting_settles_to_the_provider_reported_usage():
    pages = prose_doc(40, 3900)

    def respond(req):  # what OpenAI would report: fewer prompt tokens than estimated
        return FakeReply(content=json.dumps(extraction_reply(_user(req))), prompt_tokens=12_345,
                         completion_tokens=1_111)

    run_budget = RunBudget()
    child = run_budget.child()
    fake = FakeProvider(respond)
    out = run(windows.extract_document(fake, child, FIELDS, pages, document_name="x.pdf"))
    n = len(out.windows)
    assert n == 3
    assert child.used == run_budget.used == n * (12_345 + 1_111)
    assert child.reserved == 0 == run_budget.reserved
    assert (out.usage.prompt_tokens, out.usage.completion_tokens) == (n * 12_345, n * 1_111)
    totals = run_budget.totals()
    assert (totals.prompt_tokens, totals.completion_tokens) == (n * 12_345, n * 1_111)


def _user(req) -> str:
    return "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts())
