"""FakeProvider: a scripted Provider for pipeline and API tests (spec section 7).

It goes through the real Provider call loop, so budget reservation, usage
records, refusal handling, schema retry and transient retry behave exactly as
with OpenAIProvider. Only `_send` is scripted.

Script items, consumed one per HTTP attempt:
    dict                 -> JSON content
    str                  -> raw content (text calls, or invalid JSON)
    FakeReply(...)       -> full control of content, finish reason and usage
    FakeRefusal("why")   -> refusal
    FakeTransient(kind)  -> rate_limit | server_error | connection | timeout
    Exception instance   -> raised as is
Or pass a callable `responder(request) -> item` instead of a list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Union

from .base import Provider, Sleep
from .budget import estimate_tokens
from .config import ProviderConfig
from .errors import TransientError
from .types import ProviderRequest, RawResponse


@dataclass(frozen=True)
class FakeReply:
    content: str | None = None
    refusal: str | None = None
    finish_reason: str = "stop"
    prompt_tokens: int | None = None  # None -> estimated from the request
    completion_tokens: int | None = None  # None -> estimated from the content
    cached_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class FakeRefusal:
    refusal: str = "I can't help with that."


@dataclass(frozen=True)
class FakeTransient:
    kind: str = "rate_limit"


ScriptItem = Union[dict, str, FakeReply, FakeRefusal, FakeTransient, BaseException]


async def _no_sleep(_: float) -> None:
    return None


class FakeProvider(Provider):
    def __init__(
        self,
        script: Iterable[ScriptItem] | Callable[[ProviderRequest], ScriptItem],
        config: ProviderConfig | None = None,
        *,
        sleep: Sleep | None = None,
    ):
        super().__init__(config, sleep=sleep or _no_sleep)
        self._responder = script if callable(script) else None
        self._script: list[Any] = [] if callable(script) else list(script)
        self.requests: list[ProviderRequest] = []

    async def _send(self, request: ProviderRequest) -> RawResponse:
        self.requests.append(request)
        if self._responder is not None:
            item = self._responder(request)
        elif self._script:
            item = self._script.pop(0)
        else:
            raise AssertionError("FakeProvider script exhausted")
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, FakeTransient):
            raise TransientError(item.kind)
        if isinstance(item, FakeRefusal):
            item = FakeReply(content=None, refusal=item.refusal)
        if isinstance(item, dict):
            item = FakeReply(content=json.dumps(item))
        if isinstance(item, str):
            item = FakeReply(content=item)
        prompt = item.prompt_tokens if item.prompt_tokens is not None else self.estimate_input_tokens(
            request.messages, request.schema)
        completion = item.completion_tokens if item.completion_tokens is not None else estimate_tokens(
            item.content or item.refusal or "")
        return RawResponse(item.content, item.refusal, item.finish_reason, prompt, completion,
                           item.cached_tokens, item.reasoning_tokens, request.model)
