"""Pydantic schemas and enums shared by the pipeline, routes and storage.

Pure data: nothing here imports SQLAlchemy, so the pipeline and the provider
layer can use these types without a database.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Sequence
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

# --------------------------------------------------------------------- enums


class EvidenceState(StrEnum):
    """Evidence state of one extracted fact (M1, D5).

    pending    extracted, not yet verified
    verified   quote found in the PDF's own text layer, value inside it
    scan_read  quote found only in model-OCR text of a scanned page (D5):
               shown as "Read from scan, unverified", page-level highlight,
               excluded from the verification-rate metric
    not_found  model gave a value but its quote, or the value inside the
               quote, is not in the document: shown as "Couldn't verify"
    absent     model returned no value: shown as "Not specified", no chip
    failed     extraction for the document failed: "Extraction failed", retry
    conflicting long documents only (pipeline.merge): the document states two or
               more different values, each verified, and no endorsement rule
               picks one. Shown as "Conflicting values" with a chip per value
               and a conflicting_values notice; never counted as verified
               (the eval reports it as not_found)
    """

    PENDING = "pending"
    VERIFIED = "verified"
    SCAN_READ = "scan_read"
    NOT_FOUND = "not_found"
    ABSENT = "absent"
    FAILED = "failed"
    CONFLICTING = "conflicting"


CITED_STATES = frozenset({EvidenceState.VERIFIED, EvidenceState.SCAN_READ})


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"  # documents parsing / extracting / verifying
    COMPOSING = "composing"  # all documents settled, writing the report
    DONE = "done"
    PARTIAL = "partial"  # report written, some documents failed or budget ran out
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunDocumentStatus(StrEnum):
    """Per-document progress row in the Upload accordion (DG4)."""

    QUEUED = "queued"
    READING = "reading"
    EXTRACTING = "extracting"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"


# --------------------------------------------------------------------- workflow definition


class FactField(BaseModel):
    """One field of a workflow's fact schema (stored as JSON on `workflow`).

    kind (slice 3, D13): "scalar" is one value per document (every field before slice 3);
    "forms" asks the same extraction call for a forms array, and each form becomes a fact
    keyed form:<normalized number> (see the policy check fact schema below).
    "endorsements" (slice 4, certificates) asks the same call for an endorsements array; each
    endorsement becomes facts keyed cert:endorsement:<normalized number>:<attribute> (see below).
    """

    key: str
    label: str
    description: str = ""
    section: str | None = None  # section-plan id the field is usually reported in
    kind: Literal["scalar", "forms", "endorsements"] = "scalar"


class SectionPlanItem(BaseModel):
    id: str
    number: str
    title: str
    guidance: str = ""


# --------------------------------------------------------------------- proposal outline (slice 2)
#
# A proposal's outline is an ordered list of blocks. It is untrusted input: the
# wizard sends it, and its titles and instructions reach the compose prompt (in
# the delimited brief channel, never as facts). Single-block rules live on
# OutlineBlock; rules across blocks live in `outline_errors`.
#
# D3: a block names the fact sections it shows (`fact_sections`); facts in a
# section no block names are dropped from the proposal, never relocated.
# D4: a fact section has at most one table, so a table id is `<fact section>-table`:
# unique, and stable when blocks are reordered or retitled (S5).


class BlockKind(StrEnum):
    COVER_PAGE = "cover_page"  # locked, id "cover", first; generated in code
    TABLE_OF_CONTENTS = "table_of_contents"  # locked, id "toc", second; generated in code
    CLIENT_DETAILS = "client_details"
    LINE_COMPARISON = "line_comparison"
    PREMIUM_COMPARISON = "premium_comparison"
    SUBJECTIVITIES = "subjectivities"
    NARRATIVE = "narrative"  # prose only; may repeat fact sections that have a table elsewhere


# kind -> (required block id, required position)
LOCKED_BLOCKS: dict[BlockKind, tuple[str, int]] = {
    BlockKind.COVER_PAGE: ("cover", 0),
    BlockKind.TABLE_OF_CONTENTS: ("toc", 1),
}
LOCKED_BLOCK_IDS = frozenset(i for i, _ in LOCKED_BLOCKS.values())
TABLE_KINDS = frozenset({BlockKind.CLIENT_DETAILS, BlockKind.LINE_COMPARISON,
                         BlockKind.PREMIUM_COMPARISON, BlockKind.SUBJECTIVITIES})

MAX_OUTLINE_BLOCKS = 20
MAX_TITLE_CHARS = 120
MAX_DESCRIPTION_CHARS = 400
MAX_INSTRUCTION_CHARS = 1000
MAX_FACT_SECTIONS_PER_BLOCK = 10
MAX_CONTEXT_CHARS = 4000
MAX_NAME_CHARS = 200
# Lowercase, no hyphen: generated ids ("<block>-p1", "<fact section>-table") can never collide.
SLUG_ID_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
TEMPLATE_SLUG_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # tab, LF and CR allowed in long text
_LINE_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

SlugId = Annotated[str, Field(pattern=SLUG_ID_PATTERN)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_CHARS)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_NAME_CHARS)]


def _no_line_control(value: str) -> str:
    if _LINE_CONTROL.search(value):
        raise ValueError("must be one line of text without control characters")
    return value


def _no_control(value: str) -> str:
    if _CONTROL.search(value):
        raise ValueError("must not contain control characters")
    return value


class OutlineBlock(BaseModel):
    """One block of a proposal outline (template block or per-run block)."""

    model_config = ConfigDict(extra="forbid")

    id: SlugId
    kind: BlockKind
    title: Title
    description: str = Field("", max_length=MAX_DESCRIPTION_CHARS)
    ai_instruction: str = Field("", max_length=MAX_INSTRUCTION_CHARS)
    fact_sections: list[SlugId] = Field(default_factory=list, max_length=MAX_FACT_SECTIONS_PER_BLOCK)
    locked: bool = False  # derived from `kind`; a client value is ignored

    @field_validator("title")
    @classmethod
    def _title_one_line(cls, v: str) -> str:
        return _no_line_control(v)

    @field_validator("description", "ai_instruction")
    @classmethod
    def _text_clean(cls, v: str) -> str:
        return _no_control(v.strip())

    @model_validator(mode="after")
    def _derive_locked(self) -> OutlineBlock:
        self.locked = self.kind in LOCKED_BLOCKS
        return self

    @property
    def has_table(self) -> bool:
        return self.kind in TABLE_KINDS


class ProposalParameters(BaseModel):
    """`POST /api/runs` parameters for the proposal-builder workflow (as the client sends them)."""

    model_config = ConfigDict(extra="forbid")

    template_id: StrictInt | Annotated[str, Field(pattern=TEMPLATE_SLUG_PATTERN)]
    broker_name: Name = ""
    brokerage_name: Name = ""
    client_name: Name = ""
    additional_context: str = Field("", max_length=MAX_CONTEXT_CHARS)
    no_ai_prose: StrictBool = False
    # None: use the template's blocks as they are
    outline: list[OutlineBlock] | None = Field(None, max_length=MAX_OUTLINE_BLOCKS)

    @field_validator("broker_name", "brokerage_name", "client_name")
    @classmethod
    def _names_one_line(cls, v: str) -> str:
        return _no_line_control(v)

    @field_validator("additional_context")
    @classmethod
    def _context_clean(cls, v: str) -> str:
        return _no_control(v.strip())


class TemplateSnapshot(BaseModel):
    """The resolved template, copied into run.parameters at run creation (D6)."""

    id: int
    slug: str
    name: str
    blocks: list[OutlineBlock]
    theme: dict[str, Any] = Field(default_factory=dict)
    variables: list[str] = Field(default_factory=list)


class StoredProposal(BaseModel):
    """What run.parameters holds for a proposal-builder run (validated parameters plus the snapshot)."""

    template_id: int
    broker_name: str = ""
    brokerage_name: str = ""
    client_name: str = ""
    additional_context: str = ""
    no_ai_prose: bool = False
    outline: list[OutlineBlock]
    template: TemplateSnapshot


Loc = tuple[str | int, ...]


def outline_errors(outline: Sequence[OutlineBlock],
                   known_fact_sections: Collection[str] | None = None) -> list[tuple[Loc, str]]:
    """Rules across blocks. Each error is (location relative to the outline list, message).

    Locked blocks present once, with their fixed id and position; unique ids; at
    least one section besides the locked ones; locked blocks own no facts; a table
    block owns at least one fact section; fact sections known (when the catalogue
    is given) and listed once per block; at most one table per fact section (D4).
    """
    errors: list[tuple[Loc, str]] = []
    for kind, (block_id, pos) in LOCKED_BLOCKS.items():
        name = "Cover Page" if kind == BlockKind.COVER_PAGE else "Table of Contents"
        where = [i for i, b in enumerate(outline) if b.kind == kind]
        if not where:
            errors.append(((), f"The outline must keep the locked {name} block."))
            continue
        for i in where:
            if i != pos:
                errors.append(((i, "kind"), f"The {name} is locked as block {pos + 1} and appears only once."))
            elif outline[i].id != block_id:
                errors.append(((i, "id"), f"The {name} block must have the id {block_id!r}."))
    seen_ids: dict[str, int] = {}
    for i, b in enumerate(outline):
        if b.id in seen_ids:
            errors.append(((i, "id"), f"Block id {b.id!r} is already used by block {seen_ids[b.id] + 1}."))
        else:
            seen_ids[b.id] = i
        if b.id in LOCKED_BLOCK_IDS and b.kind not in LOCKED_BLOCKS:
            errors.append(((i, "id"), f"Block id {b.id!r} is reserved for a locked block."))
    if not any(b.kind not in LOCKED_BLOCKS for b in outline):
        errors.append(((), "Add at least one section besides the Cover Page and Table of Contents."))
    table_owner: dict[str, str] = {}
    for i, b in enumerate(outline):
        if b.kind in LOCKED_BLOCKS and b.fact_sections:
            errors.append(((i, "fact_sections"), "A locked block shows no document facts."))
            continue
        if b.has_table and not b.fact_sections:
            errors.append(((i, "fact_sections"), "A comparison block needs at least one fact section."))
        listed: set[str] = set()
        for j, fs in enumerate(b.fact_sections):
            if fs in listed:
                errors.append(((i, "fact_sections", j), f"Fact section {fs!r} is listed twice."))
                continue
            listed.add(fs)
            if known_fact_sections is not None and fs not in known_fact_sections:
                errors.append(((i, "fact_sections", j), f"Unknown fact section {fs!r}."))
                continue
            if b.has_table:
                if fs in table_owner:
                    errors.append(((i, "fact_sections", j),
                                   f"Fact section {fs!r} already has a table in block {table_owner[fs]!r}; "
                                   "a fact section can have only one table."))
                else:
                    table_owner[fs] = b.id
    return errors


class ParameterErrors(ValueError):
    """Invalid run parameters: (location relative to `parameters`, message) per problem."""

    def __init__(self, errors: Sequence[tuple[Loc, str]]):
        super().__init__("; ".join(m for _, m in errors))
        self.errors = list(errors)


def parse_proposal_parameters(raw: Any, known_fact_sections: Collection[str] | None = None) -> ProposalParameters:
    """Validate client parameters for a proposal run. Raises ParameterErrors with field paths."""
    try:
        params = ProposalParameters.model_validate(raw)
    except ValidationError as exc:
        raise ParameterErrors([(tuple(e["loc"]), e["msg"]) for e in exc.errors()]) from None
    if params.outline is not None:
        problems = outline_errors(params.outline, known_fact_sections)
        if problems:
            raise ParameterErrors([(("outline", *loc), msg) for loc, msg in problems])
    return params


# --------------------------------------------------------------------- extraction I/O (step 2)


class ExtractedFact(BaseModel):
    """What the model returns per field (spec section 4 step 2).

    `page` is 1-based and local to the one document being extracted.
    `value` is null when the document does not state the field.
    """

    model_config = ConfigDict(extra="forbid")

    field: str
    value: str | None
    page: int | None
    quote: str | None


class ExtractionOutput(BaseModel):
    """Whole-document extraction result (D8: first option compared, count kept)."""

    model_config = ConfigDict(extra="forbid")

    facts: list[ExtractedFact]
    option_count: int = 1
    option_label: str | None = None


# --------------------------------------------------------------------- policy check fact schema (slice 3, T3)
#
# D13 A: one extraction pass per window returns the scalar fields below plus a forms array;
# each form becomes a fact keyed form:<normalized number> (form_key), so the same form aligns
# across expiring, quote and renewal however its number is spaced. Special provisions are not
# extracted as items (no stable identity across documents). Single-location policies only
# (D17: multi-location and multi-schedule coverage identity is a TODO).

FORM_KEY_PREFIX = "form:"
MAX_FORM_NUMBER_CHARS = 40
_FORM_NUMBER_JUNK = re.compile(r"[^0-9A-Z]")


def form_key(number: str | None) -> str | None:
    """form:<number> with case, spacing and separators removed ("HO 00 03", "ho-00-03" and
    "HO0003" are one form), or None for an empty or oversized number."""
    compact = _FORM_NUMBER_JUNK.sub("", unicodedata.normalize("NFKC", number or "").upper())
    if not compact or len(compact) > MAX_FORM_NUMBER_CHARS:
        return None
    return FORM_KEY_PREFIX + compact


def is_form_key(key: str | None) -> bool:
    return bool(key) and key.startswith(FORM_KEY_PREFIX) and len(key) > len(FORM_KEY_PREFIX)


def scalar_fields(fields: Sequence[FactField]) -> list[FactField]:
    return [f for f in fields if f.kind == "scalar"]


def _pc(key: str, label: str, description: str, section: str, kind: str = "scalar") -> FactField:
    return FactField(key=key, label=label, description=description, section=section, kind=kind)


POLICY_CHECK_FIELDS: list[FactField] = [
    _pc("policy_number", "Policy Number", "Policy number as printed on the declarations.", "policy"),
    _pc("policy_term", "Policy Term", "Policy period: effective and expiration dates as printed.", "policy"),
    _pc("named_insured", "Named Insured", "Name of the named insured as printed.", "insured"),
    _pc("insured_address", "Insured Address", "Mailing or residence premises address of the named insured.",
        "insured"),
    _pc("coverage_a_limit", "Coverage A (Dwelling)", "Coverage A, Dwelling, limit of liability.", "coverage"),
    _pc("coverage_b_limit", "Coverage B (Other Structures)", "Coverage B, Other Structures, limit.", "coverage"),
    _pc("coverage_c_limit", "Coverage C (Personal Property)", "Coverage C, Personal Property, limit.", "coverage"),
    _pc("coverage_d_limit", "Coverage D (Loss of Use)", "Coverage D, Loss of Use, limit.", "coverage"),
    _pc("coverage_e_limit", "Coverage E (Personal Liability)", "Coverage E, Personal Liability, each occurrence.",
        "coverage"),
    _pc("coverage_f_limit", "Coverage F (Medical Payments)", "Coverage F, Medical Payments to Others, each person.",
        "coverage"),
    _pc("deductible_all_peril", "All Other Perils Deductible",
        "Deductible that applies to all other perils (AOP), amount or percent as printed.", "deductible"),
    _pc("deductible_wind_hail", "Wind/Hail Deductible",
        "Windstorm or hail deductible, amount or percent as printed. Null when the policy has none.", "deductible"),
    _pc("deductible_hurricane", "Hurricane Deductible",
        "Named storm or hurricane deductible, amount or percent as printed. Null when the policy has none.",
        "deductible"),
    _pc("total_premium", "Total Premium", "Total annual premium payable, as one figure.", "premium"),
    _pc("discount_multi_policy", "Multi-Policy Discount",
        "Multi-policy or package discount, amount or percent as printed. Null when not applied.", "discount"),
    _pc("discount_protective_device", "Protective Device Discount",
        "Protective device, alarm or sprinkler discount, as printed. Null when not applied.", "discount"),
    _pc("discount_claims_free", "Claims-Free Discount",
        "Claims-free or loss-free discount, as printed. Null when not applied.", "discount"),
    _pc("discount_loyalty", "Loyalty Discount",
        "Loyalty, renewal or years-insured discount, as printed. Null when not applied.", "discount"),
    _pc("forms", "Forms and Endorsements",
        "Every policy form and endorsement the document lists or prints, with its form number and edition date.",
        "forms", kind="forms"),
]


# --------------------------------------------------------------------- certificate fact schema (slice 4, C3)
#
# One combined pass per window, like Policy Check's forms (D13): the scalar cert: fields below plus an
# endorsements array. One declarations document is one line of coverage (C7); a document stating several
# lines (a package policy) contributes no facts (pipeline.certificate notices it). Each endorsement becomes
# facts keyed cert:endorsement:<normalized form number>:<attribute> (schemas.ENDORSEMENT_ATTRIBUTES;
# form_number holds "<number> <edition>" like a form fact). The
# endorsement's type (additional insured, waiver, primary and non-contributory; blanket or scheduled) is
# classified in code from its verified title and named party (pipeline.coi_checks), never taken from the model.

CERT_PREFIX = "cert:"
ENDORSEMENT_KEY_PREFIX = "cert:endorsement:"
# form_number: "<number> <edition>" as printed; type: the printed title, which says what the endorsement
# does (classified in code into ENDORSEMENT_TYPES, pipeline.coi_checks); the rest as printed.
ENDORSEMENT_ATTRIBUTES = ("form_number", "type", "policy_number", "named_party", "condition")
ENDORSEMENT_PARTS = ENDORSEMENT_ATTRIBUTES[1:]  # each extracted with its own value, page and quote
_ENDORSEMENT_KEY_RE = re.compile(r"^cert:endorsement:([0-9A-Z]{1,40}):([a-z_]+)$")


def endorsement_key(number: str | None, attribute: str) -> str | None:
    """cert:endorsement:<number without case, spacing or separators>:<attribute>, or None for an empty
    or oversized number (the form_key rule) or an unknown attribute."""
    key = form_key(number)
    if key is None or attribute not in ENDORSEMENT_ATTRIBUTES:
        return None
    return f"{ENDORSEMENT_KEY_PREFIX}{key[len(FORM_KEY_PREFIX):]}:{attribute}"


def is_endorsement_key(key: str | None) -> bool:
    mo = _ENDORSEMENT_KEY_RE.match(key or "")
    return bool(mo) and mo.group(2) in ENDORSEMENT_ATTRIBUTES


def split_endorsement_key(key: str) -> tuple[str, str]:
    """(compact form number, attribute) of an endorsement fact key."""
    mo = _ENDORSEMENT_KEY_RE.match(key)
    if not mo:
        raise ValueError(f"not an endorsement key: {key!r}")
    return mo.group(1), mo.group(2)


def _cert(key: str, label: str, description: str, section: str, kind: str = "scalar") -> FactField:
    return FactField(key=key, label=label, description=description, section=section, kind=kind)


_LIMIT_NOTE = " As printed on the declarations, for example $1,000,000. Null when the policy does not state it."

CERTIFICATE_FIELDS: list[FactField] = [
    _cert("cert:named_insured", "Named Insured", "Name of the first named insured as printed on the declarations.",
          "insured"),
    _cert("cert:insured_address", "Insured Address", "Mailing address of the named insured as printed.", "insured"),
    _cert("cert:producer", "Producer", "Producing agent or broker named on the declarations, as printed.", "insured"),
    _cert("cert:carrier", "Insurer", "Insurance company issuing the policy (not the agent or broker), as printed.",
          "policy"),
    _cert("cert:naic", "NAIC Number", "The insurer's NAIC company code as printed. Null when not printed.", "policy"),
    _cert("cert:policy_number", "Policy Number", "Policy number as printed on the declarations.", "policy"),
    _cert("cert:line", "Line of Coverage",
          "The line of coverage as printed in the declarations title or coverage part, for example 'Commercial "
          "General Liability', 'Business Auto', 'Commercial Umbrella', 'Workers Compensation and Employers "
          "Liability' or 'Commercial Property'. If the declarations state several lines (a package policy), "
          "return the title that names the package. Null for a document that is only endorsements.", "policy"),
    _cert("cert:effective_date", "Effective Date",
          "Policy period effective (start) date as printed on the declarations; not an endorsement's own "
          "effective date.", "policy"),
    _cert("cert:expiration_date", "Expiration Date", "Policy period expiration (end) date as printed on the "
          "declarations.", "policy"),
    _cert("cert:gl_each_occurrence", "Each Occurrence", "General liability each occurrence limit." + _LIMIT_NOTE,
          "limits"),
    _cert("cert:gl_general_aggregate", "General Aggregate", "General liability general aggregate limit." + _LIMIT_NOTE,
          "limits"),
    _cert("cert:gl_products_completed_ops_aggregate", "Products-Completed Operations Aggregate",
          "General liability products-completed operations aggregate limit." + _LIMIT_NOTE, "limits"),
    _cert("cert:gl_personal_advertising_injury", "Personal and Advertising Injury",
          "Personal and advertising injury limit." + _LIMIT_NOTE, "limits"),
    _cert("cert:gl_damage_to_rented_premises", "Damage to Rented Premises",
          "Damage to premises rented to you limit." + _LIMIT_NOTE, "limits"),
    _cert("cert:gl_medical_expense", "Medical Expense", "Medical expense limit, any one person." + _LIMIT_NOTE, "limits"),
    _cert("cert:auto_combined_single_limit", "Combined Single Limit",
          "Auto liability combined single limit, each accident." + _LIMIT_NOTE, "limits"),
    _cert("cert:umbrella_each_occurrence", "Umbrella Each Occurrence",
          "Umbrella or excess liability each occurrence limit." + _LIMIT_NOTE, "limits"),
    _cert("cert:umbrella_aggregate", "Umbrella Aggregate", "Umbrella or excess liability aggregate limit." + _LIMIT_NOTE,
          "limits"),
    _cert("cert:wc_statutory", "Workers Compensation Statutory Limits",
          "Workers compensation limits as printed, for example 'Statutory' or 'Per Statute'. Null when not "
          "printed.", "limits"),
    _cert("cert:wc_el_each_accident", "Employers Liability Each Accident",
          "Employers liability bodily injury by accident, each accident." + _LIMIT_NOTE, "limits"),
    _cert("cert:wc_el_disease_each_employee", "Employers Liability Disease Each Employee",
          "Employers liability bodily injury by disease, each employee." + _LIMIT_NOTE, "limits"),
    _cert("cert:wc_el_disease_policy_limit", "Employers Liability Disease Policy Limit",
          "Employers liability bodily injury by disease, policy limit." + _LIMIT_NOTE, "limits"),
    _cert("endorsements", "Endorsements",
          "Additional insured, waiver of subrogation and primary and non-contributory endorsements.",
          "endorsements", kind="endorsements"),
]


# Slice 5a loss runs: document facts of a loss-run PDF (spreadsheets read the same keys from labelled cells in
# code). Claim rows and printed totals are NOT facts: they have their own tables (D9).
LOSS_RUN_FIELDS: list[FactField] = [
    FactField(key="lr:carrier", label="Carrier", section="documents",
              description="The insurance company whose claims this loss run lists, as printed (not the broker)."),
    FactField(key="lr:named_insured", label="Named Insured", section="documents",
              description="The insured (client) the loss run is for, as printed."),
    FactField(key="lr:valuation_date", label="Valued as of", section="documents",
              description=("The date the loss run's amounts are valued as of (printed as 'Valued as of', "
                           "'Valuation date', 'Evaluated as of' or 'As of'), as printed. Not the run or print date "
                           "when a valuation date is also printed; null when no valuation date is printed.")),
]


# --------------------------------------------------------------------- artifact content


class Segment(BaseModel):
    """A run of prose. Prose cites by passage id."""

    text: str
    passage_ids: list[int] = Field(default_factory=list)


class Notice(BaseModel):
    """A warning row (DG5). Run-level in ArtifactContent.notices; cell-level in Cell.notice.

    conflicting_values (T14, cell-level): one document states different values
    for the field and no rule picks one; the message names every value and its
    page. The cell's state is `conflicting` and no value is shown as verified.
    """

    kind: Literal["compose_fallback", "budget_exhausted", "documents_failed", "info", "conflicting_values"]
    message: str
    retryable: bool = False
    dismissible: bool = False


class ConflictValue(BaseModel):
    """One of the different values a long document states for a field (T14)."""

    value: str | None
    page_no: int | None = None
    passage_id: int | None = None  # its chip; also listed in Cell.passage_ids


class Cell(BaseModel):
    value: str | None = None
    passage_ids: list[int] = Field(default_factory=list)
    fact_ids: list[int] = Field(default_factory=list)
    state: EvidenceState | None = None
    edited: bool = False
    # Filled on the first user edit: the extracted value and its chips stay
    # available as "original source".
    original_value: str | None = None
    original_passage_ids: list[int] = Field(default_factory=list)
    # T14: cell-level warning, for example conflicting_values in a long document.
    notice: Notice | None = None
    # T14: state `conflicting` only: every value the document states, earliest page first.
    conflicts: list[ConflictValue] = Field(default_factory=list)


class Column(BaseModel):
    key: str  # document letter, "A" to "H"
    label: str
    document_id: int | None = None
    warning: str | None = None  # column-level notice (failed doc, multi-option)


class Row(BaseModel):
    key: str  # fact field key
    label: str
    cells: dict[str, Cell]  # column key -> cell


class HeadingBlock(BaseModel):
    type: Literal["heading"] = "heading"
    id: str
    level: int = 3
    text: str


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    id: str
    segments: list[Segment]


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    id: str
    title: str | None = None  # e.g. "Supporting Evidence"
    items: list[list[Segment]]


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    id: str
    title: str | None = None
    columns: list[Column]
    rows: list[Row]


class IssueBlock(BaseModel):
    type: Literal["issue"] = "issue"
    id: str
    code: str  # ISSUE-001
    title: str
    severity: str | None = None
    category: str | None = None
    impact: list[Segment] = Field(default_factory=list)
    affected_layers: list[str] = Field(default_factory=list)
    follow_form: str | None = None


# --------------------------------------------------------------------- policy check discrepancy (slice 3, T5)
#
# D7 A: a Policy Check discrepancy is one block of the ordinary ArtifactContent,
# so versions, chips, stale flags, chat and export keep working. Resolve state
# (open / resolved) is NOT stored here: it lives in the discrepancy_state table,
# so a regenerate never wipes a decision.
#
# Slot conventions (one Cell per role; the diff in pipeline/diff.py fills them):
#   role missing from `slots`, or state `absent`     -> "N/A" (no document, or it does not state the value)
#   state `failed`                                   -> "Not read" (D11: the document or its budget failed)
#   state `conflicting` / `not_found`                -> "Needs review" in that slot (D15), conflicts listed
#   state `verified` / `scan_read`                   -> the value with its chips
# `item_state` needs_review (D15): the renewal or expiring value is conflicting
# or unverified, so the key is never reported as removed or as equal.


class PolicyRole(StrEnum):
    """Document roles of a Policy Check run, in display order (the D8 input slot keys)."""

    EXPIRING = "expiring"
    QUOTE = "quote"
    RENEWAL = "renewal"
    AMS = "ams"

    @property
    def label(self) -> str:
        return "AMS" if self is PolicyRole.AMS else self.value.capitalize()


class Severity(StrEnum):
    """Severity tiers of the rule table (pipeline/diff.py), most severe first."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class DiscrepancyItemState(StrEnum):
    DISCREPANCY = "discrepancy"  # the renewal differs from a compared role
    NEEDS_REVIEW = "needs_review"  # D15: a renewal or expiring value is conflicting or unverified


