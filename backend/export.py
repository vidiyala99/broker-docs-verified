"""Export one artifact version (spec section 4 step 7, T8).

XLSX holds the tables only (state table: "XLSX holds tables only"), one sheet
per table plus a Sources sheet mapping chip labels to file and page. Each
cell's evidence (source chips, edited with the original value, read from
scan, couldn't verify) goes in a cell comment so the grid stays clean.
Document-derived strings are always written as text, never as formulas
(a value starting with "=" would otherwise run as a formula in Excel).

PDF holds the full report: notices, sections (with the "may be out of date"
tag), prose with chip labels, tables, and a Sources appendix. A proposal with
cover and TOC blocks (front_matter) gets a cover page and a contents page with
page numbers; reports without them render exactly as in slice 1.

DOCX lives in backend/export_docx.py and reuses cell_note and _source_note.

Policy Check discrepancies (slice 3): the PDF renders each one (title, severity, the
explanation and one row of role slots with chips, export_docx.slot_text wording); the XLSX
gets a Discrepancies sheet with one row per item, so the export holds the comparison too.

Both functions take plain data (`ExportData`), so they run in a worker thread.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.schemas import (
    KEY_DIFFERENCE_SECTION_ID,
    ArtifactContent,
    Cell,
    CertificateBlock,
    ClaimBlock,
    DiscrepancyBlock,
    DiscrepancyItemState,
    EvidenceState,
    PolicyRole,
    HeadingBlock,
    IssueBlock,
    ListBlock,
    ParagraphBlock,
    Section,
    Segment,
    TableBlock,
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MIME = "application/pdf"


@dataclass(frozen=True)
class ExportData:
    title: str
    run_id: int
    version: int
    content: ArtifactContent
    chips: dict[str, dict[str, Any]]  # views.chips shape, keyed by str(passage id)
    # Raw theme from the run's template snapshot (theme_from_parameters); DOCX only.
    theme: dict[str, Any] | None = None
    # The version's own timestamp: DOCX metadata, so a re-export stays byte-identical.
    created_at: datetime | None = None


# ----------------------------------------------------------------- template snapshot (D6)

# Lane A snapshots the resolved template into run.parameters when a proposal run
# is created (D6), so an old version re-exports with the theme it was made with.
# The exporter reads the first of these keys that holds a dict with a "theme"
# dict. Expected snapshot shape (every theme field optional; a missing or invalid
# field falls back to export_docx.DEFAULT_THEME field by field):
#
#   run.parameters["template"] = {
#       "slug": "standard", "blocks": [...], "variables": [...],
#       "theme": {
#           "font": "Calibri",                 # font family for body and headings
#           "heading_color": "#1F2A44",        # hex RGB, with or without "#"
#           "heading_sizes": {"title": 26, "1": 16, "2": 13, "3": 11},  # points;
#                                              # "h1"/"heading1" keys also accepted
#       },
#   }
TEMPLATE_SNAPSHOT_KEYS = ("template", "template_snapshot")


def theme_from_parameters(parameters: Any) -> dict[str, Any] | None:
    """The raw theme dict from a run's template snapshot, or None (slice 1 runs)."""
    if not isinstance(parameters, dict):
        return None
    for key in TEMPLATE_SNAPSHOT_KEYS:
        snap = parameters.get(key)
        if isinstance(snap, dict) and isinstance(snap.get("theme"), dict):
            return snap["theme"]
    return None


# ----------------------------------------------------------------- cover page and TOC

# Proposal artifacts carry the cover page and the table of contents as ordinary
# heading/paragraph/list blocks, flagged by id (spec section 3): a section whose
# id is "cover" or "toc", or a section holding a block with that id (or an id
# starting "cover-" / "toc-"). Slice 1 artifacts have neither, so their exports
# do not change.
COVER_ID = "cover"
TOC_ID = "toc"


def _front_kind(section: Section) -> str | None:
    for key in (COVER_ID, TOC_ID):
        if section.id == key or any(b.id == key or b.id.startswith(key + "-") for b in section.blocks):
            return key
    return None


@dataclass(frozen=True)
class FrontMatter:
    cover: Section | None
    toc: Section | None
    body: list[Section]


