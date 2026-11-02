"""Provider configuration. Model names, prices and limits live here, not in code.

UNCONFIRMED values are marked. The spec (section 9, open question 2) leaves the
model choice to plan time; the defaults below are names present in the
installed openai SDK's ChatModel list (openai 3.42.1), not names confirmed
against the owner's account, and the prices are stand-ins. Override any of
them with the BROKERDOCS_* environment variables read by `from_env`, or by
passing a ProviderConfig.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from .types import CallRole


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input_per_mtok: float
    cached_input_per_mtok: float
    output_per_mtok: float


# UNCONFIRMED: model names exist in the SDK's ChatModel literal, availability on
# the owner's key was not checked (no key in this environment).
DEFAULT_MODELS: dict[CallRole, str] = {
    CallRole.EXTRACT: "gpt-5.4-mini",
    CallRole.VISION: "gpt-5.4-mini",
    CallRole.COMPOSE: "gpt-5.5",
    CallRole.CHAT: "gpt-5.5",
    CallRole.SUGGEST: "gpt-5.4-nano",
}

# Reserved output per call (M4). Reasoning tokens count as output, so these are generous.
DEFAULT_MAX_OUTPUT_TOKENS: dict[CallRole, int] = {
    CallRole.EXTRACT: 8_000,
    CallRole.VISION: 6_000,
    CallRole.COMPOSE: 16_000,
    CallRole.CHAT: 4_000,
    CallRole.SUGGEST: 1_000,
}

DEFAULT_TIMEOUTS_S: dict[CallRole, float] = {
    CallRole.EXTRACT: 120.0,
    CallRole.VISION: 120.0,
    CallRole.COMPOSE: 180.0,
    CallRole.CHAT: 90.0,
    CallRole.SUGGEST: 30.0,
}

# UNCONFIRMED stand-in prices, not read from a current price list. Replace before
# trusting cost figures. The fallback is deliberately high so unknown models
# over-report rather than under-report.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "gpt-5.4-mini": ModelPrice(0.25, 0.025, 2.00),
    "gpt-5.4-nano": ModelPrice(0.05, 0.005, 0.40),
    "gpt-5.5": ModelPrice(1.25, 0.125, 10.00),
}
FALLBACK_PRICE = ModelPrice(5.00, 0.50, 40.00)

# UNCONFIRMED: context windows not verified for these models. 128k is a
# conservative floor; slice 1 calls (one quote, about 10 pages) are far below it.
DEFAULT_CONTEXT_WINDOW = 128_000

RUN_TOKEN_BUDGET = 600_000  # D3
MAX_CONCURRENCY = 4  # M6
RETRY_DELAYS_S = (1.0, 2.0, 4.0)  # M8: 3 retries at 1/2/4 seconds
SCHEMA_RETRIES = 1  # spec section 6: one retry


@dataclass(frozen=True)
class ProviderConfig:
    models: dict[CallRole, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    max_output_tokens: dict[CallRole, int] = field(default_factory=lambda: dict(DEFAULT_MAX_OUTPUT_TOKENS))
    timeouts_s: dict[CallRole, float] = field(default_factory=lambda: dict(DEFAULT_TIMEOUTS_S))
    prices: dict[str, ModelPrice] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    fallback_price: ModelPrice = FALLBACK_PRICE
    context_windows: dict[str, int] = field(default_factory=dict)
    default_context_window: int = DEFAULT_CONTEXT_WINDOW
    run_token_budget: int = RUN_TOKEN_BUDGET
    max_concurrency: int = MAX_CONCURRENCY
    retry_delays_s: tuple[float, ...] = RETRY_DELAYS_S
    schema_retries: int = SCHEMA_RETRIES
    # Token estimation before a call (no tokenizer dependency). 3 chars per token
    # over-counts typical English (about 4), which is the safe side for a cap.
    chars_per_token: float = 3.0
    image_token_estimate: int = 1_600
    message_overhead_tokens: int = 4

    def model_for(self, role: CallRole) -> str:
        return self.models[role]

    def context_window(self, model: str) -> int:
        return self.context_windows.get(model, self.default_context_window)

    def price(self, model: str) -> ModelPrice:
        return self.prices.get(model, self.fallback_price)

    def estimate_cost(self, model: str, *, prompt_tokens: int, completion_tokens: int, cached_tokens: int) -> float:
        p = self.price(model)
        cached = min(cached_tokens, prompt_tokens)
        return (
            (prompt_tokens - cached) * p.input_per_mtok
            + cached * p.cached_input_per_mtok
            + completion_tokens * p.output_per_mtok
        ) / 1_000_000

    @classmethod
    def from_env(cls, env: "os._Environ[str] | dict[str, str] | None" = None) -> "ProviderConfig":
        """Defaults plus overrides: BROKERDOCS_MODEL_<ROLE>, BROKERDOCS_RUN_TOKEN_BUDGET, BROKERDOCS_MAX_CONCURRENCY."""
        env = os.environ if env is None else env
        cfg = cls()
        models = dict(cfg.models)
        for role in CallRole:
            value = env.get(f"BROKERDOCS_MODEL_{role.name}")
            if value:
                models[role] = value
        changes: dict = {"models": models}
        if env.get("BROKERDOCS_RUN_TOKEN_BUDGET"):
            changes["run_token_budget"] = int(env["BROKERDOCS_RUN_TOKEN_BUDGET"])
        if env.get("BROKERDOCS_MAX_CONCURRENCY"):
            changes["max_concurrency"] = int(env["BROKERDOCS_MAX_CONCURRENCY"])
        return replace(cfg, **changes)
