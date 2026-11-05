"""Record every model reply of a live eval run, and replay them offline.

A live run's calls run concurrently (windows, documents), so call order is not stable. Replies are
keyed by a fingerprint of the request instead: call role + every message's role, text and the
sha256 of each image. Identical requests keep a list of replies, served in recorded order.

Recording wraps the real provider's ``_send`` (one HTTP attempt): transient errors raise and are
not stored; a schema retry carries the failed reply in its conversation, so it gets its own key.

Test-only: nothing here is imported by the backend.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

FORMAT = "reply_recorder/1"

# Prompts wrap data in a random per-call boundary (``secrets.token_hex(8).upper()`` after a fixed tag, the
# prompt-injection fence in extract/pdf_rows/sheets/compose/chat). Blank it so the key is stable across runs.
_BOUNDARY = re.compile(r"\b(DOCUMENT|SHEET|FACTS|BRIEF|CONTEXT|OUTLINE) [0-9A-F]{16}\b")


def _stable(text: str) -> str:
    return _BOUNDARY.sub(r"\1 <boundary>", text)


def request_key(request) -> str:
    """Stable fingerprint of one provider request (role, messages with boundaries blanked, image bytes)."""
    h = hashlib.sha256()
    h.update(request.role.value.encode())
    for m in request.messages:
        h.update(b"\x00msg\x00" + m.role.encode())
        for part in m.parts():
            if hasattr(part, "text"):
                h.update(b"\x00text\x00" + _stable(part.text).encode("utf-8"))
            else:
                h.update(b"\x00image\x00" + hashlib.sha256(part.data).hexdigest().encode())
    return h.hexdigest()


# A compose request lists facts with database passage ids, assigned in the order documents finish (concurrent), so
# two runs of the same case can number the same passages differently. Such a request also gets a canonical key, with
# each id replaced by its order of first appearance; replay falls back to it and renumbers the recorded reply's
# passage_ids (slice 6, Compare Quotes replay). Recordings made before this have no canonical key: exact keys only.
_PASSAGE_ID = re.compile(r'"passage_id":\s*(\d+)')


def _passage_order(request) -> list[int]:
    order: list[int] = []
    for m in request.messages:
        for part in m.parts():
            for n in _PASSAGE_ID.findall(getattr(part, "text", "") or ""):
                if int(n) not in order:
                    order.append(int(n))
    return order


def canonical_key(request) -> str | None:
    """request_key with passage ids replaced by their order of first appearance; None without passage ids."""
    order = _passage_order(request)
    if not order:
        return None
    rank = {pid: i for i, pid in enumerate(order)}
    h = hashlib.sha256(b"canonical/1")
    h.update(request.role.value.encode())
    for m in request.messages:
        h.update(b"\x00msg\x00" + m.role.encode())
        for part in m.parts():
            if hasattr(part, "text"):
                text = _PASSAGE_ID.sub(lambda mo: f'"passage_id": <p{rank[int(mo.group(1))]}>', _stable(part.text))
                h.update(b"\x00text\x00" + text.encode("utf-8"))
            else:
                h.update(b"\x00image\x00" + hashlib.sha256(part.data).hexdigest().encode())
    return h.hexdigest()


def _renumber(content, recorded_order: list[int], current_order: list[int]):
    """The reply with every passage id in a passage_ids list moved from the recorded run's numbering to this run's."""
    as_text = isinstance(content, str)
    try:
        data = json.loads(content) if as_text else copy.deepcopy(content)
    except ValueError:
        return content
    mapping = {old: current_order[i] for i, old in enumerate(recorded_order) if i < len(current_order)}

    def walk(x):
        if isinstance(x, dict):
            return {k: ([mapping.get(v, v) for v in val] if k == "passage_ids" and isinstance(val, list)
                        else walk(val)) for k, val in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x

    data = walk(data)
    return json.dumps(data) if as_text else data


def _reply_dict(raw) -> dict[str, Any]:
    return {"content": raw.content, "refusal": raw.refusal, "finish_reason": raw.finish_reason,
            "prompt_tokens": raw.prompt_tokens, "completion_tokens": raw.completion_tokens,
            "cached_tokens": raw.cached_tokens, "reasoning_tokens": raw.reasoning_tokens}


def record_into(provider, store: dict[str, list[dict]]):
    """Make ``provider`` append each reply it receives to ``store[request_key]``; returns the provider."""
    send = provider._send

    async def _send(request):
        raw = await send(request)
        entry = _reply_dict(raw)
        canon = canonical_key(request)
        if canon is not None:
            entry["canonical_key"], entry["passage_order"] = canon, _passage_order(request)
        store.setdefault(request_key(request), []).append(entry)
        return raw

    provider._send = _send
    return provider


def recording_provider(store: dict[str, list[dict]]):
    """An OpenAIProvider (from the environment) that records every reply into ``store``."""
    from backend.providers import OpenAIProvider

    return record_into(OpenAIProvider.from_env(), store)


def close_provider(prov) -> None:
    """Close the provider's HTTP client after ``asyncio.run`` has returned (best effort)."""
    import asyncio

    close = getattr(getattr(prov, "_client", None), "close", None)
    if close is None:
        return
    try:
        asyncio.run(close())
    except Exception:  # the client's loop is gone; nothing left to release that matters here
        pass


def replay_provider(replies: dict[str, list[dict]]):
    """A FakeProvider answering each request with its recorded reply. An unrecorded request fails the
    test, naming the role, so a prompt change shows up as a clear miss rather than a wrong answer."""
    from backend.providers import FakeProvider
    from backend.providers.fake import FakeReply

    queues = {k: list(v) for k, v in copy.deepcopy(replies).items()}
    canonical: dict[str, list[dict]] = defaultdict(list)
    for v in queues.values():
        for r in v:
            if r.get("canonical_key"):
                canonical[r["canonical_key"]].append(r)
    served: dict[str, int] = defaultdict(int)

    def respond(request):
        key = request_key(request)
        queue = queues.get(key)
        renumber = False
        if not queue:
            canon = canonical_key(request)
            queue = canonical.get(canon) if canon else None
            key, renumber = (canon, True) if queue else (key, False)
        if not queue:
            raise AssertionError(f"no recorded reply for a {request.role.value} request ({key[:12]}); "
                                 "the prompt, the page render or the window plan changed since recording")
        i = min(served[key], len(queue) - 1)  # identical requests beyond the recorded count reuse the last
        served[key] += 1
        r = queue[i]
        content = _renumber(r["content"], r["passage_order"], _passage_order(request)) if renumber else r["content"]
        return FakeReply(content=content, refusal=r["refusal"], finish_reason=r["finish_reason"] or "stop",
                         prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"],
                         cached_tokens=r.get("cached_tokens", 0), reasoning_tokens=r.get("reasoning_tokens", 0))

    return FakeProvider(respond)


def write_recording(path: Path, cases: dict[str, dict[str, list[dict]]], meta: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"format": FORMAT, **meta, "cases": {cid: {"replies": r} for cid, r in sorted(cases.items())}}
    path.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path
