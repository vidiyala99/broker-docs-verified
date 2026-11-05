"""Slice 6 T2 (R8): the wrapping sheet renderer for Excel quotes.

The loss-run `render_page` cuts every cell to one line, so a coverage wording in a quote summary could never be read,
cited or verified. Excel quotes get their own renderer: every cell's full text wrapped across lines and pages, the
header row repeated, the sheet name and row numbers printed. `parse_workbook` (its zip and XML caps) is reused with a
higher per-cell cap that refuses rather than cuts; a value beyond column 80 refuses the file too.
"""

from __future__ import annotations

import io

import openpyxl
import pytest

from api_support import api  # noqa: F401
from backend import office_docx, uploads
from backend.loss_runs import sheets
from backend.office_docx import OfficeError
from backend.pipeline import parse, verify
from backend.pipeline.parse import ParseError, ParseLimits
from backend.schemas import EvidenceState
from office_fixtures import LONG_AI, XLSX_CONTENT_TYPE, xlsx_bytes

LIMITS = ParseLimits()


def _render(data: bytes, name: str = "quote.xlsx", max_pages: int = 300) -> office_docx.RenderedOffice:
    office_docx.scan_package(data, "xlsx")
    return office_docx.render_workbook(office_docx.parse_quote_workbook(data, name), name, max_pages)


def _pages(pdf: bytes):
    return parse.parse_pdf(pdf).pages


def _verify(pages, value: str, quote: str, page: int | None = None):
    return verify.verify_fact(verify.CandidateFact("f", value, page, quote), pages)


def _inside(rects, place) -> bool:
    return bool(rects) and all(place.y0 - 0.5 <= r[1] and r[3] <= place.y1 + 0.5 for r in rects)


def test_long_cell_wraps_keeps_every_word_and_verifies_inside_its_row():
    data = xlsx_bytes({"Summary": [["Term", "Harborline", "Juniper"],
                                   ["Additional insured", LONG_AI, "Scheduled only"],
                                   ["Total premium", "$26,391.14", "$14,490.00"]]})
    out = _render(data)
    pages = _pages(out.pdf)
    words = [w for w in pages[0].words]
    lines = {round(w[1]) for w in words if w[4] in LONG_AI.split()}
    assert len(lines) >= 2  # wrapped, never cut with "..."
    assert "..." not in pages[0].text
    assert " ".join(LONG_AI.split()) in " ".join(pages[0].text.split())
    result = _verify(pages, "Blanket", LONG_AI, page=1)
    assert result.state == EvidenceState.VERIFIED and result.page_no == 1
    [place] = out.rows[(0, 2)]
    assert place.page_no == 1 and _inside(result.rects, place)
    label = _verify(pages, "$26,391.14", "Total premium $26,391.14")  # a label cell and the value cell beside it
    assert label.state == EvidenceState.VERIFIED and _inside(label.rects, out.rows[(0, 3)][0])


def test_a_citation_lands_on_the_right_page_and_row():
    rows = [["Location", "Building limit", "Notes"]]
    rows += [[f"Location {i}", f"${i * 1000:,}", f"Sprinklered, masonry non-combustible, built {1950 + i}"]
             for i in range(1, 151)]
    out = _render(xlsx_bytes({"Schedule": rows}))
    assert out.page_count >= 3
    pages = _pages(out.pdf)
    for row_no in (5, 77, 140):
        [place] = out.rows[(0, row_no)]
        quote = f"Location {row_no - 1} ${(row_no - 1) * 1000:,}"
        result = _verify(pages, f"${(row_no - 1) * 1000:,}", quote, page=place.page_no)
        assert result.state == EvidenceState.VERIFIED and result.page_no == place.page_no, row_no
        assert _inside(result.rects, place), (row_no, result.rects, place)
        gutter = [w for w in pages[place.page_no - 1].words if w[4] == str(row_no) and w[0] < 70]
        assert gutter and place.y0 <= gutter[0][1] <= place.y1  # the printed row number sits on the row


def test_every_page_prints_the_sheet_and_repeats_the_header_row():
    rows = [["Coverage", "Limit"]] + [[f"Item {i}", f"${i},000"] for i in range(120)]
    out = _render(xlsx_bytes({"Limits": rows, "Notes": [["Subjectivities"], ["Signed application"]]}))
    pages = _pages(out.pdf)
    assert len(pages) == out.page_count >= 3
    for p in pages[:-1]:
        assert p.text.startswith("quote.xlsx\nSheet Limits, rows ")
        assert "Coverage" in p.text and "Limit" in p.text  # header on every page of the sheet
    assert pages[1].text.split("\n")[1] != "Sheet Limits, rows 1 to 1"
    assert "rows 1 to" not in pages[1].text.split("\n")[1]  # a repeated header is not one of the page's rows
    assert pages[-1].text.startswith("quote.xlsx\nSheet Notes, rows 1 to 2")  # each sheet opens its own page


