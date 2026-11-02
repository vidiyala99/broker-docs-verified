"""Step 1, parse: PyMuPDF text and word boxes per page, OCR triggers, caps.

Caps (D3, all configurable through ParseLimits): 80 pages and 15 MB per
document, 8 documents per run. Over a cap raises ParseError whose message
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
    max_pages: int = 80
    max_bytes: int = 15 * _MB
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
