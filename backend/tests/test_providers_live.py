"""Live smoke test against the real OpenAI API.

Runs only with `pytest -m live` AND OPENAI_API_KEY set in the environment.
The key is read from the environment only; this file never reads .env.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from providers import CallRole, Message, OpenAIProvider, RunBudget, StructuredSchema

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _live_only(request):
    if "live" not in (request.config.getoption("-m") or ""):
        pytest.skip("live test: run with pytest -m live")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("live test: OPENAI_API_KEY not set")


def test_live_structured_extraction_is_cheap_and_strict():
    schema = StructuredSchema.from_dict(
        "live_fact",
        {
            "type": "object",
            "properties": {"named_insured": {"type": "string"}, "page": {"type": "integer"}},
            "required": ["named_insured", "page"],
        },
    )
    provider = OpenAIProvider.from_env()
    budget = RunBudget(limit=20_000)
    messages = [
        Message.system("Extract the named insured and the page it appears on from the document data block."),
        Message.user("<document>\n[page 1]\nNamed Insured: Bluefin Logistics LLC\n</document>"),
    ]
    res = asyncio.run(
        provider.complete_json(role=CallRole.EXTRACT, messages=messages, schema=schema, budget=budget, label="live")
    )
    assert "Bluefin" in res.data["named_insured"]
    assert res.data["page"] == 1
    t = budget.totals()
    assert t.calls >= 1 and t.prompt_tokens > 0 and t.cost_usd > 0
