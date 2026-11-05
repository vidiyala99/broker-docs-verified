r"""Word and Excel quotes rendered to a PDF record at upload (slice 6 T1/T2; D1, D2, R5, R8, F6, F8).

Compare Quotes (and Proposal Builder, which reuses a comparison's files) take .docx and .xlsx quotes. Each one is
rendered here, in Python, to a PDF that from then on takes the normal parse, extract, verify, chip and source-viewer
path unchanged; the original file is kept beside it for Download (uploads.store_original).

    bytes ─► scan_package (untrusted zip, before any parser sees it):
               not a zip / bad CRC ───────────────────────────► unreadable ("Couldn't read this file")
               > max_entries entries, or > max_uncompressed_bytes unpacked (zip bomb) ──► refused, limit named
               main part missing (word/document.xml | xl/workbook.xml) ──► format_mismatch (or unreadable)
               vbaProject.bin / macro-enabled content type ───► macros
               a relationship with TargetMode="External" (other than a plain hyperlink), xl/externalLinks/ ──►
                                                                external_links
               an XML part with a DTD or entity declaration ──► unsafe_xml (Office XML never has one)
         ─► docx: render_docx: python-docx (its parser runs with resolve_entities=False) reads the body's paragraphs
                  and tables in document order; tracked changes show the accepted text (insertions kept, deletions
                  dropped); headers, footers, images, text boxes and comments are not drawn
            xlsx: sheets.parse_workbook with COMPARE limits (a cell over the cap or a value beyond column 80 refuses
                  the file, merged values drawn once) ─► render_workbook: every cell's full text wrapped across lines
                  and pages, header row repeated, sheet name and row numbers printed (R8; the loss-run render_page,
                  which cuts cells to one line, is not used)
         ─► both lay out every page first and refuse a file over the page cap before drawing anything (Section 4)
         ─► reportlab draws the pages in Helvetica (the AMS renderer's font, ams.py; the PDF base-14 font, so
            nothing is embedded): characters it cannot draw print as "?", byte-identical output for the same input

Text stays citable: each cell's lines are drawn together (a quote that wraps inside a cell is one run of words in the
text layer), row numbers are drawn after the cells of a page so they never split a quote across rows.
"""

from __future__ import annotations

import io
import math
import re
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from reportlab.pdfbase.pdfmetrics import stringWidth

from backend.loss_runs import sheets
from backend.pipeline.parse import ParseError, ParseErrorCode

_MB = 1024 * 1024


@dataclass(frozen=True)
class OfficeLimits:
    max_entries: int = 2_000
    max_uncompressed_bytes: int = sheets.DEFAULT_SHEET_LIMITS.max_uncompressed_bytes  # reuse the sheets zip cap
    max_xml_part_bytes: int = 32 * _MB  # python-docx holds the whole body XML in memory
    # xlsx quotes (R8): a long coverage wording fits; a quote summary is not a 100,000-row loss run
    max_cell_chars: int = 2_000
    max_columns: int = sheets.DEFAULT_SHEET_LIMITS.max_columns
    max_rows: int = 20_000
    max_cells: int = 400_000
    max_sheets: int = sheets.DEFAULT_SHEET_LIMITS.max_sheets


DEFAULT_OFFICE_LIMITS = OfficeLimits()


class OfficeError(Exception):
    """A Word or Excel file cannot be used. `message` is safe to show (same attributes as parse.ParseError)."""

    def __init__(self, code: str, message: str, limit: int | None = None):
        super().__init__(message)
        self.code, self.message, self.limit = code, message, limit


UNREADABLE_MESSAGE = "Couldn't read this file. It may be damaged: open it, save it again and upload it."
_NAMES = {"docx": ("Word", ".docx"), "xlsx": ("Excel", ".xlsx")}


def unreadable() -> OfficeError:
    return OfficeError("unreadable", UNREADABLE_MESSAGE)


def too_many_pages(max_pages: int) -> ParseError:
    return ParseError(ParseErrorCode.TOO_MANY_PAGES,
                      f"This file is more than {max_pages} pages once converted to PDF; the limit is {max_pages} "
                      "pages per document.", limit=max_pages)


# --------------------------------------------------------------------- untrusted zip scan

_MAIN_PART = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml"}
_REL_RE = re.compile(rb"<(?:\w+:)?Relationship\b[^>]*>", re.I)
_EXTERNAL_RE = re.compile(rb"""TargetMode\s*=\s*["']External["']""", re.I)
_TYPE_RE = re.compile(rb"""Type\s*=\s*["']([^"']*)["']""", re.I)
_DTD_RE = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.I)
_HYPERLINK_TYPE = b"/relationships/hyperlink"


