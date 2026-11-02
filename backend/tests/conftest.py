"""Shared fixtures. PDFs are generated in-test with PyMuPDF (small, deterministic)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import fitz
import pytest
from sqlalchemy.orm import Session

from backend import db


def make_pdf(
    pages: Sequence[str | None],
    *,
    image_pages: dict[int, tuple[float, float, float, float]] | None = None,
    fontsize: float = 11,
    password: str | None = None,
) -> bytes:
    """Build a PDF. pages[i] is the text of page i+1 (None = blank page).

    image_pages maps a 1-based page number to an image rect (x0, y0, x1, y1)
    in points, drawn as a flat grey raster so the page has image coverage.
    """
    image_pages = image_pages or {}
    doc = fitz.open()
    for index, text in enumerate(pages, start=1):
        page = doc.new_page(width=612, height=792)
        if index in image_pages:
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
            pix.clear_with(180)
            page.insert_image(fitz.Rect(*image_pages[index]), pixmap=pix)
        if text:
            page.insert_textbox(
                fitz.Rect(54, 54, 558, 738), text, fontsize=fontsize, fontname="helv"
            )
    if password:
        data = doc.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "o"
        )
    else:
        data = doc.tobytes(garbage=0, deflate=False)
    doc.close()
    return data


@pytest.fixture
def pdf_factory():
    return make_pdf


@pytest.fixture
def session() -> Iterator[Session]:
    engine = db.make_engine("sqlite://")
    db.init_db(engine)
    factory = db.make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()
