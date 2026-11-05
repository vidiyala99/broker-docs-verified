"""Slice 6 T1 (D1, D2, R5, F6, F7, F8): Word and Excel quotes at intake.

POST /api/documents takes .docx and .xlsx beside PDFs. The format is decided by the extension and the content; the
file is scanned as an untrusted zip (zip bomb, macros, external links, entity declarations, the other format), rendered
to a PDF record in Python (python-docx + reportlab, the AMS renderer's Helvetica), and the original is kept for
Download. Word and Excel quotes go only in Compare Quotes and Proposal Builder runs; other workflows refuse them.
"""

from __future__ import annotations

import hashlib
import sqlite3

import fitz
import pytest

from api_support import DOC_A, api, make_api  # noqa: F401
from backend import db, models, office_docx, uploads
from backend.office_docx import OfficeError, OfficeLimits
from backend.pipeline import parse
from backend.pipeline.parse import ParseError, ParseLimits
from backend.pipeline.runner import SlotError, check_slot_kinds, files_dir
from conftest import make_pdf
from office_fixtures import (
    DOCX_CONTENT_TYPE,
    QUOTE_DOCX_LINES,
    QUOTE_XLSX_ROWS,
    XLSX_CONTENT_TYPE,
    docx_bytes,
    rezip,
    tracked_paragraph,
    xlsx_bytes,
)

LIMITS = ParseLimits()


def _text(pdf: bytes) -> str:
    return "\n".join(p.text for p in parse.parse_pdf(pdf).pages)


def _render(data: bytes, max_pages: int = 300) -> office_docx.RenderedOffice:
    office_docx.scan_package(data, "docx")
    return office_docx.render_docx(data, max_pages)


# --------------------------------------------------------------------- docx renderer


def test_docx_paragraphs_and_heading_render_in_document_order():
    data = docx_bytes(["Named Insured: Cedar Hollow LLC", "Total Premium: $26,391.14"], heading="Harborline Quote",
                      after=["Quote valid until 2026-12-31"])
    out = _render(data)
    text = _text(out.pdf)
    assert out.page_count == 1
    order = [text.index(s) for s in ("Harborline Quote", "Named Insured: Cedar Hollow LLC",
                                     "Total Premium: $26,391.14", "Quote valid until 2026-12-31")]
    assert order == sorted(order)
    with fitz.open(stream=out.pdf, filetype="pdf") as doc:
        fonts = {f[3] for f in doc[0].get_fonts()}
    assert fonts <= {"Helvetica", "Helvetica-Bold"}  # the AMS renderer's font, nothing embedded


def test_docx_tables_render_as_ruled_grids_with_every_cell_in_row_order():
    rows = [["Coverage", "Limit"], ["Each Occurrence", "$1,000,000"], ["General Aggregate", "$2,000,000"]]
    out = _render(docx_bytes(["Schedule of limits"], rows, after=["After the table"]))
    text = _text(out.pdf)
    positions = [text.index(c) for r in rows for c in r]
    assert positions == sorted(positions)
    assert text.index("Schedule of limits") < positions[0] and text.index("After the table") > positions[-1]
    with fitz.open(stream=out.pdf, filetype="pdf") as doc:
        assert len(doc[0].get_drawings()) >= 6  # one ruled box per cell


def test_docx_long_table_cell_wraps_and_long_document_flows_onto_pages():
    long = "Blanket additional insured where required by written contract " * 6
    out = _render(docx_bytes([f"Paragraph {i} " + "word " * 40 for i in range(40)], [["Additional insured", long]]))
    assert out.page_count >= 3
    text = " ".join(_text(out.pdf).split())
    assert " ".join(long.split()) in text  # every word kept, wrapped inside its cell


def test_docx_tracked_changes_show_the_accepted_text():
    data = docx_bytes(edit=lambda d: tracked_paragraph(d, "Waiver of subrogation: ", "Blanket", "Scheduled only"))
    text = _text(_render(data).pdf)
    assert "Waiver of subrogation: Blanket" in text and "Scheduled only" not in text


def test_docx_empty_document_is_refused_plainly():
    with pytest.raises(OfficeError) as exc:
        _render(docx_bytes())
    assert exc.value.code == "empty" and "no text" in exc.value.message


def test_docx_render_is_deterministic_and_undrawable_characters_print_as_question_marks():
    data = docx_bytes(["Insured: Cedar Hollow LLC 中文", "Fee: €250"])
    a, b = _render(data).pdf, _render(data).pdf
    assert a == b
    text = _text(a)
    assert "Cedar Hollow LLC ??" in text and "€250" in text