class DiscrepancyBlock(BaseModel):
    """One Policy Check discrepancy: canonical key, severity, deterministic text (D14), slots per role."""

    type: Literal["discrepancy"] = "discrepancy"
    id: str  # derived from `key` by the diff, stable across regeneration
    key: str  # canonical fact key, e.g. coverage_a_limit or form:<normalized number>
    title: str
    severity: Severity
    item_state: DiscrepancyItemState = DiscrepancyItemState.DISCREPANCY
    lob: str | None = None
    prose: list[Segment] = Field(default_factory=list)  # the explanation
    slots: dict[PolicyRole, Cell] = Field(default_factory=dict)

    def slot(self, role: PolicyRole) -> Cell | None:
        return self.slots.get(role)

    def passage_ids(self) -> set[int]:
        """Citations in the explanation and in every slot (outside finding 6)."""
        found: set[int] = {p for s in self.prose for p in s.passage_ids}
        for cell in self.slots.values():
            found.update(cell.passage_ids)
        return found


# --------------------------------------------------------------------- certificate (slice 4, C4)
#
# A certificate draft is one ordinary ArtifactContent (D1/P3): a CertificateBlock (what prints, every field a
# Cell with its evidence state and chips) and one ClaimBlock per checked claim. The broker's decision on a
# claim (open, resolved = overridden with a reason, removed) lives in discrepancy_state, like a Policy Check
# discrepancy, so a rebuild never wipes it. A removed claim's content is left out of the CertificateBlock
# (D13); the ClaimBlock stays so the check list can say "Removed".


