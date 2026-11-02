"""OpenAIProvider against a fake AsyncOpenAI client. No network."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx2 as httpx
import openai
import pytest
from openai.types.chat import ChatCompletion

from providers import (
    BudgetExhausted,
    CallRole,
    ContextOverflow,
    ImagePart,
    Message,
    MissingApiKey,
    OpenAIProvider,
    OutputTruncated,
    ProviderConfig,
    ProviderRequestError,
    ProviderTimeout,
    ProviderUnavailable,
    RefusalError,
    RunBudget,
    SchemaValidationError,
    StructuredSchema,
    TextPart,
)

SCHEMA = StructuredSchema.from_dict(
    "facts",
    {
        "type": "object",
        "properties": {"value": {"type": "string"}, "page": {"type": "integer"}},
        "required": ["value", "page"],
    },
)
GOOD = {"value": "Acme LLC", "page": 2}
_REQ = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def completion(content=None, *, refusal=None, finish="stop", prompt=100, out=20, cached=0, reasoning=0, model="m"):
    if isinstance(content, dict):
        content = json.dumps(content)
    return ChatCompletion.model_validate(
        {
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": content, "refusal": refusal},
                }
            ],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": out,
                "total_tokens": prompt + out,
                "prompt_tokens_details": {"cached_tokens": cached},
                "completion_tokens_details": {"reasoning_tokens": reasoning},
            },
        }
    )


def rate_limit():
    return openai.RateLimitError("slow down", response=httpx.Response(429, request=_REQ), body=None)


def server_error():
    return openai.InternalServerError("boom", response=httpx.Response(500, request=_REQ), body=None)


def auth_error():
    return openai.AuthenticationError("bad key", response=httpx.Response(401, request=_REQ), body=None)


class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("fake client called more times than scripted")
        item = self.script.pop(0)
        if callable(item):
            item = await item(kwargs)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClient:
    def __init__(self, script):
        self.completions = FakeCompletions(script)
        self.chat = type("Chat", (), {"completions": self.completions})()


class Sleeps:
    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, d):
        self.delays.append(d)


def make(script, *, config=None, budget=None):
    client = FakeClient(script)
    sleeps = Sleeps()
    cfg = config or ProviderConfig()
    provider = OpenAIProvider(config=cfg, client=client, sleep=sleeps)
    return provider, client.completions, sleeps, budget or RunBudget(limit=cfg.run_token_budget)


def msgs():
    return [Message.system("Extract facts."), Message.user("<document>page 1 text</document>")]


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- happy path


def test_structured_call_sends_strict_schema_and_accounts_usage():
    cfg = ProviderConfig()
    provider, fake, _, budget = make([completion(GOOD, prompt=1000, out=200, cached=400, reasoning=50)], config=cfg)
    res = run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget, label="doc-A"))
    assert res.data == GOOD
    assert res.attempts == 1
    assert res.model == cfg.models[CallRole.EXTRACT]

    sent = fake.calls[0]
    assert sent["model"] == cfg.models[CallRole.EXTRACT]
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["response_format"]["json_schema"]["schema"] == SCHEMA.json_schema
    assert sent["max_completion_tokens"] == cfg.max_output_tokens[CallRole.EXTRACT]
    assert sent["store"] is False
    assert sent["timeout"] == cfg.timeouts_s[CallRole.EXTRACT]
    # R3: no tools or web access during extraction
    for key in ("tools", "tool_choice", "functions", "web_search_options"):
        assert key not in sent
    assert sent["messages"][0] == {"role": "system", "content": "Extract facts."}

    u = res.usage
    assert (u.prompt_tokens, u.completion_tokens, u.cached_tokens, u.reasoning_tokens) == (1000, 200, 400, 50)
    expected_cost = cfg.estimate_cost(res.model, prompt_tokens=1000, completion_tokens=200, cached_tokens=400)
    assert u.cost_usd == pytest.approx(expected_cost)
    assert budget.used == 1200 and budget.reserved == 0
    t = budget.totals()
    assert t.calls == 1 and t.cost_usd == pytest.approx(expected_cost)
    rec = budget.records[0]
    assert (rec.role, rec.label, rec.outcome, rec.attempt) == (CallRole.EXTRACT, "doc-A", "ok", 1)


def test_pydantic_schema_returns_parsed_model():
    from pydantic import BaseModel

    class Fact(BaseModel):
        value: str
        page: int

    provider, _, _, budget = make([completion(GOOD)])
    res = run(
        provider.complete_json(
            role=CallRole.EXTRACT, messages=msgs(), schema=StructuredSchema.from_model("fact", Fact), budget=budget
        )
    )
    assert isinstance(res.parsed, Fact) and res.parsed.page == 2


def test_text_call_and_model_override():
    provider, fake, _, budget = make([completion("Carrier B is cheaper [p3].")])
    res = run(provider.complete_text(role=CallRole.CHAT, messages=msgs(), budget=budget, model="custom-model"))
    assert res.text == "Carrier B is cheaper [p3]."
    assert fake.calls[0]["model"] == "custom-model"
    assert "response_format" not in fake.calls[0]


def test_vision_message_is_converted_to_image_parts():
    provider, fake, _, budget = make([completion("page text")])
    m = Message.user([TextPart("Transcribe this page."), ImagePart(data=b"\x89PNG-bytes", mime="image/png")])
    run(provider.complete_text(role=CallRole.VISION, messages=[m], budget=budget))
    content = fake.calls[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Transcribe this page."}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1]["image_url"]["detail"] == "high"


# ---------------------------------------------------------------- refusal


def test_refusal_raises_without_retry_and_is_still_billed():
    provider, fake, _, budget = make([completion(None, refusal="I can't help with that.", prompt=500, out=10)])
    with pytest.raises(RefusalError) as exc:
        run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget, label="doc-B"))
    assert exc.value.refusal == "I can't help with that."
    assert exc.value.code == "refusal"
    assert exc.value.label == "doc-B"
    assert len(fake.calls) == 1
    assert budget.used == 510
    assert budget.records[0].outcome == "refusal"


def test_content_filter_finish_is_a_refusal():
    provider, _, _, budget = make([completion(None, finish="content_filter")])
    with pytest.raises(RefusalError):
        run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))


# ---------------------------------------------------------------- schema-invalid retry


def test_schema_invalid_then_valid_retries_once_with_correction():
    provider, fake, _, budget = make(
        [completion({"value": "x"}, prompt=100, out=10), completion(GOOD, prompt=150, out=20)]
    )
    res = run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))
    assert res.data == GOOD
    assert res.attempts == 2
    assert len(fake.calls) == 2
    retry_msgs = fake.calls[1]["messages"]
    assert retry_msgs[:2] == fake.calls[0]["messages"]
    assert retry_msgs[2]["role"] == "assistant"
    assert retry_msgs[3]["role"] == "user" and "page" in retry_msgs[3]["content"]
    assert budget.used == 280
    assert [r.outcome for r in budget.records] == ["schema_invalid", "ok"]
    # usage on the result covers both attempts
    assert res.usage.prompt_tokens == 250 and res.usage.completion_tokens == 30


def test_schema_invalid_twice_raises():
    provider, fake, _, budget = make([completion("not json at all"), completion({"value": 3, "page": 1})])
    with pytest.raises(SchemaValidationError) as exc:
        run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget, label="doc-C"))
    assert exc.value.code == "schema_invalid"
    assert exc.value.attempts == 2
    assert exc.value.errors
    assert len(fake.calls) == 2
    assert budget.totals().calls == 2


def test_schema_retry_count_is_configurable():
    cfg = ProviderConfig(schema_retries=0)
    provider, fake, _, budget = make([completion({"value": "x"})], config=cfg)
    with pytest.raises(SchemaValidationError):
        run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))
    assert len(fake.calls) == 1


def test_truncated_output_raises_without_retry():
    provider, fake, _, budget = make([completion('{"value": "Ac', finish="length", out=8000)])
    with pytest.raises(OutputTruncated):
        run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))
    assert len(fake.calls) == 1
    assert budget.records[0].outcome == "truncated"


# ---------------------------------------------------------------- budget (M4)


def test_budget_checked_before_sending():
    cfg = ProviderConfig()
    budget = RunBudget(limit=cfg.max_output_tokens[CallRole.EXTRACT])  # reserved output alone fills it
    provider, fake, _, _ = make([completion(GOOD)], config=cfg)
    with pytest.raises(BudgetExhausted) as exc:
        run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget, label="doc-D"))
    assert exc.value.code == "budget_exhausted"
    assert exc.value.label == "doc-D"
    assert fake.calls == []
    assert budget.used == 0 and budget.reserved == 0


def test_budget_exhaustion_on_schema_retry_keeps_first_attempt_billed():
    cfg = ProviderConfig(max_output_tokens={**ProviderConfig().max_output_tokens, CallRole.EXTRACT: 1000})
    budget = RunBudget(limit=1000 + 200)
    # first attempt actually uses 1100 tokens, leaving too little for the retry reservation
    provider, fake, _, _ = make([completion({"value": "x"}, prompt=1000, out=100)], config=cfg)
    with pytest.raises(BudgetExhausted):
        run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))
    assert len(fake.calls) == 1
    assert budget.used == 1100


def test_budget_exhaustion_mid_run_gives_partial_result():
    """Five documents extracted in order against a budget that fits about three."""
    cfg = ProviderConfig(max_output_tokens={**ProviderConfig().max_output_tokens, CallRole.EXTRACT: 1000})
    per_call_actual = 1500
    budget = RunBudget(limit=3 * per_call_actual + 1000)
    script = [completion(GOOD, prompt=1400, out=100) for _ in range(5)]
    provider, fake, _, _ = make(script, config=cfg)

    async def extract_all():
        out = {}
        for doc in ["A", "B", "C", "D", "E"]:
            try:
                r = await provider.complete_json(
                    role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget, label=doc
                )
                out[doc] = r.data
            except BudgetExhausted as e:
                out[doc] = e.code
        return out

    out = run(extract_all())
    assert [out[d] for d in "ABC"] == [GOOD, GOOD, GOOD]
    assert out["D"] == "budget_exhausted" and out["E"] == "budget_exhausted"
    assert len(fake.calls) == 3
    assert budget.exhausted
    assert budget.used == 3 * per_call_actual


def test_context_window_checked_before_sending():
    cfg = ProviderConfig(context_windows={ProviderConfig().models[CallRole.EXTRACT]: 9000})
    provider, fake, _, budget = make([completion(GOOD)], config=cfg)
    big = [Message.user("x" * 40_000)]
    with pytest.raises(ContextOverflow) as exc:
        run(provider.complete_json(role=CallRole.EXTRACT, messages=big, schema=SCHEMA, budget=budget))
    assert exc.value.limit == 9000
    assert fake.calls == [] and budget.used == 0


def test_estimate_input_tokens_counts_text_images_and_schema():
    provider, _, _, _ = make([])
    base = provider.estimate_input_tokens([Message.user("hello")])
    with_img = provider.estimate_input_tokens([Message.user([TextPart("hello"), ImagePart(b"x")])])
    with_schema = provider.estimate_input_tokens([Message.user("hello")], schema=SCHEMA)
    assert with_img - base >= provider.config.image_token_estimate
    assert with_schema > base


# ---------------------------------------------------------------- transient errors and timeouts


def test_rate_limit_retries_with_backoff_then_succeeds():
    provider, fake, sleeps, budget = make([rate_limit(), server_error(), completion(GOOD)])
    res = run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))
    assert res.data == GOOD
    assert sleeps.delays == [1.0, 2.0]
    assert len(fake.calls) == 3
    assert [r.outcome for r in budget.records] == ["rate_limit", "server_error", "ok"]
    assert budget.reserved == 0


def test_rate_limit_exhausts_retries():
    provider, fake, sleeps, budget = make([rate_limit()] * 4)
    with pytest.raises(ProviderUnavailable) as exc:
        run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))
    assert sleeps.delays == [1.0, 2.0, 4.0]
    assert len(fake.calls) == 4
    assert exc.value.attempts == 4
    assert budget.used == 0 and budget.reserved == 0


def test_connection_error_is_transient():
    provider, _, sleeps, budget = make([openai.APIConnectionError(request=_REQ), completion(GOOD)])
    run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))
    assert sleeps.delays == [1.0]


def test_auth_error_is_not_retried():
    provider, fake, sleeps, budget = make([auth_error()])
    with pytest.raises(ProviderRequestError) as exc:
        run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))
    assert exc.value.status == 401
    assert len(fake.calls) == 1 and sleeps.delays == []


def test_sdk_timeout_retries_then_raises_and_charges_estimate():
    provider, fake, sleeps, budget = make([openai.APITimeoutError(request=_REQ)] * 4)
    with pytest.raises(ProviderTimeout) as exc:
        run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))
    assert exc.value.code == "timeout"
    assert len(fake.calls) == 4 and sleeps.delays == [1.0, 2.0, 4.0]
    # a timed-out request may still have been processed: input estimate is charged, flagged as estimated
    assert all(r.outcome == "timeout" and r.estimated for r in budget.records)
    assert budget.used > 0 and budget.reserved == 0


def test_hung_call_is_cut_by_wall_clock_timeout():
    async def hang(_):
        await asyncio.sleep(10)

    cfg = ProviderConfig(timeouts_s={**ProviderConfig().timeouts_s, CallRole.EXTRACT: 0.05}, retry_delays_s=(0.0,))
    provider, fake, _, budget = make([hang, completion(GOOD)], config=cfg)
    res = run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget))
    assert res.data == GOOD
    assert len(fake.calls) == 2
    assert budget.records[0].outcome == "timeout"


def test_concurrency_is_capped():
    state = {"now": 0, "peak": 0}

    async def slow(_):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        return completion(GOOD)

    provider, _, _, budget = make([slow] * 10)

    async def go():
        await asyncio.gather(
            *[
                provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=budget)
                for _ in range(10)
            ]
        )

    run(go())
    assert state["peak"] == provider.config.max_concurrency == 4


# ---------------------------------------------------------------- key handling (R3)


def test_from_env_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingApiKey):
        OpenAIProvider.from_env()


def test_key_never_appears_in_repr_or_logs(monkeypatch, caplog):
    secret = "sk-test-DO-NOT-LEAK-123"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    provider = OpenAIProvider.from_env()
    assert secret not in repr(provider) and secret not in str(provider)

    fake = FakeClient([rate_limit(), completion(GOOD)])
    provider._client = fake
    provider._sleep = Sleeps()
    caplog.set_level(logging.DEBUG, logger="brokerdocs.providers")
    run(provider.complete_json(role=CallRole.EXTRACT, messages=msgs(), schema=SCHEMA, budget=RunBudget()))
    assert caplog.records, "provider should log each call"
    assert secret not in caplog.text
    assert "page 1 text" not in caplog.text  # document content is not logged either
