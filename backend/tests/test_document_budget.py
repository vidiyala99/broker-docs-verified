"""T14 per-document budget: a DocumentBudget child reserves from the run and caps one document."""

from __future__ import annotations

import pytest

from backend.providers import BudgetExhausted, DocumentBudget, DocumentBudgetExhausted, ProviderConfig, RunBudget
from backend.providers.types import CallRecord, CallRole


def test_defaults_are_1_5m_per_run_and_500k_per_document():
    assert RunBudget().limit == 1_500_000
    assert RunBudget().child().limit == 500_000
    cfg = ProviderConfig()
    assert (cfg.run_token_budget, cfg.document_token_budget) == (1_500_000, 500_000)
    env = ProviderConfig.from_env({"BROKERDOCS_DOCUMENT_TOKEN_BUDGET": "123"})
    assert env.document_token_budget == 123


def test_child_charges_itself_and_the_run():
    run = RunBudget(limit=1000)
    a = run.child(400, label="doc-a")
    assert isinstance(a, DocumentBudget)
    r = a.reserve(300)
    assert (a.reserved, run.reserved, a.remaining) == (300, 300, 100)
    a.settle(r, 120)
    assert (a.used, run.used, a.reserved, run.reserved) == (120, 120, 0, 0)
    r2 = a.reserve(200)
    a.release(r2)
    assert (a.used, run.used, a.reserved, run.reserved) == (120, 120, 0, 0)


def test_child_ceiling_refuses_without_touching_the_run():
    run = RunBudget(limit=1000)
    a = run.child(400)
    a.settle(a.reserve(350), 350)
    with pytest.raises(DocumentBudgetExhausted) as exc:
        a.reserve(100)
    assert exc.value.code == "document_budget_exhausted" and isinstance(exc.value, BudgetExhausted)
    assert a.exhausted and not run.exhausted and run.reserved == 0
    b = run.child(400)  # another document still gets its share
    b.settle(b.reserve(400), 400)
    assert run.used == 750


def test_run_exhaustion_is_reported_as_the_runs():
    run = RunBudget(limit=500)
    a, b = run.child(400), run.child(400)
    a.settle(a.reserve(400), 400)
    with pytest.raises(BudgetExhausted) as exc:
        b.reserve(200)
    assert not isinstance(exc.value, DocumentBudgetExhausted) and run.exhausted and b.exhausted


def test_child_records_go_to_the_run_ledger_and_hook():
    seen = []
    run = RunBudget(limit=1000, on_record=seen.append)
    rec = CallRecord(role=CallRole.EXTRACT, model="m", prompt_tokens=1, completion_tokens=1, cached_tokens=0,
                     reasoning_tokens=0, cost_usd=0.0, attempt=1, outcome="ok", latency_s=0.0, label="x")
    run.child(100).record(rec)
    assert run.records == [rec] and seen == [rec]


def test_child_ceiling_must_be_positive():
    with pytest.raises(ValueError):
        RunBudget().child(0)
