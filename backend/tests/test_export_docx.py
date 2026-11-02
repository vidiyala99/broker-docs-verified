"""T5 (slice 2): DOCX export with real Word footnotes, plus PDF cover and TOC.

python-docx has no footnote API, so the exporter writes the footnotes part as
raw WordprocessingML. These tests check that part structurally (content type,
relationship, separator footnotes -1 and 0, reference ids, styles, settings
order) and reopen the file with python-docx, because Word is not part of the
test environment.
"""

from __future__ import annotations

import io
import zipfile

from docx import Document
from lxml import etree

from api_support import DOC_A, api  # noqa: F401
from backend import db, export, export_docx, models
from backend.schemas import (
    ArtifactContent,
    Cell,
    Column,
    ConflictValue,
    EvidenceState,
    HeadingBlock,
    ListBlock,
    Notice,
    ParagraphBlock,
    Row,
    Section,
    Segment,
    TableBlock,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
FOOTNOTES_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
FOOTNOTES_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
V = EvidenceState.VERIFIED


def _chip(pid: int, label: str, page: int, *, verified: bool = True, letter: str = "A",
          filename: str = "a.pdf") -> dict:
    return {"passage_id": pid, "label": label, "letter": letter, "document_id": 1, "filename": filename,
            "page_no": page, "verified": verified, "rects": [], "page_width": 612.0, "page_height": 792.0}


CHIPS = {"1": _chip(1, "A1", 1), "2": _chip(2, "A2", 3), "3": _chip(3, "A3", 20), "4": _chip(4, "A4", 90),
         "5": _chip(5, "B1", 2, verified=False, letter="B", filename="b.pdf")}


def _table() -> TableBlock:
    cells = {
        "premium": {"A": Cell(value="$26,391.14", passage_ids=[1], state=V),
                    "B": Cell(value="$14,490.00", passage_ids=[5], state=EvidenceState.SCAN_READ)},
        "retention": {"A": Cell(value="Conflicting values", passage_ids=[3, 4], state=EvidenceState.CONFLICTING,
                                conflicts=[ConflictValue(value="$5,000", page_no=20, passage_id=3),
                                           ConflictValue(value="$7,500", page_no=90, passage_id=4)]),
                      "B": Cell(value="Couldn't verify", state=EvidenceState.NOT_FOUND)},
        "limit": {"A": Cell(value="$2,000,000", passage_ids=[], state=V, edited=True,
                            original_value="$1,000,000", original_passage_ids=[2]),
                  "B": Cell(value="Not specified", state=EvidenceState.ABSENT)},
    }
    labels = {"premium": "Total Premium", "retention": "Retention", "limit": "Each Occurrence Limit"}
    return TableBlock(id="premium-table", title="Premium", columns=[
        Column(key="A", label="Harborline", document_id=1),
        Column(key="B", label="Juniper", document_id=2, warning="Read from a scanned quote")],
        rows=[Row(key=k, label=labels[k], cells=v) for k, v in cells.items()])


def slice1_content() -> ArtifactContent:
    return ArtifactContent(
        notices=[Notice(kind="info", message="One document was scanned.")],
        sections=[
            Section(id="summary", number="1", title="Executive Summary", blocks=[
                ParagraphBlock(id="summary-p", segments=[
                    Segment(text="Harborline costs $26,391.14.", passage_ids=[1]),
                    Segment(text="Juniper is cheaper.", passage_ids=[])]),
                ListBlock(id="summary-l", title="Key points", items=[[Segment(text="Limit doubled.",
                                                                              passage_ids=[2])]])]),
            Section(id="premium", number="3.2", title="Premium", level=2, stale=True, blocks=[
                HeadingBlock(id="premium-h", text="Premium detail"), _table()]),
        ])


def proposal_content() -> ArtifactContent:
    c = slice1_content()
    cover = Section(id="cover", number="", title="Cover Page", blocks=[
        HeadingBlock(id="cover-title", level=1, text="Insurance Proposal for Cedar Hollow LLC"),
        ParagraphBlock(id="cover-broker", segments=[Segment(text="Prepared by Dana Broker, Acme Brokerage")])])
    toc = Section(id="toc", number="", title="Table of Contents", blocks=[
        HeadingBlock(id="toc-title", level=2, text="Table of Contents"),
        ListBlock(id="toc", items=[[Segment(text="1 Executive Summary")], [Segment(text="3.2 Premium")]])])
    return ArtifactContent(notices=c.notices, sections=[cover, toc, *c.sections])


def data_for(content: ArtifactContent, theme=None) -> export.ExportData:
    return export.ExportData(title="Compare Quotes", run_id=7, version=2, content=content, chips=CHIPS,
                             theme=theme)


def parts(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return {n: z.read(n) for n in z.namelist()}


def xml(blob: bytes, name: str):
    return etree.fromstring(parts(blob)[name])


def footnotes(blob: bytes) -> dict[int, str]:
    """Normal footnotes: id -> text (separators excluded)."""
    root = xml(blob, "word/footnotes.xml")
    out = {}
    for fn in root.findall("w:footnote", NS):
        if fn.get(f"{{{W}}}type") in ("separator", "continuationSeparator"):
            continue
        out[int(fn.get(f"{{{W}}}id"))] = "".join(fn.itertext()).strip()
    return out


def ref_ids(blob: bytes) -> list[int]:
    root = xml(blob, "word/document.xml")
    return [int(r.get(f"{{{W}}}id")) for r in root.iter(f"{{{W}}}footnoteReference")]


# --------------------------------------------------------------------- spike: footnote plumbing


def test_footnotes_part_has_content_type_and_document_relationship():
    blob = export_docx.to_docx(data_for(slice1_content()))
    p = parts(blob)
    assert "word/footnotes.xml" in p
    ct = etree.fromstring(p["[Content_Types].xml"])
    overrides = {o.get("PartName"): o.get("ContentType") for o in ct}
    assert overrides["/word/footnotes.xml"] == FOOTNOTES_CT
    rels = etree.fromstring(p["word/_rels/document.xml.rels"])
    fn_rels = [r for r in rels if r.get("Type") == FOOTNOTES_RT]
    assert len(fn_rels) == 1 and fn_rels[0].get("Target") == "footnotes.xml"
    assert fn_rels[0].get("TargetMode") is None  # internal part


def test_footnotes_part_starts_with_the_separators_word_expects():
    root = xml(export_docx.to_docx(data_for(slice1_content())), "word/footnotes.xml")
    assert root.tag == f"{{{W}}}footnotes"
    first, second = root.findall("w:footnote", NS)[:2]
    assert (first.get(f"{{{W}}}type"), first.get(f"{{{W}}}id")) == ("separator", "-1")
    assert first.find("w:p/w:r/w:separator", NS) is not None
    assert (second.get(f"{{{W}}}type"), second.get(f"{{{W}}}id")) == ("continuationSeparator", "0")
    assert second.find("w:p/w:r/w:continuationSeparator", NS) is not None


def test_every_reference_has_exactly_one_footnote_numbered_in_document_order():
    blob = export_docx.to_docx(data_for(slice1_content()))
    refs, notes = ref_ids(blob), footnotes(blob)
    assert refs == list(range(1, len(refs) + 1))  # unique, in reading order
    assert sorted(notes) == refs
    root = xml(blob, "word/footnotes.xml")
    for fn in root.findall("w:footnote", NS)[2:]:
        p = fn.find("w:p", NS)
        assert p.find("w:pPr/w:pStyle", NS).get(f"{{{W}}}val") == "FootnoteText"
        first_run = p.find("w:r", NS)
        assert first_run.find("w:rPr/w:rStyle", NS).get(f"{{{W}}}val") == "FootnoteReference"
        assert first_run.find("w:footnoteRef", NS) is not None
    doc = xml(blob, "word/document.xml")
    for ref in doc.iter(f"{{{W}}}footnoteReference"):
        run = ref.getparent()
        assert run.find("w:rPr/w:rStyle", NS).get(f"{{{W}}}val") == "FootnoteReference"


def test_footnote_styles_exist_and_settings_declare_the_separators_in_schema_order():
    blob = export_docx.to_docx(data_for(slice1_content()))
    styles = xml(blob, "word/styles.xml")
    by_id = {s.get(f"{{{W}}}styleId"): s for s in styles.findall("w:style", NS)}
    assert by_id["FootnoteText"].find("w:name", NS).get(f"{{{W}}}val") == "footnote text"
    ref = by_id["FootnoteReference"]
    assert ref.get(f"{{{W}}}type") == "character"
    assert ref.find("w:rPr/w:vertAlign", NS).get(f"{{{W}}}val") == "superscript"
    settings = xml(blob, "word/settings.xml")
    names = [etree.QName(e).localname for e in settings]
    assert names.index("footnotePr") < names.index("compat")
    assert names.index("updateFields") < names.index("footnotePr")
    ids = [f.get(f"{{{W}}}id") for f in settings.find("w:footnotePr", NS).findall("w:footnote", NS)]
    assert ids == ["-1", "0"]


def test_docx_reopens_with_python_docx_and_keeps_its_footnotes_on_resave():
    blob = export_docx.to_docx(data_for(proposal_content()))
    doc = Document(io.BytesIO(blob))
    assert any(p.text == "Executive Summary" or "Executive Summary" in p.text for p in doc.paragraphs)
    rels = [r for r in doc.part.rels.values() if r.reltype == FOOTNOTES_RT]
    assert len(rels) == 1 and rels[0].target_part.partname == "/word/footnotes.xml"
    out = io.BytesIO()
    doc.save(out)
    assert footnotes(out.getvalue()) == footnotes(blob)
    assert ref_ids(out.getvalue()) == ref_ids(blob)


def test_every_part_is_well_formed_xml():
    blob = export_docx.to_docx(data_for(proposal_content()))
    for name, raw in parts(blob).items():
        if name.endswith((".xml", ".rels")):
            etree.fromstring(raw)


# --------------------------------------------------------------------- DOCX content


def body_paragraphs(blob: bytes) -> list[tuple[str, str]]:
    """(style id, text) for every body paragraph outside tables, in order."""
    root = xml(blob, "word/document.xml")
    out = []
    for p in root.find("w:body", NS).findall("w:p", NS):
        style = p.find("w:pPr/w:pStyle", NS)
        text = "".join(t.text or "" for t in p.iter(f"{{{W}}}t"))
        out.append((style.get(f"{{{W}}}val") if style is not None else "Normal", text))
    return out


def test_cover_page_comes_first_and_uses_the_cover_blocks():
    blob = export_docx.to_docx(data_for(proposal_content()))
    root = xml(blob, "word/document.xml")
    paras = body_paragraphs(blob)
    assert paras[0] == ("Title", "Insurance Proposal for Cedar Hollow LLC")
    assert ("Normal", "Prepared by Dana Broker, Acme Brokerage") in paras[:4]
    assert ("Normal", "Run 7, version 2") in paras[:4]
    # the cover ends with a page break before the TOC heading
    body = root.find("w:body", NS).findall("w:p", NS)
    toc_head = next(i for i, (s, _) in enumerate(paras) if s == "TOCHeading")
    assert any(b.get(f"{{{W}}}type") == "page" for b in body[toc_head - 1].iter(f"{{{W}}}br"))
    # cover and toc sections are not rendered again as body sections
    assert not any(t in ("Cover Page", "Table of Contents") for s, t in paras if s.startswith("Heading"))


def test_slice1_artifact_gets_a_generated_cover_from_the_title():
    paras = body_paragraphs(export_docx.to_docx(data_for(slice1_content())))
    assert paras[0] == ("Title", "Compare Quotes")
    assert paras[1] == ("Normal", "Run 7, version 2")


def test_toc_is_a_word_field_prefilled_with_the_section_headings_in_order():
    blob = export_docx.to_docx(data_for(proposal_content()))
    paras = body_paragraphs(blob)
    assert ("TOCHeading", "Table of Contents") in paras
    entries = [(s, t) for s, t in paras if s in ("TOC1", "TOC2")]
    assert entries == [("TOC1", "1 Executive Summary"), ("TOC2", "3.2 Premium"), ("TOC1", "Sources")]
    root = xml(blob, "word/document.xml")
    instr = "".join(t.text for t in root.iter(f"{{{W}}}instrText"))
    assert instr.strip().startswith('TOC \\o "1-2"')
    kinds = [f.get(f"{{{W}}}fldCharType") for f in root.iter(f"{{{W}}}fldChar")]
    assert kinds == ["begin", "separate", "end"]


def test_toc_follows_the_outline_after_reorder_delete_and_add():
    c = proposal_content()
    cover, toc, summary, premium = c.sections
    added = Section(id="claims", number="4", title="Claims Handling", blocks=[
        ParagraphBlock(id="claims-p", segments=[Segment(text="Report claims within 30 days.")])])
    c = ArtifactContent(sections=[cover, toc, added, premium])  # summary deleted, new block first
    entries = [t for s, t in body_paragraphs(export_docx.to_docx(data_for(c))) if s in ("TOC1", "TOC2")]
    assert entries == ["4 Claims Handling", "3.2 Premium", "Sources"]


def test_sections_use_heading_styles_and_mark_stale_sections_outside_the_heading():
    paras = body_paragraphs(export_docx.to_docx(data_for(slice1_content())))
    assert ("Heading1", "1 Executive Summary") in paras
    i = paras.index(("Heading2", "3.2 Premium"))
    assert "may be out of date" in paras[i + 1][1]
    assert ("Heading3", "Premium detail") in paras
    assert ("Normal", "Notice: One document was scanned.") in paras
    assert ("ListBullet", "Limit doubled.") in paras


def table_texts(blob: bytes) -> list[list[str]]:
    doc = Document(io.BytesIO(blob))
    return [[c.text for c in row.cells] for row in doc.tables[0].rows]


def test_table_cells_write_unverified_states_as_words_never_as_bare_values():
    rows = table_texts(export_docx.to_docx(data_for(slice1_content())))
    assert rows[0][0] == "Field" and rows[0][1] == "A: Harborline"
    assert rows[0][2].startswith("B: Juniper") and "Read from a scanned quote" in rows[0][2]
    premium, retention, limit = rows[1:]
    assert premium == ["Total Premium", "$26,391.14", "$14,490.00 (Read from scan, unverified)"]
    assert retention == ["Retention", "Conflicting values: $5,000 (page 20); $7,500 (page 90)", "Couldn't verify"]
    assert limit == ["Each Occurrence Limit", "$2,000,000 (edited)", "Not specified"]


def test_not_found_cell_never_shows_the_model_value_even_if_stored():
    assert export_docx.cell_text(Cell(value="$9,999", state=EvidenceState.NOT_FOUND)) == "Couldn't verify"
    assert export_docx.cell_text(Cell(value="$1", state=EvidenceState.SCAN_READ)) != "$1"


def test_cell_footnotes_carry_cell_note_and_prose_footnotes_carry_the_source_note():
    data = data_for(slice1_content())
    notes = list(footnotes(export_docx.to_docx(data)).values())
    table = data.content.find_table("premium-table")
    expected = [export.cell_note(data, row.cells[c]) for row in table.rows for c in "AB"]
    expected = [n.replace("\n", "") for n in expected if n]
    assert notes[:2] == ["Source A1: a.pdf page 1", "Source A2: a.pdf page 3"]  # prose, list item
    assert notes[2:] == expected
    assert len(notes) == 7
    assert any(n.startswith("Read from scan, unverified") and "B1: b.pdf page 2" in n for n in notes)
    assert any(n.startswith("Conflicting values: ") and "A4: a.pdf page 90" in n for n in notes)
    assert any(n.startswith("Edited by user. Original value: $1,000,000") for n in notes)


def test_sources_list_ends_the_document():
    blob = export_docx.to_docx(data_for(proposal_content()))
    paras = body_paragraphs(blob)
    i = paras.index(("Heading1", "Sources"))
    assert [t for _, t in paras[i + 1:]] == [
        "A1: a.pdf page 1. Verified in the PDF text", "A2: a.pdf page 3. Verified in the PDF text",
        "A3: a.pdf page 20. Verified in the PDF text", "A4: a.pdf page 90. Verified in the PDF text",
        "B1: b.pdf page 2. Read from scan, unverified"]
    body = xml(blob, "word/document.xml").find("w:body", NS)
    assert etree.QName(body[-1]).localname == "sectPr"
    assert etree.QName(body[-2]).localname == "p"  # nothing but the list after the heading


def test_sources_list_is_present_without_chips():
    data = export.ExportData(title="T", run_id=1, version=1, content=ArtifactContent(), chips={})
    blob = export_docx.to_docx(data)
    assert body_paragraphs(blob)[-2:] == [("Heading1", "Sources"), ("Normal", "No sources are cited in this report.")]
    assert footnotes(blob) == {}


def test_same_version_exports_byte_identical():
    from datetime import datetime, timezone
    when = datetime(2026, 11, 2, 9, 30, tzinfo=timezone.utc)

    def once() -> bytes:
        return export_docx.to_docx(export.ExportData(title="Compare Quotes", run_id=7, version=2,
                                                     content=proposal_content(), chips=CHIPS, created_at=when,
                                                     theme={"font": "Georgia"}))
    a, b = once(), once()
    assert a == b
    core = parts(a)["docProps/core.xml"].decode()
    assert "2026-11-02T09:30:00Z" in core and "python-docx" not in core
    with zipfile.ZipFile(io.BytesIO(a)) as z:
        assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_untrusted_text_with_control_characters_and_formula_prefixes_exports():
    c = ArtifactContent(sections=[Section(id="s", number="1", title="Bad\x01Title", blocks=[
        ParagraphBlock(id="p", segments=[Segment(text="=HYPERLINK(\"x\")\x0b tail", passage_ids=[1])])])])
    paras = body_paragraphs(export_docx.to_docx(data_for(c)))
    assert ("Heading1", "1 BadTitle") in paras
    assert ("Normal", '=HYPERLINK("x") tail') in paras


# --------------------------------------------------------------------- theme


def style_xml(blob: bytes, style_id: str):
    styles = xml(blob, "word/styles.xml")
    return next(s for s in styles.findall("w:style", NS) if s.get(f"{{{W}}}styleId") == style_id)


def test_default_theme_applies_when_the_run_has_no_snapshot():
    blob = export_docx.to_docx(data_for(slice1_content()))
    h1 = style_xml(blob, "Heading1")
    assert h1.find("w:rPr/w:rFonts", NS).get(f"{{{W}}}ascii") == "Calibri"
    assert h1.find("w:rPr/w:rFonts", NS).get(f"{{{W}}}asciiTheme") is None  # a theme font would win
    assert h1.find("w:rPr/w:color", NS).get(f"{{{W}}}val") == "1F2A44"
    assert h1.find("w:rPr/w:color", NS).get(f"{{{W}}}themeColor") is None
    assert h1.find("w:rPr/w:sz", NS).get(f"{{{W}}}val") == "32"  # 16 pt in half-points


def test_snapshot_theme_sets_fonts_colors_and_sizes():
    theme = {"font": "Georgia", "heading_color": "#7a1f3d", "heading_sizes": {"title": 30, "h1": 20, "heading2": 14}}
    blob = export_docx.to_docx(data_for(proposal_content(), theme=theme))
    assert style_xml(blob, "Heading1").find("w:rPr/w:sz", NS).get(f"{{{W}}}val") == "40"
    assert style_xml(blob, "Heading2").find("w:rPr/w:sz", NS).get(f"{{{W}}}val") == "28"
    assert style_xml(blob, "Title").find("w:rPr/w:sz", NS).get(f"{{{W}}}val") == "60"
    assert style_xml(blob, "Heading1").find("w:rPr/w:color", NS).get(f"{{{W}}}val") == "7A1F3D"
    assert style_xml(blob, "Normal").find("w:rPr/w:rFonts", NS).get(f"{{{W}}}ascii") == "Georgia"
    defaults = xml(blob, "word/styles.xml").find("w:docDefaults/w:rPrDefault/w:rPr/w:rFonts", NS)
    assert defaults.get(f"{{{W}}}ascii") == "Georgia" and defaults.get(f"{{{W}}}asciiTheme") is None


def test_resolve_theme_falls_back_field_by_field_on_bad_input():
    d = export_docx.DEFAULT_THEME
    assert export_docx.resolve_theme(None) == d
    assert export_docx.resolve_theme("Georgia") == d
    bad = export_docx.resolve_theme({"font": "<script>", "heading_color": "red",
                                     "heading_sizes": {"1": 500, "2": "big", "9": 12, "3": True, "title": 20}})
    assert bad["font"] == d["font"] and bad["heading_color"] == d["heading_color"]
    assert bad["heading_sizes"] == {**d["heading_sizes"], "title": 20}
    assert export_docx.resolve_theme({"font": "Source Sans 3"})["font"] == "Source Sans 3"
    assert export_docx.DEFAULT_THEME["heading_sizes"]["title"] == 26  # never mutated


def test_theme_from_parameters_reads_the_template_snapshot():
    theme = {"font": "Georgia"}
    assert export.theme_from_parameters({"template": {"slug": "premier", "theme": theme}}) == theme
    assert export.theme_from_parameters({"template_snapshot": {"theme": theme}}) == theme
    assert export.theme_from_parameters({}) is None
    assert export.theme_from_parameters(None) is None
    assert export.theme_from_parameters({"template": {"theme": "Georgia"}}) is None
    assert export.theme_from_parameters({"template": 3}) is None


# --------------------------------------------------------------------- front matter detection


def test_front_matter_finds_cover_and_toc_by_section_or_block_id_only():
    fm = export.front_matter(proposal_content())
    assert fm.cover.id == "cover" and fm.toc.id == "toc" and [s.id for s in fm.body] == ["summary", "premium"]
    by_block = ArtifactContent(sections=[
        Section(id="s0", number="", title="Cover", blocks=[HeadingBlock(id="cover", text="Proposal")]),
        Section(id="s1", number="", title="Contents", blocks=[ListBlock(id="toc-list", items=[])]),
        Section(id="coverage", number="2", title="Coverage", blocks=[
            HeadingBlock(id="coverage-h", text="Coverage"), ListBlock(id="tocsin", items=[])])])
    fm = export.front_matter(by_block)
    assert fm.cover.id == "s0" and fm.toc.id == "s1" and [s.id for s in fm.body] == ["coverage"]
    none = export.front_matter(slice1_content())
    assert none.cover is None and none.toc is None and len(none.body) == 2


# --------------------------------------------------------------------- PDF cover and TOC


def pdf_pages(blob: bytes) -> list[str]:
    import fitz
    return [p.get_text() for p in fitz.open(stream=blob, filetype="pdf")]


def test_pdf_starts_with_cover_page_then_contents_with_page_numbers():
    pages = pdf_pages(export.to_pdf(data_for(proposal_content())))
    cover, contents, body = pages[0], pages[1], "\n".join(pages[2:])
    assert "Insurance Proposal for Cedar Hollow LLC" in cover and "Prepared by Dana Broker" in cover
    assert "Run 7, version 2" in cover and "Executive Summary" not in cover
    assert "Table of Contents" in contents
    lines = [x.strip() for x in contents.splitlines() if x.strip()]
    assert "1 Executive Summary" in lines and "3.2 Premium" in lines and "Sources" in lines
    leader = lines[lines.index("1 Executive Summary") + 1]
    assert leader.startswith(".") and leader.split()[-1] == "3"  # dot leader, then its page number
    assert "Executive Summary" in body and "Harborline" in body and "Sources" in body
    assert "Cover Page" not in "\n".join(pages)  # front-matter sections are not body sections


def test_pdf_with_a_cover_but_no_toc_has_no_contents_page():
    c = proposal_content()
    c = ArtifactContent(notices=c.notices, sections=[c.sections[0], *c.sections[2:]])
    pages = pdf_pages(export.to_pdf(data_for(c)))
    assert "Insurance Proposal for Cedar Hollow LLC" in pages[0] and "Executive Summary" not in pages[0]
    assert "Table of Contents" not in "\n".join(pages)
    assert "Executive Summary" in pages[1]


def test_pdf_of_a_slice1_report_is_unchanged_single_flow():
    pages = pdf_pages(export.to_pdf(data_for(slice1_content())))
    first = [x for x in pages[0].splitlines() if x.strip()]
    assert first[:2] == ["Compare Quotes", "Run 7, version 2"]
    assert "Executive Summary" in pages[0] and "Contents" not in "\n".join(pages)


# --------------------------------------------------------------------- API


def _done_run(api):
    doc = api.upload_pages(DOC_A, "a.pdf")
    run, _ = api.run([doc])
    return run, api.artifact(run["id"])


def test_api_exports_docx_with_footnotes_and_identical_bytes_twice(api):
    run, art = _done_run(api)
    url = f"/api/artifacts/{art['id']}/export"
    r = api.client.get(url, params={"format": "docx"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == export_docx.DOCX_MIME
    assert r.headers["content-disposition"] == f'attachment; filename="compare-quotes-run-{run["id"]}-v1.docx"'
    assert int(r.headers["x-export-tables"]) >= 1 and "x-export-note" not in r.headers
    notes = footnotes(r.content)
    assert notes and ref_ids(r.content) == sorted(notes)
    assert any("Harborline Specialty" in t for t in (c for row in table_texts(r.content) for c in row)) or \
        any("Harborline Specialty" in t for _, t in body_paragraphs(r.content))
    assert body_paragraphs(r.content)[-1][1].startswith("A")  # Sources list ends it
    assert api.client.get(url, params={"format": "docx"}).content == r.content
    old = api.client.get(url, params={"format": "docx", "version": 1})
    assert old.status_code == 200 and old.content == r.content


def test_api_docx_uses_the_theme_from_the_run_template_snapshot(api):
    run, art = _done_run(api)
    with db.session_scope(api.ctx.factory) as s:
        s.get(models.Run, run["id"]).parameters = {"template": {"slug": "premier",
                                                                "theme": {"font": "Georgia", "heading_color": "7A1F3D"}}}
    r = api.client.get(f"/api/artifacts/{art['id']}/export", params={"format": "docx"})
    assert r.status_code == 200
    assert style_xml(r.content, "Heading1").find("w:rPr/w:color", NS).get(f"{{{W}}}val") == "7A1F3D"
    assert style_xml(r.content, "Normal").find("w:rPr/w:rFonts", NS).get(f"{{{W}}}ascii") == "Georgia"


def test_api_docx_failure_is_export_failed_not_silent(api, monkeypatch, caplog):
    _, art = _done_run(api)

    def boom(data):
        raise ValueError("Harborline Specialty broke the renderer")
    monkeypatch.setattr(export_docx, "to_docx", boom)
    r = api.client.get(f"/api/artifacts/{art['id']}/export", params={"format": "docx"})
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "export_failed", "message": "Export failed. Retry."}}
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "format=docx" in logged and "ValueError" in logged and "Harborline" not in logged


def test_api_rejects_unknown_formats_and_keeps_xlsx_and_pdf(api):
    _, art = _done_run(api)
    url = f"/api/artifacts/{art['id']}/export"
    assert api.client.get(url, params={"format": "doc"}).status_code == 422
    assert api.client.get(url, params={"format": "xlsx"}).headers["content-type"] == export.XLSX_MIME
    assert api.client.get(url, params={"format": "pdf"}).headers["content-type"] == export.PDF_MIME
