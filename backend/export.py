"""Export one artifact version (spec section 4 step 7, T8).

XLSX holds the tables only (state table: "XLSX holds tables only"), one sheet
per table plus a Sources sheet mapping chip labels to file and page. Each
cell's evidence (source chips, edited with the original value, read from
scan, couldn't verify) goes in a cell comment so the grid stays clean.
Document-derived strings are always written as text, never as formulas
(a value starting with "=" would otherwise run as a formula in Excel).

PDF holds the full report: notices, sections (with the "may be out of date"
tag), prose with chip labels, tables, and a Sources appendix.

Both functions take plain data (`ExportData`), so they run in a worker thread.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.schemas import (
    ArtifactContent,
    Cell,
    EvidenceState,
    HeadingBlock,
    IssueBlock,
    ListBlock,
    ParagraphBlock,
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
    if not tables:
        ws = wb.create_sheet(_sheet_name("Report", used), 0)
        _text(ws, 1, 1, "This report has no tables.")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


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

    pagesize = landscape(letter)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch, title=data.title,
                            author="BrokerDocs (local)")
    width = pagesize[0] - 1.2 * inch
    story: list[Any] = [Paragraph(escape(data.title), head1),
                        Paragraph(f"Run {data.run_id}, version {data.version}", small), Spacer(1, 6)]
    for n in data.content.notices:
        story += [Paragraph("Notice: " + escape(n.message), notice), Spacer(1, 4)]
    for section in data.content.sections:
        title = f"{escape(section.number)} {escape(section.title)}"
        if section.stale:
            title += ' <font color="#4B5563" size="8">(may be out of date)</font>'
        story.append(Paragraph(title, head2 if section.level <= 1 else head3))
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
    if data.chips:
        story.append(Paragraph("Sources", head2))
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
    doc.build(story)
    return buf.getvalue()


def table_count(content: ArtifactContent) -> int:
    return sum(1 for _, b in content.iter_blocks() if isinstance(b, TableBlock))


__all__ = ["ExportData", "PDF_MIME", "XLSX_MIME", "cell_note", "filename", "table_count", "to_pdf", "to_xlsx"]