class ClaimKind(StrEnum):
    WORDING = "wording"  # a requested wording on one printed policy line (D14)
    IN_FORCE = "in_force"  # a printed policy is in force on the certificate date
    INSURED = "insured"  # the named insured is the same on every printed policy
    DESCRIPTION = "description"  # the broker's description of operations (D11 sign-off)
    EDITED = "edited"  # a printed value the broker edited (D12 sign-off)


class ClaimVerdict(StrEnum):
    SUPPORTED = "supported"
    NOT_SUPPORTED = "not_supported"
    NEEDS_SIGNOFF = "needs_signoff"  # typed by the broker: confirmed with a reason or removed


class ClaimBlock(BaseModel):
    """One checked claim of a certificate draft (pipeline.coi_checks)."""

    type: Literal["claim"] = "claim"
    id: str  # "claim-" + key, stable across rebuilds
    key: str  # e.g. additional_insured:A, in_force:B, insured, description, edited:A:cert:gl_each_occurrence
    kind: ClaimKind
    title: str
    verdict: ClaimVerdict
    reason: str | None = None  # why it is not supported, or what to confirm
    condition: str | None = None  # D14: the endorsement's quoted condition
    wording: str | None = None  # additional_insured | waiver_of_subrogation | primary_noncontributory
    policy_letter: str | None = None  # the policy (document letter) the claim is about
    removable: bool = True  # D13: an insured mismatch cannot be removed
    prose: list[Segment] = Field(default_factory=list)  # the explanation, citing its evidence
    basis: list[str] = Field(default_factory=list)  # what the verdict rests on (fingerprint input)

    def passage_ids(self) -> set[int]:
        return {p for s in self.prose for p in s.passage_ids}


