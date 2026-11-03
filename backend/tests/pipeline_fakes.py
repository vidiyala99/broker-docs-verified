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
from backend.schemas import CERTIFICATE_FIELDS, POLICY_CHECK_FIELDS, scalar_fields

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


# --------------------------------------------------------------------- Policy Check (slice 3)

POLICY_LABELS = {f.key: f.label for f in scalar_fields(POLICY_CHECK_FIELDS)}
_FORM_LINE = re.compile(r"^\s*([A-Z]{2,3} \d{2} \d{2}) (\d{2} \d{2})\b")


def policy_extraction_reply(text: str) -> dict:
    """The combined pass (D13): "Label: value" lines for the policy-check scalar fields, plus a
    forms array from lines that open with a form number and edition ("HO 00 03 05 11 ...")."""
    block = text[text.index("<<<DOCUMENT"): text.rindex(">>>")]
    page = 0
    found: dict[str, dict] = {}
    forms: list[dict] = []
    for line in block.splitlines():
        m = re.match(r"\[page (\d+)\]", line.strip())
        if m:
            page = int(m.group(1))
            continue
        for key, label in POLICY_LABELS.items():
            mm = re.match(rf"\s*{re.escape(label)}:\s*(.+?)\s*$", line)
            if mm and key not in found:
                found[key] = {"field": key, "value": mm.group(1), "page": page, "quote": line.strip()}
        fm = _FORM_LINE.match(line)
        if fm:
            forms.append({"number": fm.group(1), "edition": fm.group(2), "title": None, "page": page,
                          "quote": line.strip()})
    return {"facts": list(found.values()), "forms": forms, "options_detected": 1, "option_compared": None}


def policy_responder(tamper: Callable[[str, dict], dict] | None = None) -> Callable[[ProviderRequest], object]:
    """Extraction only: a Policy Check run never asks the model to compose (D14).
    `tamper(document text, reply)` may change a reply (for unverifiable values)."""

    def respond(req: ProviderRequest):
        text = _user_text(req)
        if req.role is CallRole.EXTRACT:
            reply = policy_extraction_reply(text)
            return tamper(text, reply) if tamper else reply
        if req.role is CallRole.VISION:
            return ""
        raise AssertionError(f"policy check made an unexpected {req.role} call")

    return respond


# --------------------------------------------------------------------- Certificates (slice 4)

CERT_LABELS = {f.key: f.label for f in scalar_fields(CERTIFICATE_FIELDS)}
_CERT_FORM = re.compile(r"^\s*Form ([A-Z]{2} ?\d{2} ?\d{2,4})(?: (\d{2} \d{2}))?\s*$")
_CERT_PARTS = {"Policy Number": "policy_number", "Additional Insured": "named_party", "Waiver Party": "named_party",
               "Applies": "condition"}


def cert_extraction_reply(text: str) -> dict:
    """The certificate combined pass: "Label: value" lines for the cert: scalar fields (outside an
    endorsement block), plus an endorsements array. An endorsement block opens with a line
    "Form CG 20 10 04 13"; its next line is its printed title (type); inside it "Policy Number:",
    "Additional Insured:" / "Waiver Party:" and "Applies:" lines are its parts."""
    block = text[text.index("<<<DOCUMENT"): text.rindex(">>>")]
    page = 0
    found: dict[str, dict] = {}
    ends: list[dict] = []
    current: dict | None = None
    expect_title = False
    for line in block.splitlines():
        m = re.match(r"\[page (\d+)\]", line.strip())
        if m:
            page, current, expect_title = int(m.group(1)), None, False
            continue
        fm = _CERT_FORM.match(line)
        if fm:
            current = {"form_number": fm.group(1), "edition": fm.group(2), "page": page, "quote": line.strip(),
                       **{p: {"value": None, "page": None, "quote": None}
                          for p in ("type", "policy_number", "named_party", "condition")}}
            ends.append(current)
            expect_title = True
            continue
        if current is not None and expect_title and line.strip():
            current["type"] = {"value": line.strip(), "page": page, "quote": line.strip()}
            expect_title = False
            continue
        if current is not None:
            for label, part in _CERT_PARTS.items():
                mm = re.match(rf"\s*{re.escape(label)}:\s*(.+?)\s*$", line)
                if mm:
                    current[part] = {"value": mm.group(1), "page": page, "quote": line.strip()}
            continue
        for key, label in CERT_LABELS.items():
            mm = re.match(rf"\s*{re.escape(label)}:\s*(.+?)\s*$", line)
            if mm and key not in found:
                found[key] = {"field": key, "value": mm.group(1), "page": page, "quote": line.strip()}
    return {"facts": list(found.values()), "endorsements": ends, "options_detected": 1, "option_compared": None}


def cert_responder(ocr_pages: dict[int, str] | None = None) -> Callable[[ProviderRequest], object]:
    """Extraction (and OCR) only: a certificate run never asks the model to compose (C5)."""
    ocr_pages = ocr_pages or {}

    def respond(req: ProviderRequest):
        text = _user_text(req)
        if req.role is CallRole.EXTRACT:
            return cert_extraction_reply(text)
        if req.role is CallRole.VISION:
            n = int(re.search(r"Page (\d+)\.", text).group(1))
            return ocr_pages.get(n, "")
        raise AssertionError(f"certificate run made an unexpected {req.role} call")

    return respond


def any_responder(ocr_pages: dict[int, str] | None = None) -> Callable[[ProviderRequest], object]:
    """Compare Quotes, Policy Check and certificate runs in one app (the extraction reply follows the
    workflow's field list in the prompt)."""
    smart, policy, cert = smart_responder(ocr_pages), policy_responder(), cert_responder(ocr_pages)

    def respond(req: ProviderRequest):
        text = _user_text(req)
        if req.role is CallRole.EXTRACT and "- cert:" in text:
            return cert(req)
        if req.role is CallRole.EXTRACT and "- coverage_a_limit" in text:
            return policy(req)
        return smart(req)

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
