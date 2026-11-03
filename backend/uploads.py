"""Upload checks and storage (T7, R3, D3).

Order for one uploaded file:

    read at most max_bytes + 1 bytes ── more ──► reject too_large (names the MB limit)
    empty ──► reject empty
    no %PDF- magic in the first 1024 bytes ──► reject not_pdf ("Not a PDF")
    open with PyMuPDF
        damaged ──► accept, flagged corrupt (the run fails only its column, spec section 6)
        password ──► accept, flagged encrypted (same)
        pages > max_pages ──► reject too_many_pages (names the page limit)
    store under files_dir()/<uuid4 hex>, written to a temp name then renamed

The client's filename is display text only: it is reduced to a base name,
stripped of control and path characters, and never used to build a path.

AMS exports (slice 3 T1, R1 D4 A) take a separate path, so the one above stays
PDF-only:

    name ends .json or .csv, and the content agrees (JSON opens with { or [) ──no──► not_ams
    ams.parse_ams: caps, screening, flat record ──bad──► reject (code names the limit)
    ams.render_pdf ─► inspect_pdf on the rendered bytes (page cap) ─► store the PDF under
    <uuid4 hex> (served, parsed and verified like any PDF) and the original under
    <uuid4 hex>.orig (kept, read by load_original)
"""

from __future__ import annotations

import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

import fitz

from backend import ams
from backend.pipeline import parse
from backend.pipeline.parse import ParseError, ParseErrorCode, ParseLimits
from backend.pipeline.runner import files_dir

_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
MAX_DISPLAY_NAME = 200


def safe_display_name(raw: str | None) -> str:
    """Base name of a client filename, safe to show and to put in a header."""
    name = unicodedata.normalize("NFKC", raw or "")
    name = re.split(r"[\\/]", name)[-1]
    name = _UNSAFE.sub("", name).strip().strip(".").strip()
    if len(name) > MAX_DISPLAY_NAME:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: MAX_DISPLAY_NAME - len(ext) - 1] + dot + ext) if dot and len(ext) <= 10 \
            else name[:MAX_DISPLAY_NAME]
    return name or "document.pdf"


@dataclass(frozen=True)
class Inspection:
    page_count: int | None
    parse_error: str | None = None  # ParseErrorCode for an accepted but unreadable PDF
    parse_message: str | None = None


def inspect_pdf(data: bytes, limits: ParseLimits) -> Inspection:
    """Upload-time checks. Raises ParseError (message names the limit) for a rejection."""
    if not data:
        raise ParseError(ParseErrorCode.EMPTY, "This file is empty.")
    parse.check_size(len(data), limits)
    if not parse.is_pdf_bytes(data):
        raise ParseError(ParseErrorCode.NOT_PDF, "Not a PDF. Only PDF files can be compared.")
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return Inspection(None, str(ParseErrorCode.CORRUPT), "This PDF is damaged and could not be read.")
    try:
        if doc.needs_pass:
            return Inspection(None, str(ParseErrorCode.ENCRYPTED),
                              "This PDF is password-protected. Remove the password and upload it again.")
        pages = doc.page_count
    finally:
        doc.close()
    if pages == 0:
        raise ParseError(ParseErrorCode.EMPTY, "This PDF has no pages.")
    parse.check_page_count(pages, limits)
    return Inspection(pages)


@dataclass(frozen=True)
class PreparedAms:
    pdf: bytes  # rendered record: what is stored, served, parsed and verified
    original: bytes  # the uploaded file, kept unchanged
    page_count: int
    rendered: ams.RenderedAms
    facts: ams.MappedFacts


_AMS_EXTENSIONS: dict[str, ams.AmsFormat] = {".json": "json", ".csv": "csv"}


def prepare_ams(data: bytes, filename: str, limits: ParseLimits,
                ams_limits: ams.AmsLimits = ams.DEFAULT_AMS_LIMITS) -> PreparedAms:
    """Upload-time AMS checks and rendering. Raises AmsError or ParseError (message names the limit)."""
    ext = os.path.splitext(filename)[1].lower()
    fmt = _AMS_EXTENSIONS.get(ext)
    if fmt is None:
        raise ams.AmsError("not_ams", ams.NOT_AMS_MESSAGE)
    if not data:
        raise ams.AmsError("empty", "This file is empty.")
    if parse.is_pdf_bytes(data) or ams.sniff_format(data) != fmt:
        raise ams.AmsError("not_ams", ams.NOT_AMS_MESSAGE)
    rendered = ams.render_pdf(ams.parse_ams(data, ams_limits))
    info = inspect_pdf(rendered.pdf, limits)  # the rendered PDF meets the same caps as any PDF
    return PreparedAms(rendered.pdf, data, info.page_count or rendered.page_count, rendered,
                       ams.map_facts(rendered))


def new_stored_name() -> str:
    return uuid.uuid4().hex


_ORIGINAL_SUFFIX = ".orig"


def _write_atomic(target: Path, data: bytes) -> None:
    tmp = target.with_name(f"{target.name}.part")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)


def _target(stored_name: str, suffix: str = "") -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", stored_name or ""):
        raise ValueError("stored names are generated UUIDs")
    directory = files_dir().resolve()
    target = (directory / f"{stored_name}{suffix}").resolve()
    if target.parent != directory:  # pragma: no cover - guaranteed by the name check
        raise ValueError("stored file must live in the files folder")
    return target


def store_bytes(data: bytes, stored_name: str) -> Path:
    """Write under files_dir() with a generated name only (D4). Atomic: temp file then rename."""
    target = _target(stored_name)
    _write_atomic(target, data)
    return target


def store_original(data: bytes, stored_name: str) -> Path:
    """Keep an AMS export's uploaded file beside its rendered PDF (R1 D4 A)."""
    target = _target(stored_name, _ORIGINAL_SUFFIX)
    _write_atomic(target, data)
    return target


def load_original(stored_name: str) -> bytes:
    """The kept original of an AMS document (feed it to ams.facts_from_original)."""
    return _target(stored_name, _ORIGINAL_SUFFIX).read_bytes()


def remove_stored(stored_name: str) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", stored_name or ""):
        for suffix in ("", _ORIGINAL_SUFFIX):
            try:
                (files_dir() / f"{stored_name}{suffix}").unlink()
            except FileNotFoundError:
                pass