class CertificateField(BaseModel):
    """One printed value: `cell.value` is what prints ("Not stated" when not verified)."""

    key: str  # the cert: fact key (or "description" style keys for typed values)
    label: str
    cell: Cell


class CertificatePolicy(BaseModel):
    """One coverage row: one declarations document (C7)."""

    letter: str  # the document's letter in the run (column key of its cells)
    insurer: str | None = None  # insurer letter on the certificate (A, B, ...)
    document_id: int | None = None
    line: str  # general_liability | auto | umbrella | workers_comp | property | other
    line_label: str
    fields: list[CertificateField] = Field(default_factory=list)
    wording: list[str] = Field(default_factory=list)  # wording printed on this line


class CertificateInsurer(BaseModel):
    letter: str
    name: str
    naic: str | None = None


class CertificateBlock(BaseModel):
    type: Literal["certificate"] = "certificate"
    id: str = "certificate"
    layout: str = "own-v1"  # the field map the PDF uses (C5: ACORD 25 would be a second map)
    certificate_date: str  # ISO date
    holder_name: str
    holder_address: str = ""
    producer: CertificateField | None = None
    insured: list[CertificateField] = Field(default_factory=list)
    insurers: list[CertificateInsurer] = Field(default_factory=list)
    policies: list[CertificatePolicy] = Field(default_factory=list)
    description: str | None = None

    INSURED_COLUMN: ClassVar[str] = "insured"

    def iter_fields(self):
        """(column key, field) for every printed value: insured and producer under "insured", each
        policy's fields under its letter."""
        if self.producer is not None:
            yield self.INSURED_COLUMN, self.producer
        for f in self.insured:
            yield self.INSURED_COLUMN, f
        for p in self.policies:
            for f in p.fields:
                yield p.letter, f

    def find_field(self, row_key: str, column_key: str) -> CertificateField:
        for col, f in self.iter_fields():
            if col == column_key and f.key == row_key:
                return f
        raise KeyError(f"no certificate field {row_key!r} in {column_key!r}")

    def passage_ids(self) -> set[int]:
        found: set[int] = set()
        for _, f in self.iter_fields():
            found.update(f.cell.passage_ids)
            found.update(c.passage_id for c in f.cell.conflicts if c.passage_id is not None)
        return found