def _read_part(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Iterator[bytes]:
    with zf.open(info) as fh:  # zipfile stops at the declared size and checks the CRC
        while True:
            chunk = fh.read(_MB)
            if not chunk:
                return
            yield chunk


def scan_package(data: bytes, fmt: str, limits: OfficeLimits = DEFAULT_OFFICE_LIMITS) -> None:
    """Refuse an Office zip that is damaged, oversized when unpacked, the other format, macro-enabled, linked to
    external content or carrying XML entity declarations. Raises OfficeError (message names the reason)."""
    app, ext = _NAMES[fmt]
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError):
        raise unreadable() from None
    with zf:
        if len(infos) > limits.max_entries:
            raise OfficeError("too_many_entries", f"This file holds more than {limits.max_entries:,} parts; the "
                                                  f"limit is {limits.max_entries:,}.", limit=limits.max_entries)
        if sum(i.file_size for i in infos) > limits.max_uncompressed_bytes:
            mb = limits.max_uncompressed_bytes // _MB
            raise OfficeError("unpacked_too_large", f"This file unpacks to more than {mb} MB; the limit is {mb} MB.",
                              limit=limits.max_uncompressed_bytes)
        names = {i.filename.lower(): i for i in infos}
        main = _main_part(zf, names)
        if main is None or not main.startswith(_MAIN_PART[fmt].split("/")[0] + "/"):
            other = next((f for f, part in _MAIN_PART.items()
                          if f != fmt and (part in names or (main or "").startswith(part.split("/")[0] + "/"))), None)
            if other is not None:
                other_app, other_ext = _NAMES[other]
                article = "an" if other_app == "Excel" else "a"
                raise OfficeError("format_mismatch", f"This file is named {ext} but is {article} {other_app} file. "
                                                     f"Rename it to {other_ext} and upload it again.")
            raise OfficeError("format_mismatch", f"This file is named {ext} but is not a {app} document. Upload the "
                                                 f"original {app} file.")
        if any(n.endswith("vbaproject.bin") or n.endswith("vbadata.xml") for n in names):
            raise _macros(app, ext)
        if fmt == "xlsx" and any(n.startswith("xl/externallinks/") for n in names):
            raise _external(app)
        try:
            for info in infos:
                lower = info.filename.lower()
                if not (lower.endswith(".xml") or lower.endswith(".rels")):
                    continue
                if info.file_size > limits.max_xml_part_bytes:
                    mb = limits.max_xml_part_bytes // _MB
                    raise OfficeError("unpacked_too_large", f"A part of this file unpacks to more than {mb} MB; the "
                                                            f"limit is {mb} MB per part.",
                                      limit=limits.max_xml_part_bytes)
                if lower.endswith(".rels"):
                    body = b"".join(_read_part(zf, info))
                    _check_xml(body)
                    for rel in _REL_RE.findall(body):
                        if _EXTERNAL_RE.search(rel):
                            kind = _TYPE_RE.search(rel)
                            if kind is None or not kind.group(1).lower().endswith(_HYPERLINK_TYPE):
                                raise _external(app)
                    continue
                tail = b""
                for chunk in _read_part(zf, info):
                    _check_xml(tail + chunk)
                    if lower == "[content_types].xml" and b"macroenabled" in (tail + chunk).lower():
                        raise _macros(app, ext)
                    tail = chunk[-32:]
        except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError, RuntimeError):
            raise unreadable() from None


_OFFICE_DOC_TYPE = b"/relationships/officedocument"
_TARGET_RE = re.compile(rb"""Target\s*=\s*["']([^"']*)["']""", re.I)


def _main_part(zf: zipfile.ZipFile, names: dict[str, zipfile.ZipInfo]) -> str | None:
    """The package's main part (lower case) from _rels/.rels, else the usual name when present."""
    rels = names.get("_rels/.rels")
    if rels is not None and rels.file_size <= _MB:
        try:
            body = zf.read(rels)
        except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError, RuntimeError):
            raise unreadable() from None
        for rel in _REL_RE.findall(body):
            kind, target = _TYPE_RE.search(rel), _TARGET_RE.search(rel)
            if kind and target and kind.group(1).lower().endswith(_OFFICE_DOC_TYPE):
                part = target.group(1).decode("utf-8", "replace").lstrip("/").lower()
                if part in names:
                    return part
    return next((part for part in _MAIN_PART.values() if part in names), None)


