"""The Provider interface and the vendor-neutral call loop.

Decision order for one logical call (complete_json / complete_text):

    for each schema attempt (1 + schema_retries):
        context check   estimated input + reserved output <= model window, else ContextOverflow
        for each transport attempt (1 + len(retry_delays_s)):
            reserve     budget.reserve(estimate + reserved output), else BudgetExhausted (nothing sent)
            send        under the concurrency semaphore, with a wall-clock timeout
              transient (429, 5xx, connection, timeout) -> settle, record, back off, retry
              response                                   -> settle actual usage, record
        refusal / content_filter -> RefusalError (no retry)
        finish_reason length     -> OutputTruncated (no retry)
        JSON parse + schema check fails -> append the bad output and the errors, retry
    schema still invalid -> SchemaValidationError

Subclasses implement only `_send(request) -> RawResponse`, raising
TransientError for retryable failures and ProviderError for the rest.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Sequence

from .budget import RunBudget, estimate_tokens
from .config import ProviderConfig
from .errors import (
    ContextOverflow,
    OutputTruncated,
    ProviderTimeout,
    ProviderUnavailable,
    RefusalError,
    SchemaValidationError,
    TransientError,
)
from .schema import StructuredSchema
from .types import (
    CallRecord,
    CallRole,
    CallUsage,
    ImagePart,
    Message,
    Outcome,
    ProviderRequest,
    RawResponse,
    StructuredResult,
    TextPart,
    TextResult,
)

log = logging.getLogger("brokerdocs.providers")


@dataclass
class _Attempt:
    raw: RawResponse
    kind: Outcome
    data: Any = None
    errors: list[str] = field(default_factory=list)
    usage: CallUsage = field(default_factory=CallUsage)


Sleep = Callable[[float], Awaitable[Any]]


class Provider(abc.ABC):
    """Wraps one model vendor. Pipeline code depends only on this class."""

    def __init__(self, config: ProviderConfig | None = None, *, sleep: Sleep | None = None):
        self.config = config or ProviderConfig()
        self._sleep: Sleep = sleep or asyncio.sleep
        self._semaphores: dict[int, asyncio.Semaphore] = {}

    # ------------------------------------------------------------ public API

    async def complete_json(
        self,
        *,
        role: CallRole,
        messages: Sequence[Message],
        schema: StructuredSchema,
        budget: RunBudget,
        label: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> StructuredResult:
        """Structured call with strict schema. Raises a ProviderError subclass on failure."""
        model = model or self.config.model_for(role)
        convo = list(messages)
        usage = CallUsage()
        attempts = 0
        errors: list[str] = []
        for _ in range(1 + self.config.schema_retries):
            attempts += 1
            outcome = await self._call(role, model, convo, schema, budget, label, max_output_tokens, timeout_s)
            usage = usage + outcome.usage
            self._raise_for_terminal(outcome, label, usage)
            if not outcome.errors:
                return StructuredResult(data=outcome.data, usage=usage, attempts=attempts, model=model,
                                        parsed=schema.parse(outcome.data))
            errors = outcome.errors
            log.warning("schema-invalid output label=%s attempt=%d errors=%d", label, attempts, len(errors))
            convo = [
                *convo,
                Message.assistant(outcome.raw.content or ""),
                Message.user(
                    "Your previous output failed schema validation:\n- "
                    + "\n- ".join(errors[:20])
                    + "\nReturn only JSON that matches the schema exactly."
                ),
            ]
        raise SchemaValidationError(errors, attempts, label=label, usage=usage)

    async def complete_text(
        self,
        *,
        role: CallRole,
        messages: Sequence[Message],
        budget: RunBudget,
        label: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> TextResult:
        """Free-text call (chat answers, OCR transcription, suggestions)."""
        model = model or self.config.model_for(role)
        outcome = await self._call(role, model, list(messages), None, budget, label, max_output_tokens, timeout_s)
        self._raise_for_terminal(outcome, label, outcome.usage)
        return TextResult(text=outcome.raw.content or "", usage=outcome.usage, attempts=1, model=model)

    def estimate_input_tokens(self, messages: Sequence[Message], schema: StructuredSchema | None = None) -> int:
        cfg = self.config
        total = 0
        for m in messages:
            total += cfg.message_overhead_tokens
            for part in m.parts():
                if isinstance(part, TextPart):
                    total += estimate_tokens(part.text, cfg.chars_per_token)
                elif isinstance(part, ImagePart):
                    total += cfg.image_token_estimate
        if schema is not None:
            total += estimate_tokens(json.dumps(schema.json_schema), cfg.chars_per_token)
        return total

    def reserved_tokens(self, role: CallRole, messages: Sequence[Message], schema: StructuredSchema | None = None,
                        max_output_tokens: int | None = None) -> int:
        """What one attempt of this call would reserve from the budget. Lets the runner pre-check D3."""
        return self.estimate_input_tokens(messages, schema) + (max_output_tokens or self.config.max_output_tokens[role])

    # ------------------------------------------------------------ backend hook

    @abc.abstractmethod
    async def _send(self, request: ProviderRequest) -> RawResponse:
        """One HTTP attempt. Raise TransientError for retryable failures."""

    # ------------------------------------------------------------ internals

    async def _call(self, role, model, convo, schema, budget, label, max_output_tokens, timeout_s):
        cfg = self.config
        max_out = max_output_tokens or cfg.max_output_tokens[role]
        timeout = timeout_s or cfg.timeouts_s[role]
        est_in = self.estimate_input_tokens(convo, schema)
        window = cfg.context_window(model)
        if est_in + max_out > window:
            raise ContextOverflow(estimated=est_in + max_out, limit=window, model=model, label=label)
        request = ProviderRequest(role, model, tuple(convo), max_out, timeout, schema, label)

        delays = cfg.retry_delays_s
        last_kind = "unknown"
        for attempt in range(1, len(delays) + 2):
            reservation = budget.reserve(est_in + max_out, label=label)
            started = time.monotonic()
            try:
                async with self._semaphore():
                    raw = await asyncio.wait_for(self._send(request), timeout=timeout)
            except (TransientError, asyncio.TimeoutError) as e:
                kind = e.kind if isinstance(e, TransientError) else "timeout"
                last_kind = kind
                # A timed-out request may still have been processed and billed: charge the
                # input estimate so the cap stays safe. Other transient failures charge 0.
                charged = est_in if kind == "timeout" else 0
                budget.settle(reservation, charged)
                budget.record(CallRecord(role, model, charged, 0, 0, 0,
                                         self._cost(model, charged, 0, 0), attempt, kind,
                                         time.monotonic() - started, label, estimated=charged > 0))
                log.warning("model call transient failure kind=%s role=%s model=%s label=%s attempt=%d",
                            kind, role.value, model, label, attempt)
                if attempt <= len(delays):
                    await self._sleep(delays[attempt - 1])
                    continue
                if kind == "timeout":
                    raise ProviderTimeout(attempt, label=label) from None
                raise ProviderUnavailable(kind, attempt, label=label) from None
            except BaseException:
                budget.release(reservation)
                raise

            budget.settle(reservation, raw.prompt_tokens + raw.completion_tokens)
            cost = self._cost(model, raw.prompt_tokens, raw.completion_tokens, raw.cached_tokens)
            result = self._classify(raw, schema)
            budget.record(CallRecord(role, model, raw.prompt_tokens, raw.completion_tokens, raw.cached_tokens,
                                     raw.reasoning_tokens, cost, attempt, result.kind,
                                     time.monotonic() - started, label))
            log.info("model call role=%s model=%s label=%s attempt=%d outcome=%s prompt=%d completion=%d "
                     "cost_usd=%.6f", role.value, model, label, attempt, result.kind, raw.prompt_tokens,
                     raw.completion_tokens, cost)
            result.usage = CallUsage(raw.prompt_tokens, raw.completion_tokens, raw.cached_tokens,
                                     raw.reasoning_tokens, cost)
            return result
        raise AssertionError(f"unreachable ({last_kind})")  # pragma: no cover

    @staticmethod
    def _classify(raw: RawResponse, schema: StructuredSchema | None) -> "_Attempt":
        if raw.refusal or raw.finish_reason == "content_filter":
            return _Attempt(raw, "refusal")
        if raw.finish_reason == "length":
            return _Attempt(raw, "truncated")
        if schema is None:
            return _Attempt(raw, "ok")
        if not raw.content:
            return _Attempt(raw, "schema_invalid", errors=["empty output"])
        try:
            data = json.loads(raw.content)
        except json.JSONDecodeError as e:
            return _Attempt(raw, "schema_invalid", errors=[f"not valid JSON: {e.msg} at {e.pos}"])
        errors = schema.validate(data)
        return _Attempt(raw, "schema_invalid" if errors else "ok", data=data, errors=errors)

    @staticmethod
    def _raise_for_terminal(outcome: "_Attempt", label, usage) -> None:
        if outcome.kind == "refusal":
            raise RefusalError(outcome.raw.refusal or "content filtered", label=label, usage=usage)
        if outcome.kind == "truncated":
            raise OutputTruncated("output hit the max_completion_tokens limit", label=label, usage=usage)

    def _cost(self, model: str, prompt: int, completion: int, cached: int) -> float:
        return self.config.estimate_cost(model, prompt_tokens=prompt, completion_tokens=completion, cached_tokens=cached)

    def _semaphore(self) -> asyncio.Semaphore:
        # One semaphore per running event loop (tests run several loops in one process).
        loop = asyncio.get_running_loop()
        sem = self._semaphores.get(id(loop))
        if sem is None:
            sem = self._semaphores[id(loop)] = asyncio.Semaphore(self.config.max_concurrency)
        return sem
