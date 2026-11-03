"""Pydantic schemas and enums shared by the pipeline, routes and storage.

Pure data: nothing here imports SQLAlchemy, so the pipeline and the provider
layer can use these types without a database.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal

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
    """

    key: str
    label: str
    description: str = ""
    section: str | None = None  # section-plan id the field is usually reported in
    kind: Literal["scalar", "forms"] = "scalar"


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


Block = Annotated[
    HeadingBlock | ParagraphBlock | ListBlock | TableBlock | IssueBlock | DiscrepancyBlock,
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

    def find_cell(self, block_id: str, row_key: str, column_key: str) -> Cell:
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
            elif isinstance(block, DiscrepancyBlock):
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