def _check_xml(buf: bytes) -> None:
    if _DTD_RE.search(buf):
        raise OfficeError("unsafe_xml", "Couldn't read this file: it declares XML entities, which Office files do "
                                        "not use. Save it again from Word or Excel and upload it.")


def _macros(app: str, ext: str) -> OfficeError:
    return OfficeError("macros", f"This {app} file contains macros, which are not accepted. Save it as a {ext} file "
                                 "without macros and upload it again.")


def _external(app: str) -> OfficeError:
    return OfficeError("external_links", f"This {app} file links to content outside the file (an external link), "
                                         "which is not accepted. Remove the link or paste the values in, then upload "
                                         "it again.")


# --------------------------------------------------------------------- text and pages (shared)

FONT = "Helvetica"  # the AMS renderer's font (ams.py, PyMuPDF "helv" = PDF base-14 Helvetica)
FONT_BOLD = "Helvetica-Bold"


_HYPHENS = {"‐": "-", "‑": "-", "‒": "-", "−": "-"}


def _encodable(text: str) -> bool:
    try:
        text.encode("cp1252")  # reportlab's base-14 fonts draw WinAnsi (cp1252) only
    except UnicodeEncodeError:
        return False
    return True


def _drawable_char(ch: str) -> str:
    if _encodable(ch):
        return ch
    if ch in _HYPHENS:
        return _HYPHENS[ch]
    compat = unicodedata.normalize("NFKC", ch)  # a ligature such as "fi" draws as its letters
    return compat if compat != ch and _encodable(compat) else "?"


def screen(raw: str) -> str:
    """One line of untrusted text as drawn: control and format characters removed, other spaces made plain and
    collapsed, characters the font cannot draw shown as "?"."""
    out = []
    for ch in raw:
        cat = unicodedata.category(ch)
        if ch in "\t\n\r\v\f":
            out.append(" ")
        elif cat[0] == "C":
            continue
        elif cat[0] == "Z":
            out.append(" ")
        else:
            out.append(_drawable_char(ch))
    return " ".join("".join(out).split())


def _width(text: str, font: str, size: float) -> float:
    return stringWidth(text, font, size)


def wrap(text: str, width: float, font: str = FONT, size: float = 10.0) -> list[str]:
    """Words onto lines no wider than `width`; a single over-long word is broken by characters."""
    width = max(width, size)
    lines: list[str] = []
    cur = ""
    for word in text.split(" "):
        if not word:
            continue
        while _width(word, font, size) > width:
            lo, hi = 1, len(word)
            while lo < hi:  # longest prefix that fits
                mid = (lo + hi + 1) // 2
                if _width(word[:mid], font, size) <= width:
                    lo = mid
                else:
                    hi = mid - 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:lo])
            word = word[lo:]
        trial = f"{cur} {word}" if cur else word
        if _width(trial, font, size) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# A page is a list of draw ops, top-left origin like Page.words:
#   ("t", x, baseline_y, text, font, size, gray)   ("r", x, y, w, h)   ("l", x1, y1, x2, y2)
Op = tuple


@dataclass
class _Pages:
    width: float
    height: float
    top: float
    bottom: float
    max_pages: int
    pages: list[list[Op]] = field(default_factory=list)
    y: float = 0.0

    def __post_init__(self) -> None:
        self.new_page()

    def new_page(self) -> None:
        if len(self.pages) >= self.max_pages:
            raise too_many_pages(self.max_pages)
        self.pages.append([])
        self.y = self.top

    @property
    def ops(self) -> list[Op]:
        return self.pages[-1]

    @property
    def page_no(self) -> int:
        return len(self.pages)

    @property
    def at_top(self) -> bool:
        return self.y <= self.top + 0.01

    def room(self) -> float:
        return self.bottom - self.y


def _draw(pages: list[list[Op]], width: float, height: float) -> bytes:
    from reportlab.pdfgen import canvas  # noqa: PLC0415 - heavy import, only for Office uploads

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height), invariant=1, pageCompression=1)
    c.setLineWidth(0.5)
    for ops in pages:
        for op in ops:
            if op[0] == "t":
                _, x, y, text, font, size, gray = op
                c.setFont(font, size)
                c.setFillGray(gray)
                c.drawString(x, height - y, text)
            elif op[0] == "r":
                _, x, y, w, h = op
                c.setStrokeGray(0.72)
                c.rect(x, height - y - h, w, h, stroke=1, fill=0)
            elif op[0] == "l":
                _, x1, y1, x2, y2 = op
                c.setStrokeGray(0.72)
                c.line(x1, height - y1, x2, height - y2)
        c.showPage()
    c.save()
    return buf.getvalue()


