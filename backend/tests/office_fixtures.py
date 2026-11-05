"""Synthetic Word and Excel quotes for the slice 6 intake tests (built in-test, small, deterministic content)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Sequence

import docx
import openpyxl
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

LONG_AI = ("Blanket additional insured where required by written contract, including ongoing and completed "
           "operations, for any person or organization the named insured has agreed in writing to add before loss")


def docx_bytes(paragraphs: Sequence[str] = (), table: Sequence[Sequence[str]] | None = None, *,
               heading: str | None = None, after: Sequence[str] = (),
               edit: Callable[[docx.document.Document], None] | None = None) -> bytes:
    d = docx.Document()
    if heading:
        d.add_heading(heading, 1)
    for text in paragraphs:
        d.add_paragraph(text)
    if table:
        t = d.add_table(rows=len(table), cols=max(len(r) for r in table))
        for i, row in enumerate(table):
            for j, text in enumerate(row):
                t.cell(i, j).text = text
    for text in after:
        d.add_paragraph(text)
    if edit is not None:
        edit(d)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def tracked_paragraph(d, kept: str, inserted: str, deleted: str) -> None:
    """A paragraph with a tracked insertion and a tracked deletion: "<kept><inserted>" once accepted."""
    p = d.add_paragraph(kept)
    ins = OxmlElement("w:ins")
    ins.set(qn("w:id"), "1")
    ins.set(qn("w:author"), "Underwriter")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = inserted
    r.append(t)
    ins.append(r)
    p._p.append(ins)
    dele = OxmlElement("w:del")
    dele.set(qn("w:id"), "2")
    dele.set(qn("w:author"), "Underwriter")
    r2 = OxmlElement("w:r")
    dt = OxmlElement("w:delText")
    dt.text = deleted
    r2.append(dt)
    dele.append(r2)
    p._p.append(dele)


def xlsx_bytes(sheets: dict[str, Sequence[Sequence[object]]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def rezip(data: bytes, *, add: dict[str, bytes] | None = None,
          replace: Callable[[str, bytes], bytes] | None = None, drop: Sequence[str] = ()) -> bytes:
    """A copy of an Office zip with entries added, rewritten or dropped."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for info in src.infolist():
            if info.filename in drop:
                continue
            body = src.read(info.filename)
            zf.writestr(info.filename, replace(info.filename, body) if replace else body)
        for name, body in (add or {}).items():
            zf.writestr(name, body)
    return out.getvalue()


QUOTE_DOCX_LINES = ["Carrier: Harborline Specialty", "Named Insured: Cedar Hollow LLC", "Total Premium: $26,391.14"]
QUOTE_XLSX_ROWS = [["Carrier: Juniper Peak"], ["Named Insured: Cedar Hollow LLC"], ["Total Premium: $14,490.00"],
                   ["Additional insured", LONG_AI]]
