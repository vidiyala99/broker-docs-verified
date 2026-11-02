"""DOCX export of one artifact version (slice 2, T5).

Layout: cover page, table of contents, notices, sections (prose, lists, issues,
tables), then a Sources list. Citation chips become real Word footnotes: the
footnote text is the existing `_source_note` (prose) or `cell_note` (table
cells) from backend.export, so the DOCX says exactly what the XLSX comments
and the PDF say. Cells in the `Couldn't verify`, `Conflicting values` and
`Read from scan` states are written as that state in words plus the footnoted
source note, never as a bare value.

python-docx has no footnote API. `_Footnotes` writes the footnotes part as
WordprocessingML (see docs/specs/2026-11-02-docx-footnote-spike.md): the part
at /word/footnotes.xml with its content type and a document relationship, the
separator (id -1) and continuation separator (id 0) footnotes Word expects,
`footnote text` / `footnote reference` styles, a `w:footnotePr` in settings,
and one `w:footnoteReference` run per citation, numbered in reading order.

The TOC is a real Word TOC field, pre-filled with the section headings so it
reads correctly before Word updates it; `updateFields` asks Word to fill in the
page numbers on open.

Deterministic: the same version exported twice is byte-identical (fixed core
properties from the version's timestamp, fixed zip entry times and order).
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.opc.constants import CONTENT_TYPE as CT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from lxml import etree

from backend.export import (
    ExportData,
    _conflict_list,
    _source_note,
    cell_note,
    first_heading,
    front_matter,
)
from backend.schemas import (
    Cell,
    EvidenceState,
    HeadingBlock,
    IssueBlock,
    ListBlock,
    ParagraphBlock,
    Section,
    Segment,
    TableBlock,
)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# Used when the run has no template snapshot (slice 1 runs) and, field by field,
# when a snapshot field is missing or invalid.
DEFAULT_THEME: dict[str, Any] = {
    "font": "Calibri",
    "heading_color": "1F2A44",
    "heading_sizes": {"title": 26, "1": 16, "2": 13, "3": 11},
}
SOURCES_HEADING = "Sources"
BODY_SIZE = 10
TABLE_SIZE = 8.5
TEXT_WIDTH_INCHES = 7.0  # Letter portrait, 0.75 inch margins
_FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)  # when ExportData has no version timestamp
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

_FONT_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{0,63}$")
_HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")
# XML 1.0 cannot hold these; document text is untrusted, so drop them.
_XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def clean(text: Any) -> str:
    return _XML_BAD.sub("", "" if text is None else str(text))


# --------------------------------------------------------------------- theme


def resolve_theme(raw: Any) -> dict[str, Any]:
    """A safe theme: snapshot values where valid, DEFAULT_THEME elsewhere."""
    theme = {"font": DEFAULT_THEME["font"], "heading_color": DEFAULT_THEME["heading_color"],
             "heading_sizes": dict(DEFAULT_THEME["heading_sizes"])}
    if not isinstance(raw, dict):
        return theme
    font = raw.get("font")
    if isinstance(font, str) and _FONT_OK.match(font.strip()):
        theme["font"] = font.strip()
    color = raw.get("heading_color")
    if isinstance(color, str) and (m := _HEX.match(color.strip())):
        theme["heading_color"] = m.group(1).upper()
    sizes = raw.get("heading_sizes")
    if isinstance(sizes, dict):
        for key, value in sizes.items():
            k = str(key).strip().lower()
            k = "title" if k == "title" else re.sub(r"^(heading|level|h)[ _-]?", "", k)
            if k not in theme["heading_sizes"]:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 6 <= value <= 72:
                theme["heading_sizes"][k] = value
    return theme


# --------------------------------------------------------------------- footnotes


def _w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _el(parent, tag: str, **attrs):
    e = etree.SubElement(parent, _w(tag))
    for k, v in attrs.items():
        e.set(_w(k), str(v))
    return e


class _Footnotes:
    """Collects footnotes while the body is written; becomes /word/footnotes.xml."""

    def __init__(self) -> None:
        self.root = etree.Element(_w("footnotes"), nsmap={"w": W_NS})
        for fid, kind in ((-1, "separator"), (0, "continuationSeparator")):
            fn = _el(self.root, "footnote", type=kind, id=fid)
            p = _el(fn, "p")
            ppr = _el(p, "pPr")
            _el(ppr, "spacing", after=0, line=240, lineRule="auto")
            _el(_el(p, "r"), kind)
        self.count = 0

    def add(self, paragraph, text: str) -> int:
        """Append a footnote reference run to `paragraph`; the note holds `text`."""
        self.count += 1
        fid = self.count
        fn = _el(self.root, "footnote", id=fid)
        p = _el(fn, "p")
        _el(_el(p, "pPr"), "pStyle", val="FootnoteText")
        ref = _el(p, "r")
        _el(_el(ref, "rPr"), "rStyle", val="FootnoteReference")
        _el(ref, "footnoteRef")
        r = _el(p, "r")
        for i, line in enumerate(clean(text).split("\n")):
            if i:
                _el(r, "br")
            t = _el(r, "t")
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            t.text = (" " if i == 0 else "") + line
        run = paragraph.add_run()
        run._r.style = "FootnoteReference"
        fref = OxmlElement("w:footnoteReference")
        fref.set(qn("w:id"), str(fid))
        run._r.append(fref)
        return fid

    def attach(self, document) -> None:
        blob = etree.tostring(self.root, xml_declaration=True, encoding="UTF-8", standalone=True)
        part = Part(PackURI("/word/footnotes.xml"), CT.WML_FOOTNOTES, blob, document.part.package)
        document.part.relate_to(part, RT.FOOTNOTES)


# Children of w:settings that must come after w:updateFields / w:footnotePr
# (CT_Settings sequence, ECMA-376 Part 1, 17.15.1.78).
_AFTER_FOOTNOTE_PR = ("endnotePr", "compat", "docVars", "rsids", "mathPr", "attachedSchema", "themeFontLang",
                      "clrSchemeMapping", "doNotIncludeSubdocsInStats", "doNotAutoCompressPictures",
                      "forceUpgrade", "captions", "readModeInkLockDown", "smartTagType", "schemaLibrary",
                      "shapeDefaults", "doNotEmbedSmartTags", "decimalSymbol", "listSeparator")
_AFTER_UPDATE_FIELDS = ("hdrShapeDefaults", "footnotePr", *_AFTER_FOOTNOTE_PR)


def _insert_setting(settings, element, successors: tuple[str, ...]) -> None:
    for child in settings:
        if etree.QName(child).localname in successors:
            child.addprevious(element)
            return
    settings.append(element)


def _prepare_settings(document) -> None:
    settings = document.settings.element
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    _insert_setting(settings, update, _AFTER_UPDATE_FIELDS)
    pr = OxmlElement("w:footnotePr")
    for fid in ("-1", "0"):
        f = OxmlElement("w:footnote")
        f.set(qn("w:id"), fid)
        pr.append(f)
    _insert_setting(settings, pr, _AFTER_FOOTNOTE_PR)


# --------------------------------------------------------------------- styles


def _set_fonts(rpr_parent_font, rfonts_owner, font: str) -> None:
    """Set an explicit font and drop theme-font attributes, which would win over it."""
    rpr_parent_font.name = font
    rpr = rfonts_owner.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        rfonts.attrib.pop(qn(f"w:{attr}"), None)
    for attr in ("ascii", "hAnsi", "cs", "eastAsia"):
        rfonts.set(qn(f"w:{attr}"), font)


def _color(style, hex_rgb: str) -> None:
    style.font.color.rgb = RGBColor.from_string(hex_rgb)
    color = style.element.rPr.find(qn("w:color"))
    for attr in ("themeColor", "themeShade", "themeTint"):
        color.attrib.pop(qn(f"w:{attr}"), None)


def _add_style(document, name: str, style_id: str, kind):
    style = document.styles.add_style(name, kind)
    style.element.set(qn("w:styleId"), style_id)
    return style


def _prepare_styles(document, theme: dict[str, Any]) -> None:
    font, color, sizes = theme["font"], theme["heading_color"], theme["heading_sizes"]
    styles = document.styles
    # docDefaults use theme fonts; replace them so the snapshot font applies everywhere.
    defaults = document.styles.element.find(qn("w:docDefaults"))
    if defaults is not None:
        rfonts = defaults.find(f"{qn('w:rPrDefault')}/{qn('w:rPr')}/{qn('w:rFonts')}")
        if rfonts is not None:
            for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
                rfonts.attrib.pop(qn(f"w:{attr}"), None)
            for attr in ("ascii", "hAnsi", "cs", "eastAsia"):
                rfonts.set(qn(f"w:{attr}"), font)
    normal = styles["Normal"]
    _set_fonts(normal.font, normal.element, font)
    normal.font.size = Pt(BODY_SIZE)
    for name, key in (("Title", "title"), ("Heading 1", "1"), ("Heading 2", "2"), ("Heading 3", "3")):
        st = styles[name]
        _set_fonts(st.font, st.element, font)
        st.font.size = Pt(sizes[key])
        _color(st, color)
    sub = styles["Subtitle"]
    _set_fonts(sub.font, sub.element, font)
    styles["Title"].paragraph_format.space_before = Inches(2)
    for name in ("TOC Heading", "Caption"):
        st = styles[name]
        _set_fonts(st.font, st.element, font)
        _color(st, color)

    text = _add_style(document, "footnote text", "FootnoteText", WD_STYLE_TYPE.PARAGRAPH)
    text.base_style = normal
    text.font.size = Pt(8)
    text.paragraph_format.space_after = Pt(0)
    ref = _add_style(document, "footnote reference", "FootnoteReference", WD_STYLE_TYPE.CHARACTER)
    ref.font.superscript = True
    for level in (1, 2):
        toc = _add_style(document, f"toc {level}", f"TOC{level}", WD_STYLE_TYPE.PARAGRAPH)
        toc.base_style = normal
        toc.paragraph_format.left_indent = Inches(0.25 * (level - 1))
        toc.paragraph_format.space_after = Pt(3)
        toc.paragraph_format.tab_stops.add_tab_stop(Inches(TEXT_WIDTH_INCHES), WD_TAB_ALIGNMENT.RIGHT,
                                                    WD_TAB_LEADER.DOTS)


# --------------------------------------------------------------------- body


def cell_text(cell: Cell) -> str:
    """What a table cell says in the DOCX. Unverified states are words, never a bare value."""
    value = cell.value if cell.value is not None else "Not specified"
    if cell.edited:
        return f"{value} (edited)"
    if cell.state == EvidenceState.SCAN_READ:
        return f"{value} (Read from scan, unverified)"
    if cell.state == EvidenceState.CONFLICTING:
        listed = _conflict_list(cell)
        return "Conflicting values: " + listed if listed else "Conflicting values"
    if cell.state == EvidenceState.NOT_FOUND:
        return "Couldn't verify"
    return value


class _Writer:
    def __init__(self, data: ExportData) -> None:
        self.data = data
        self.theme = resolve_theme(data.theme)
        self.doc = Document()
        self.notes = _Footnotes()
        self.front = front_matter(data.content)
        self._page_setup()
        _prepare_styles(self.doc, self.theme)
        _prepare_settings(self.doc)
        self._core_properties()

    # ---- setup

    def _page_setup(self) -> None:
        sec = self.doc.sections[0]
        sec.orientation = WD_ORIENT.PORTRAIT
        sec.page_width, sec.page_height = Inches(8.5), Inches(11)
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            setattr(sec, side, Inches(0.75))

    def _core_properties(self) -> None:
        cp = self.doc.core_properties
        when = self.data.created_at or _FIXED_TIME
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        when = when.astimezone(UTC).replace(tzinfo=None, microsecond=0)
        cp.title = clean(self.data.title)
        cp.author = "BrokerDocs (local)"
        cp.last_modified_by = "BrokerDocs (local)"
        cp.comments = f"Run {self.data.run_id}, version {self.data.version}"
        cp.revision = 1
        cp.created = cp.modified = when

    # ---- small writers

    def segments(self, paragraph, segments: list[Segment]) -> None:
        for i, seg in enumerate(segments):
            text = clean(seg.text)
            paragraph.add_run((" " if i and text else "") + text)
            note = _source_note(self.data, seg.passage_ids)
            if note:
                self.notes.add(paragraph, "Source " + note)

    def page_break(self) -> None:
        self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---- cover and TOC

    def cover(self) -> None:
        cover = self.front.cover
        title = first_heading(cover) or self.data.title
        self.doc.add_paragraph(clean(title), style="Title")
        used_title = False
        for block in cover.blocks if cover else []:
            if isinstance(block, HeadingBlock):
                if not used_title and block.text == title:
                    used_title = True
                    continue
                self.doc.add_paragraph(clean(block.text), style="Subtitle")
            elif isinstance(block, ParagraphBlock):
                self.segments(self.doc.add_paragraph(), block.segments)
            elif isinstance(block, ListBlock):
                for item in block.items:
                    self.segments(self.doc.add_paragraph(), item)
        meta = self.doc.add_paragraph()
        run = meta.add_run(f"Run {self.data.run_id}, version {self.data.version}")
        run.font.size = Pt(8)
        self.page_break()

    def toc_entries(self) -> list[tuple[int, str]]:
        """What Word's own TOC update produces: the section headings, then Sources."""
        entries = [(1 if s.level <= 1 else 2, clean(f"{s.number} {s.title}".strip())) for s in self.front.body]
        return [*entries, (1, SOURCES_HEADING)]

    def toc(self) -> None:
        heading = first_heading(self.front.toc) or (self.front.toc.title if self.front.toc else "") or "Contents"
        self.doc.add_paragraph(clean(heading), style="TOC Heading")
        entries = self.toc_entries()
        paragraphs = [self.doc.add_paragraph(style=f"toc {level}") for level, _ in entries]
        first, last = paragraphs[0], paragraphs[-1]
        self._fld(first, "begin")
        instr = first.add_run()._r
        it = OxmlElement("w:instrText")
        it.set(qn("xml:space"), "preserve")
        it.text = ' TOC \\o "1-2" \\h \\z \\u '
        instr.append(it)
        self._fld(first, "separate")
        for p, (_, text) in zip(paragraphs, entries, strict=True):
            p.add_run(text)
        self._fld(last, "end")
        self.page_break()

    @staticmethod
    def _fld(paragraph, kind: str) -> None:
        fld = OxmlElement("w:fldChar")
        fld.set(qn("w:fldCharType"), kind)
        paragraph.add_run()._r.append(fld)

    # ---- sections

    def notices(self) -> None:
        for n in self.data.content.notices:
            p = self.doc.add_paragraph()
            label = p.add_run("Notice: ")
            label.bold = True
            p.add_run(clean(n.message))

    def section(self, section: Section) -> None:
        self.doc.add_paragraph(clean(f"{section.number} {section.title}".strip()),
                               style="Heading 1" if section.level <= 1 else "Heading 2")
        if section.stale:  # its own paragraph, so the tag stays out of the TOC
            tag = self.doc.add_paragraph().add_run("This section may be out of date: it cites an edited value.")
            tag.italic = True
            tag.font.size = Pt(8.5)
        for block in section.blocks:
            if isinstance(block, HeadingBlock):
                self.doc.add_paragraph(clean(block.text), style="Heading 3")
            elif isinstance(block, ParagraphBlock):
                self.segments(self.doc.add_paragraph(), block.segments)
            elif isinstance(block, ListBlock):
                if block.title:
                    self.doc.add_paragraph().add_run(clean(block.title)).bold = True
                for item in block.items:
                    self.segments(self.doc.add_paragraph(style="List Bullet"), item)
            elif isinstance(block, IssueBlock):
                p = self.doc.add_paragraph()
                p.add_run(clean(f"{block.code} {block.title}")).bold = True
                meta = ", ".join(x for x in (block.severity, block.category) if x)
                if meta:
                    p.add_run(clean(f" ({meta})"))
                if block.impact:
                    self.segments(self.doc.add_paragraph(), block.impact)
            elif isinstance(block, TableBlock):
                self.table(block)

    def table(self, block: TableBlock) -> None:
        if block.title:
            cap = self.doc.add_paragraph(style="Caption")
            cap.add_run(clean(block.title))
        t = self.doc.add_table(rows=1 + len(block.rows), cols=1 + len(block.columns))
        t.style = self.doc.styles["Table Grid"]
        head = t.rows[0]
        tr_pr = head._tr.get_or_add_trPr()
        tbl_header = OxmlElement("w:tblHeader")
        tr_pr.append(tbl_header)
        self._cell(head.cells[0], "Field", bold=True, shade=True)
        for j, col in enumerate(block.columns, start=1):
            p = self._cell(head.cells[j], f"{col.key}: {col.label}", bold=True, shade=True)
            if col.warning:
                p.add_run().add_break()
                w = p.add_run(clean(col.warning))
                w.italic = True
                w.font.size = Pt(7.5)
        for i, row in enumerate(block.rows, start=1):
            cells = t.rows[i].cells
            self._cell(cells[0], row.label, bold=True)
            for j, col in enumerate(block.columns, start=1):
                cell = row.cells.get(col.key)
                if cell is None:
                    continue
                p = self._cell(cells[j], cell_text(cell))
                note = cell_note(self.data, cell)
                if note:
                    self.notes.add(p, note)
        self.doc.add_paragraph()

    @staticmethod
    def _cell(cell, text: str, *, bold: bool = False, shade: bool = False):
        p = cell.paragraphs[0]
        run = p.add_run(clean(text))
        run.bold = bold
        run.font.size = Pt(TABLE_SIZE)
        if shade:
            tc_pr = cell._tc.get_or_add_tcPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), "F3F4F6")
            tc_pr.append(shd)
        return p

    def sources(self) -> None:
        self.doc.add_paragraph(SOURCES_HEADING, style="Heading 1")
        chips = sorted(self.data.chips.values(), key=lambda c: (c["letter"], c["page_no"]))
        if not chips:
            self.doc.add_paragraph("No sources are cited in this report.")
        for c in chips:
            p = self.doc.add_paragraph(style="List Bullet")
            p.add_run(clean(c["label"])).bold = True
            evidence = "Verified in the PDF text" if c["verified"] else "Read from scan, unverified"
            p.add_run(clean(f": {c['filename']} page {c['page_no']}. {evidence}"))

    # ---- whole document

    def build(self) -> bytes:
        self.cover()
        self.toc()
        self.notices()
        for section in self.front.body:
            self.section(section)
        self.sources()
        self.notes.attach(self.doc)
        buf = io.BytesIO()
        self.doc.save(buf)
        return _normalize_zip(buf.getvalue())


def _normalize_zip(blob: bytes) -> bytes:
    """Same entries, same order, fixed timestamps and attributes: byte-identical re-exports."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            z = zipfile.ZipInfo(info.filename, date_time=_ZIP_TIME)
            z.compress_type = zipfile.ZIP_DEFLATED
            z.external_attr = 0o600 << 16
            z.create_system = 0
            dst.writestr(z, src.read(info.filename))
    return out.getvalue()


def to_docx(data: ExportData) -> bytes:
    return _Writer(data).build()


__all__ = ["DEFAULT_THEME", "DOCX_MIME", "cell_text", "resolve_theme", "to_docx"]
