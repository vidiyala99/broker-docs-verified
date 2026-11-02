"""A FakeProvider responder that behaves like a careful model (no network).

extract  reads the data block, finds "Label: value" lines for each field label
         and returns them with their [page N] tag and the line as the quote.
vision   returns the text registered for that page number in `ocr_pages`.
compose  cites the first cited fact in the FACTS block with its exact value.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable

from backend.db import COMPARE_QUOTES
from backend.providers import CallRole, FakeProvider, ProviderRequest
from backend.providers.types import TextPart

LABELS = {f["key"]: f["label"] for f in COMPARE_QUOTES["fact_schema"]}


def _user_text(req: ProviderRequest) -> str:
    out = []
    for m in req.messages:
        if m.role == "user":
            out.extend(p.text for p in m.parts() if isinstance(p, TextPart))
    return "\n".join(out)


def extraction_reply(text: str, options: int = 1, option: str | None = None) -> dict:
    block = text[text.index("<<<DOCUMENT"): text.rindex(">>>")]
    page = 0
    found: dict[str, dict] = {}
    for line in block.splitlines():
        m = re.match(r"\[page (\d+)\]", line.strip())
        if m:
            page = int(m.group(1))
            continue
        for key, label in LABELS.items():
            mm = re.match(rf"\s*{re.escape(label)}:\s*(.+?)\s*$", line)
            if mm and key not in found:
                found[key] = {"field": key, "value": mm.group(1), "page": page, "quote": line.strip()}
    return {"facts": list(found.values()), "options_detected": options, "option_compared": option}


def compose_reply(text: str) -> dict:
    block = text[text.index("<<<FACTS"):]
    payload = json.loads(block[block.index("\n") + 1: block.rindex("FACTS")])
    facts = payload["facts"]
    if not facts:
        return {"sections": [{"section_id": "executive_summary", "paragraphs": [
            {"segments": [{"text": "No values were verified.", "passage_ids": []}]}]}]}
    f = facts[0]
    return {"sections": [{"section_id": "executive_summary", "paragraphs": [{"segments": [
        {"text": f"{f['document']} states {f['label']} {f['value']}.", "passage_ids": [f["passage_id"]]}]}]}]}


def smart_responder(ocr_pages: dict[int, str] | None = None) -> Callable[[ProviderRequest], object]:
    ocr_pages = ocr_pages or {}

    def respond(req: ProviderRequest):
        text = _user_text(req)
        if req.role is CallRole.EXTRACT:
            return extraction_reply(text)
        if req.role is CallRole.VISION:
            n = int(re.search(r"Page (\d+)\.", text).group(1))
            return ocr_pages.get(n, "")
        if req.role is CallRole.COMPOSE:
            return compose_reply(text)
        raise AssertionError(f"unexpected role {req.role}")

    return respond


class GatedProvider(FakeProvider):
    """Blocks every call of `gate_role` until `gate` is set (for cancel tests)."""

    def __init__(self, responder, gate_role: CallRole = CallRole.EXTRACT, **kw):
        super().__init__(responder, **kw)
        self.gate = asyncio.Event()
        self.gate_role = gate_role
        self.waiting = 0

    async def _send(self, request):
        if request.role is self.gate_role:
            self.waiting += 1
            await self.gate.wait()
        return await super()._send(request)
