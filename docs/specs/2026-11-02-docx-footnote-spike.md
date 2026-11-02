# DOCX footnote spike (slice 2, T5)

Date: 2026-11-02. Finding: **real Word footnotes work with python-docx plus low-level XML. The fallback
(superscript numbers plus a Sources list) is not needed.** The spike code is the production code:
`backend/export_docx.py` (`_Footnotes`, `_prepare_settings`, `_prepare_styles`).

## What it takes

python-docx 1.2.0 has no footnote API and its default template has no footnotes part. Five pieces are
written by hand:

1. Part `/word/footnotes.xml` (generic `docx.opc.part.Part`, content type
   `application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml`), related from the
   document part with the `.../relationships/footnotes` type. python-docx writes the `[Content_Types].xml`
   Override and the relationship itself on save.
2. The two special footnotes Word expects first: `w:type="separator" w:id="-1"` (run with `w:separator`)
   and `w:type="continuationSeparator" w:id="0"` (run with `w:continuationSeparator`).
3. Styles `footnote text` (id `FootnoteText`, 8 pt) and `footnote reference` (id `FootnoteReference`,
   character, superscript). The default template has neither.
4. `w:footnotePr` in `settings.xml` naming footnotes -1 and 0, inserted in `CT_Settings` sequence order
   (after `w:updateFields`, before `w:endnotePr`/`w:compat`).
5. Per citation, a body run `<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteReference w:id="n"/></w:r>`
   and a footnote `n` whose paragraph starts with the `footnoteRef` run. Ids run 1..n in reading order;
   Word numbers them itself.

Footnote text reuses `cell_note` (table cells) and `_source_note` (prose), as decided in C2.

## How it was verified

- Offline tests (`backend/tests/test_export_docx.py`, first block): content type and relationship,
  separators -1 and 0, every reference has exactly one footnote, ids 1..n in reading order, styles and
  settings order, every part well-formed, reopen with python-docx and re-save keeps the footnotes.
- Word itself turned out to be installed on the owner's machine (Office 16). A one-off COM check
  (not part of the test suite) opened the export with `OpenAndRepair=false`: no repair prompt,
  `Footnotes.Count` = 7 with the expected texts and anchors, `TablesOfContents.Count` = 1 and the TOC
  updated with page numbers, theme font and heading colour applied, saved to PDF with footnotes at the
  page foot.
- LibreOffice headless conversion to PDF also renders all footnotes.

## Related decisions made in the spike

- TOC: a real `TOC \o "1-2" \h \z \u` field, pre-filled with the section headings plus Sources so it reads
  correctly before an update; `w:updateFields` makes Word fill in page numbers on open (Word asks the
  user once whether to update fields).
- The "may be out of date" tag is its own paragraph under the heading so it stays out of the TOC.
- Byte-identical re-export: core properties use the version's `created_at`; zip entries are rewritten with
  a fixed timestamp and attributes in the original order.
- Document text is untrusted: XML-illegal control characters are dropped before writing.
