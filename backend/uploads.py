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

Word and Excel quotes (slice 6 T1/T2, D1 D2 R5 R8) share POST /api/documents with PDFs:

    upload_format: name .doc/.xls ──► legacy_format ("Save it as .docx/.xlsx"); .docm/.xlsm ──► macros
                   name .docx/.xlsx: content a zip ──► prepare_office; OLE ──► encrypted; else ──► format_mismatch
                   any other name ──► the PDF path above (a zip named .pdf is not_pdf)
    prepare_office: office_docx.scan_package (zip bomb, macros, external links, entity declarations, the other
                    format) ─► render_docx | parse_quote_workbook + render_workbook (page cap before drawing)
                    ─► inspect_pdf on the rendered bytes ─► store the PDF under <uuid4 hex> and the original under
                    <uuid4 hex>.orig (GET /api/documents/{id}/original returns it)
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
        raise ParseError(ParseErrorCode.NOT_PDF, NOT_A_QUOTE_FILE_MESSAGE)
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


NOT_A_QUOTE_FILE_MESSAGE = "Not a PDF, Word (.docx) or Excel (.xlsx) file. Upload the quote in one of those formats."

# Slice 6 (D1, R5): Word and Excel quotes. The format is decided by the extension AND the content (magic bytes).
SourceFormat = str  # "pdf" | "docx" | "xlsx" (AMS: "json" | "csv"; spreadsheet loss runs: "xlsx" | "csv")
_OFFICE_EXTENSIONS = {".docx": "docx", ".xlsx": "xlsx"}
_LEGACY = {".doc": ("Word", ".docx"), ".xls": ("Excel", ".xlsx"), ".docm": ("Word", ".docx"),
           ".dotm": ("Word", ".docx"), ".xlsm": ("Excel", ".xlsx"), ".xltm": ("Excel", ".xlsx")}
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .doc/.xls, and any password-protected Office file


def upload_format(data: bytes, filename: str) -> SourceFormat:
    """pdf | docx | xlsx for one upload to POST /api/documents. Raises OfficeError for a refused Office file.

    A name ending .docx/.xlsx must hold an Office zip; .doc/.xls (and macro-enabled .docm/.xlsm) are refused with
    "Save it as ..."; anything else takes the PDF path (inspect_pdf: magic bytes, whatever the name)."""
    from backend.office_docx import OfficeError  # noqa: PLC0415

    ext = os.path.splitext(filename)[1].lower()
    if ext in _LEGACY:
        app, modern = _LEGACY[ext]
        if ext in (".doc", ".xls"):
            raise OfficeError("legacy_format", f"This is an older {app} file ({ext}). Save it as {modern} and "
                                               "upload again.")
        raise OfficeError("macros", f"This {app} file can contain macros ({ext}), which are not accepted. Save it "
                                    f"as {modern} and upload again.")
    fmt = _OFFICE_EXTENSIONS.get(ext)
    if fmt is None or not data:
        return "pdf"
    if data.startswith(_ZIP_MAGIC):
        return fmt
    app = "Word" if fmt == "docx" else "Excel"
    if data.startswith(_OLE_MAGIC):
        raise OfficeError("encrypted", f"This {app} file is password-protected or saved in an older format. Remove "
                                       f"the password or save it as {ext}, then upload it again.")
    raise OfficeError("format_mismatch", f"This file is named {ext} but is not a {app} document"
                                         f"{' (it is a PDF: rename it to .pdf)' if parse.is_pdf_bytes(data) else ''}. "
                                         f"Upload the original {app} file.")


@dataclass(frozen=True)
class PreparedOffice:
    pdf: bytes  # rendered record: what is stored, served, parsed and verified
    original: bytes  # the uploaded file, kept unchanged (Download)
    page_count: int
    source_format: SourceFormat  # docx | xlsx


def prepare_office(data: bytes, filename: str, limits: ParseLimits, fmt: SourceFormat | None = None,
                   office_limits=None) -> PreparedOffice:
    """Upload-time Word/Excel checks and rendering (slice 6 T1/T2), the same shape as prepare_ams.
    Raises OfficeError or ParseError (message names the reason or the limit)."""
    from backend import office_docx  # noqa: PLC0415 - reportlab and python-docx only for Office uploads

    office_limits = office_limits or office_docx.DEFAULT_OFFICE_LIMITS
    fmt = fmt or upload_format(data, filename)
    if fmt not in ("docx", "xlsx"):
        raise office_docx.OfficeError("format_mismatch", NOT_A_QUOTE_FILE_MESSAGE)
    if not data:
        raise ParseError(ParseErrorCode.EMPTY, "This file is empty.")
    parse.check_size(len(data), limits)
    office_docx.scan_package(data, fmt, office_limits)
    if fmt == "docx":
        rendered = office_docx.render_docx(data, limits.max_pages)
    else:
        book = office_docx.parse_quote_workbook(data, filename, office_limits)
        rendered = office_docx.render_workbook(book, filename, limits.max_pages)
    info = inspect_pdf(rendered.pdf, limits)  # the rendered PDF meets the same caps as any PDF
    if info.parse_error:  # pragma: no cover - our own renderer's output always opens
        raise office_docx.unreadable()
    return PreparedOffice(rendered.pdf, data, info.page_count or rendered.page_count, fmt)


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
    """Keep an AMS export's (R1 D4 A) or a Word/Excel quote's (slice 6) uploaded file beside its rendered PDF."""
    target = _target(stored_name, _ORIGINAL_SUFFIX)
    _write_atomic(target, data)
    return target


def load_original(stored_name: str) -> bytes:
    """The kept original of an AMS document (feed it to ams.facts_from_original)."""
    return _target(stored_name, _ORIGINAL_SUFFIX).read_bytes()


# Slice 5a: a spreadsheet loss run keeps its original (<uuid>.orig) and its parsed rows (<uuid>.rows.jsonl, R5); it
# has no stored PDF: its sheet pages are rendered on demand (routes/documents.py sheet-pages).
SHEET_CACHE_SUFFIX = ".rows.jsonl"


def sheet_cache_path(stored_name: str) -> Path:
    return _target(stored_name, SHEET_CACHE_SUFFIX)


def store_sheet_cache(book, stored_name: str) -> dict:
    """Write the parsed rows beside the original; returns the layout (sheets, pages, mapped headers)."""
    from backend.loss_runs import sheets  # noqa: PLC0415

    return sheets.write_cache(book, sheet_cache_path(stored_name))


def sheet_meta(stored_name: str) -> dict | None:
    """The layout line of a spreadsheet's parsed-rows cache, or None when it is missing."""
    from backend.loss_runs import sheets  # noqa: PLC0415

    try:
        return sheets.read_meta(sheet_cache_path(stored_name))
    except (OSError, ValueError):
        return None


def remove_stored(stored_name: str) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", stored_name or ""):
        for suffix in ("", _ORIGINAL_SUFFIX, SHEET_CACHE_SUFFIX):
            try:
                (files_dir() / f"{stored_name}{suffix}").unlink()
            except FileNotFoundError:
                pass
