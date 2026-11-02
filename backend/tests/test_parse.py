"""T3: parse with OCR triggers (sparse text, image-heavy) and caps."""

from __future__ import annotations

import pytest

from backend.pipeline import parse
from backend.pipeline.parse import ParseError, ParseErrorCode, ParseLimits

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
LONG = " ".join(["Commercial general liability coverage is provided."] * 16)  # > 600 chars


def test_digital_pdf_text_and_word_boxes(pdf_factory):
    data = pdf_factory(["Named Insured: Acme Widgets LLC", "Policy Term: 12 months"])
    doc = parse.parse_pdf(data)
    assert doc.page_count == 2
    assert [p.page_no for p in doc.pages] == [1, 2]
    p1 = doc.pages[0]
    assert "Acme Widgets LLC" in p1.text
    assert p1.needs_ocr is False and p1.ocr is False
    assert p1.ocr_text is None
    assert p1.extraction_text == p1.text
    words = [w.text for w in p1.words]
    assert words[:3] == ["Named", "Insured:", "Acme"]
    w = p1.words[0]
    assert 0 <= w.x0 < w.x1 <= p1.width and 0 <= w.y0 < w.y1 <= p1.height
    assert p1.width == 612 and p1.height == 792
    assert len(doc.sha256) == 64


def test_sparse_page_flagged_for_ocr_without_ocr_fn(pdf_factory):
    data = pdf_factory(["Normal page with plenty of words on it, quite a lot.", None, "p. 3"])
    doc = parse.parse_pdf(data)
    assert [p.needs_ocr for p in doc.pages] == [False, True, True]
    assert doc.pages[1].ocr_reason == "sparse_text"
    assert doc.pages[1].ocr is False  # nothing ran yet
    assert [p.page_no for p in doc.ocr_pending] == [2, 3]


def test_ocr_threshold_is_25_non_whitespace_chars(pdf_factory):
    just_under = "a" * 24
    exactly = "b" * 25
    doc = parse.parse_pdf(pdf_factory([just_under, exactly, "  ".join("c" * 30)]))
    assert [p.needs_ocr for p in doc.pages] == [True, False, False]
    assert doc.pages[0].char_count == 24


def test_ocr_fn_fills_sparse_pages(pdf_factory):
    data = pdf_factory(["Digital page with enough text to skip the OCR pass.", None])
    calls = []

    def fake_ocr(png: bytes, page_no: int) -> str:
        calls.append((png[:8], page_no))
        return "SCANNED Named Insured: Beta Corp"

    doc = parse.parse_pdf(data, ocr=fake_ocr)
    assert calls == [(PNG_MAGIC, 2)]
    scanned = doc.pages[1]
    assert scanned.ocr is True
    assert scanned.ocr_text == "SCANNED Named Insured: Beta Corp"
    assert scanned.extraction_text == scanned.ocr_text
    assert scanned.text == ""  # digital layer stays as found
    assert scanned.words == []
    assert doc.pages[0].ocr is False
    assert doc.ocr_pending == []


def test_apply_ocr_later_matches_inline_ocr(pdf_factory):
    data = pdf_factory([None])
    doc = parse.parse_pdf(data)
    assert doc.pages[0].needs_ocr
    parse.apply_ocr(doc, data, lambda png, n: "Later OCR text for the page")
    assert doc.pages[0].ocr_text == "Later OCR text for the page"


def test_image_heavy_page_with_little_text_is_ocrd(pdf_factory):
    # image covers about 50 percent of the page and text is well under 600 chars
    text = "Schedule of forms attached. " * 4
    data = pdf_factory([text], image_pages={1: (0, 396, 612, 792)})
    page = parse.parse_pdf(data).pages[0]
    assert page.image_ratio == pytest.approx(0.5, abs=0.01)
    assert page.char_count >= 25
    assert page.needs_ocr is True
    assert page.ocr_reason == "image_heavy"


def test_image_heavy_page_with_long_text_is_not_ocrd(pdf_factory):
    data = pdf_factory([LONG], image_pages={1: (0, 396, 612, 792)})
    page = parse.parse_pdf(data).pages[0]
    assert page.char_count >= 600
    assert page.needs_ocr is False


def test_small_image_does_not_trigger_ocr(pdf_factory):
    data = pdf_factory(["Producer: Northwind Brokerage, Chicago IL"], image_pages={1: (0, 0, 100, 100)})
    page = parse.parse_pdf(data).pages[0]
    assert page.image_ratio < 0.4
    assert page.needs_ocr is False


