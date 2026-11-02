"""Model provider layer (spec section 3, task T2).

Pipeline code imports from here only:

    from providers import OpenAIProvider, RunBudget, CallRole, Message, StructuredSchema
"""

from .base import Provider
from .budget import DocumentBudget, Reservation, RunBudget, estimate_tokens
from .config import ModelPrice, ProviderConfig
from .errors import (
    BudgetExhausted,
    ContextOverflow,
    DocumentBudgetExhausted,
    MissingApiKey,
    OutputTruncated,
    ProviderError,
    ProviderRequestError,
    ProviderTimeout,
    ProviderUnavailable,
    RefusalError,
    SchemaBuildError,
    SchemaValidationError,
)
from .fake import FakeProvider
from .openai_provider import OpenAIProvider
from .schema import StructuredSchema, fact_extraction_schema, strict_json_schema, validate_instance
from .types import (
    CallRecord,
    CallRole,
    CallUsage,
    ImagePart,
    Message,
    ProviderRequest,
    RawResponse,
    StructuredResult,
    TextPart,
    TextResult,
    UsageTotals,
)

__all__ = [
    "BudgetExhausted", "CallRecord", "CallRole", "CallUsage", "ContextOverflow", "DocumentBudget",
    "DocumentBudgetExhausted", "FakeProvider", "ImagePart",
    "Message", "MissingApiKey", "ModelPrice", "OpenAIProvider", "OutputTruncated", "Provider", "ProviderConfig",
    "ProviderError", "ProviderRequest", "ProviderRequestError", "ProviderTimeout", "ProviderUnavailable",
    "RawResponse", "RefusalError", "Reservation", "RunBudget", "SchemaBuildError", "SchemaValidationError",
    "StructuredResult", "StructuredSchema", "TextPart", "TextResult", "UsageTotals", "estimate_tokens",
    "fact_extraction_schema", "strict_json_schema", "validate_instance",
]
