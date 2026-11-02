"""Run budget and usage accounting (M4, D3)."""

from __future__ import annotations

import threading

import pytest

from providers.budget import RunBudget, estimate_tokens
from providers.config import ModelPrice, ProviderConfig
from providers.errors import BudgetExhausted
from providers.types import CallRecord, CallRole


def _record(**kw) -> CallRecord:
    base = dict(
        role=CallRole.EXTRACT,
        model="m",
        prompt_tokens=100,
        completion_tokens=50,
        cached_tokens=0,
        reasoning_tokens=0,
        cost_usd=0.0,
        attempt=1,
        outcome="ok",
        latency_s=0.1,
        label="doc-1",
    )
    base.update(kw)
    return CallRecord(**base)


def test_default_limit_is_1_5m():
    # T14 raised the run budget from 600k (D3) to 1.5M; per-document ceiling 500k
    assert RunBudget().limit == 1_500_000


def test_reserve_settle_release():
    b = RunBudget(limit=1000)
    r = b.reserve(600, label="a")
    assert b.reserved == 600 and b.remaining == 400
    b.settle(r, 250)
    assert b.used == 250 and b.reserved == 0 and b.remaining == 750
    r2 = b.reserve(700, label="b")
    b.release(r2)
    assert b.remaining == 750
    with pytest.raises(ValueError):
        b.settle(r2, 1)  # already closed


def test_exhaustion_raises_and_is_not_sticky():
    b = RunBudget(limit=1000)
    b.settle(b.reserve(900, label="a"), 900)
    with pytest.raises(BudgetExhausted) as exc:
        b.reserve(200, label="doc-2")
    assert exc.value.requested == 200 and exc.value.remaining == 100
    assert b.exhausted is True
    b.settle(b.reserve(50, label="small"), 50)  # a later call that fits still runs
    assert b.used == 950


def test_settle_may_exceed_reservation_and_remaining_floors_at_zero():
    b = RunBudget(limit=100)
    b.settle(b.reserve(90, label="a"), 150)
    assert b.used == 150 and b.remaining == 0
    assert not b.can_afford(1)


def test_reservations_are_thread_safe():
    b = RunBudget(limit=10_000)
    ok = []
    fails = []

    def worker():
        for _ in range(100):
            try:
                b.reserve(10, label="t")
                ok.append(1)
            except BudgetExhausted:
                fails.append(1)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 1000 and len(fails) == 200
    assert b.reserved == 10_000


def test_records_totals_and_callback():
    seen = []
    b = RunBudget(limit=10_000, on_record=seen.append)
    b.record(_record(prompt_tokens=100, completion_tokens=10, cached_tokens=40, cost_usd=0.5))
    b.record(_record(role=CallRole.COMPOSE, prompt_tokens=200, completion_tokens=20, reasoning_tokens=5, cost_usd=0.25))
    t = b.totals()
    assert (t.calls, t.prompt_tokens, t.completion_tokens) == (2, 300, 30)
    assert (t.cached_tokens, t.reasoning_tokens) == (40, 5)
    assert t.total_tokens == 330
    assert t.cost_usd == pytest.approx(0.75)
    assert t.by_role[CallRole.EXTRACT].calls == 1
    assert t.by_role[CallRole.COMPOSE].cost_usd == pytest.approx(0.25)
    assert len(seen) == 2 and b.records == seen


def test_callback_failure_does_not_break_accounting():
    def boom(_):
        raise RuntimeError("db down")

    b = RunBudget(limit=10_000, on_record=boom)
    b.record(_record())
    assert b.totals().calls == 1


def test_cost_with_cached_tokens():
    cfg = ProviderConfig(prices={"m": ModelPrice(input_per_mtok=2.0, cached_input_per_mtok=0.5, output_per_mtok=8.0)})
    cost = cfg.estimate_cost("m", prompt_tokens=1_000_000, completion_tokens=500_000, cached_tokens=400_000)
    # 600k uncached * 2 + 400k cached * 0.5 + 500k out * 8, per million
    assert cost == pytest.approx(1.2 + 0.2 + 4.0)


def test_unknown_model_uses_fallback_price():
    cfg = ProviderConfig(prices={}, fallback_price=ModelPrice(1.0, 1.0, 1.0))
    assert cfg.estimate_cost("nope", prompt_tokens=1_000_000, completion_tokens=0, cached_tokens=0) == pytest.approx(1.0)


def test_estimate_tokens_is_conservative_and_monotonic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd" * 100) >= 100  # at least the usual 4 chars per token
    assert estimate_tokens("a" * 1000) > estimate_tokens("a" * 10)


def test_negative_or_zero_reservation_rejected():
    b = RunBudget(limit=10)
    with pytest.raises(ValueError):
        b.reserve(-1, label="x")
    with pytest.raises(ValueError):
        RunBudget(limit=0)
