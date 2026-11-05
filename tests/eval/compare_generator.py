"""Synthetic Compare Quotes fixtures in three formats, with hand-written truth (slice 6, task T6).

Spec ``docs/specs/2026-11-05-compare-match-design.md`` section 6 and ledger R6, R7, R9 (D10), R12 (D13):

* ``three_format``: one account (Larchmont Tile and Stone LLC) quoted three ways: A a PDF quote, B a Word (.docx)
  quote, C an Excel (.xlsx) coverage summary. It exercises every mark kind: a scheduled-only additional insured, a
  scheduled-only waiver and a not-included primary and non-contributory (worse, "Others: ..."), exclusions worse
  against a printed schedule ("None of these" only because A prints its full schedule), a partial exclusion list
  (C prints one exclusion and no schedule) and the lowest premium (best).
* ``docx_variant``: a second account (Fenmore Millwork Co.): A a PDF proposal, B a Word quote, C a PDF quote. It
  exercises D10 the other way round: B prints a full schedule with a watch-list exclusion, A prints only "selected
  exclusions", so B is never marked worse because A printed less, while A is worse against B's complete schedule;
  C prints an exclusion title but no schedule (Not stated, not "None of these"); primary and non-contributory is
  stated by one quote only (no mark).

Every value below is written by hand from what the documents print. Expected marks (``EXPECTED_MARKS``) are
written by hand as well and never computed by backend code (O6): the gate compares them with the marks in the run's
artifact response. The only backend code used here is the upload renderer (``backend.uploads.prepare_office``), to
find on which page of the rendered record (the PDF the pipeline reads and cites) each printed line lands.

Deterministic: the same library versions give byte-identical files. Word and Excel packages are rewritten with fixed
zip timestamps and core properties. All names are fictional; every document says "Synthetic sample data".

Usage::

    python tests/eval/compare_generator.py --out tests/fixtures/compare
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parents[1]
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import quote_generator as qg  # noqa: E402

GENERATOR_VERSION = 1
TRUTH_SCHEMA_VERSION = 1
DEFAULT_SEED = 20261105
SYNTHETIC_LABEL = "Synthetic sample data"
SYNTHETIC_LINE = (f"{SYNTHETIC_LABEL}: a quote made for software testing. Every name, address and amount is "
                  "fictional.")
GATE_BUCKET = "compare_formats"
NOT_STATED = qg.NOT_STATED
FIXED_ZIP_TIME = (2026, 11, 5, 0, 0, 0)
FIXED_DATE = datetime(2026, 11, 5, 0, 0, 0)
DEFAULT_OUT = REPO_ROOT / "tests" / "fixtures" / "compare"

# The four term rows in the order a document prints them (labels as printed).
TERM_LABELS = {"additional_insured": "Additional Insured", "waiver_of_subrogation": "Waiver of Subrogation",
               "primary_noncontributory": "Primary and Non-Contributory"}


# ---------------------------------------------------------------------------
# What each document prints (hand-written)
# ---------------------------------------------------------------------------


@dataclass
class Doc:
    fixture_id: str
    fmt: str  # pdf | docx | xlsx
    kind: str  # quote | proposal | summary (document_kind, display only)
    carrier: str
    title: str
    profile: dict[str, str | None]  # quote_generator FIELDS (+ "premium", printed, unscored) -> printed value
    terms: dict[str, str]  # term row -> printed wording after the label (absent: the document is silent)
    term_values: dict[str, str]  # term row -> expected cell category (hand-written)
    schedule_heading: str | None = None  # a printed heading that sets out all forms and exclusions (R9)
    schedule: list[tuple[str, str]] = field(default_factory=list)  # (form number, title) rows under the heading
    exclusion_titles: list[str] = field(default_factory=list)  # titles of the exclusions printed
    partial_label: str | None = None  # "Selected exclusions (...)": a list that is not the full schedule
    exclusions_value: str = NOT_STATED  # expected notable_exclusions cell (hand-written)
    description: str = ""


def _profile(insured: tuple[str, str], producer: str, carrier: str, *, admitted: bool, rating: str, period: str,
             form: str, territory: str | None, premium: str, fees: str, total: str, occ: str, agg: str,
             deductible: str, valid: str) -> dict[str, str | None]:
    return {"named_insured": insured[0], "address": insured[1], "carrier_name": carrier,
            "admitted_status": "Admitted" if admitted else "Non-Admitted", "am_best_rating": rating,
            "policy_term": period, "territory": territory, "producer": producer, "policy_form": form,
            "premium": premium, "taxes_and_fees": fees, "total_premium": total, "each_occurrence_limit": occ,
            "aggregate_limit": agg, "retention": deductible, "rate": None, "quote_expiration": valid}


OCC_FORM = "CG 00 01 04 13 Commercial General Liability Coverage Form (Occurrence)"
LARCHMONT = ("Larchmont Tile and Stone LLC", "742 Ferrous Way, Unit 4, Wilmington, DE 19801")
LARCHMONT_PRODUCER = "Copperline Insurance Advisors LLC"
FENMORE = ("Fenmore Millwork Co.", "19 Tannery Row, Lowell, MA 01852")
FENMORE_PRODUCER = "Tamsin Gale Risk Services Inc."
POLLUTION = "Total Pollution Exclusion Endorsement"


def build_docs() -> list[Doc]:
    kestrel = "Kestrel Point Casualty Company"
    brackwater = "Brackwater Specialty Insurance Company"
    hollis = "Hollis Bay Mutual Insurance Company"
    ember = "Ember Ridge Assurance Company"
    quillon = "Quillon Indemnity Company"
    sorrel = "Sorrel Creek Fire and Casualty Company"
    return [
        # ---------------- three_format
        Doc("cmp_tri_a_quote", "pdf", "quote", kestrel, "Commercial General Liability Quote",
            _profile(LARCHMONT, LARCHMONT_PRODUCER, kestrel, admitted=True, rating="A (Excellent), XI",
                     period="03/01/2027 to 03/01/2028", form=OCC_FORM,
                     territory="United States of America, its territories and possessions, Puerto Rico and Canada",
                     premium="$18,170.00", fees="$250.00", total="$18,420.00", occ="$1,000,000", agg="$2,000,000",
                     deductible="$2,500", valid="02/15/2027"),
            terms={"additional_insured": "Blanket additional insured where required by written contract",
                   "waiver_of_subrogation": "Blanket waiver where required by written contract",
                   "primary_noncontributory": "Included where required by written contract"},
            term_values={"additional_insured": "Blanket", "waiver_of_subrogation": "Blanket",
                         "primary_noncontributory": "Included"},
            schedule_heading="Schedule of Forms and Endorsements",
            schedule=[("CG 21 49 09 99", POLLUTION), ("CG 21 09 06 15", "Exclusion: Unmanned Aircraft"),
                      ("CG 21 67 12 04", "Fungi or Bacteria Exclusion")],
            exclusion_titles=[POLLUTION, "Exclusion: Unmanned Aircraft", "Fungi or Bacteria Exclusion"],
            exclusions_value="None of these",
            description="PDF quote, 2 pages. Blanket additional insured and waiver, primary and non-contributory "
                        "included; prints its full Schedule of Forms and Endorsements with three exclusions, none "
                        "on the watch list (None of these)."),
        Doc("cmp_tri_b_quote", "docx", "quote", brackwater, "Commercial General Liability Quote",
            _profile(LARCHMONT, LARCHMONT_PRODUCER, brackwater, admitted=False, rating="A- (Excellent), VIII",
                     period="March 1, 2027 to March 1, 2028", form=OCC_FORM,
                     territory="United States of America, its territories and possessions",
                     premium="$15,900.00", fees="$1,075.00", total="$16,975.00", occ="$1,000,000", agg="$2,000,000",
                     deductible="$5,000", valid="February 15, 2027"),
            terms={"additional_insured": "Scheduled additional insureds only, as listed in the schedule",
                   "waiver_of_subrogation": "Blanket where required by written contract",
                   "primary_noncontributory": "Not included"},
            term_values={"additional_insured": "Scheduled only", "waiver_of_subrogation": "Blanket",
                         "primary_noncontributory": "Not included"},
            schedule_heading="Schedule of Forms and Endorsements",
            schedule=[("BSI 21 03 01 24", "Exclusion: Action Over"),
                      ("BSI 21 07 01 24", "Exclusion: Residential Construction"), ("CG 21 49 09 99", POLLUTION)],
            exclusion_titles=["Exclusion: Action Over", "Exclusion: Residential Construction", POLLUTION],
            exclusions_value="Action over, Residential / habitational work",
            description="Word quote. Lowest Total Payable of the three; additional insured scheduled only, primary "
                        "and non-contributory not included; its full schedule lists action over and residential "
                        "construction exclusions."),
        Doc("cmp_tri_c_summary", "xlsx", "summary", hollis, "Coverage Summary",
            _profile(LARCHMONT, LARCHMONT_PRODUCER, hollis, admitted=True, rating="A+ (Superior), XV",
                     period="2027-03-01 to 2028-03-01", form=OCC_FORM, territory="United States of America",
                     premium="$19,630.00", fees="$250.00", total="$19,880.00", occ="$1,000,000", agg="$2,000,000",
                     deductible="$1,000", valid="2027-02-15"),
            terms={"additional_insured": "Blanket, where required by written contract",
                   "waiver_of_subrogation": "Scheduled only, per the schedule of waivers",
                   "primary_noncontributory": "Included"},
            term_values={"additional_insured": "Blanket", "waiver_of_subrogation": "Scheduled only",
                         "primary_noncontributory": "Included"},
            exclusion_titles=["Subsidence / Earth Movement Exclusion"], partial_label="Key Exclusions",
            exclusions_value="Subsidence / earth movement",
            description="Excel coverage summary, one sheet of term and value rows. Waiver scheduled only; lists one "
                        "key exclusion (subsidence) and no schedule, so its list is partial."),
        # ---------------- docx_variant
        Doc("cmp_docx_a_proposal", "pdf", "proposal", ember, "Insurance Proposal",
            _profile(FENMORE, FENMORE_PRODUCER, ember, admitted=True, rating="A (Excellent), XII",
                     period="07/01/2027 to 07/01/2028", form=OCC_FORM,
                     territory="United States of America, its territories and possessions",
                     premium="$21,800.00", fees="$350.00", total="$22,150.00", occ="$1,000,000", agg="$2,000,000",
                     deductible="$2,500", valid="06/15/2027"),
            terms={"additional_insured": "Not included",
                   "waiver_of_subrogation": "Blanket, where required by written contract"},
            term_values={"additional_insured": "Not included", "waiver_of_subrogation": "Blanket",
                         "primary_noncontributory": NOT_STATED},
            exclusion_titles=["Wrap-up / OCIP Exclusion"],
            partial_label="Selected exclusions (see the policy for the full list)",
            exclusions_value="Wrap-up / OCIP",
            description="PDF proposal, 2 pages. Additional insured not included; blanket waiver; silent on primary "
                        "and non-contributory; prints only selected exclusions (wrap-up), no schedule."),
        Doc("cmp_docx_b_quote", "docx", "quote", quillon, "Commercial General Liability Quote",
            _profile(FENMORE, FENMORE_PRODUCER, quillon, admitted=False, rating="A- (Excellent), IX",
                     period="July 1, 2027 to July 1, 2028", form=OCC_FORM, territory="United States of America",
                     premium="$20,850.00", fees="$1,050.00", total="$21,900.00", occ="$1,000,000", agg="$2,000,000",
                     deductible="$5,000", valid="June 15, 2027"),
            terms={"additional_insured": "Blanket additional insured status for any person or organization when "
                                         "required by written contract",
                   "waiver_of_subrogation": "Not included",
                   "primary_noncontributory": "Included"},
            term_values={"additional_insured": "Blanket", "waiver_of_subrogation": "Not included",
                         "primary_noncontributory": "Included"},
            schedule_heading="Schedule of Forms and Endorsements",
            schedule=[("QIC 21 44 03 25", "Exclusion: Designated Ongoing Operations"), ("CG 21 49 09 99", POLLUTION)],
            exclusion_titles=["Exclusion: Designated Ongoing Operations", POLLUTION],
            exclusions_value="Designated ongoing operations",
            description="Word quote. Blanket additional insured, no waiver, primary and non-contributory included; "
                        "its full schedule lists a designated ongoing operations exclusion."),
        Doc("cmp_docx_c_quote", "pdf", "quote", sorrel, "Commercial General Liability Quote",
            _profile(FENMORE, FENMORE_PRODUCER, sorrel, admitted=True, rating="A (Excellent), X",
                     period="07/01/2027 - 07/01/2028", form=OCC_FORM, territory="State of Massachusetts only",
                     premium="$20,150.00", fees="$250.00", total="$20,400.00", occ="$1,000,000", agg="$2,000,000",
                     deductible="$2,500", valid="06/15/2027"),
            terms={"waiver_of_subrogation": "Scheduled only, per the attached schedule"},
            term_values={"additional_insured": NOT_STATED, "waiver_of_subrogation": "Scheduled only",
                         "primary_noncontributory": NOT_STATED},
            exclusion_titles=["Unmanned Aircraft Exclusion"],
            partial_label="Selected exclusions (see the policy for the full list)",
            exclusions_value=NOT_STATED,
            description="PDF quote, 2 pages. Lowest Total Payable of the three; waiver scheduled only; silent on "
                        "additional insured and primary and non-contributory; prints one selected exclusion and no "
                        "schedule (Not stated, never None of these)."),
    ]


@dataclass
class Case:
    case_id: str
    documents: list[str]  # column order
    description: str
    expected_marks: list[dict]


def _mark(document: str, row: str, kind: str, reason: str) -> dict:
    return {"document": document, "row": row, "kind": kind, "reason": reason}


# Hand-written expected marks (R12, D13), read from the documents above with the rules in API.md: endorsement rows
# rank Blanket > Scheduled only > Not included (P&NC Included > Not included) and need two stated values; an
# exclusion is worse only against a document that prints a full schedule; the strictly lowest Total Payable is best
# when every document states one.
CASES = [
    Case("three_format", ["cmp_tri_a_quote", "cmp_tri_b_quote", "cmp_tri_c_summary"],
         "One account quoted as a PDF quote (A), a Word quote (B) and an Excel coverage summary (C).",
         [_mark("cmp_tri_b_quote", "additional_insured", "worse", "Others: Blanket"),
          _mark("cmp_tri_c_summary", "waiver_of_subrogation", "worse", "Others: Blanket"),
          _mark("cmp_tri_b_quote", "primary_noncontributory", "worse", "Others: Included"),
          _mark("cmp_tri_b_quote", "notable_exclusions", "worse",
                "Excludes action over and residential / habitational work"),
          _mark("cmp_tri_c_summary", "notable_exclusions", "worse", "Excludes subsidence / earth movement"),
          _mark("cmp_tri_b_quote", "total_premium", "best", "Lowest premium")]),
    Case("docx_variant", ["cmp_docx_a_proposal", "cmp_docx_b_quote", "cmp_docx_c_quote"],
         "A second account: a PDF proposal (A), a Word quote (B) and a PDF quote (C).",
         [_mark("cmp_docx_a_proposal", "additional_insured", "worse", "Others: Blanket"),
          _mark("cmp_docx_b_quote", "waiver_of_subrogation", "worse", "Others: Blanket"),
          _mark("cmp_docx_c_quote", "waiver_of_subrogation", "worse", "Others: Blanket"),
          _mark("cmp_docx_a_proposal", "notable_exclusions", "worse", "Excludes wrap-up / OCIP"),
          _mark("cmp_docx_c_quote", "total_premium", "best", "Lowest premium")]),
]


# ---------------------------------------------------------------------------
# Printed lines (one source for every format)
# ---------------------------------------------------------------------------


DECL = ["named_insured", "address", "producer", "carrier_name", "admitted_status", "am_best_rating", "policy_term",
        "policy_form", "territory"]
PREMIUM = ["premium", "taxes_and_fees", "total_premium"]
LIMITS = ["each_occurrence_limit", "aggregate_limit", "retention"]


def _fact_rows(doc: Doc, keys: list[str]) -> list[tuple[str, str, str | None]]:
    """(label, value, scored field or None) for the printed fields among `keys`."""
    out = []
    for k in keys:
        v = doc.profile.get(k)
        if v is not None:
            out.append((qg.LABELS[k], v, k if k in qg.FIELDS else None))
    return out


def _term_rows(doc: Doc) -> list[tuple[str, str, str]]:
    return [(TERM_LABELS[k], doc.terms[k], k) for k in TERM_LABELS if k in doc.terms]


# ---------------------------------------------------------------------------
# PDF (PyMuPDF, through quote_generator's page model)
# ---------------------------------------------------------------------------


def build_pdf(doc: Doc) -> qg.Fixture:
    n = 2
    p1 = qg.Cursor()
    qg._header(p1, doc.carrier, f"{doc.title}    {SYNTHETIC_LABEL}", 1, n)
    p1.line(SYNTHETIC_LINE, size=8)
    p1.y += 6
    p1.line("Declarations", size=12, bold=True)
    p1.y += 4
    for f in DECL:
        p1.fact(doc.profile, f)
    p1.y += 10
    p1.line("Premium Summary", size=12, bold=True)
    p1.y += 4
    for f in PREMIUM:
        p1.fact(doc.profile, f)
    p1.y += 10
    p1.line("Limits of Insurance", size=12, bold=True)
    p1.y += 4
    for f in LIMITS:
        p1.fact(doc.profile, f)

    p2 = qg.Cursor()
    qg._header(p2, doc.carrier, f"{doc.title}    {SYNTHETIC_LABEL}", 2, n)
    p2.line("Coverage Highlights" if doc.kind == "proposal" else "Endorsements and Conditions", size=12, bold=True)
    p2.y += 4
    for label, wording, _ in _term_rows(doc):
        p2.line(f"{label}: {wording}")
    p2.y += 8
    if doc.schedule_heading:
        p2.line(doc.schedule_heading, size=11, bold=True)
        for number, title in doc.schedule:
            p2.line(f"{number} {title}", size=9.5)
        p2.y += 8
    if doc.partial_label:
        p2.line(f"{doc.partial_label}: {'; '.join(doc.exclusion_titles)}")
        p2.y += 8
    p2.fact(doc.profile, "quote_expiration")
    p2.y += 8
    p2.para(qg.FILLER[1])
    return qg.Fixture(doc.fixture_id, GATE_BUCKET, GATE_BUCKET, doc.description,
                      [qg.PageSpec(runs=p.runs) for p in (p1, p2)], carrier=doc.carrier,
                      accepted_values=qg._accepted(doc.profile))


# ---------------------------------------------------------------------------
# Word (python-docx) and Excel (openpyxl), with fixed package metadata
# ---------------------------------------------------------------------------


_CORE_DATE = re.compile(rb"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:\2>)")


def normalize_package(data: bytes) -> bytes:
    """The same Office zip with fixed entry timestamps and attributes and fixed core-property dates, so the bytes
    depend only on the content."""
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                body = _CORE_DATE.sub(rb"\g<1>2026-11-05T00:00:00Z\g<3>", body)
            zi = zipfile.ZipInfo(info.filename, date_time=FIXED_ZIP_TIME)
            zi.create_system = 0
            zi.external_attr = 0
            zi.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(zi, body, compresslevel=6)
    return buf.getvalue()


def build_docx(doc: Doc) -> bytes:
    import docx  # python-docx (a backend dependency)

    d = docx.Document()
    cp = d.core_properties
    cp.author, cp.last_modified_by, cp.title = "synthetic", "synthetic", f"{doc.fixture_id} ({SYNTHETIC_LABEL})"
    cp.subject, cp.keywords = "synthetic sample data: insurance quote for software testing", SYNTHETIC_LABEL
    cp.created, cp.modified, cp.revision = FIXED_DATE, FIXED_DATE, 1
    d.add_heading(doc.carrier, level=1)
    d.add_paragraph(f"{doc.title}. {SYNTHETIC_LINE}")
    for heading, keys in (("Declarations", DECL), ("Premium Summary", PREMIUM), ("Limits of Insurance", LIMITS)):
        d.add_heading(heading, level=2)
        for label, value, _ in _fact_rows(doc, keys):
            d.add_paragraph(f"{label}: {value}")
    d.add_heading("Endorsements and Conditions", level=2)
    for label, wording, _ in _term_rows(doc):
        d.add_paragraph(f"{label}: {wording}")
    if doc.schedule_heading:
        d.add_heading(doc.schedule_heading, level=2)
        t = d.add_table(rows=1 + len(doc.schedule), cols=2)
        t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Form", "Title"
        for i, (number, title) in enumerate(doc.schedule, 1):
            t.rows[i].cells[0].text, t.rows[i].cells[1].text = number, title
    if doc.partial_label:
        d.add_paragraph(f"{doc.partial_label}: {'; '.join(doc.exclusion_titles)}")
    d.add_heading("Binding Terms", level=2)
    for label, value, _ in _fact_rows(doc, ["quote_expiration"]):
        d.add_paragraph(f"{label}: {value}")
    d.add_paragraph(qg.FILLER[1])
    buf = io.BytesIO()
    d.save(buf)
    return normalize_package(buf.getvalue())


def xlsx_rows(doc: Doc) -> list[tuple[str, str]]:
    rows = [("Term", "Value"), (SYNTHETIC_LABEL, "Coverage summary made for software testing; every name and amount "
                                                "is fictional"),
            ("Document", f"{doc.carrier} {doc.title}")]
    rows += [(label, value) for label, value, _ in _fact_rows(doc, DECL + PREMIUM + LIMITS)]
    rows += [(label, wording) for label, wording, _ in _term_rows(doc)]
    if doc.partial_label:
        rows += [(doc.partial_label, t) for t in doc.exclusion_titles]
    rows += [(label, value) for label, value, _ in _fact_rows(doc, ["quote_expiration"])]
    return rows


def build_xlsx(doc: Doc) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Coverage Summary"
    for r in xlsx_rows(doc):
        ws.append(list(r))  # every value is text (no number formats to lose, R8)
    wb.properties.creator = wb.properties.lastModifiedBy = "synthetic"
    wb.properties.title = f"{doc.fixture_id} ({SYNTHETIC_LABEL})"
    wb.properties.created = wb.properties.modified = FIXED_DATE
    buf = io.BytesIO()
    wb.save(buf)
    return normalize_package(buf.getvalue())


def render_record(data: bytes, filename: str) -> bytes:
    """The PDF record the upload route stores for a Word or Excel file (backend.uploads.prepare_office)."""
    from backend.pipeline.parse import DEFAULT_LIMITS
    from backend.uploads import prepare_office

    return prepare_office(data, filename, DEFAULT_LIMITS).pdf


# ---------------------------------------------------------------------------
# Truth
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    return " ".join(s.split())


def record_texts(pdf: bytes) -> list[str]:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        return [_norm(p.get_text()) for p in d]


def _pages_with(texts: list[str], needle: str) -> list[int]:
    n = _norm(needle)
    return [i for i, t in enumerate(texts, 1) if n in t]


def _located(texts: list[str], value: str, quote: str) -> tuple[int, list[int]]:
    """(page of the quote, pages where the value occurs) in the record; both must exist."""
    qp = _pages_with(texts, quote)
    vp = _pages_with(texts, value)
    if not qp or not vp:
        raise ValueError(f"{quote!r} not found in the rendered record")
    return qp[0], vp


def _quote(doc: Doc, label: str, value: str) -> str:
    """The printed text a reader would quote for one labelled value: the whole line in a PDF or Word document, the
    value cell in a spreadsheet (its label is another cell)."""
    return value if doc.fmt == "xlsx" else f"{label}: {value}"


def build_doc_truth(doc: Doc, seed: int, source: bytes, record: bytes) -> dict:
    texts = record_texts(record)
    facts: dict[str, dict | None] = {f: None for f in qg.FIELDS}
    for label, value, key in _fact_rows(doc, DECL + PREMIUM + LIMITS + ["quote_expiration"]):
        if key is None:
            continue
        quote = _quote(doc, label, value)
        page, pages = _located(texts, value, quote)
        facts[key] = {"value": value, "page": page, "pages": pages, "quote": quote, "source": "text",
                      "accepted_values": qg._accepted(doc.profile).get(key, [])}
    terms: dict[str, dict] = {}
    for key in TERM_LABELS:
        expected = doc.term_values[key]
        if key in doc.terms:
            wording = doc.terms[key]
            quote = _quote(doc, TERM_LABELS[key], wording)
            page, pages = _located(texts, wording, quote)
            terms[key] = {"value": expected, "printed": wording, "page": page, "pages": pages, "quote": quote,
                          "accepted_values": []}
        else:
            if expected != NOT_STATED:
                raise ValueError(f"{doc.fixture_id}.{key}: a silent document is Not stated")
            terms[key] = {"value": NOT_STATED, "printed": None, "page": None, "pages": [], "quote": None,
                          "accepted_values": []}
    schedule = None
    if doc.schedule_heading:
        page, pages = _located(texts, doc.schedule_heading, doc.schedule_heading)
        schedule = {"value": doc.schedule_heading, "title": doc.schedule_heading, "page": page, "pages": pages,
                    "quote": doc.schedule_heading}
    titles = []
    for title in doc.exclusion_titles:
        if doc.schedule_heading:
            number = next(n for n, t in doc.schedule if t == title)
            quote = title if doc.fmt != "pdf" else f"{number} {title}"
        elif doc.fmt == "xlsx":
            quote = title
        else:
            quote = f"{doc.partial_label}: {'; '.join(doc.exclusion_titles)}"
        page, pages = _located(texts, title, quote)
        titles.append({"value": title, "title": title, "page": page, "pages": pages, "quote": quote})
    all_pages = sorted({p for t in titles for p in t["pages"]} | set(schedule["pages"] if schedule else []))
    terms["notable_exclusions"] = {"value": doc.exclusions_value, "complete": schedule is not None,
                                   "schedule": schedule, "titles": titles, "pages": all_pages, "accepted_values": []}
    with fitz.open(stream=record, filetype="pdf") as d:
        page_count = d.page_count
    return {
        "schema_version": TRUTH_SCHEMA_VERSION,
        "generator": "compare_generator",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "fixture_id": doc.fixture_id,
        "file": f"{doc.fixture_id}.{doc.fmt}",
        "format": doc.fmt,
        "document_kind": doc.kind,
        "variant": GATE_BUCKET,
        "gate_bucket": GATE_BUCKET,
        "description": f"{SYNTHETIC_LABEL}. {doc.description}",
        "synthetic": True,
        "synthetic_label": SYNTHETIC_LABEL,
        "carrier": doc.carrier,
        "page_count": page_count,
        "size_bytes": len(source),
        "record_sha256": hashlib.sha256(record).hexdigest(),
        "expect": {"parse": "ok", "failure_reason": None, "ocr_pages": [], "option_count": 1},
        "primary_option": None,
        "facts": facts,
        "compare_terms": terms,
        "decoys": [],
        "injection": None,
        "password": None,
        "notes": "Truth written by hand from the printed text; pages are those of the rendered record.",
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _write_json(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def library_versions() -> dict[str, str]:
    import docx
    import openpyxl
    import reportlab

    return {"pymupdf": fitz.VersionBind, "python_docx": getattr(docx, "__version__", "?"),
            "openpyxl": openpyxl.__version__, "reportlab": reportlab.Version}


def generate(out_dir: Path, seed: int = DEFAULT_SEED) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"generator": "compare_generator", "generator_version": GENERATOR_VERSION, "seed": seed,
                "synthetic_label": SYNTHETIC_LABEL, "libraries": library_versions(), "documents": [], "cases": []}
    for i, doc in enumerate(build_docs()):
        name = f"{doc.fixture_id}.{doc.fmt}"
        if doc.fmt == "pdf":
            source = qg.render_pdf(build_pdf(doc), random.Random(seed * 1009 + i))
            record = source
        elif doc.fmt == "docx":
            source = build_docx(doc)
            record = render_record(source, name)
        else:
            source = build_xlsx(doc)
            record = render_record(source, name)
        truth = build_doc_truth(doc, seed, source, record)
        (out_dir / name).write_bytes(source)
        _write_json(out_dir / f"{doc.fixture_id}.truth.json", truth)
        manifest["documents"].append({
            "fixture_id": doc.fixture_id, "file": name, "truth": f"{doc.fixture_id}.truth.json", "format": doc.fmt,
            "document_kind": doc.kind, "gate_bucket": GATE_BUCKET, "sha256": hashlib.sha256(source).hexdigest(),
            "record_sha256": truth["record_sha256"], "deterministic_bytes": True,
            "description": truth["description"]})
    known = {d.fixture_id for d in build_docs()}
    for case in CASES:
        assert set(case.documents) <= known, case.case_id
        body = {"schema_version": TRUTH_SCHEMA_VERSION, "case_id": case.case_id, "workflow": "compare-quotes",
                "description": f"{SYNTHETIC_LABEL}. {case.description}", "synthetic": True,
                "synthetic_label": SYNTHETIC_LABEL, "documents": case.documents,
                "expected_marks": case.expected_marks,
                "notes": "Expected marks are written by hand (R12, D13), never computed by compare_rules."}
        _write_json(out_dir / f"{case.case_id}.case.json", body)
        manifest["cases"].append({"case_id": case.case_id, "truth": f"{case.case_id}.case.json",
                                  "documents": case.documents})
    _write_json(out_dir / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)
    m = generate(args.out, args.seed)
    print(f"wrote {len(m['documents'])} documents and {len(m['cases'])} cases to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