@dataclass(frozen=True)
class RowPlace:
    """Where a spreadsheet row was drawn: its page and vertical band (PDF points, top-left origin)."""

    page_no: int
    y0: float
    y1: float


@dataclass(frozen=True)
class RenderedOffice:
    pdf: bytes
    page_count: int
    # xlsx only: (sheet index, sheet row number) -> every place the row was drawn (a tall row may span pages)
    rows: dict[tuple[int, int], list[RowPlace]] = field(default_factory=dict)


# --------------------------------------------------------------------- docx

DOCX_PAGE = (612.0, 792.0)  # Letter, as the AMS renderer
DOCX_MARGIN = 54.0
BODY_SIZE, BODY_LEAD = 10.0, 14.0
HEAD_SIZE, HEAD_LEAD = 12.0, 16.0
CELL_SIZE, CELL_LEAD, CELL_PAD = 9.0, 12.0, 4.0
_MAX_INDENT = 144.0


def _w(tag: str) -> str:
    from docx.oxml.ns import qn  # noqa: PLC0415

    return qn(f"w:{tag}")


_MC_ALTERNATE = "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"


class _Tags:
    """Qualified WordprocessingML tag names (looked up once python-docx is imported)."""

    def __init__(self) -> None:
        for name in ("p", "tbl", "tr", "tc", "sdt", "sdtContent", "customXml", "smartTag", "t", "tab", "ptab", "br",
                     "cr", "noBreakHyphen", "del", "moveFrom", "drawing", "pict", "object", "delText", "instrText",
                     "pPr", "rPr", "pStyle", "numPr", "numId", "ind", "outlineLvl", "tcPr", "gridSpan", "tblGrid",
                     "gridCol", "val", "w", "left", "start", "sectPr", "tblPr", "trPr"):
            setattr(self, name, _w(name))
        self.skip = {self.delete, self.moveFrom, self.drawing, self.pict, self.object, self.delText, self.instrText,
                     self.pPr, self.rPr, self.tcPr, self.tblPr, self.trPr, _MC_ALTERNATE}

    @property
    def delete(self) -> str:
        return getattr(self, "del")


def _tag(el: Any) -> str | None:
    tag = getattr(el, "tag", None)
    return tag if isinstance(tag, str) else None  # comments and processing instructions have no str tag


def _content(el: Any, T: _Tags) -> Iterator[Any]:
    """Block-level children (paragraphs, tables), looking through content controls and custom XML wrappers."""
    for child in el:
        tag = _tag(child)
        if tag in (T.p, T.tbl):
            yield child
        elif tag in (T.sdt, T.customXml):
            inner = child.find(T.sdtContent) if tag == T.sdt else child
            if inner is not None:
                yield from _content(inner, T)


def _para_text(p: Any, T: _Tags) -> str:
    """Accepted text of a paragraph: insertions kept, deletions and moved-from text dropped, field codes, drawings
    and text boxes skipped; tabs as spaces, line breaks as newlines."""
    out: list[str] = []

    def walk(el: Any) -> None:
        for child in el:
            tag = _tag(child)
            if tag is None or tag in T.skip:
                continue
            if tag == T.t:
                out.append(child.text or "")
            elif tag in (T.tab, T.ptab):
                out.append(" ")
            elif tag in (T.br, T.cr):
                out.append("\n")
            elif tag == T.noBreakHyphen:
                out.append("-")
            else:
                walk(child)

    walk(p)
    return "".join(out)


def _attr(el: Any, T: _Tags, *names: str) -> str | None:
    if el is None:
        return None
    for name in names:
        value = el.get(getattr(T, name))
        if value is not None:
            return value
    return None


