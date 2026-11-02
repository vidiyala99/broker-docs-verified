"""Pydantic schemas and enums shared by the pipeline, routes and storage.

Pure data: nothing here imports SQLAlchemy, so the pipeline and the provider
layer can use these types without a database.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    """

    PENDING = "pending"
    VERIFIED = "verified"
    SCAN_READ = "scan_read"
    NOT_FOUND = "not_found"
    ABSENT = "absent"
    FAILED = "failed"


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
    """One field of a workflow's fact schema (stored as JSON on `workflow`)."""

    key: str
    label: str
    description: str = ""
    section: str | None = None  # section-plan id the field is usually reported in


class SectionPlanItem(BaseModel):
    id: str
    number: str
    title: str
    guidance: str = ""


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


# --------------------------------------------------------------------- artifact content


class Segment(BaseModel):
    """A run of prose. Prose cites by passage id."""

    text: str
    passage_ids: list[int] = Field(default_factory=list)


class Notice(BaseModel):
    """A warning row (DG5). Run-level in ArtifactContent.notices; cell-level in Cell.notice.

    conflicting_values (T14, cell-level): one document states different values
    for the field and no rule picks one; the message names every value and its
    page, and the cell shows the earliest.
    """

    kind: Literal["compose_fallback", "budget_exhausted", "documents_failed", "info", "conflicting_values"]
    message: str
    retryable: bool = False
    dismissible: bool = False


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


Block = Annotated[
    HeadingBlock | ParagraphBlock | ListBlock | TableBlock | IssueBlock,
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