# --------------------------------------------------------------------- loss runs (slice 5a)
#
# The loss review report (D2): a totals strip and the check list, built in code by loss_runs.ledger. The claims
# themselves are NOT in the artifact; they are paged from claim_row (GET /api/loss-runs/{id}/claims). Broker
# decisions on a check (accept a gap, pick a conflicting value) live in discrepancy_state, keyed by the block id,
# reopened by `fingerprint` like a Policy Check discrepancy. An "unverified rows" check is decided row by row
# (claim_decision): its status is computed and it has no discrepancy_state row (decided_by "rows").


class LossMetricTotal(BaseModel):
    cents: int = 0
    from_claims: int = 0  # counted claims that give this amount


class LossDocumentSummary(BaseModel):
    document_id: int
    letter: str
    filename: str
    kind: str  # quote (a PDF) | spreadsheet
    carrier: str | None = None
    valuation_date: str | None = None  # ISO
    status: str  # done | failed | partial | queued ...
    rows: int = 0
    counted_rows: int = 0
    unverified_rows: int = 0
    printed_totals: int = 0
    unread_pages: list[int] = Field(default_factory=list)


class LossTotalsBlock(BaseModel):
    type: Literal["loss_totals"] = "loss_totals"
    id: str = "loss-totals"
    claims: int = 0  # distinct counted claims (carrier + claim key), not lines (D5)
    open_claims: int = 0
    paid: LossMetricTotal = Field(default_factory=LossMetricTotal)
    reserve: LossMetricTotal = Field(default_factory=LossMetricTotal)
    incurred: LossMetricTotal = Field(default_factory=LossMetricTotal)
    recovery: LossMetricTotal = Field(default_factory=LossMetricTotal)
    deductible: LossMetricTotal = Field(default_factory=LossMetricTotal)
    unreconciled: bool = False  # a check is open
    as_of: str | None = None  # latest valuation date of the review (the "Last 3 months" anchor)
    counts: dict[str, int] = Field(default_factory=dict)  # rows, counted, superseded, conflicting, left_out, ...
    documents: list[LossDocumentSummary] = Field(default_factory=list)


