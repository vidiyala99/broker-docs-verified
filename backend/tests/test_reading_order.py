"""Extraction text for two-column tables (labels in one column, values in another).

Live eval 2026-11-02 (policy check, pc_homeowners renewal): the text layer of a two-column
schedule lists all twelve values, then all twelve labels. The model had to pair them by
position; it quoted "$360,000\\nCoverage A (Dwelling)" (no token run of the page in any order,
so every limit came out "Couldn't verify" and the diff said Needs review), and it shifted the
deductibles by one (wind/hail "$2,500", the all-peril value, with a label-less quote that
verified). The extraction text now reads such a table row by row, as printed, so the label
and its value sit on one line and a label-and-value quote is a token run of verify's visual
row stream.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.pipeline import extract, parse
from backend.pipeline.verify import CandidateFact, verify_fact
from backend.schemas import EvidenceState

ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
RENEWAL = ROOT / "policy" / "pc_homeowners_renewal.pdf"
CONDO_EXPIRING = ROOT / "policy" / "pc_condo_injection_expiring.pdf"
CARRIER_A = ROOT / "quotes" / "carrier_a_multicol.pdf"


def _pages(path: Path):
    return parse.parse_pdf(path.read_bytes()).pages


def _tokens(text: str) -> Counter:
    return Counter(text.split())


RENEWAL_ROWS = [
    ("Coverage A (Dwelling)", "$360,000"),
    ("Coverage B (Other Structures)", "$40,000"),
    ("Other Structures Rented to Others endorsement limit", "$30,000"),
    ("Coverage C (Personal Property)", "$170,000"),
    ("Coverage D (Loss of Use)", "$80,000"),
    ("Coverage E (Personal Liability)", "$300,000"),
    ("Coverage F (Medical Payments)", "$5,000"),
    ("All Other Perils Deductible", "$2,500"),
    ("Wind/Hail Deductible", "2%"),
    ("Total Premium", "$2,412.00"),
    ("Multi-Policy Discount", "10%"),
    ("Protective Device Discount", "3%"),
]


def test_two_column_schedule_reads_row_by_row_with_each_label_next_to_its_value():
    page = _pages(RENEWAL)[1]
    text = extract.page_text(page)
    lines = text.splitlines()
    for label, value in RENEWAL_ROWS:
        assert f"{label} {value}" in lines, (label, value, text)
    # the rows keep the printed order, and nothing is lost or added
    assert [ln for ln in lines if any(ln == f"{a} {b}" for a, b in RENEWAL_ROWS)] == \
        [f"{a} {b}" for a, b in RENEWAL_ROWS]
    assert _tokens(text) == _tokens(page.text)
    assert lines[2] == "LIMITS, DEDUCTIBLES, PREMIUM AND DISCOUNTS"  # text before the table stays first
    assert lines[-1].startswith("renewal is removed")  # and text after it stays last


@pytest.mark.parametrize("label, value", RENEWAL_ROWS)
def test_every_row_of_the_reordered_table_verifies_as_a_label_and_value_quote(label, value):
    pages = _pages(RENEWAL)
    r = verify_fact(CandidateFact("f", value, 2, f"{label} {value}"), pages)
    assert r.state == EvidenceState.VERIFIED and r.page_no == 2, r


def test_a_label_never_verifies_with_a_neighbouring_rows_value():
    """The failure of the live run: the wind/hail label with the all-peril value."""
    pages = _pages(RENEWAL)
    r = verify_fact(CandidateFact("deductible_wind_hail", "$2,500", 2, "Wind/Hail Deductible $2,500"), pages)
    assert not r.cited


def test_side_by_side_label_value_blocks_are_left_alone():
    """Page 1 prints two columns of label-over-value fields (POLICY NUMBER over its value,
    NAMED INSURED over its value). Their text-layer order already reads correctly, and a
    row-by-row read would pair two labels; the page text is unchanged."""
    page = _pages(RENEWAL)[0]
    assert extract.page_text(page) == page.text


@pytest.mark.parametrize("path, page_no", [(RENEWAL, 3), (CARRIER_A, 1), (CARRIER_A, 3)])
def test_pages_without_a_value_column_are_unchanged(path, page_no):
    page = _pages(path)[page_no - 1]
    assert extract.page_text(page) == page.text


def test_compare_quotes_multicol_schedule_reads_row_by_row_too():
    page = _pages(CARRIER_A)[1]
    lines = extract.page_text(page).splitlines()
    for row in ("Annual Premium $25,075.00", "Taxes and Fees $1,316.14", "Total Payable $26,391.14",
                "Rate $5.466 per $1,000 of gross sales", "Each Occurrence Limit $1,000,000",
                "General Aggregate Limit $2,000,000", "Deductible $2,500"):
        assert row in lines
    assert _tokens("\n".join(lines)) == _tokens(page.text)


def test_condo_expiring_two_column_schedule_reads_row_by_row():
    page = _pages(CONDO_EXPIRING)[1]
    lines = extract.page_text(page).splitlines()
    assert "Coverage A (Dwelling) $60,000" in lines
    assert "All Other Perils Deductible $500" in lines


def test_extraction_prompt_carries_the_row_by_row_text():
    pages = _pages(RENEWAL)
    msgs = extract.build_extraction_messages([], pages, document_name="r.pdf", boundary="B0")
    user = msgs[1].content if isinstance(msgs[1].content, str) else str(msgs[1].content)
    assert "Coverage A (Dwelling) $360,000" in user
    assert "Wind/Hail Deductible 2%" in user


# --------------------------------------------------------------------- synthetic edge cases


@dataclass
class _P:
    page_no: int
    text: str
    words: list | None
    ocr_text: str | None = None


def _page(columns: list[tuple], *, step: float = 16, offsets: list[float] | None = None) -> _P:
    """One single-line block per cell, columns (x, cells[, first y]) in text-layer order (as
    the generator prints them). offsets shift the cells of the x > 100 column."""
    words, text, b = [], [], 0
    for col in columns:
        x, cells, y0 = (*col, 140)[:3]
        for i, s in enumerate(cells):
            y = y0 + step * i + ((offsets or [0] * len(cells))[i] if x > 100 else 0)
            for wn, w in enumerate(s.split()):
                words.append([x + 40 * wn, y, x + 40 * wn + 35, y + 12, w, b, 0, wn])
            text.append(s)
            b += 1
    return _P(1, "\n".join(text) + "\n", words)


def test_values_listed_after_their_labels_also_read_row_by_row():
    p = _page([(72, ["Total Premium", "Deductible"]), (330, ["$1,000", "$500"])])
    assert extract.page_text(p).splitlines() == ["Total Premium $1,000", "Deductible $500"]


def test_value_column_must_align_row_for_row_or_nothing_moves():
    p = _page([(330, ["$1,000", "$500"]), (72, ["Total Premium", "Deductible"])], offsets=[0, 9])
    assert extract.page_text(p) == p.text


def test_a_column_of_words_without_digits_is_not_treated_as_values():
    p = _page([(330, ["Included", "Excluded"]), (72, ["Flood", "Earthquake"])])
    assert extract.page_text(p) == p.text


def test_a_single_aligned_row_is_left_alone():
    p = _page([(330, ["$1,000"]), (72, ["Total Premium"])])
    assert extract.page_text(p) == p.text


def test_words_as_json_lists_and_missing_words_are_handled():
    p = _page([(330, ["$1,000", "$500"]), (72, ["Total Premium", "Deductible"])])
    assert extract.page_text(p).splitlines() == ["Total Premium $1,000", "Deductible $500"]
    assert extract.page_text(_P(1, "Total Premium $1,000", None)) == "Total Premium $1,000"
    assert extract.page_text(_P(1, "", [], ocr_text="OCR text")) == "OCR text"


def test_ocr_text_still_wins_over_a_reordered_text_layer():
    p = _page([(330, ["$1,000", "$500"]), (72, ["Total Premium", "Deductible"])])
    p.ocr_text = "scanned"
    assert extract.page_text(p) == "scanned"


def test_reordered_text_never_invents_or_drops_a_token():
    p = _page([(72, ["Schedule"], 100), (330, ["$1,000", "$500", "2%"]),
               (72, ["Total Premium", "Deductible", "Wind/Hail"]), (72, ["Footer text here"], 300)])
    out = extract.page_text(p)
    assert _tokens(out) == _tokens(p.text)
    assert re.search(r"^Wind/Hail 2%$", out, re.M)