# --------------------------------------------------------------------- refusals (untrusted zip, formats)


def _prepare(data: bytes, name: str, **kw):
    return uploads.prepare_office(data, name, kw.pop("limits", LIMITS), **kw)


def test_zip_bomb_is_refused_before_parsing():
    data = rezip(docx_bytes(["x"]), add={"word/media/bomb.bin": b"\0" * (3 * 1024 * 1024)})
    with pytest.raises(OfficeError) as exc:
        _prepare(data, "q.docx", office_limits=OfficeLimits(max_uncompressed_bytes=2 * 1024 * 1024))
    assert exc.value.code == "unpacked_too_large" and exc.value.limit == 2 * 1024 * 1024
    many = rezip(docx_bytes(["x"]), add={f"customXml/item{i}.bin": b"1" for i in range(30)})
    with pytest.raises(OfficeError) as exc:
        _prepare(many, "q.docx", office_limits=OfficeLimits(max_entries=20))
    assert exc.value.code == "too_many_entries" and exc.value.limit == 20


def test_default_unpacked_cap_reuses_the_sheets_zip_cap():
    from backend.loss_runs import sheets

    assert office_docx.DEFAULT_OFFICE_LIMITS.max_uncompressed_bytes == sheets.DEFAULT_SHEET_LIMITS.max_uncompressed_bytes


@pytest.mark.parametrize("fmt", ["docx", "xlsx"])
def test_macros_are_refused(fmt):
    base = docx_bytes(["x"]) if fmt == "docx" else xlsx_bytes({"Quote": QUOTE_XLSX_ROWS})
    part = "word/vbaProject.bin" if fmt == "docx" else "xl/vbaProject.bin"
    with pytest.raises(OfficeError) as exc:
        _prepare(rezip(base, add={part: b"VBA"}), f"q.{fmt}")
    assert exc.value.code == "macros" and "macros" in exc.value.message
    enabled = rezip(base, replace=lambda n, b: b.replace(b"main+xml", b"main.macroEnabled.main+xml")
                    if n == "[Content_Types].xml" else b)
    with pytest.raises(OfficeError) as exc:
        _prepare(enabled, f"q.{fmt}")
    assert exc.value.code == "macros"


@pytest.mark.parametrize("name", ["q.docm", "q.xlsm"])
def test_macro_enabled_extensions_are_refused(name):
    with pytest.raises(OfficeError) as exc:
        uploads.upload_format(b"PK\x03\x04", name)
    assert exc.value.code == "macros"


def _external_rel(n: str, b: bytes) -> bytes:
    if n != "word/_rels/document.xml.rels":
        return b
    rel = (b'<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
           b'attachedTemplate" Target="https://example.invalid/t.dotx" TargetMode="External"/>')
    return b.replace(b"</Relationships>", rel + b"</Relationships>")


def _hyperlink_rel(n: str, b: bytes) -> bytes:
    if n != "word/_rels/document.xml.rels":
        return b
    rel = (b'<Relationship Id="rIdH" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
           b'hyperlink" Target="https://carrier.example/terms" TargetMode="External"/>')
    return b.replace(b"</Relationships>", rel + b"</Relationships>")


def test_external_links_are_refused_but_a_plain_hyperlink_is_not():
    with pytest.raises(OfficeError) as exc:
        _prepare(rezip(docx_bytes(["x"]), replace=_external_rel), "q.docx")
    assert exc.value.code == "external_links" and "external link" in exc.value.message
    ok = _prepare(rezip(docx_bytes(["Total Premium: $1"]), replace=_hyperlink_rel), "q.docx")
    assert ok.source_format == "docx" and ok.page_count == 1
    sheet = rezip(xlsx_bytes({"Quote": QUOTE_XLSX_ROWS}), add={"xl/externalLinks/externalLink1.xml": b"<x/>"})
    with pytest.raises(OfficeError) as exc:
        _prepare(sheet, "q.xlsx")
    assert exc.value.code == "external_links"


def test_entity_declarations_are_refused():
    bad = rezip(docx_bytes(["x"]), replace=lambda n, b: b.replace(b"?>", b'?><!DOCTYPE w [<!ENTITY a "aaaa">]>', 1)
                if n == "word/document.xml" else b)
    with pytest.raises(OfficeError) as exc:
        _prepare(bad, "q.docx")
    assert exc.value.code == "unsafe_xml"