class LossEvidence(BaseModel):
    """Where a check's printed line is: a PDF page (rects in PDF points) or a sheet row (sheet page on demand)."""

    document_id: int
    page_no: int | None = None
    sheet_name: str | None = None
    sheet_row: int | None = None
    quote: str | None = None
    rects: list[list[float]] = Field(default_factory=list)
    claim_row_id: int | None = None
    printed_total_id: int | None = None


class LossCheckOption(BaseModel):
    """One side of a conflicting claim: a document's lines of the claim, summed (D5)."""

    document_id: int
    label: str
    valuation_date: str | None = None
    paid: int | None = None
    reserve: int | None = None
    incurred: int | None = None
    recovery: int | None = None
    deductible: int | None = None
    claim_row_ids: list[int] = Field(default_factory=list)


class LossCheckBlock(BaseModel):
    """One check of a loss review: a printed total (matches / off by $X), a conflicting claim, or a document's
    unverified rows."""

    type: Literal["loss_check"] = "loss_check"
    id: str
    kind: Literal["total", "conflict", "unverified"]
    verdict: Literal["matches", "off", "needs_review", "conflicting", "unverified", "handled"]
    decided_by: Literal["broker", "rows"] = "broker"
    title: str
    message: str
    #: A printed total's own label as printed ("Total, all policies", "Subtotal policy BCC 22 77310"); None when the
    #: line has none or the check is not a total. Display only: not part of the fingerprint.
    label: str | None = None
    document_id: int | None = None
    metric: str | None = None
    scope_kind: str | None = None
    scope_value: str | None = None
    printed: int | None = None  # cents (claim_count: a count)
    computed: int | None = None
    difference: int | None = None  # printed - computed
    claim_key: str | None = None
    carrier_key: str | None = None
    rows: int | None = None  # unverified: rows still undecided
    filter: dict[str, Any] = Field(default_factory=dict)  # GET /claims query that opens the rows in scope
    evidence: list[LossEvidence] = Field(default_factory=list)
    options: list[LossCheckOption] = Field(default_factory=list)
    picked_document_id: int | None = None
    fingerprint: str = ""
    # Status as the ledger computed it (matches the discrepancy_state row after reconcile; computed for "rows").
    status: Literal["open", "resolved", "match"] = "open"
    needs_action: bool = False
    changed_since_resolved: bool = False


