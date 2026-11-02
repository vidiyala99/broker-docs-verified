"""Value types shared by every Provider implementation."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Sequence, Union

if TYPE_CHECKING:
    from .schema import StructuredSchema


class CallRole(str, enum.Enum):
    """Which pipeline step a call serves. Selects model, output reservation and timeout."""

    EXTRACT = "extract"
    VISION = "vision"
    COMPOSE = "compose"
    CHAT = "chat"
    SUGGEST = "suggest"


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime: str = "image/png"
    detail: Literal["low", "high", "auto"] = "high"

    def __repr__(self) -> str:  # keep page bytes out of logs and tracebacks
        return f"ImagePart(mime={self.mime!r}, bytes={len(self.data)}, detail={self.detail!r})"


ContentPart = Union[TextPart, ImagePart]


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: Union[str, tuple[ContentPart, ...]]

    @classmethod
    def system(cls, text: str) -> "Message":
        return cls("system", text)

    @classmethod
    def user(cls, content: Union[str, Sequence[ContentPart]]) -> "Message":
        return cls("user", content if isinstance(content, str) else tuple(content))

    @classmethod
    def assistant(cls, text: str) -> "Message":
        return cls("assistant", text)

    def parts(self) -> tuple[ContentPart, ...]:
        return (TextPart(self.content),) if isinstance(self.content, str) else self.content


@dataclass(frozen=True)
class ProviderRequest:
    """What a backend's `_send` receives. One request is one HTTP attempt."""

    role: CallRole
    model: str
    messages: tuple[Message, ...]
    max_output_tokens: int
    timeout_s: float
    schema: "StructuredSchema | None" = None
    label: str | None = None


@dataclass(frozen=True)
class RawResponse:
    """What a backend's `_send` returns, normalized across vendors."""

    content: str | None
    refusal: str | None
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    model: str | None = None


@dataclass(frozen=True)
class CallUsage:
    """Tokens and estimated cost of one logical call (all attempts summed)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "CallUsage") -> "CallUsage":
        return CallUsage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.cached_tokens + other.cached_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
            self.cost_usd + other.cost_usd,
        )


Outcome = Literal[
    "ok", "refusal", "schema_invalid", "truncated", "rate_limit", "server_error", "connection", "timeout"
]


@dataclass(frozen=True)
class CallRecord:
    """One HTTP attempt, for the run's token and cost log (spec section 6)."""

    role: CallRole
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    cost_usd: float
    attempt: int
    outcome: Outcome
    latency_s: float
    label: str | None = None
    estimated: bool = False  # True when tokens are our estimate, not provider-reported


@dataclass(frozen=True)
class StructuredResult:
    data: dict[str, Any]
    usage: CallUsage
    attempts: int
    model: str
    parsed: Any = None  # pydantic instance when the schema came from a model


@dataclass(frozen=True)
class TextResult:
    text: str
    usage: CallUsage
    attempts: int
    model: str


@dataclass
class RoleTotals:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class UsageTotals(RoleTotals):
    by_role: dict[CallRole, RoleTotals] = field(default_factory=dict)
