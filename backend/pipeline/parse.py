"""Step 1, parse: PyMuPDF text and word boxes per page, OCR triggers, caps.

Caps (D3, raised by T14; all configurable through ParseLimits): 300 pages
and 50 MB per document, 8 documents per run. Documents over 15 pages are
extracted in page windows (pipeline.windows). Over a cap raises ParseError whose message
names the limit, so the user sees why.

OCR triggers (M3, M8): a page is sent to the vision pass when it has fewer
than 25 non-whitespace characters, or when images cover 40 percent or more of
its area and it has fewer than 600 characters. The vision pass itself is the
caller's `ocr` function (the provider lives in backend/providers.py); this
module only renders the page and stores what comes back in `ocr_text`. The
digital text layer and its word boxes are kept as found, so verify can still
check a quote independently against them on a mixed page (D5).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import NamedTuple

import fitz

OcrFn = Callable[[bytes, int], str]
"""(PNG bytes of the rendered page, 1-based page number) -> page text."""

_MB = 1024 * 1024


@dataclass(frozen=True)
class ParseLimits:
    max_pages: int = 300  # T14 (D3 had 80)
    max_bytes: int = 50 * _MB  # T14 (D3 had 15 MB)
    max_documents_per_run: int = 8
    ocr_min_chars: int = 25  # fewer non-whitespace chars than this -> OCR
    image_heavy_ratio: float = 0.40
    image_heavy_max_chars: int = 600
    ocr_dpi: int = 150


DEFAULT_LIMITS = ParseLimits()


class ParseErrorCode(StrEnum):
    EMPTY = "empty"
    NOT_PDF = "not_pdf"
    CORRUPT = "corrupt"
    ENCRYPTED = "encrypted"
    TOO_LARGE = "too_large"
    TOO_MANY_PAGES = "too_many_pages"
    TOO_MANY_DOCUMENTS = "too_many_documents"


class ParseError(Exception):
    """A document cannot be used. `message` is safe to show to the user."""

    def __init__(self, code: ParseErrorCode, message: str, limit: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.limit = limit


class Word(NamedTuple):
    """One PyMuPDF word box. Serializes to a JSON list in the same order."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int
    line: int
    word: int


@dataclass
class ParsedPage:
    page_no: int  # 1-based
    text: str  # digital text layer ("" when the page has none)
    words: list[Word] | None  # digital word boxes; None = boxes unavailable
    width: float
    height: float
    char_count: int  # non-whitespace characters in the digital layer
    image_ratio: float  # share of the page area covered by images, 0..1
    needs_ocr: bool
    ocr_reason: str | None  # "sparse_text" | "image_heavy" | None
    ocr_text: str | None = None  # text from the vision pass, once it ran

    @property
    def ocr(self) -> bool:
        """True once the vision pass produced text for this page."""
        return self.ocr_text is not None

    @property
    def extraction_text(self) -> str:
        """Text to send to extraction: OCR text when it is non-empty, else the digital layer."""
        return self.ocr_text if self.ocr_text else self.text


@dataclass
class ParsedDocument:
    page_count: int
    sha256: str
    size_bytes: int
    pages: list[ParsedPage] = field(default_factory=list)

    @property
    def ocr_pending(self) -> list[ParsedPage]:
        return [p for p in self.pages if p.needs_ocr and not p.ocr]


# --------------------------------------------------------------------- cap checks


def _mb(n: int) -> str:
    return f"{n / _MB:g} MB"


def is_pdf_bytes(data: bytes) -> bool:
    """PDF magic bytes. Readers accept the header anywhere in the first 1024 bytes."""
    return b"%PDF-" in data[:1024]


def check_size(size_bytes: int, limits: ParseLimits = DEFAULT_LIMITS) -> None:
    if size_bytes > limits.max_bytes:
        raise ParseError(
            ParseErrorCode.TOO_LARGE,
            f"This file is {_mb(size_bytes)}; the limit is {_mb(limits.max_bytes)} per document.",
            limit=limits.max_bytes,
        )


def check_page_count(pages: int, limits: ParseLimits = DEFAULT_LIMITS) -> None:
    if pages > limits.max_pages:
        raise ParseError(
            ParseErrorCode.TOO_MANY_PAGES,
            f"This file has {pages} pages; the limit is {limits.max_pages} pages per document.",
            limit=limits.max_pages,
        )


def check_document_count(count: int, limits: ParseLimits = DEFAULT_LIMITS) -> None:
    if count > limits.max_documents_per_run:
        raise ParseError(
            ParseErrorCode.TOO_MANY_DOCUMENTS,
            f"{count} files selected; the limit is {limits.max_documents_per_run} documents per run.",
            limit=limits.max_documents_per_run,
        )


# --------------------------------------------------------------------- parse


