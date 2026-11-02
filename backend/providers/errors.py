"""Provider error hierarchy.

Every error carries a stable `code` so the pipeline (T5, T6) can map it to an
evidence state or a user-facing reason without string matching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import CallUsage


class ProviderError(Exception):
    code = "provider_error"

    def __init__(self, message: str, *, label: str | None = None, usage: "CallUsage | None" = None):
        super().__init__(message)
        self.label = label
        self.usage = usage


class SchemaBuildError(ValueError):
    """A schema cannot be expressed in OpenAI strict mode. A programming error, raised at build time."""


class MissingApiKey(ProviderError):
    code = "missing_key"


class RefusalError(ProviderError):
    """The model refused (refusal field set, or content_filter finish). Not retried."""

    code = "refusal"

    def __init__(self, refusal: str, **kw):
        super().__init__(f"model refused: {refusal}", **kw)
        self.refusal = refusal


class SchemaValidationError(ProviderError):
    """Output failed local schema validation on every allowed attempt."""

    code = "schema_invalid"

    def __init__(self, errors: list[str], attempts: int, **kw):
        super().__init__(f"output failed schema validation after {attempts} attempt(s): {errors[:3]}", **kw)
        self.errors = errors
        self.attempts = attempts


class OutputTruncated(ProviderError):
    """finish_reason == length: the output hit max_completion_tokens. Not retried."""

    code = "truncated"


class BudgetExhausted(ProviderError):
    """The run budget cannot cover this call's reservation. Nothing was sent."""

    code = "budget_exhausted"

    def __init__(self, requested: int, remaining: int, **kw):
        super().__init__(f"budget exhausted: call needs {requested} tokens, {remaining} remain", **kw)
        self.requested = requested
        self.remaining = remaining


class DocumentBudgetExhausted(BudgetExhausted):
    """One document's own ceiling (T14, DocumentBudget) cannot cover this call. Nothing was sent."""

    code = "document_budget_exhausted"


class ContextOverflow(ProviderError):
    """Estimated input plus reserved output exceeds the model context window. Nothing was sent."""

    code = "context_overflow"

    def __init__(self, estimated: int, limit: int, model: str, **kw):
        super().__init__(f"call needs about {estimated} tokens, {model} context is {limit}", **kw)
        self.estimated = estimated
        self.limit = limit
        self.model = model


class ProviderTimeout(ProviderError):
    code = "timeout"

    def __init__(self, attempts: int, **kw):
        super().__init__(f"model call timed out after {attempts} attempt(s)", **kw)
        self.attempts = attempts


class ProviderUnavailable(ProviderError):
    """Rate limit, 5xx or connection failure that outlasted the bounded retries."""

    code = "unavailable"

    def __init__(self, kind: str, attempts: int, **kw):
        super().__init__(f"provider unavailable ({kind}) after {attempts} attempt(s)", **kw)
        self.kind = kind
        self.attempts = attempts


class ProviderRequestError(ProviderError):
    """Non-retryable request error (400, 401, 403, 404, 422). Usually configuration."""

    code = "request_error"

    def __init__(self, message: str, status: int | None = None, **kw):
        super().__init__(message, **kw)
        self.status = status


class TransientError(Exception):
    """Raised by a backend's `_send` for retryable failures. Internal to the provider layer."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind  # rate_limit | server_error | connection | timeout
