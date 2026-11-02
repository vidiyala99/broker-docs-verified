"""OpenAIProvider: Chat Completions with strict structured outputs.

Security (R3): the key comes from the OPENAI_API_KEY environment variable
only, is handed straight to the SDK client, and is never stored on this
object, logged, or included in repr. Requests never carry tools, functions or
web search, and are sent with store=False.

SDK retries are disabled (max_retries=0) so the bounded 1/2/4 second policy
in Provider is the only retry layer (M8).
"""

from __future__ import annotations

import base64
import os
from typing import Any

import openai

from .base import Provider, Sleep
from .config import ProviderConfig
from .errors import MissingApiKey, ProviderRequestError, TransientError
from .types import ImagePart, Message, ProviderRequest, RawResponse, TextPart


class OpenAIProvider(Provider):
    def __init__(self, config: ProviderConfig | None = None, *, client: Any, sleep: Sleep | None = None):
        """`client` is an openai.AsyncOpenAI (or a test double with .chat.completions.create)."""
        super().__init__(config, sleep=sleep)
        self._client = client

    @classmethod
    def from_env(cls, config: ProviderConfig | None = None, *, sleep: Sleep | None = None) -> "OpenAIProvider":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise MissingApiKey("OPENAI_API_KEY is not set")
        client = openai.AsyncOpenAI(api_key=key, max_retries=0)
        return cls(config or ProviderConfig.from_env(), client=client, sleep=sleep)

    def __repr__(self) -> str:
        return f"OpenAIProvider(models={ {r.value: m for r, m in self.config.models.items()} })"

    __str__ = __repr__

    async def _send(self, request: ProviderRequest) -> RawResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [_to_openai(m) for m in request.messages],
            "max_completion_tokens": request.max_output_tokens,
            "store": False,
            "timeout": request.timeout_s,
        }
        if request.schema is not None:
            kwargs["response_format"] = request.schema.response_format()
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except openai.APITimeoutError as e:  # subclass of APIConnectionError, check first
            raise TransientError("timeout", type(e).__name__) from None
        except openai.APIConnectionError as e:
            raise TransientError("connection", type(e).__name__) from None
        except openai.RateLimitError:
            raise TransientError("rate_limit") from None
        except openai.InternalServerError as e:
            raise TransientError("server_error", str(e.status_code)) from None
        except openai.APIStatusError as e:
            if e.status_code in (408, 409) or e.status_code >= 500:
                raise TransientError("server_error", str(e.status_code)) from None
            raise ProviderRequestError(f"OpenAI rejected the request ({e.status_code})", status=e.status_code,
                                       label=request.label) from None

        choice = resp.choices[0]
        msg = choice.message
        usage = resp.usage
        prompt_details = getattr(usage, "prompt_tokens_details", None) if usage else None
        completion_details = getattr(usage, "completion_tokens_details", None) if usage else None
        return RawResponse(
            content=msg.content,
            refusal=getattr(msg, "refusal", None),
            finish_reason=choice.finish_reason,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=(getattr(prompt_details, "cached_tokens", None) or 0),
            reasoning_tokens=(getattr(completion_details, "reasoning_tokens", None) or 0),
            model=resp.model,
        )


def _to_openai(m: Message) -> dict[str, Any]:
    if isinstance(m.content, str):
        return {"role": m.role, "content": m.content}
    parts: list[dict[str, Any]] = []
    for p in m.content:
        if isinstance(p, TextPart):
            parts.append({"type": "text", "text": p.text})
        elif isinstance(p, ImagePart):
            url = f"data:{p.mime};base64,{base64.b64encode(p.data).decode('ascii')}"
            parts.append({"type": "image_url", "image_url": {"url": url, "detail": p.detail}})
    return {"role": m.role, "content": parts}