def front_matter(content: ArtifactContent) -> FrontMatter:
    cover = toc = None
    body: list[Section] = []
    for section in content.sections:
        if section.id == KEY_DIFFERENCE_SECTION_ID:
            continue  # slice 6: the Compare Key difference is a screen callout, not exported (spec 3.3)
        kind = _front_kind(section)
        if kind == COVER_ID and cover is None:
            cover = section
        elif kind == TOC_ID and toc is None:
            toc = section
        else:
            body.append(section)
    return FrontMatter(cover, toc, body)


def first_heading(section: Section | None) -> str | None:
    if section is None:
        return None
    return next((b.text for b in section.blocks if isinstance(b, HeadingBlock) and b.text.strip()), None)


def filename(data: ExportData, ext: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", data.title.lower()).strip("-") or "report"
    return f"{slug}-run-{data.run_id}-v{data.version}.{ext}"


def _labels(data: ExportData, ids: list[int]) -> list[str]:
    return [data.chips[str(i)]["label"] for i in ids if str(i) in data.chips]


def _source_note(data: ExportData, ids: list[int]) -> str:
    parts = []
    for i in ids:
        c = data.chips.get(str(i))
        if c:
            parts.append(f"{c['label']}: {c['filename']} page {c['page_no']}")
    return "; ".join(parts)


def _conflict_list(cell: Cell) -> str:
    """'$5,000 (page 20); $7,500 (page 90)' for a T14 conflicting cell."""
    return "; ".join(f"{c.value} (page {c.page_no})" if c.page_no is not None else f"{c.value}"
                     for c in cell.conflicts)


def cell_note(data: ExportData, cell: Cell) -> str:
    notes = []
    if cell.edited:
        orig = cell.original_value if cell.original_value is not None else "Not specified"
        notes.append(f"Edited by user. Original value: {orig}")
        if cell.original_passage_ids:
            notes.append("Original source " + _source_note(data, cell.original_passage_ids))
    elif cell.state == EvidenceState.SCAN_READ:
        notes.append("Read from scan, unverified")
    elif cell.state == EvidenceState.NOT_FOUND:
        notes.append("Couldn't verify: the model's value was not found in the document")
    elif cell.state == EvidenceState.CONFLICTING:
        notes.append("Conflicting values: " + _conflict_list(cell) + ". None is shown as verified")
    if cell.passage_ids:
        notes.append("Source " + _source_note(data, cell.passage_ids))
    return "\n".join(notes)


# --------------------------------------------------------------------- XLSX

_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")
_HEADER_FILL = PatternFill("solid", fgColor="F3F4F6")


def _sheet_name(raw: str, used: set[str]) -> str:
    base = _BAD_SHEET.sub(" ", raw).strip().strip("'")[:31] or "Table"
    name, n = base, 2
    while name.lower() in used:
        suffix = f" ({n})"
        name = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(name.lower())
    return name


def _text(ws, row: int, col: int, value: Any, *, bold: bool = False):
    """Write a value as a string cell. Never as a formula, whatever it starts with."""
    cell = ws.cell(row=row, column=col)
    cell.value = "" if value is None else str(value)
    cell.data_type = "s"
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    if bold:
        cell.font = Font(bold=True)
    return cell


def to_xlsx(data: ExportData) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    tables = [(section, block) for section, block in data.content.iter_blocks() if isinstance(block, TableBlock)]
    for section, table in tables:
        ws = wb.create_sheet(_sheet_name(f"{section.number} {table.title or section.title}", used))
        _text(ws, 1, 1, f"{data.title}: {section.number} {table.title or section.title} (version {data.version})",
              bold=True)
        _text(ws, 2, 1, "Field", bold=True).fill = _HEADER_FILL
        for j, col in enumerate(table.columns, start=2):
            _text(ws, 2, j, f"{col.key}: {col.label}", bold=True).fill = _HEADER_FILL
            if col.warning:
                ws.cell(row=2, column=j).comment = Comment(col.warning, "BrokerDocs")
        for i, row in enumerate(table.rows, start=3):
            _text(ws, i, 1, row.label, bold=True)
            for j, col in enumerate(table.columns, start=2):
                cell = row.cells.get(col.key)
                if cell is None:
                    continue
                shown = cell.value if cell.value is not None else "Not specified"
                x = _text(ws, i, j, shown + (" (edited)" if cell.edited else ""))
                note = cell_note(data, cell)
                if note:
                    x.comment = Comment(note, "BrokerDocs", width=320, height=120)
        ws.freeze_panes = "B3"
        ws.column_dimensions["A"].width = 26
        for j in range(2, len(table.columns) + 2):
            ws.column_dimensions[get_column_letter(j)].width = 30
    src = wb.create_sheet(_sheet_name("Sources", used))
    for j, head in enumerate(("Chip", "Document", "File", "Page", "Evidence"), start=1):
        _text(src, 1, j, head, bold=True).fill = _HEADER_FILL
    for i, c in enumerate(sorted(data.chips.values(), key=lambda c: (c["letter"], c["page_no"])), start=2):
        _text(src, i, 1, c["label"])
        _text(src, i, 2, c["letter"])
        _text(src, i, 3, c["filename"])
        src.cell(row=i, column=4, value=int(c["page_no"]))
        _text(src, i, 5, "Verified in the PDF text" if c["verified"] else "Read from scan, unverified")
    for col, width in zip("ABCDE", (8, 10, 40, 8, 28)):
        src.column_dimensions[col].width = width
    discrepancies = [b for _, b in data.content.iter_blocks() if isinstance(b, DiscrepancyBlock)]
    if discrepancies:
        _discrepancy_sheet(wb.create_sheet(_sheet_name("Discrepancies", used), 0), data, discrepancies)
    elif not tables:
        ws = wb.create_sheet(_sheet_name("Report", used), 0)
        _text(ws, 1, 1, "This report has no tables.")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _discrepancy_sheet(ws, data: ExportData, blocks: list[DiscrepancyBlock]) -> None:
    from backend.export_docx import slot_text  # export_docx imports this module

    heads = ["Severity", "Discrepancy", *(r.label for r in PolicyRole), "Explanation"]
    _text(ws, 1, 1, f"{data.title}: Discrepancies (version {data.version})", bold=True)
    for j, head in enumerate(heads, start=1):
        _text(ws, 2, j, head, bold=True).fill = _HEADER_FILL
    for i, b in enumerate(blocks, start=3):
        review = " (needs review)" if b.item_state == DiscrepancyItemState.NEEDS_REVIEW else ""
        _text(ws, i, 1, b.severity.label)
        _text(ws, i, 2, b.title + review)
        for j, role in enumerate(PolicyRole, start=3):
            cell = b.slot(role)
            x = _text(ws, i, j, slot_text(cell))
            note = _source_note(data, cell.passage_ids) if cell is not None else ""
            if note:
                x.comment = Comment(note, "BrokerDocs", width=320, height=120)
        _text(ws, i, len(heads), " ".join(seg.text for seg in b.prose))
    ws.freeze_panes = "C3"
    for j, width in enumerate((10, 40, 24, 24, 24, 24, 60), start=1):
        ws.column_dimensions[get_column_letter(j)].width = width


# --------------------------------------------------------------------- PDF


def _seg_markup(data: ExportData, segments: list[Segment]) -> str:
    out = []
    for s in segments:
        labels = _labels(data, s.passage_ids)
        chip = f' <font color="#6356F0" size="7">[{", ".join(labels)}]</font>' if labels else ""
        out.append(escape(s.text) + chip)
    return " ".join(out)


def _cell_markup(data: ExportData, cell: Cell) -> str:
    value = escape(cell.value if cell.value is not None else "Not specified")
    tags = []
    if cell.edited:
        orig = cell.original_value if cell.original_value is not None else "Not specified"
        tags.append(f"edited; original: {escape(orig)}")
    elif cell.state == EvidenceState.SCAN_READ:
        tags.append("read from scan, unverified")
    elif cell.state == EvidenceState.CONFLICTING:
        tags.append("conflicting values: " + escape(_conflict_list(cell)))
    labels = _labels(data, cell.passage_ids or cell.original_passage_ids)
    chip = f' <font color="#6356F0" size="6">[{", ".join(labels)}]</font>' if labels else ""
    tag = f'<br/><font color="#4B5563" size="6">({"; ".join(tags)})</font>' if tags else ""
    return value + chip + tag


def to_pdf(data: ExportData) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12,
                          textColor=colors.HexColor("#35383B"))
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9.5)
    head1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=16,
                           textColor=colors.HexColor("#35383B"))
    head2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12,
                           textColor=colors.HexColor("#35383B"), spaceBefore=10)
    head3 = ParagraphStyle("h3", parent=head2, fontSize=10.5)
    notice = ParagraphStyle("notice", parent=body, backColor=colors.HexColor("#F3F4F6"), borderPadding=4)

    class _Doc(SimpleDocTemplate):
        def afterFlowable(self, flowable):  # feeds the contents page (proposals only)
            entry = getattr(flowable, "_toc_entry", None)
            if entry is not None:
                self.notify("TOCEntry", (entry[0], entry[1], self.page))

    def toc_mark(flowable, level: int, text: str):
        if fm.toc is not None:
            flowable._toc_entry = (level, escape(text))
        return flowable

    fm = front_matter(data.content)
    pagesize = landscape(letter)
    buf = io.BytesIO()
    doc = _Doc(buf, pagesize=pagesize, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
               topMargin=0.6 * inch, bottomMargin=0.6 * inch, title=data.title,
               author="BrokerDocs (local)")
    width = pagesize[0] - 1.2 * inch
    if fm.cover is None and fm.toc is None:  # slice 1 reports: unchanged
        story: list[Any] = [Paragraph(escape(data.title), head1),
                            Paragraph(f"Run {data.run_id}, version {data.version}", small), Spacer(1, 6)]
    else:
        story = _pdf_front_pages(data, fm, styles, body, small, head1)
    for n in data.content.notices:
        story += [Paragraph("Notice: " + escape(n.message), notice), Spacer(1, 4)]
    for section in fm.body:
        plain = f"{section.number} {section.title}".strip()
        title = f"{escape(section.number)} {escape(section.title)}"
        if section.stale:
            title += ' <font color="#4B5563" size="8">(may be out of date)</font>'
        story.append(toc_mark(Paragraph(title, head2 if section.level <= 1 else head3),
                              0 if section.level <= 1 else 1, plain))
        for block in section.blocks:
            if isinstance(block, HeadingBlock):
                story.append(Paragraph(escape(block.text), head3))
            elif isinstance(block, ParagraphBlock):
                story.append(Paragraph(_seg_markup(data, block.segments), body))
            elif isinstance(block, ListBlock):
                if block.title:
                    story.append(Paragraph(f"<b>{escape(block.title)}</b>", body))
                for item in block.items:
                    story.append(Paragraph(_seg_markup(data, item), body, bulletText="•"))
            elif isinstance(block, IssueBlock):
                meta = ", ".join(x for x in (block.severity, block.category) if x)
                story.append(Paragraph(f"<b>{escape(block.code)} {escape(block.title)}</b>"
                                       + (f" ({escape(meta)})" if meta else ""), body))
                if block.impact:
                    story.append(Paragraph(_seg_markup(data, block.impact), body))
            elif isinstance(block, TableBlock):
                head = [Paragraph("<b>Field</b>", small)] + [
                    Paragraph(f"<b>{escape(c.key)}: {escape(c.label)}</b>"
                              + (f'<br/><font color="#4B5563" size="6">{escape(c.warning)}</font>'
                                 if c.warning else ""), small) for c in block.columns]
                rows = [head]
                for row in block.rows:
                    rows.append([Paragraph(f"<b>{escape(row.label)}</b>", small)] + [
                        Paragraph(_cell_markup(data, row.cells[c.key]) if c.key in row.cells else "", small)
                        for c in block.columns])
                first = 1.5 * inch
                rest = (width - first) / max(1, len(block.columns))
                t = Table(rows, colWidths=[first] + [rest] * len(block.columns), repeatRows=1)
                t.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]))
                story += [Spacer(1, 4), t, Spacer(1, 6)]
            elif isinstance(block, DiscrepancyBlock):
                story += _pdf_discrepancy(data, block, body, small, width)
            elif isinstance(block, ClaimBlock):  # slice 4: one certificate check
                story.append(Paragraph(f"<b>{escape(block.title)}</b> ({escape(str(block.verdict).replace('_', ' '))}): "
                                       + _seg_markup(data, block.prose), body))
            elif isinstance(block, CertificateBlock):  # slice 4: the printed values (issued PDF: own layout)
                for col, f in block.iter_fields():
                    story.append(Paragraph(f"<b>{escape(f.label)}</b> ({escape(col)}): {_cell_markup(data, f.cell)}",
                                           small))
    if data.chips:
        story.append(toc_mark(Paragraph("Sources", head2), 0, "Sources"))
        rows = [[Paragraph(f"<b>{h}</b>", small) for h in ("Chip", "File", "Page", "Evidence")]]
        for c in sorted(data.chips.values(), key=lambda c: (c["letter"], c["page_no"])):
            rows.append([Paragraph(escape(c["label"]), small), Paragraph(escape(c["filename"]), small),
                         Paragraph(str(c["page_no"]), small),
                         Paragraph("Verified in the PDF text" if c["verified"] else "Read from scan, unverified",
                                   small)])
        t = Table(rows, colWidths=[0.7 * inch, 4.5 * inch, 0.6 * inch, 2.4 * inch], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6"))]))
        story.append(t)
    if fm.toc is not None:
        doc.multiBuild(story)  # a second pass fills in the contents page numbers
    else:
        doc.build(story)
    return buf.getvalue()


def _pdf_discrepancy(data: ExportData, block: DiscrepancyBlock, body, small, width: float) -> list[Any]:
    """One Policy Check discrepancy: title with severity, explanation, a row of role slots."""
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    from backend.export_docx import slot_text  # export_docx imports this module

    review = ", needs review" if block.item_state == DiscrepancyItemState.NEEDS_REVIEW else ""
    out: list[Any] = [Paragraph(f"<b>{escape(block.title)}</b> ({escape(block.severity.label)}{review})", body)]
    if block.prose:
        out.append(Paragraph(_seg_markup(data, block.prose), body))
    roles = list(PolicyRole)
    cells = []
    for role in roles:
        cell = block.slot(role)
        labels = _labels(data, cell.passage_ids) if cell is not None else []
        chip = f' <font color="#6356F0" size="7">[{", ".join(labels)}]</font>' if labels else ""
        cells.append(Paragraph(escape(slot_text(cell)) + chip, small))
    t = Table([[Paragraph(f"<b>{escape(r.label)}</b>", small) for r in roles], cells],
              colWidths=[width / len(roles)] * len(roles))
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return [*out, Spacer(1, 2), t, Spacer(1, 8)]


def _pdf_front_pages(data: ExportData, fm: FrontMatter, styles, body, small, head1) -> list[Any]:
    """Cover page and contents page for a proposal (spec section 5)."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import PageBreak, Paragraph, Spacer
    from reportlab.platypus.tableofcontents import TableOfContents

    ink = colors.HexColor("#35383B")
    title_style = ParagraphStyle("cover_title", parent=head1, fontSize=26, leading=32, alignment=TA_CENTER)
    sub_style = ParagraphStyle("cover_sub", parent=body, fontSize=13, leading=17, alignment=TA_CENTER)
    line_style = ParagraphStyle("cover_line", parent=body, fontSize=11, leading=15, alignment=TA_CENTER)
    meta_style = ParagraphStyle("cover_meta", parent=small, alignment=TA_CENTER)
    story: list[Any] = []
    if fm.cover is not None:
        title = first_heading(fm.cover) or data.title
        story += [Spacer(1, 1.6 * inch), Paragraph(escape(title), title_style), Spacer(1, 18)]
        used_title = False
        for block in fm.cover.blocks:
            if isinstance(block, HeadingBlock):
                if not used_title and block.text == title:
                    used_title = True
                    continue
                story.append(Paragraph(escape(block.text), sub_style))
            elif isinstance(block, ParagraphBlock):
                story.append(Paragraph(_seg_markup(data, block.segments), line_style))
            elif isinstance(block, ListBlock):
                story += [Paragraph(_seg_markup(data, item), line_style) for item in block.items]
        story += [Spacer(1, 24), Paragraph(f"Run {data.run_id}, version {data.version}", meta_style), PageBreak()]
    else:
        story += [Paragraph(escape(data.title), head1),
                  Paragraph(f"Run {data.run_id}, version {data.version}", small), Spacer(1, 6)]
    if fm.toc is not None:
        toc = TableOfContents()
        toc.levelStyles = [
            ParagraphStyle("toc0", parent=body, fontSize=10.5, leading=16, textColor=ink),
            ParagraphStyle("toc1", parent=body, fontSize=9.5, leading=14, leftIndent=18, textColor=ink),
        ]
        toc.dotsMinLevel = 0
        heading = first_heading(fm.toc) or fm.toc.title or "Contents"
        story += [Paragraph(escape(heading), head1), Spacer(1, 8), toc, PageBreak()]
    return story


def table_count(content: ArtifactContent) -> int:
    return sum(1 for _, b in content.iter_blocks() if isinstance(b, TableBlock))


__all__ = ["COVER_ID", "TEMPLATE_SNAPSHOT_KEYS", "TOC_ID", "ExportData", "FrontMatter", "PDF_MIME", "XLSX_MIME",
           "cell_note", "filename", "first_heading", "front_matter", "table_count", "theme_from_parameters",
           "to_pdf", "to_xlsx"]