Block = Annotated[
    HeadingBlock | ParagraphBlock | ListBlock | TableBlock | IssueBlock | DiscrepancyBlock | ClaimBlock
    | CertificateBlock | LossTotalsBlock | LossCheckBlock,
    Field(discriminator="type"),
]


class Section(BaseModel):
    id: str
    number: str  # "3.1"
    title: str
    level: int = 1
    stale: bool = False  # D9: prose cites an edited cell
    blocks: list[Block] = Field(default_factory=list)


class ArtifactContent(BaseModel):
    """A report as structured blocks; stored as JSON per artifact version."""

    sections: list[Section] = Field(default_factory=list)
    notices: list[Notice] = Field(default_factory=list)

    # ---- queries

    def iter_blocks(self):
        for section in self.sections:
            for block in section.blocks:
                yield section, block

    def find_table(self, block_id: str) -> TableBlock:
        for _, block in self.iter_blocks():
            if isinstance(block, TableBlock) and block.id == block_id:
                return block
        raise KeyError(f"no table block {block_id!r}")

    def find_certificate(self) -> CertificateBlock | None:
        return next((b for _, b in self.iter_blocks() if isinstance(b, CertificateBlock)), None)

    def find_cell(self, block_id: str, row_key: str, column_key: str) -> Cell:
        """A table cell, or (slice 4) a printed certificate value: block "certificate", row the field
        key, column the policy letter or "insured"."""
        cert = self.find_certificate()
        if cert is not None and cert.id == block_id:
            return cert.find_field(row_key, column_key).cell
        table = self.find_table(block_id)
        for row in table.rows:
            if row.key == row_key:
                if column_key not in row.cells:
                    raise KeyError(f"no column {column_key!r} in row {row_key!r}")
                return row.cells[column_key]
        raise KeyError(f"no row {row_key!r} in table {block_id!r}")

    def passage_ids(self) -> set[int]:
        """Every passage id cited anywhere in the current content."""
        found: set[int] = set()

        def segs(segments: list[Segment]) -> None:
            for s in segments:
                found.update(s.passage_ids)

        for _, block in self.iter_blocks():
            if isinstance(block, ParagraphBlock):
                segs(block.segments)
            elif isinstance(block, ListBlock):
                for item in block.items:
                    segs(item)
            elif isinstance(block, IssueBlock):
                segs(block.impact)
            elif isinstance(block, TableBlock):
                for row in block.rows:
                    for cell in row.cells.values():
                        found.update(cell.passage_ids)
            elif isinstance(block, DiscrepancyBlock | ClaimBlock | CertificateBlock):
                found.update(block.passage_ids())
        return found

    # ---- edits (return a new object; the receiver is never mutated)

    def edit_cell(self, block_id: str, row_key: str, column_key: str, value: str | None) -> ArtifactContent:
        """Apply a user edit to one table cell.

        The cell is marked edited and its chips move to `original_passage_ids`
        (spec section 3). On repeated edits the first extracted value and its
        chips are kept as the original source.
        """
        new = self.model_copy(deep=True)
        cell = new.find_cell(block_id, row_key, column_key)
        if not cell.edited:
            cell.original_value = cell.value
            cell.original_passage_ids = list(cell.passage_ids)
        cell.value = value
        cell.passage_ids = []
        cell.edited = True
        return new