@pytest.mark.parametrize("name,data,code", [
    ("q.xlsx", "DOCX", "format_mismatch"),  # a Word file named .xlsx
    ("q.docx", "XLSX", "format_mismatch"),  # an Excel file named .docx
    ("q.docx", "PDF", "format_mismatch"),  # a PDF named .docx
    ("q.docx", b"plain text, not a zip", "format_mismatch"),
    ("q.docx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, "encrypted"),  # OLE: password-protected or legacy
])
def test_extension_and_content_must_agree(name, data, code):
    data = {"DOCX": docx_bytes(["x"]), "XLSX": xlsx_bytes({"Q": [["x"]]}), "PDF": make_pdf(["x"])}.get(data, data)
    with pytest.raises(OfficeError) as exc:
        _prepare(data, name)
    assert exc.value.code == code


def test_mismatch_names_the_other_format():
    with pytest.raises(OfficeError) as exc:
        _prepare(xlsx_bytes({"Q": [["x"]]}), "q.docx")
    assert exc.value.message == "This file is named .docx but is an Excel file. Rename it to .xlsx and upload it again."


@pytest.mark.parametrize("name,expect", [("old.xls", "Save it as .xlsx and upload again."),
                                         ("old.doc", "Save it as .docx and upload again.")])
def test_legacy_formats_say_how_to_fix_them(name, expect):
    with pytest.raises(OfficeError) as exc:
        uploads.upload_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", name)
    assert exc.value.code == "legacy_format" and exc.value.message.endswith(expect)


@pytest.mark.parametrize("name,data", [
    ("q.docx", "BROKEN_XML"),
    ("q.docx", "TRUNCATED"),
    ("q.xlsx", "BROKEN_SHEET"),
])
def test_a_corrupt_office_file_is_couldnt_read_this_file(name, data):
    good_docx = docx_bytes(["Total Premium: $1"])
    data = {
        "BROKEN_XML": rezip(good_docx, replace=lambda n, b: b[: len(b) // 2] if n == "word/document.xml" else b),
        "TRUNCATED": good_docx[: len(good_docx) // 2],
        "BROKEN_SHEET": rezip(xlsx_bytes({"Q": QUOTE_XLSX_ROWS}),
                              replace=lambda n, b: b"<worksheet><sheetData><row" if n.startswith("xl/worksheets/")
                              else b),
    }[data]
    with pytest.raises(OfficeError) as exc:
        _prepare(data, name)
    assert exc.value.code == "unreadable"
    assert exc.value.message.startswith("Couldn't read this file")


def test_docx_over_the_page_cap_is_refused_before_drawing(monkeypatch):
    drawn = []
    monkeypatch.setattr(office_docx, "_draw", lambda *a: drawn.append(a) or b"")
    data = docx_bytes([f"Line {i} " + "word " * 60 for i in range(60)])
    with pytest.raises(ParseError) as exc:
        _prepare(data, "q.docx", limits=ParseLimits(max_pages=2))
    assert exc.value.code == parse.ParseErrorCode.TOO_MANY_PAGES and exc.value.limit == 2
    assert drawn == []


# --------------------------------------------------------------------- API


def _upload(api, *files):
    r = api.client.post("/api/documents", files=[("files", (n, d, t)) for n, d, t in files])
    assert r.status_code == 200, r.text
    return r.json()


def test_api_docx_quote_is_stored_as_a_pdf_record_and_downloads_as_the_original(api):
    data = docx_bytes(QUOTE_DOCX_LINES)
    out = _upload(api, ("harborline.docx", data, DOCX_CONTENT_TYPE))
    assert out["rejected"] == []
    doc = out["documents"][0]
    assert doc["kind"] == "quote" and doc["source_format"] == "docx" and doc["page_count"] == 1
    assert doc["size_bytes"] == len(data) and doc["parse_error"] is None

    served = api.client.get(f"/api/documents/{doc['id']}/file")
    assert served.status_code == 200 and served.headers["content-type"] == "application/pdf"
    assert "Total Premium: $26,391.14" in _text(served.content)
    assert doc["sha256"] == hashlib.sha256(served.content).hexdigest()  # the record read and cached on

    original = api.client.get(f"/api/documents/{doc['id']}/original")
    assert original.status_code == 200 and original.content == data
    assert original.headers["content-type"] == DOCX_CONTENT_TYPE
    assert original.headers["content-disposition"].startswith('attachment; filename="harborline.docx"')

    listed = api.client.get("/api/documents").json()["documents"]
    assert [d["source_format"] for d in listed] == ["docx"]


def test_api_xlsx_quote_and_pdf_carry_their_source_format(api):
    pdf = make_pdf(DOC_A)
    out = _upload(api, ("juniper.xlsx", xlsx_bytes({"Quote": QUOTE_XLSX_ROWS}), XLSX_CONTENT_TYPE),
                  ("a.pdf", pdf, "application/pdf"))
    assert out["rejected"] == []
    assert [(d["kind"], d["source_format"]) for d in out["documents"]] == [("quote", "xlsx"), ("quote", "pdf")]
    pdf_doc = out["documents"][1]
    original = api.client.get(f"/api/documents/{pdf_doc['id']}/original")
    assert original.content == pdf and original.headers["content-type"] == "application/pdf"


def test_api_refusals_are_inline_and_store_nothing(api):
    out = _upload(api, ("old.xls", b"\xd0\xcf\x11\xe0", "application/vnd.ms-excel"),
                  ("old.doc", b"\xd0\xcf\x11\xe0", "application/msword"),
                  ("bad.docx", docx_bytes(["x"])[:300], DOCX_CONTENT_TYPE),
                  ("macro.docx", rezip(docx_bytes(["x"]), add={"word/vbaProject.bin": b"VBA"}), DOCX_CONTENT_TYPE),
                  ("fake.pdf", docx_bytes(["x"]), "application/pdf"),
                  ("notes.txt", b"hello", "text/plain"))
    assert out["documents"] == []
    codes = {r["filename"]: (r["code"], r["message"]) for r in out["rejected"]}
    assert codes["old.xls"] == ("legacy_format", "This is an older Excel file (.xls). Save it as .xlsx and upload "
                                                 "again.")
    assert codes["old.doc"][1].endswith("Save it as .docx and upload again.")
    assert codes["bad.docx"][0] == "unreadable" and codes["bad.docx"][1].startswith("Couldn't read this file")
    assert codes["macro.docx"][0] == "macros"
    assert codes["fake.pdf"][0] == "not_pdf" and codes["fake.pdf"][1].startswith("Not a PDF")
    assert codes["notes.txt"][0] == "not_pdf"
    assert [p.name for p in files_dir().iterdir()] == []


def test_api_compare_run_reads_docx_and_xlsx_quotes(api):
    out = _upload(api, ("harborline.docx", docx_bytes(QUOTE_DOCX_LINES), DOCX_CONTENT_TYPE),
                  ("juniper.xlsx", xlsx_bytes({"Quote": QUOTE_XLSX_ROWS}), XLSX_CONTENT_TYPE))
    ids = [d["id"] for d in out["documents"]]
    run, events = api.run(ids)
    assert run["status"] == "done", run
    assert [(d["status"], d["source_format"]) for d in run["documents"]] == [("done", "docx"), ("done", "xlsx")]
    doc_event = next(e for e in events if e["type"] == "document.status")
    assert doc_event["data"]["source_format"] in ("docx", "xlsx")
    art = api.artifact(run["id"])
    premium = next(row for s in art["content"]["sections"] for b in s["blocks"] if b["type"] == "table"
                   for row in b["rows"] if row["key"] == "total_premium")
    assert {k: (c["value"], c["state"]) for k, c in premium["cells"].items()} == {
        "A": ("$26,391.14", "verified"), "B": ("$14,490.00", "verified")}


# --------------------------------------------------------------------- R5: where Word and Excel quotes may go


def _docx_doc(api) -> int:
    out = _upload(api, ("q.docx", docx_bytes(QUOTE_DOCX_LINES), DOCX_CONTENT_TYPE))
    return out["documents"][0]["id"]


def test_r5_proposal_builder_takes_a_word_quote(api):
    r = api.client.post("/api/runs", json={"document_ids": [_docx_doc(api)], "workflow": "proposal-builder",
                                           "parameters": {"template_id": "standard"}})
    assert r.status_code == 201, r.text
    api.events(r.json()["run"]["id"])
    assert api.client.get(f"/api/runs/{r.json()['run']['id']}").json()["documents"][0]["status"] == "done"


def test_r5_policy_check_refuses_a_word_quote(api):
    exp, ren = api.upload_pages(DOC_A, "exp.pdf"), api.upload_pages(DOC_A, "ren.pdf")
    q = _docx_doc(api)
    r = api.client.post("/api/runs", json={"document_ids": [exp, ren, q], "workflow": "policy-check",
                                           "slots": {str(exp): "expiring", str(ren): "renewal", str(q): "quote"}})
    assert r.status_code == 400, r.text
    assert r.json()["error"] == {"code": "invalid_run", "message": "Quote takes PDF documents."}


def test_r5_certificate_refuses_a_word_quote_and_hides_it_from_the_picker(api):
    q = _docx_doc(api)
    params = {"holder_name": "Smith & Sons Construction", "holder_address": "4 Elm Street\nAlbany NY",
              "certificate_date": "2026-11-05", "wording": {"additional_insured": True}}
    r = api.client.post("/api/runs", json={"document_ids": [q], "workflow": "certificate",
                                           "client_name": "Cedar Hollow LLC", "parameters": params})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["message"] == "Policies takes PDF documents."
    pdf = api.upload_pages(DOC_A, "a.pdf")
    r = api.client.post("/api/runs", json={"document_ids": [q, pdf], "client_name": "Cedar Hollow LLC"})
    assert r.status_code == 201, r.text
    api.events(r.json()["run"]["id"])
    listed = api.client.get("/api/certificates/documents", params={"client": "Cedar Hollow LLC"}).json()
    assert [d["id"] for d in listed["documents"]] == [pdf]


def test_r5_loss_review_refuses_an_excel_quote(api):
    out = _upload(api, ("q.xlsx", xlsx_bytes({"Quote": QUOTE_XLSX_ROWS}), XLSX_CONTENT_TYPE))
    r = api.client.post("/api/runs", json={"document_ids": [out["documents"][0]["id"]], "workflow": "loss-run",
                                           "client_name": "Cedar Hollow LLC"})
    assert r.status_code == 400, r.text
    assert "loss run" in r.json()["error"]["message"]


def test_r5_slot_rules():
    slots = [{"key": "quotes", "label": "Quotes", "accepts": db.QUOTE_TYPES},
             {"key": "loss_runs", "label": "Loss runs", "accepts": f"application/pdf,{db.SPREADSHEET_TYPES}"},
             {"key": "renewal", "label": "Renewal Policy", "accepts": "application/pdf"}]
    check_slot_kinds(slots, ["quote", "quote", "quote"], ["quotes"] * 3, ["pdf", "docx", "xlsx"])
    check_slot_kinds(slots, ["spreadsheet"], ["loss_runs"], ["xlsx"])
    check_slot_kinds(slots, ["quote"], ["renewal"], None)  # callers without formats: unchanged
    with pytest.raises(SlotError, match="Renewal Policy takes PDF documents."):
        check_slot_kinds(slots, ["quote"], ["renewal"], ["xlsx"])
    with pytest.raises(SlotError, match="read only by a loss review"):  # a loss-run spreadsheet stays out of Compare
        check_slot_kinds(slots, ["spreadsheet"], ["quotes"], ["xlsx"])
    with pytest.raises(SlotError, match="as a loss run"):
        check_slot_kinds(slots, ["quote"], ["loss_runs"], ["xlsx"])


def test_r5_seeded_slots_list_word_and_excel_only_for_compare_and_proposal():
    by_slug = {wf["slug"]: wf for wf in (db.COMPARE_QUOTES, db.PROPOSAL_BUILDER, db.POLICY_CHECK, db.CERTIFICATE)}
    for slug, wf in by_slug.items():
        for slot in wf["input_slots"]:
            office = db.DOCX_TYPE in slot["accepts"] or db.XLSX_TYPE in slot["accepts"]
            assert office == (slug in (db.COMPARE_QUOTES_SLUG, db.PROPOSAL_BUILDER_SLUG)), (slug, slot)


# --------------------------------------------------------------------- schema (F7: no migrations)


def test_a_database_without_document_source_format_fails_fast(tmp_path):
    path = tmp_path / "brokerdocs.db"
    engine = db.make_engine(f"sqlite:///{path}")
    db.init_db(engine)
    engine.dispose()
    con = sqlite3.connect(path)
    try:
        con.execute("ALTER TABLE document DROP COLUMN source_format")
        con.commit()
    finally:
        con.close()
    engine = db.make_engine(f"sqlite:///{path}")
    try:
        with pytest.raises(db.SchemaOutdated, match="document.source_format"):
            db.init_db(engine)
    finally:
        engine.dispose()


def test_source_format_defaults_to_pdf(session):
    doc = models.Document(workspace_id=db.DEFAULT_WORKSPACE_ID, filename="a.pdf", stored_name="a" * 32,
                          size_bytes=1, sha256="0" * 64)
    session.add(doc)
    session.flush()
    assert doc.source_format == "pdf"
