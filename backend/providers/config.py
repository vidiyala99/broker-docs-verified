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
    CallRole.LOSS_ROWS: "gpt-5.4-mini",
    CallRole.LOSS_HEADERS: "gpt-5.4-mini",
}

# Reserved output per call (M4). Reasoning tokens count as output, so these are generous.
DEFAULT_MAX_OUTPUT_TOKENS: dict[CallRole, int] = {
    CallRole.EXTRACT: 8_000,
    CallRole.VISION: 6_000,
    CallRole.COMPOSE: 16_000,
    CallRole.CHAT: 4_000,
    CallRole.SUGGEST: 1_000,
    CallRole.LOSS_ROWS: 8_000,  # one 2-page window's rows (about 60); a cut-off reply is split (R2)
    CallRole.LOSS_HEADERS: 1_500,
}

DEFAULT_TIMEOUTS_S: dict[CallRole, float] = {
    CallRole.EXTRACT: 120.0,
    CallRole.VISION: 120.0,
    CallRole.COMPOSE: 180.0,
    CallRole.CHAT: 90.0,
    CallRole.SUGGEST: 30.0,
    CallRole.LOSS_ROWS: 120.0,
    CallRole.LOSS_HEADERS: 60.0,
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

# Typical output of one call, for planning many calls up front (the windows
# pre-check). Each call still reserves its full max_output_tokens when sent.
# A 20-field extract reply with quotes is about 1.5k visible tokens; 3k leaves
# room for reasoning tokens.
DEFAULT_EXPECTED_OUTPUT_TOKENS: dict[CallRole, int] = {
    CallRole.EXTRACT: 3_000,
    CallRole.VISION: 1_500,
    CallRole.COMPOSE: 8_000,
    CallRole.CHAT: 1_000,
    CallRole.SUGGEST: 300,
    CallRole.LOSS_ROWS: 3_000,
    CallRole.LOSS_HEADERS: 300,
}

RUN_TOKEN_BUDGET = 1_500_000  # T14 (D3 had 600k)
# Per-document ceiling inside the run budget. T14 set 400k; the first live
# eval showed a legitimate 300-page document cannot fit it. Arithmetic for the
# largest allowed document (300 pages, the parse cap) at the long_300 fixture's
# density (dense policy prose, 3.9k chars per page, max 4.2k):
#   23 windows of 15 pages (2 overlap) send 1.33M page chars plus 4.6k chars of
#   prompt and schema per window = 1.44M chars = 360k estimated input tokens
#   at 4 chars per token (about 299k actual at the measured 4.7)
#   + 19 windows x 3k expected output + 4 in-flight windows x 8k reserved
#   output = 89k
#   = 449k pre-check need (477k at 4.2k chars on every page).
# 500k covers that with headroom and still lets three such documents share one
# 1.5M run.
DOCUMENT_TOKEN_BUDGET = 500_000
# Slice 5a loss runs (L8, R2, O5). PROVISIONAL until the T7 fixture measurements set them [re-measure before the
# gate]. A loss-run PDF is read twice: the lr: fact pass (15-page windows) and the row pass (2-page windows, about
# one call per page). Estimate for the ~120-page fixture: 119 row windows x (about 2.5k input + 3k output) plus the
# fact pass (9 windows x about 18k) = about 820k; 1.2M per document leaves headroom, and a Retry after a budget stop
# gets a fresh allowance of this size for each document it reads (O5/D11).
LOSS_DOCUMENT_TOKEN_BUDGET = 1_200_000
LOSS_RUN_TOKEN_BUDGET = 4_000_000
MAX_CONCURRENCY = 4  # M6
RETRY_DELAYS_S = (1.0, 2.0, 4.0)  # M8: 3 retries at 1/2/4 seconds
SCHEMA_RETRIES = 1  # spec section 6: one retry


@dataclass(frozen=True)
class ProviderConfig:
    models: dict[CallRole, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    max_output_tokens: dict[CallRole, int] = field(default_factory=lambda: dict(DEFAULT_MAX_OUTPUT_TOKENS))
    expected_output_tokens: dict[CallRole, int] = field(
        default_factory=lambda: dict(DEFAULT_EXPECTED_OUTPUT_TOKENS))
    timeouts_s: dict[CallRole, float] = field(default_factory=lambda: dict(DEFAULT_TIMEOUTS_S))
    prices: dict[str, ModelPrice] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    fallback_price: ModelPrice = FALLBACK_PRICE
    context_windows: dict[str, int] = field(default_factory=dict)
    default_context_window: int = DEFAULT_CONTEXT_WINDOW
    run_token_budget: int = RUN_TOKEN_BUDGET
    document_token_budget: int = DOCUMENT_TOKEN_BUDGET
    loss_document_token_budget: int = LOSS_DOCUMENT_TOKEN_BUDGET
    loss_run_token_budget: int = LOSS_RUN_TOKEN_BUDGET
    max_concurrency: int = MAX_CONCURRENCY
    retry_delays_s: tuple[float, ...] = RETRY_DELAYS_S
    schema_retries: int = SCHEMA_RETRIES
    # Token estimation before a call (no tokenizer dependency). Calibrated on the
    # first live eval: the long_120 fixture's ten windows are 581k chars of
    # prompt and schema and were billed about 122k prompt tokens, so 4.7 chars
    # per token for dense policy prose. 4.0 over-counts that by about 18%, the
    # safety margin. The earlier 3.0 over-counted by 57% and refused a
    # 300-page document that fits (480k estimated, about 300k actual).
    chars_per_token: float = 4.0
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
        """Defaults plus overrides: BROKERDOCS_MODEL_<ROLE>, BROKERDOCS_RUN_TOKEN_BUDGET,
        BROKERDOCS_DOCUMENT_TOKEN_BUDGET, BROKERDOCS_MAX_CONCURRENCY."""
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
        if env.get("BROKERDOCS_DOCUMENT_TOKEN_BUDGET"):
            changes["document_token_budget"] = int(env["BROKERDOCS_DOCUMENT_TOKEN_BUDGET"])
        if env.get("BROKERDOCS_MAX_CONCURRENCY"):
            changes["max_concurrency"] = int(env["BROKERDOCS_MAX_CONCURRENCY"])
        return replace(cfg, **changes)