def _open(data: bytes) -> fitz.Document:
    if not data:
        raise ParseError(ParseErrorCode.EMPTY, "This file is empty.")
    if not is_pdf_bytes(data):
        raise ParseError(ParseErrorCode.NOT_PDF, "Not a PDF. Only PDF files can be compared.")
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except (fitz.FileDataError, fitz.EmptyFileError, RuntimeError, ValueError) as exc:
        raise ParseError(
            ParseErrorCode.CORRUPT, "This PDF is damaged and could not be read."
        ) from exc
    if doc.needs_pass:
        doc.close()
        raise ParseError(
            ParseErrorCode.ENCRYPTED,
            "This PDF is password-protected. Remove the password and upload it again.",
        )
    if doc.page_count == 0:
        doc.close()
        raise ParseError(ParseErrorCode.EMPTY, "This PDF has no pages.")
    return doc


def _non_ws(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _union_area(rects: list[fitz.Rect]) -> float:
    """Exact area of a union of axis-aligned rectangles (coordinate compression)."""
    rects = [r for r in rects if not r.is_empty]
    if not rects:
        return 0.0
    xs = sorted({r.x0 for r in rects} | {r.x1 for r in rects})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        spans = sorted((r.y0, r.y1) for r in rects if r.x0 <= left and r.x1 >= right)
        covered, cur_start, cur_end = 0.0, None, None
        for y0, y1 in spans:
            if cur_end is None or y0 > cur_end:
                if cur_end is not None:
                    covered += cur_end - cur_start
                cur_start, cur_end = y0, y1
            else:
                cur_end = max(cur_end, y1)
        if cur_end is not None:
            covered += cur_end - cur_start
        area += covered * (right - left)
    return area


def _image_ratio(page: fitz.Page) -> float:
    page_rect = page.rect
    page_area = page_rect.width * page_rect.height
    if page_area <= 0:
        return 0.0
    rects = []
    for info in page.get_image_info():
        rect = fitz.Rect(info["bbox"]) & page_rect  # clip to the page
        rects.append(rect)
    return min(1.0, _union_area(rects) / page_area)


def _parse_page(page: fitz.Page, page_no: int, limits: ParseLimits) -> ParsedPage:
    text = page.get_text("text")
    words = [
        Word(round(w[0], 2), round(w[1], 2), round(w[2], 2), round(w[3], 2), w[4], w[5], w[6], w[7])
        for w in page.get_text("words")
    ]
    chars = _non_ws(text)
    ratio = round(_image_ratio(page), 4)
    reason = None
    if chars < limits.ocr_min_chars:
        reason = "sparse_text"
    elif ratio >= limits.image_heavy_ratio and chars < limits.image_heavy_max_chars:
        reason = "image_heavy"
    return ParsedPage(
        page_no=page_no,
        text=text,
        words=words,
        width=page.rect.width,
        height=page.rect.height,
        char_count=chars,
        image_ratio=ratio,
        needs_ocr=reason is not None,
        ocr_reason=reason,
    )


def parse_pdf(
    data: bytes, limits: ParseLimits = DEFAULT_LIMITS, ocr: OcrFn | None = None
) -> ParsedDocument:
    """Parse a PDF into pages. Raises ParseError for any unusable document.

    With `ocr`, pages that trip an OCR trigger are rendered and read at once.
    Without it, those pages keep `needs_ocr=True` and the caller may run
    `apply_ocr` later (for example inside the run's budget and semaphore).
    """
    if data:
        check_size(len(data), limits)
    doc = _open(data)
    try:
        check_page_count(doc.page_count, limits)
        parsed = ParsedDocument(
            page_count=doc.page_count,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            pages=[_parse_page(doc[i], i + 1, limits) for i in range(doc.page_count)],
        )
    finally:
        doc.close()
    if ocr is not None:
        apply_ocr(parsed, data, ocr, limits)
    return parsed


def render_page_png(data: bytes, page_no: int, dpi: int = DEFAULT_LIMITS.ocr_dpi) -> bytes:
    doc = _open(data)
    try:
        return doc[page_no - 1].get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()


def apply_ocr(
    parsed: ParsedDocument, data: bytes, ocr: OcrFn, limits: ParseLimits = DEFAULT_LIMITS
) -> ParsedDocument:
    """Run the vision pass on every page still waiting for it (mutates `parsed`).

    Raises ParseError(EMPTY) when no page has any text after OCR. Errors from
    `ocr` propagate so the caller can fail that document's column.
    """
    pending = parsed.ocr_pending
    if pending:
        doc = _open(data)
        try:
            for page in pending:
                png = doc[page.page_no - 1].get_pixmap(dpi=limits.ocr_dpi).tobytes("png")
                page.ocr_text = (ocr(png, page.page_no) or "").strip()
        finally:
            doc.close()
    if all(_non_ws(p.extraction_text) == 0 for p in parsed.pages):
        raise ParseError(ParseErrorCode.EMPTY, "No readable text was found in this PDF.")
    return parsed


# --------------------------------------------------------------------- reading order for extraction

_VALUE_MAX_TOKENS = 6  # "$5.466 per $1,000 of gross sales"
_COLUMN_X_TOLERANCE = 1.0  # points: one column's lines start at one x


@dataclass
class _Line:
    text: str
    x0: float
    x1: float
    y0: float
    y1: float

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 0.1)

    @property
    def value_like(self) -> bool:
        return any(c.isdigit() for c in self.text) and len(self.text.split()) <= _VALUE_MAX_TOKENS


