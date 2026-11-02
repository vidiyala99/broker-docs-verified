"""FakeProvider: the scripted Provider that pipeline tests (T5, T6, T8) use."""

from __future__ import annotations

import asyncio

import pytest

from providers import (
    BudgetExhausted,
    CallRole,
    FakeProvider,
    Message,
    Provider,
    ProviderTimeout,
    RefusalError,
    RunBudget,
    SchemaValidationError,
    StructuredSchema,
)
from providers.fake import FakeRefusal, FakeReply, FakeTransient

SCHEMA = StructuredSchema.from_dict("s", {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]})


def run(coro):
    return asyncio.run(coro)


def call(p, budget, **kw):
    return p.complete_json(role=CallRole.EXTRACT, messages=[Message.user("doc")], schema=SCHEMA, budget=budget, **kw)


def test_fake_is_a_provider_and_records_requests():
    p = FakeProvider([{"a": 1}])
    assert isinstance(p, Provider)
    b = RunBudget()
    res = run(call(p, b, label="doc-1"))
    assert res.data == {"a": 1}
    assert p.requests[0].label == "doc-1"
    assert p.requests[0].schema is SCHEMA
    assert b.totals().calls == 1 and b.used > 0


def test_fake_shares_refusal_retry_and_budget_logic():
    p = FakeProvider([FakeRefusal("no"), {"a": "bad"}, {"a": "still bad"}])
    b = RunBudget()
    with pytest.raises(RefusalError):
        run(call(p, b))
    with pytest.raises(SchemaValidationError):
        run(call(p, b))
    with pytest.raises(BudgetExhausted):
        run(call(FakeProvider([{"a": 1}]), RunBudget(limit=10)))


def test_fake_transient_and_explicit_usage():
    p = FakeProvider([FakeTransient("timeout")] * 4, sleep=_nosleep)
    with pytest.raises(ProviderTimeout):
        run(call(p, RunBudget()))
    p2 = FakeProvider([FakeReply(content='{"a": 2}', prompt_tokens=7, completion_tokens=3)])
    b = RunBudget()
    res = run(call(p2, b))
    assert res.usage.prompt_tokens == 7 and b.used == 10


def test_fake_responder_callable_and_text():
    p = FakeProvider(lambda req: {"a": len(req.messages)})
    assert run(call(p, RunBudget())).data == {"a": 1}
    t = FakeProvider(["hello"])
    assert run(t.complete_text(role=CallRole.CHAT, messages=[Message.user("hi")], budget=RunBudget())).text == "hello"


def test_fake_exhausted_script_fails_loudly():
    p = FakeProvider([])
    with pytest.raises(AssertionError):
        run(call(p, RunBudget()))


async def _nosleep(_):
    return None