def _para_style(p: Any, T: _Tags) -> tuple[bool, bool, float]:
    """(heading, list item, left indent in points)."""
    ppr = p.find(T.pPr)
    if ppr is None:
        return False, False, 0.0
    style = (_attr(ppr.find(T.pStyle), T, "val") or "").lower()
    heading = style.startswith("heading") or style in ("title", "subtitle") or ppr.find(T.outlineLvl) is not None
    num = ppr.find(T.numPr)
    listed = num is not None and (_attr(num.find(T.numId), T, "val") or "1") != "0"
    indent = 0.0
    raw = _attr(ppr.find(T.ind), T, "left", "start")
    if raw:
        try:
            indent = min(max(int(raw) / 20.0, 0.0), _MAX_INDENT)
        except ValueError:
            indent = 0.0
    return heading, listed, indent


def _cell_lines(tc: Any, T: _Tags) -> list[str]:
    """A table cell's text as logical lines: its paragraphs, and a nested table's rows (cells joined by spaces)."""
    lines: list[str] = []
    for block in _content(tc, T):
        if _tag(block) == T.p:
            lines.extend(_para_text(block, T).split("\n"))
        else:
            for tr in _rows(block, T):
                lines.append("  ".join(" ".join(_cell_lines(c, T)) for c in _cells(tr, T)))
    return [screen(line) for line in lines]


def _rows(tbl: Any, T: _Tags) -> Iterator[Any]:
    for child in tbl:
        tag = _tag(child)
        if tag == T.tr:
            yield child
        elif tag in (T.sdt, T.customXml):
            inner = child.find(T.sdtContent) if tag == T.sdt else child
            if inner is not None:
                yield from _rows(inner, T)


def _cells(tr: Any, T: _Tags) -> Iterator[Any]:
    for child in tr:
        tag = _tag(child)
        if tag == T.tc:
            yield child
        elif tag in (T.sdt, T.customXml):
            inner = child.find(T.sdtContent) if tag == T.sdt else child
            if inner is not None:
                yield from _cells(inner, T)


def _span(tc: Any, T: _Tags) -> int:
    tcpr = tc.find(T.tcPr)
    raw = _attr(tcpr.find(T.gridSpan) if tcpr is not None else None, T, "val")
    try:
        return min(max(int(raw), 1), 64) if raw else 1
    except ValueError:
        return 1