def _text_lines(words) -> list[_Line]:
    """Text-layer lines (block, line) in text-layer order, from word boxes (Word or JSON list)."""
    lines: dict[tuple[int, int], list] = {}
    for w in words:
        lines.setdefault((int(w[5]), int(w[6])), []).append(w)
    out = []
    for ws in lines.values():
        out.append(_Line(" ".join(str(w[4]) for w in ws), min(float(w[0]) for w in ws),
                         max(float(w[2]) for w in ws), min(float(w[1]) for w in ws),
                         max(float(w[3]) for w in ws)))
    return out


def _columns(lines: list[_Line]) -> list[list[int]]:
    """Runs of consecutive lines that start at one x and go down the page: one printed column."""
    runs: list[list[int]] = []
    for i, ln in enumerate(lines):
        if runs:
            prev = lines[runs[-1][-1]]
            if abs(ln.x0 - lines[runs[-1][0]].x0) <= _COLUMN_X_TOLERANCE and ln.yc > prev.yc:
                runs[-1].append(i)
                continue
        runs.append([i])
    return runs


def _aligned(a: _Line, b: _Line) -> bool:
    return abs(a.yc - b.yc) <= min(a.height, b.height) / 2


def _pair_window(values: list[int], other: list[int], lines: list[_Line]) -> list[int] | None:
    """The lines of `other` that sit on the printed rows of `values`, one each, in order."""
    first = lines[values[0]]
    for j, k in enumerate(other):
        if _aligned(first, lines[k]):
            window = other[j:j + len(values)]
            if len(window) == len(values) and all(_aligned(lines[v], lines[k2]) for v, k2 in zip(values, window)):
                return window
            return None
    return None


def reading_text(text: str, words) -> str:
    """The page text sent to extraction: the text layer, except that a two-column table whose
    value column the text layer lists apart from its labels is read row by row, as printed.

    PyMuPDF's text layer follows the PDF's blocks. A schedule drawn as a column of labels and
    a column of values (one block per cell) then reads every value, then every label, and the
    model has to pair them by position (live eval 2026-11-02, pc_homeowners renewal: label-less
    and reversed quotes, and one deductible paired with its neighbour's value). Here such a
    pair of columns becomes one line per printed row, left to right ("Coverage A (Dwelling)
    $360,000"), the same order as verify's visual row stream, so a label-and-value quote
    verifies. Every token is kept; only line order and line breaks change.

    A column is paired only when it is a value column (two or more lines, each with a digit
    and at most six words), the column next to it in text-layer order has a line on each of
    its rows, the two do not overlap across the page, and the other column is not a value
    column itself. Anything else (side-by-side label-over-value fields, two columns of prose,
    a column of words such as "Included") is left as the text layer has it, and so is a page
    without word boxes."""
    if not words:
        return text
    lines = _text_lines(words)
    runs = _columns(lines)
    merged: dict[int, str] = {}  # first line index of a pair -> its row lines
    skip: set[int] = set()
    for r, run in enumerate(runs):
        if len(run) < 2 or any(i in skip for i in run) or not all(lines[i].value_like for i in run):
            continue
        for n in (r + 1, r - 1):
            if not 0 <= n < len(runs) or any(i in skip for i in runs[n]):
                continue
            window = _pair_window(run, runs[n], lines)
            if window is None or all(lines[i].value_like for i in window):
                continue
            vx0, vx1 = min(lines[i].x0 for i in run), max(lines[i].x1 for i in run)
            ox0, ox1 = min(lines[i].x0 for i in window), max(lines[i].x1 for i in window)
            if not (vx1 < ox0 or ox1 < vx0):
                continue
            rows = []
            for v, o in zip(run, window):
                pair = sorted((lines[v], lines[o]), key=lambda ln: ln.x0)
                rows.append(f"{pair[0].text} {pair[1].text}")
            merged[min(run[0], window[0])] = "\n".join(rows)
            skip.update(run)
            skip.update(window)
            break
    if not merged:
        return text
    out = []
    for i, ln in enumerate(lines):
        if i in merged:
            out.append(merged[i])
        elif i not in skip:
            out.append(ln.text)
    return "\n".join(out) + "\n"
