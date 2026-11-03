"""The issued certificate PDF (slice 4, C7): the own one-page layout ("own-v1", C6), from frozen values.

Not an ACORD form (no licensed blank, C6): producer, insured, insurers with NAIC, a coverage table (one
row per printed policy with its wording and limits), description of operations, holder, and a
cancellation clause, in the Paper trail voice (serif names, Inter-like sans for the system; Helvetica
and Times stand in, built into every PDF reader). A value that is not verified prints "Not stated"
(pipeline.certificate already wrote it so).

Every piece of free text is escaped before it reaches a reportlab Paragraph (A3/D6: "Smith & <Sons>" is
text, not markup). The page is one page by construction: the story is wrapped in KeepInFrame(shrink),
so the longest inputs the bounds allow (holder 120, address 300, description 1,500 characters) shrink
the type rather than add a page; a test renders them and counts the pages.

Pure: bytes in memory, nothing written to disk (D16: the bytes go into the issue row).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from xml.sax.saxutils import escape

from backend.pipeline import coi_checks
from backend.schemas import CertificateBlock

PDF_MIME = "application/pdf"
CANCELLATION = ("Should any of the policies above be cancelled before their expiration dates, notice will be "
                "delivered in line with the policy provisions.")
DISCLAIMER = ("This certificate is issued as a matter of information only and confers no rights on the "
              "certificate holder. It does not amend, extend or alter the coverage of the policies listed. "
              "Every value printed here was checked against the policy documents; a value that could not be "
              "verified is printed as Not stated.")
WORDING_SHORT = {coi_checks.ADDITIONAL_INSURED: "Additional insured", coi_checks.WAIVER: "Waiver of subrogation",
                 coi_checks.PRIMARY_NONCONTRIBUTORY: "Primary and non-contributory"}


@dataclass(frozen=True)
class CertificatePdf:
    certificate: CertificateBlock
    issued_on: str  # ISO date
    reference: str  # "Run 4, version 2"


def _t(value: str | None) -> str:
    return escape(value if value else "Not stated").replace("\n", "<br/>")


def render(data: CertificatePdf) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import KeepInFrame, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ink, muted, rule = colors.HexColor("#2B2A27"), colors.HexColor("#5F5B53"), colors.HexColor("#D8D2C4")
    title = ParagraphStyle("title", fontName="Times-Bold", fontSize=17, leading=21, textColor=ink)
    head = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=muted,
                          spaceBefore=6, spaceAfter=2)
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=8.5, leading=11, textColor=ink)
    name = ParagraphStyle("name", parent=body, fontName="Times-Bold", fontSize=10.5, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=7, leading=9, textColor=muted)
    cell = ParagraphStyle("cell", parent=body, fontSize=7.5, leading=9.5)

    cert = data.certificate
    width = letter[0] - 1.2 * inch
    story: list = [
        Paragraph("Certificate of Insurance", title),
        Paragraph(f"Certificate date {_t(cert.certificate_date)}. Issued {_t(data.issued_on)}. "
                  f"{_t(data.reference)}.", small),
        Spacer(1, 4), Paragraph(_t(DISCLAIMER), small),
    ]

    def block(label: str, flowables: list) -> list:
        return [Paragraph(escape(label.upper()), head), *flowables]

    producer = cert.producer.cell.value if cert.producer else None
    insured = {f.key: f.cell.value for f in cert.insured}
    left = block("Producer", [Paragraph(_t(producer), body)]) + block("Insured", [
        Paragraph(_t(insured.get("cert:named_insured")), name),
        Paragraph(_t(insured.get("cert:insured_address")), body)])
    insurer_rows = [[Paragraph(f"<b>{escape(i.letter)}</b>", cell), Paragraph(_t(i.name), cell),
                     Paragraph(f"NAIC {_t(i.naic)}", cell)] for i in cert.insurers] or \
        [[Paragraph("", cell), Paragraph("Not stated", cell), Paragraph("", cell)]]
    insurers = Table(insurer_rows, colWidths=[0.25 * inch, 2.1 * inch, 0.85 * inch])
    insurers.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1)]))
    right = block("Insurers affording coverage", [insurers])
    top = Table([[left, right]], colWidths=[width * 0.45, width * 0.55])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [Spacer(1, 2), top]

    rows = [[Paragraph(f"<b>{h}</b>", cell) for h in
             ("Ins", "Type of insurance", "Policy number", "Effective", "Expiration", "Limits")]]
    for p in cert.policies:
        values = {f.key: f for f in p.fields}

        def v(key: str) -> str:
            f = values.get(key)
            return _t(f.cell.value if f else None)

        wording = "".join(f"<br/><font size=6.5 color='#5F5B53'>{escape(WORDING_SHORT.get(w, w))}</font>"
                          for w in p.wording)
        limits = [f"{escape(f.label)}: {_t(f.cell.value)}" for f in p.fields
                  if f.key in coi_checks.LINE_LIMITS.get(p.line, ())]
        rows.append([Paragraph(escape(p.insurer or ""), cell), Paragraph(escape(p.line_label) + wording, cell),
                     Paragraph(v("cert:policy_number"), cell), Paragraph(v("cert:effective_date"), cell),
                     Paragraph(v("cert:expiration_date"), cell),
                     Paragraph("<br/>".join(limits) if limits else "Not stated", cell)])
    if len(rows) == 1:
        rows.append([Paragraph("", cell), Paragraph("No coverage is printed.", cell)] + [Paragraph("", cell)] * 4)
    coverage = Table(rows, colWidths=[0.35 * inch, 1.55 * inch, 1.15 * inch, 0.8 * inch, 0.8 * inch,
                                      width - 4.65 * inch], repeatRows=1)
    coverage.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, rule), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F1EA")), ("LEFTPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += block("Coverages", [coverage])
    story += block("Description of operations", [Paragraph(_t(cert.description) if cert.description else "None",
                                                           body)])
    holder = block("Certificate holder", [Paragraph(_t(cert.holder_name), name),
                                          Paragraph(_t(cert.holder_address) if cert.holder_address else "", body)])
    cancel = block("Cancellation", [Paragraph(_t(CANCELLATION), body)])
    bottom = Table([[holder, cancel]], colWidths=[width * 0.5, width * 0.5])
    bottom.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("BOX", (0, 0), (0, 0), 0.5, rule), ("LEFTPADDING", (0, 0), (0, 0), 6)]))
    story += [Spacer(1, 4), bottom]

    buf = io.BytesIO()
    margin = 0.6 * inch
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=margin, rightMargin=margin, topMargin=margin,
                            bottomMargin=margin, title="Certificate of Insurance", author="BrokerDocs (local)")
    frame_height = letter[1] - 2 * margin - 2  # one page by construction (shrink to fit)
    doc.build([KeepInFrame(width, frame_height, story, mode="shrink")])
    return buf.getvalue()


def page_count(pdf: bytes) -> int:
    import fitz  # PyMuPDF

    with fitz.open(stream=pdf, filetype="pdf") as d:
        return d.page_count


__all__ = ["PDF_MIME", "CertificatePdf", "page_count", "render"]