class _DocxLayout:
    def __init__(self, max_pages: int) -> None:
        w, h = DOCX_PAGE
        self.pages = _Pages(w, h, DOCX_MARGIN, h - DOCX_MARGIN, max_pages)
        self.text_w = w - 2 * DOCX_MARGIN
        self.has_text = False

    def paragraph(self, text: str, *, heading: bool, listed: bool, indent: float) -> None:
        pg = self.pages
        font, size, lead = (FONT_BOLD, HEAD_SIZE, HEAD_LEAD) if heading else (FONT, BODY_SIZE, BODY_LEAD)
        segments = [screen(s) for s in text.split("\n")]
        if not any(segments):
            pg.y += lead / 2  # an empty paragraph keeps a little space
            return
        if listed:
            segments[0] = "• " + segments[0]
        x = DOCX_MARGIN + indent
        for segment in segments:
            for line in wrap(segment, self.text_w - indent, font, size) or [""]:
                if pg.y + lead > pg.bottom and not pg.at_top:
                    pg.new_page()
                if line:
                    pg.ops.append(("t", x, pg.y + size, line, font, size, 0.0))
                    self.has_text = True
                pg.y += lead
        pg.y += lead / 2

    def table(self, tbl: Any, T: _Tags) -> None:
        rows = [[(_span(tc, T), _cell_lines(tc, T)) for tc in _cells(tr, T)] for tr in _rows(tbl, T)]
        rows = [r for r in rows if r]
        if not rows:
            return
        grid = tbl.find(T.tblGrid)
        cols: list[float] = []
        if grid is not None:
            for gc in grid.findall(T.gridCol):
                try:
                    cols.append(max(float(_attr(gc, T, "w") or 0), 0.0))
                except ValueError:
                    cols.append(0.0)
        ncols = max([len(cols)] + [sum(s for s, _ in r) for r in rows])
        if len(cols) < ncols or not sum(cols) or any(c <= 0 for c in cols):
            cols = [1.0] * ncols
        scale = self.text_w / sum(cols[:ncols])
        widths = [c * scale for c in cols[:ncols]]
        edges = [DOCX_MARGIN]
        for wdt in widths:
            edges.append(edges[-1] + wdt)
        pg = self.pages
        pg.y += 2
        for row in rows:
            placed = []  # (x, width, wrapped lines)
            ci = 0
            for span, lines in row:
                if ci >= ncols:
                    break
                span = min(span, ncols - ci)
                x0, x1 = edges[ci], edges[ci + span]
                wrapped = [w for line in lines for w in (wrap(line, x1 - x0 - 2 * CELL_PAD, FONT, CELL_SIZE) or [""])]
                placed.append((x0, x1 - x0, wrapped))
                ci += span
            self._row(placed)
        pg.y += BODY_LEAD / 2

    def _row(self, placed: list[tuple[float, float, list[str]]]) -> None:
        pg = self.pages
        total = max([len(w) for _, _, w in placed] + [1])
        body = pg.bottom - pg.top
        k = 0
        while k < total:
            remaining = total - k
            fit = int((pg.room() - 2 * CELL_PAD) // CELL_LEAD)
            if fit < remaining and not pg.at_top and remaining * CELL_LEAD + 2 * CELL_PAD <= body:
                pg.new_page()  # the whole row fits on a fresh page: move it there
                continue
            if fit < 1:
                pg.new_page()
                continue
            n = min(fit, remaining)
            h = n * CELL_LEAD + 2 * CELL_PAD
            for x0, wdt, lines in placed:  # one cell's lines together: a wrapped quote is one run of words
                for i, line in enumerate(lines[k:k + n]):
                    if line:
                        pg.ops.append(("t", x0 + CELL_PAD, pg.y + CELL_PAD + CELL_SIZE + i * CELL_LEAD, line, FONT,
                                       CELL_SIZE, 0.0))
                        self.has_text = True
            for x0, wdt, _ in placed:
                pg.ops.append(("r", x0, pg.y, wdt, h))
            pg.y += h
            k += n
            if k < total:
                pg.new_page()


def render_docx(data: bytes, max_pages: int) -> RenderedOffice:
    """The body of a .docx (paragraphs and tables in document order) as a PDF. Call scan_package first.
    Raises OfficeError (unreadable, empty) or ParseError (too_many_pages, before anything is drawn)."""
    try:
        import docx  # noqa: PLC0415 - heavy import, only for Word uploads

        body = docx.Document(io.BytesIO(data)).element.body
    except Exception:  # python-docx / lxml / zipfile errors on a damaged file (F8)
        raise unreadable() from None
    T = _Tags()
    layout = _DocxLayout(max_pages)
    try:
        for block in _content(body, T):
            if _tag(block) == T.p:
                heading, listed, indent = _para_style(block, T)
                layout.paragraph(_para_text(block, T), heading=heading, listed=listed, indent=indent)
            else:
                layout.table(block, T)
    except (OfficeError, ParseError):
        raise
    except Exception:
        raise unreadable() from None
    if not layout.has_text:
        raise OfficeError("empty", "This Word document has no text to read (images are not read). Save it as a PDF "
                                   "and upload that instead.")
    pages = layout.pages.pages
    return RenderedOffice(_draw(pages, *DOCX_PAGE), len(pages))


# --------------------------------------------------------------------- xlsx (R8: wrapping sheet renderer)

SHEET_PAGE = (792.0, 612.0)  # Letter landscape
SHEET_MARGIN = 36.0
SHEET_TOP = 66.0  # below the file name and sheet line
ROWNO_W = 30.0
SHEET_SIZE, SHEET_LEAD, SHEET_PAD = 8.0, 10.0, 3.0
MIN_COL_W, MAX_COL_W = 40.0, 280.0
_HEADER_REPEAT_MAX = 6  # a header row taller than this many lines is not repeated


def compare_sheet_limits(limits: OfficeLimits = DEFAULT_OFFICE_LIMITS) -> sheets.SheetLimits:
    """parse_workbook limits for a Compare quote (R8): higher per-cell cap, refused rather than cut, merged values
    drawn once."""
    return sheets.SheetLimits(max_rows_per_file=limits.max_rows, max_rows_per_review=limits.max_rows,
                              max_sheets=limits.max_sheets, max_uncompressed_bytes=limits.max_uncompressed_bytes,
                              max_cells=limits.max_cells, max_columns=limits.max_columns,
                              max_cell_chars=limits.max_cell_chars, refuse_over_cap=True, fill_merged_cells=False)


_SHEET_MESSAGES = {
    "too_many_rows": lambda lim: f"This spreadsheet has more than {lim:,} rows; the limit is {lim:,} per quote.",
    "too_many_cells": lambda lim: f"This spreadsheet has more than {lim:,} filled cells; the limit is {lim:,} per "
                                  "quote.",
}


def parse_quote_workbook(data: bytes, filename: str, limits: OfficeLimits = DEFAULT_OFFICE_LIMITS) -> sheets.Workbook:
    """sheets.parse_workbook (its zip and XML caps) with the Compare limits; errors in Compare words."""
    try:
        return sheets.parse_workbook(data, filename, compare_sheet_limits(limits))
    except sheets.SheetError as exc:
        if exc.code == "corrupt":
            raise unreadable() from None
        if exc.code in _SHEET_MESSAGES and exc.limit is not None:
            raise OfficeError(exc.code, _SHEET_MESSAGES[exc.code](exc.limit), limit=exc.limit) from None
        raise OfficeError(exc.code, exc.message, limit=exc.limit) from None
    except Exception:
        raise unreadable() from None


def _used_columns(rows: Iterable[sheets.Row]) -> list[int]:
    used: set[int] = set()
    for _, cells, _ in rows:
        used.update(i for i, c in enumerate(cells) if c)
    return sorted(used)


def _column_widths(rows: list[sheets.Row], cols: list[int], avail: float) -> list[float]:
    desired = []
    for ci in cols:
        longest = max((_width(c[ci], FONT, SHEET_SIZE) for _, c, _ in rows if ci < len(c) and c[ci]), default=0.0)
        desired.append(min(max(longest + 2 * SHEET_PAD + 1, MIN_COL_W), MAX_COL_W))
    total = sum(desired)
    if total <= avail:
        return desired
    floor = min(MIN_COL_W, avail / len(cols))
    spare = total - floor * len(cols)
    k = (avail - floor * len(cols)) / spare if spare > 0 else 0.0
    return [floor + (d - floor) * k for d in desired]


class _SheetLayout:
    def __init__(self, title: str, max_pages: int) -> None:
        w, h = SHEET_PAGE
        self.title = screen(title)[:200]
        self.max_pages = max_pages
        self.pages = _Pages(w, h, SHEET_TOP, h - SHEET_MARGIN, max_pages)
        self.pages.pages.clear()  # each sheet opens its own page
        self.rows: dict[tuple[int, int], list[RowPlace]] = {}
        self._gutter: list[Op] = []
        self._page_rows: list[int] = []
        self._page_data_rows: list[int] = []
        self._sheet_name = ""
        self._sheet_index = 0

    def _finish_page(self) -> None:
        if not self.pages.pages:
            return
        ops = self.pages.ops
        drawn = self._page_data_rows or self._page_rows  # a repeated header row is not one of the page's rows
        first, last = (drawn[0], drawn[-1]) if drawn else (0, 0)
        heading = [("t", SHEET_MARGIN, SHEET_MARGIN + 6, self.title, FONT_BOLD, 10.0, 0.0),
                   ("t", SHEET_MARGIN, SHEET_MARGIN + 20, f"Sheet {self._sheet_name}, rows {first} to {last}", FONT,
                    SHEET_SIZE, 0.35)]
        ops[:0] = heading  # drawn first: file and sheet name lead the page's text
        ops.extend(self._gutter)  # row numbers drawn last: they never split a quote across rows
        self._gutter, self._page_rows, self._page_data_rows = [], [], []

    def _new_page(self, header: list[tuple[float, float, list[str]]] | None, header_row: int | None) -> None:
        self._finish_page()
        self.pages.new_page()
        if header is not None and header_row is not None:
            self._draw_chunk(header, 0, max(len(w) for _, _, w in header), header_row, bold=True, record=False)

    def sheet(self, sheet: sheets.Sheet) -> None:
        rows = sheet.rows
        cols = _used_columns(rows)
        if not cols:
            return
        self._new_page(None, None)  # finishes the previous sheet's last page under its own name
        self._sheet_name = screen(sheet.name)
        self._sheet_index = sheet.index
        x = SHEET_MARGIN + ROWNO_W
        widths = _column_widths(rows, cols, SHEET_PAGE[0] - 2 * SHEET_MARGIN - ROWNO_W)
        edges = [x]
        for wdt in widths:
            edges.append(edges[-1] + wdt)

        def place(cells: list[str | None]) -> list[tuple[float, float, list[str]]]:
            out = []
            for k, ci in enumerate(cols):
                text = screen(cells[ci]) if ci < len(cells) and cells[ci] else ""
                out.append((edges[k], edges[k + 1] - edges[k],
                            wrap(text, edges[k + 1] - edges[k] - 2 * SHEET_PAD, FONT, SHEET_SIZE) if text else []))
            return out

        header_no, header_cells = rows[0][0], rows[0][1]
        header = place(header_cells)
        repeat = header if max(len(w) for _, _, w in header) <= _HEADER_REPEAT_MAX else None
        for row_no, cells, _ in rows:
            placed = place(cells)
            self._row(placed, row_no, bold=row_no == header_no, header=repeat, header_row=header_no)

    def _row(self, placed, row_no: int, *, bold: bool, header, header_row: int) -> None:
        pg = self.pages
        total = max([len(w) for _, _, w in placed] + [1])
        body = pg.bottom - pg.top
        k = 0
        while k < total:
            remaining = total - k
            fit = int((pg.room() - 2 * SHEET_PAD) // SHEET_LEAD)
            fresh = not self._page_rows or (len(self._page_rows) == 1 and self._page_rows[0] == header_row
                                            and header is not None and row_no != header_row)
            if fit < remaining and not fresh and remaining * SHEET_LEAD + 2 * SHEET_PAD <= body:
                self._new_page(header if row_no != header_row else None, header_row)
                continue
            if fit < 1:
                self._new_page(header if row_no != header_row else None, header_row)
                continue
            n = min(fit, remaining)
            self._draw_chunk(placed, k, n, row_no, bold=bold, record=True)
            k += n
            if k < total:
                self._new_page(header if row_no != header_row else None, header_row)

    def _draw_chunk(self, placed, k: int, n: int, row_no: int, *, bold: bool, record: bool) -> None:
        pg = self.pages
        n = max(n, 1)
        h = n * SHEET_LEAD + 2 * SHEET_PAD
        font = FONT_BOLD if bold else FONT
        for x0, _, lines in placed:  # one cell's lines together: a wrapped quote is one run of words
            for i, line in enumerate(lines[k:k + n]):
                pg.ops.append(("t", x0 + SHEET_PAD, pg.y + SHEET_PAD + SHEET_SIZE - 1 + i * SHEET_LEAD, line, font,
                               SHEET_SIZE, 0.0))
        left, right = SHEET_MARGIN, placed[-1][0] + placed[-1][1]
        pg.ops.append(("l", left, pg.y + h, right, pg.y + h))
        for x0, _, _ in placed:
            pg.ops.append(("l", x0, pg.y, x0, pg.y + h))
        pg.ops.append(("l", right, pg.y, right, pg.y + h))
        if not self._page_rows or self._page_rows[-1] != row_no:
            self._page_rows.append(row_no)
            self._gutter.append(("t", SHEET_MARGIN, pg.y + SHEET_PAD + SHEET_SIZE - 1, str(row_no), FONT, SHEET_SIZE,
                                 0.45))
        if record:
            if not self._page_data_rows or self._page_data_rows[-1] != row_no:
                self._page_data_rows.append(row_no)
            self.rows.setdefault((self._sheet_index, row_no), []).append(RowPlace(pg.page_no, pg.y, pg.y + h))
        pg.y += h


def min_sheet_pages(book: sheets.Workbook) -> int:
    """A lower bound on the pages a workbook renders to (every row one line high), checked before measuring."""
    per_page = int((SHEET_PAGE[1] - SHEET_MARGIN - SHEET_TOP - 2 * SHEET_PAD) // (SHEET_LEAD + 2 * SHEET_PAD)) or 1
    return sum(math.ceil(len(s.rows) / per_page) for s in book.sheets if s.rows)


def render_workbook(book: sheets.Workbook, title: str, max_pages: int) -> RenderedOffice:
    """Every sheet's full cell text, wrapped (R8). Pages are planned and counted against `max_pages` before any page
    is drawn (Section 4): ParseError too_many_pages."""
    if min_sheet_pages(book) > max_pages:
        raise too_many_pages(max_pages)
    layout = _SheetLayout(title, max_pages)
    for sheet in book.sheets:
        layout.sheet(sheet)
    layout._finish_page()
    pages = layout.pages.pages
    if not pages:
        raise OfficeError("empty", "This spreadsheet has no filled cells.")
    return RenderedOffice(_draw(pages, *SHEET_PAGE), len(pages), layout.rows)


__all__ = ["DEFAULT_OFFICE_LIMITS", "OfficeError", "OfficeLimits", "RenderedOffice", "RowPlace",
           "UNREADABLE_MESSAGE", "compare_sheet_limits", "min_sheet_pages", "parse_quote_workbook", "render_docx",
           "render_workbook", "scan_package", "screen", "wrap"]
