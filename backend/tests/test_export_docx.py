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

from backend import export, export_docx
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
