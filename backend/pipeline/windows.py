r"""Long documents (T14): extract in overlapping page windows, verify, merge.

Decision order for one document:

    pages <= window size (15)? ──yes──► one strict extract call, exactly as
        │no                              before T14 (pipeline.extract), then
        │                                verify.verify_facts: unchanged path
        ▼
    plan windows: 15 pages each, 2 pages of overlap (step 13), absolute page
    numbers; the last window ends on the last page
        │
    budget pre-check (precheck_tokens): estimated input of every window,
    plus the full reserved output of the windows that can be in flight at
    once (max_concurrency) and the expected output of the rest, must fit
    what the document may still spend (D3: checked before any model call),
    else BudgetExhausted and nothing is sent
        │
    extract every window with the same strict call (R3 wording, a fresh
    random data-block boundary per window, the [page N] tags are absolute
    document pages); windows run concurrently through the Provider's own
    semaphore (4 calls, M6), and at most that many windows of one document
    hold a budget reservation at a time; each finished window reports
    progress (done, total) to the caller on the event loop
        │ any window fails ──► sibling windows are cancelled and awaited,
        │                      the first error propagates (the document's
        │                      column fails, as for a single call)
        ▼
    verify every non-empty candidate against the FULL document
    (verify.verify_fact; the window only supplies the page hint: the model's
    page when it lies inside the window, else the window's first page)
        │
    merge.merge_document: one fact per field (see pipeline.merge)

Options (D8) across windows: the largest options_detected wins, labelled by
the first window that reported it.

Nothing here touches the database; the runner stores the result.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.pipeline import extract, merge, verify
from backend.providers import (
    BudgetExhausted,
    CallRole,
    CallUsage,
    DocumentBudget,
    DocumentBudgetExhausted,
    Provider,
)
from backend.schemas import FactField

ProgressFn = Callable[[int, int], None]
"""(windows finished, windows total), called on the event loop."""


@dataclass(frozen=True)
class WindowConfig:
    size: int = 15  # pages per window; documents this long or shorter take one call
    overlap: int = 2  # pages shared by consecutive windows

    def __post_init__(self):
        if self.size < 1:
            raise ValueError("window size must be at least 1")
        if not 0 <= self.overlap < self.size:
            raise ValueError("overlap must be at least 0 and smaller than the window size")

    @property
    def step(self) -> int:
        return self.size - self.overlap


DEFAULT_WINDOWS = WindowConfig()


@dataclass(frozen=True)
class Window:
    index: int  # 1-based
    first: int  # first page, 1-based, inclusive
    last: int  # last page, inclusive

    @property
    def pages(self) -> range:
        return range(self.first, self.last + 1)

    def __contains__(self, page_no: object) -> bool:
        return isinstance(page_no, int) and self.first <= page_no <= self.last


def needs_windows(page_count: int, config: WindowConfig = DEFAULT_WINDOWS) -> bool:
    return page_count > config.size


def plan_windows(page_count: int, config: WindowConfig = DEFAULT_WINDOWS) -> list[Window]:
    """Windows covering pages 1..page_count. One window when the document fits in one."""
    if page_count <= 0:
        return []
    if page_count <= config.size:
        return [Window(1, 1, page_count)]
    count = math.ceil((page_count - config.size) / config.step) + 1
    out = []
    for i in range(count):
        first = 1 + i * config.step
        out.append(Window(i + 1, first, min(page_count, first + config.size - 1)))
    return out


def select_windows(windows: list[Window], pages: Sequence[extract.TextPage]) -> list[Window]:
    """EXTENSION POINT (off): a page-scoring pass that skips windows unlikely to hold a fact.

    T14 ships with every window extracted. A scorer plugged in here must keep
    the window order and must never drop the first window.
    """
    return windows


@dataclass(frozen=True)
class WindowResult:
    window: Window
    result: extract.ExtractionResult


@dataclass(frozen=True)
class WindowedExtraction:
    """All windows of one long document, before verification."""

    fields: tuple[FactField, ...]
    windows: tuple[WindowResult, ...]
    option_count: int
    option_label: str | None
    usage: CallUsage
    model: str

    def candidates(self) -> list[verify.CandidateFact]:
        """Every non-empty fact of every window, with its page hint, in window order."""
        out = []
        for wr in self.windows:
            for f in wr.result.facts:
                if f.value is None:
                    continue
                hint = f.page if f.page in wr.window else wr.window.first
                out.append(verify.CandidateFact(f.field, f.value, hint, f.quote))
        return out


# --------------------------------------------------------------------- extraction


def _window_pages(pages: Sequence[extract.TextPage], window: Window) -> list[extract.TextPage]:
    return [p for p in pages if int(p.page_no) in window]


def precheck_tokens(provider: Provider, fields, pages, windows, *, document_name: str, instructions: str = "",
                    schema=None) -> int:
    """Tokens the windows of one document are planned to need, checked before any call (D3).

    Every window's estimated input (its own system prompt, field list and
    schema, counted once per call because each call sends them), plus output:
    the last `max_concurrency` windows can each hold a full reserved output at
    the peak, the earlier ones have settled to about their expected output.
    """
    schema = schema or extract.build_extraction_schema(fields)
    cfg = provider.config
    est = 0
    for w in windows:
        msgs = extract.build_extraction_messages(fields, _window_pages(pages, w), document_name=document_name,
                                                 instructions=instructions)
        est += provider.estimate_input_tokens(msgs, schema)
    max_out = cfg.max_output_tokens[CallRole.EXTRACT]
    expected = min(max_out, cfg.expected_output_tokens.get(CallRole.EXTRACT, max_out))
    in_flight = min(len(windows), max(1, cfg.max_concurrency))
    return est + (len(windows) - in_flight) * expected + in_flight * max_out


def _precheck(provider: Provider, budget, fields, pages, windows, *, document_name, instructions, label):
    schema = extract.build_extraction_schema(fields)
    need = precheck_tokens(provider, fields, pages, windows, document_name=document_name,
                           instructions=instructions, schema=schema)
    if isinstance(budget, DocumentBudget):
        own = max(0, budget.limit - budget.used - budget.reserved)
        if need > own:
            budget.exhausted = True
            raise DocumentBudgetExhausted(requested=need, remaining=own, label=label)
        run_budget = budget.parent
    else:
        run_budget = budget
    if need > run_budget.remaining:
        budget.exhausted = run_budget.exhausted = True
        raise BudgetExhausted(requested=need, remaining=run_budget.remaining, label=label)


async def extract_windows(
    provider: Provider,
    budget,
    fields: Sequence[FactField],
    pages: Sequence[extract.TextPage],
    *,
    document_name: str,
    instructions: str = "",
    label: str | None = None,
    config: WindowConfig = DEFAULT_WINDOWS,
    on_progress: ProgressFn | None = None,
) -> WindowedExtraction:
    pages = sorted(pages, key=lambda p: int(p.page_no))
    label = label or f"extract:{document_name}"
    windows = select_windows(plan_windows(len(pages), config), pages)
    _precheck(provider, budget, fields, pages, windows, document_name=document_name,
              instructions=instructions, label=label)
    results: list[WindowResult | None] = [None] * len(windows)
    gate = asyncio.Semaphore(max(1, provider.config.max_concurrency))
    done = 0

    async def one(i: int, w: Window) -> None:
        nonlocal done
        async with gate:
            res = await extract.extract_document(
                provider, budget, fields, _window_pages(pages, w), document_name=document_name,
                instructions=instructions, label=f"{label}:w{w.index}/{len(windows)}")
        results[i] = WindowResult(w, res)
        done += 1
        if on_progress is not None:
            on_progress(done, len(windows))

    try:
        async with asyncio.TaskGroup() as tg:
            for i, w in enumerate(windows):
                tg.create_task(one(i, w), name=f"{label}:w{w.index}")
    except BaseExceptionGroup as group:
        raise _first_error(group) from None

    finished = [r for r in results if r is not None]
    usage = CallUsage()
    for r in finished:
        usage = usage + r.result.usage
    count, option = 1, None
    for r in finished:
        if r.result.option_count > count:
            count, option = r.result.option_count, r.result.option_label
    return WindowedExtraction(tuple(fields), tuple(finished), count, option, usage,
                              finished[0].result.model if finished else "")


def _first_error(group: BaseExceptionGroup) -> BaseException:
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            return _first_error(exc)
        return exc
    return group  # pragma: no cover - a group always holds at least one exception


async def extract_document(
    provider: Provider,
    budget,
    fields: Sequence[FactField],
    pages: Sequence[extract.TextPage],
    *,
    document_name: str,
    instructions: str = "",
    label: str | None = None,
    config: WindowConfig = DEFAULT_WINDOWS,
    on_progress: ProgressFn | None = None,
) -> extract.ExtractionResult | WindowedExtraction:
    """Single strict call for a short document (unchanged), windows for a long one."""
    if not needs_windows(len(pages), config):
        return await extract.extract_document(provider, budget, fields, pages, document_name=document_name,
                                              instructions=instructions, label=label)
    return await extract_windows(provider, budget, fields, pages, document_name=document_name,
                                 instructions=instructions, label=label, config=config,
                                 on_progress=on_progress)


# --------------------------------------------------------------------- verify + merge


def verify_document(
    extraction: extract.ExtractionResult | WindowedExtraction,
    pages: Sequence[verify.PageLike],
    config: verify.VerifyConfig = verify.DEFAULT_CONFIG,
) -> list[verify.VerifyResult]:
    """Verify (and for windows, merge) one document's facts. Pure computation: thread-pool safe.

    Short documents go through verify.verify_facts exactly as before T14.
    Long documents: every window candidate is verified against all pages,
    then merged to one fact per field (pipeline.merge).
    """
    if isinstance(extraction, extract.ExtractionResult):
        return verify.verify_facts(extraction.facts, pages, config)
    index = verify.build_index(pages)
    seen: dict[verify.CandidateFact, verify.VerifyResult] = {}
    checked = []
    for cand in extraction.candidates():
        if cand not in seen:  # overlapping windows often return the same fact twice
            seen[cand] = verify.verify_fact(cand, index, config)
        checked.append(seen[cand])
    # page text for the endorsement rule (merge.AMEND_RE): text layer plus model-OCR text
    page_text = {int(p.page_no): f"{getattr(p, 'text', '') or ''}\n{getattr(p, 'ocr_text', None) or ''}"
                 for p in pages}
    return list(merge.merge_document(extraction.fields, checked, page_text))


__all__ = ["DEFAULT_WINDOWS", "Window", "WindowConfig", "WindowResult", "WindowedExtraction", "extract_document",
           "extract_windows", "needs_windows", "plan_windows", "precheck_tokens", "select_windows",
           "verify_document"]