def test_overlapping_images_are_not_double_counted(pdf_factory):
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
    pix.clear_with(100)
    page.insert_image(fitz.Rect(0, 0, 612, 300), pixmap=pix)
    page.insert_image(fitz.Rect(0, 0, 612, 300), pixmap=pix)
    page.insert_text((72, 500), "Short text here for the page body")
    parsed = parse.parse_pdf(doc.tobytes()).pages[0]
    assert parsed.image_ratio == pytest.approx(300 / 792, abs=0.01)
    assert parsed.needs_ocr is False


def test_ocr_returning_nothing_everywhere_means_empty_document(pdf_factory):
    data = pdf_factory([None, None])
    with pytest.raises(ParseError) as exc:
        parse.parse_pdf(data, ocr=lambda png, n: "   ")
    assert exc.value.code == ParseErrorCode.EMPTY


# ---------------------------------------------------------------- failures and caps


def test_empty_bytes():
    with pytest.raises(ParseError) as exc:
        parse.parse_pdf(b"")
    assert exc.value.code == ParseErrorCode.EMPTY


def test_not_a_pdf():
    with pytest.raises(ParseError) as exc:
        parse.parse_pdf(b"PK\x03\x04 this is a zip file")
    assert exc.value.code == ParseErrorCode.NOT_PDF
    assert "Not a PDF" in exc.value.message


def test_corrupt_pdf():
    with pytest.raises(ParseError) as exc:
        parse.parse_pdf(b"%PDF-1.7\n" + b"\x00garbage" * 50)
    assert exc.value.code == ParseErrorCode.CORRUPT


def test_password_protected_pdf(pdf_factory):
    data = pdf_factory(["Secret quote"], password="hunter2")
    with pytest.raises(ParseError) as exc:
        parse.parse_pdf(data)
    assert exc.value.code == ParseErrorCode.ENCRYPTED
    assert "password" in exc.value.message.lower()


def test_over_page_cap_names_the_limit(pdf_factory):
    # T14 raised the default cap from 80 to 300 pages (the 300/301 PDFs are in test_long_documents)
    with pytest.raises(ParseError) as exc:
        parse.check_page_count(301)
    assert exc.value.code == ParseErrorCode.TOO_MANY_PAGES
    assert exc.value.limit == 300
    assert "300 pages" in exc.value.message
    parse.check_page_count(300)
    data = pdf_factory(["x" * 40] * 3)
    with pytest.raises(ParseError) as exc2:
        parse.parse_pdf(data, limits=ParseLimits(max_pages=2))
    assert "2 pages" in exc2.value.message


def test_page_cap_is_configurable(pdf_factory):
    data = pdf_factory(["x" * 40] * 3)
    with pytest.raises(ParseError):
        parse.parse_pdf(data, limits=ParseLimits(max_pages=2))
    assert parse.parse_pdf(data, limits=ParseLimits(max_pages=3)).page_count == 3


def test_over_size_cap_names_the_limit(pdf_factory):
    data = pdf_factory(["x" * 40])
    with pytest.raises(ParseError) as exc:
        parse.parse_pdf(data, limits=ParseLimits(max_bytes=len(data) - 1))
    assert exc.value.code == ParseErrorCode.TOO_LARGE
    # the default message names the 50 MB default (T14; D3 had 15 MB)
    with pytest.raises(ParseError) as exc2:
        parse.check_size(50 * 1024 * 1024 + 1)
    assert "50 MB" in exc2.value.message
    parse.check_size(50 * 1024 * 1024)


def test_document_count_cap():
    parse.check_document_count(8)
    with pytest.raises(ParseError) as exc:
        parse.check_document_count(9)
    assert exc.value.code == ParseErrorCode.TOO_MANY_DOCUMENTS
    assert "8 documents" in exc.value.message


def test_is_pdf_bytes_tolerates_leading_junk():
    assert parse.is_pdf_bytes(b"%PDF-1.4 ...")
    assert parse.is_pdf_bytes(b"\r\n\xef\xbb\xbf%PDF-1.7")
    assert not parse.is_pdf_bytes(b"<html>%PD")


def test_multi_column_text_keeps_both_columns(pdf_factory):
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(fitz.Rect(54, 54, 290, 400), "Carrier: Atlas Mutual\nRating: A+", fontsize=11)
    page.insert_textbox(fitz.Rect(320, 54, 558, 400), "Premium: $12,500\nTerm: 12 months", fontsize=11)
    parsed = parse.parse_pdf(doc.tobytes()).pages[0]
    for frag in ("Atlas Mutual", "A+", "$12,500", "12 months"):
        assert frag in parsed.text