def test_a_row_taller_than_a_page_continues_on_the_next():
    words = " ".join(f"c{i}" for i in range(400))  # in one of 20 narrow columns: taller than a page
    out = _render(xlsx_bytes({"Q": [[f"Carrier {k}" for k in range(20)], [words] + [f"v{k}" for k in range(19)]]}))
    places = out.rows[(0, 2)]
    assert [p.page_no for p in places][:2] == [1, 2]
    text = " ".join(" ".join(p.text.split()) for p in _pages(out.pdf))
    assert all(f"c{i}" in text.split() for i in (0, 200, 399))


def test_merged_values_are_drawn_once():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Harborline Specialty quote summary", None, None])
    ws.merge_cells("A1:C1")
    ws.append(["Term", "Value", "Notes"])
    buf = io.BytesIO()
    wb.save(buf)
    text = _pages(_render(buf.getvalue()).pdf)[0].text
    assert text.count("Harborline Specialty quote summary") == 1


def test_a_cell_over_the_cap_refuses_the_file_plainly():
    cap = office_docx.DEFAULT_OFFICE_LIMITS.max_cell_chars
    assert cap > sheets.DEFAULT_SHEET_LIMITS.max_cell_chars  # higher than the loss-run cap (R8)
    ok = _render(xlsx_bytes({"Q": [["Wording", "w" * 10 + " " + "x" * (cap - 20)]]}))
    assert ok.page_count == 1
    with pytest.raises(OfficeError) as exc:
        uploads.prepare_office(xlsx_bytes({"Q": [["Wording", "y" * (cap + 1)]]}), "q.xlsx", LIMITS)
    assert exc.value.code == "cell_too_long" and exc.value.limit == cap
    assert f"{cap:,} characters" in exc.value.message


def test_a_value_beyond_column_80_refuses_the_file():
    row = [None] * 80 + ["late value"]
    with pytest.raises(OfficeError) as exc:
        uploads.prepare_office(xlsx_bytes({"Wide": [["Term"], row]}), "q.xlsx", LIMITS)
    assert exc.value.code == "too_many_columns" and exc.value.limit == 80 and "Wide" in exc.value.message


def test_loss_run_parsing_keeps_cutting_as_before():
    long = "z" * 500
    book = sheets.parse_workbook(xlsx_bytes({"a": [["Claim Number", "Claimant", "Date of Loss"], ["1", long, None],
                                                    ["y"] + [None] * 84 + ["x"]]}), "run.xlsx")
    rows = book.sheets[0].rows
    assert rows[1][1][1] == "z" * sheets.DEFAULT_SHEET_LIMITS.max_cell_chars
    assert len(rows[2][1]) <= sheets.DEFAULT_SHEET_LIMITS.max_columns


def test_page_count_is_checked_before_any_page_is_drawn(monkeypatch):
    drawn = []
    monkeypatch.setattr(office_docx, "_draw", lambda *a: drawn.append(a) or b"")
    rows = [["Term", "Wording"]] + [[f"T{i}", "long wording " * 30] for i in range(60)]
    with pytest.raises(ParseError) as exc:
        uploads.prepare_office(xlsx_bytes({"Q": rows}), "q.xlsx", ParseLimits(max_pages=3))
    assert exc.value.code == parse.ParseErrorCode.TOO_MANY_PAGES and exc.value.limit == 3
    assert drawn == []


def test_a_sheet_too_long_for_the_page_cap_is_refused_before_measuring(monkeypatch):
    rows = [[f"Row {i}"] for i in range(2000)]
    book = office_docx.parse_quote_workbook(xlsx_bytes({"Big": rows}), "big.xlsx")
    assert office_docx.min_sheet_pages(book) > 20

    def no_measuring(*a, **k):
        raise AssertionError("wrapped a cell before the page bound was checked")

    monkeypatch.setattr(office_docx, "wrap", no_measuring)
    with pytest.raises(ParseError) as exc:
        office_docx.render_workbook(book, "big.xlsx", 20)
    assert exc.value.code == parse.ParseErrorCode.TOO_MANY_PAGES


def test_rendering_is_deterministic():
    data = xlsx_bytes({"Q": [["Term", "Value"], ["Additional insured", LONG_AI]]})
    assert _render(data).pdf == _render(data).pdf


def test_api_xlsx_over_the_cell_cap_is_an_inline_refusal(api):
    data = xlsx_bytes({"Q": [["Wording", "y" * (office_docx.DEFAULT_OFFICE_LIMITS.max_cell_chars + 1)]]})
    r = api.client.post("/api/documents", files=[("files", ("q.xlsx", data, XLSX_CONTENT_TYPE))])
    assert r.status_code == 200
    assert [x["code"] for x in r.json()["rejected"]] == ["cell_too_long"]
