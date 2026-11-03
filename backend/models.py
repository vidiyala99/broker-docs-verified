"""SQLAlchemy ORM models (spec section 3 data model, M1 and M7).

Tables: workspace, workflow, proposal_template, document, page, run,
run_document, model_call, message, fact, passage, artifact, artifact_version,
discrepancy_state (slice 3), task (slice 3b), feedback.

Key rules:
- `fact` is persisted (M7); each fact carries an EvidenceState (M1).
- `passage` is the citation target, keyed by (document, page) (M7). A chip
  number is derived from passages, never stored (`assign_chip_labels`).
- Artifact content is JSON validated by `schemas.ArtifactContent`; every
  change snapshots a new `artifact_version` (never update a version in place).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from backend.schemas import (
    ArtifactContent,
    ClaimBlock,
    DiscrepancyBlock,
    EvidenceState,
    RunDocumentStatus,
    RunStatus,
    Severity,
)

MAX_DOCUMENTS_PER_RUN = 8  # D3; letters A to H (DG11)
_LETTERS = "ABCDEFGH"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# --------------------------------------------------------------------- tables


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workflow(Base):
    """A workflow definition stored as data, so custom workflows need no rewrite."""

    __tablename__ = "workflow"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id"), index=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    input_slots: Mapped[list[Any]] = mapped_column(JSON, default=list)
    instructions: Mapped[str] = mapped_column(Text, default="")
    section_plan: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [SectionPlanItem]
    fact_schema: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [FactField]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProposalTemplate(Base):
    """A proposal template (slice 2), seeded as data. A run copies the resolved template
    into run.parameters at creation (D6), so later template changes never alter a run."""

    __tablename__ = "proposal_template"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id"), index=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    blocks: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [OutlineBlock]
    theme: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # applied at export only
    variables: Mapped[list[Any]] = mapped_column(JSON, default=list)  # allowed variable names
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "document"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))  # display name only, never a path
    stored_name: Mapped[str] = mapped_column(String(64), unique=True)  # generated UUID name (D4)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(50), default="quote")
    parse_error: Mapped[str | None] = mapped_column(String(50), nullable=True)  # ParseErrorCode
    parse_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    pages: Mapped[list[Page]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Page.page_no"
    )


class Page(Base):
    """One parsed page. Attribute names match `pipeline.parse.ParsedPage`, so
    rows can be passed straight to `pipeline.verify`."""

    __tablename__ = "page"
    __table_args__ = (UniqueConstraint("document_id", "page_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    page_no: Mapped[int] = mapped_column(Integer)  # 1-based
    text: Mapped[str] = mapped_column(Text, default="")  # digital text layer
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # model OCR (D5)
    # [[x0, y0, x1, y1, text, block, line, word], ...] for the digital layer.
    # Null means no word boxes are available (seam for a later local OCR, D5/D12).
    words: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    width: Mapped[float] = mapped_column(Float, default=612.0)
    height: Mapped[float] = mapped_column(Float, default=792.0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    image_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    needs_ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    # Slice 4 (D17): which OCR method produced ocr_text (pipeline.cache.ocr_key); the extraction cache
    # copies OCR text to another document only when this matches the current method.
    ocr_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    document: Mapped[Document] = relationship(back_populates="pages")


RUN_LINES = ("commercial", "personal")  # slice 3b: household cover is "personal" (R1, R2)
QUEUE_STATES = ("open", "done", "dismissed")  # work queue (spec 2026-11-02-work-queue-design 1.1)
MAX_CLIENT_NAME_CHARS = 120


class Run(Base):
    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint("line IN ('commercial', 'personal')", name="ck_run_line"),
        CheckConstraint("queue_state IN ('open', 'done', 'dismissed')", name="ck_run_queue_state"),
        Index("ix_run_line_queue_state", "line", "queue_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id"), index=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflow.id"))
    # Slice 3b: a tag only (no engine change). Outside the duplicate-run identity (D4).
    line: Mapped[str] = mapped_column(String(20), default="commercial", server_default="commercial")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[RunStatus] = mapped_column(String(20), default=RunStatus.QUEUED)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Work queue (backend/queue.py). Tags outside the duplicate-run identity, like `line`.
    # client_name: the broker-typed client, 1 to 120 chars, null when not given (W4).
    client_name: Mapped[str | None] = mapped_column(String(MAX_CLIENT_NAME_CHARS), nullable=True)
    queue_state: Mapped[str] = mapped_column(String(20), default="open", server_default="open")
    # Compare Quotes: the Proposal Builder run last started from this comparison. A plain
    # integer with no foreign key, the same rule as Task.run_id.
    proposal_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When the item last left (or was put back in) the queue: PATCH queue_state, linked proposal
    # done, last open discrepancy resolved (D2). Done recently counts 14 days from the later of
    # this and finished_at.
    queue_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow: Mapped[Workflow] = relationship()
    documents: Mapped[list[RunDocument]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunDocument.position"
    )
    model_calls: Mapped[list[ModelCall]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Message.id"
    )
    facts: Mapped[list[Fact]] = relationship(back_populates="run", cascade="all, delete-orphan")
    artifact: Mapped[Artifact | None] = relationship(back_populates="run", uselist=False)
    discrepancy_states: Mapped[list[DiscrepancyState]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="DiscrepancyState.id"
    )


class RunDocument(Base):
    """The run's doc-to-slot map: one row per document column (A to H)."""

    __tablename__ = "run_document"
    __table_args__ = (
        UniqueConstraint("run_id", "document_id"),
        UniqueConstraint("run_id", "position"),
        Index("ix_run_document_extraction_key", "extraction_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"))
    position: Mapped[int] = mapped_column(Integer)  # 0-based column index
    slot: Mapped[str] = mapped_column(String(50), default="quotes")  # workflow input slot
    status: Mapped[RunDocumentStatus] = mapped_column(String(20), default=RunDocumentStatus.QUEUED)
    progress: Mapped[str | None] = mapped_column(String(100), nullable=True)  # "Reading pages 4/10"
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stable failure code for the column: a ParseErrorCode, a ProviderError code,
    # or cancelled / interrupted / internal_error (set by pipeline.runner).
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    option_count: Mapped[int] = mapped_column(Integer, default=1)  # D8
    option_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Extraction cache (slice 4, D3/D8/D9): the key the facts were read under (set when the document is
    # DONE), the run whose read was copied (None for a live read) and when the facts were first read.
    extraction_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reused_from_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # no FK, like Task.run_id
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship(back_populates="documents")
    document: Mapped[Document] = relationship()

    @property
    def letter(self) -> str:
        return document_letter(self.position)


class ModelCall(Base):
    """Every model call logs tokens and estimated cost on the run (spec section 6)."""

    __tablename__ = "model_call"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("document.id"), nullable=True)
    purpose: Mapped[str] = mapped_column(String(30))  # extract, vision, compose, chat, suggest
    model: Mapped[str] = mapped_column(String(100))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="model_calls")


class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user, assistant, system
    content: Mapped[str] = mapped_column(Text)
    # Chat answers as cited segments, [{"text", "passage_ids"}] (same shape as schemas.Segment).
    segments: Mapped[list[Any]] = mapped_column(JSON, default=list)
    passage_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    proposed_edits: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="messages")


class Passage(Base):
    """Citation target, keyed by (document, page) (M7).

    `verified` is true once any fact on the page was found in the PDF's own
    text layer; a page cited only through model OCR stays false (scan-read).
    Per-cell chip style always comes from the fact's own state.
    `rects` accumulates the highlight boxes of the facts citing this page.
    """

    __tablename__ = "passage"
    __table_args__ = (UniqueConstraint("document_id", "page_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    page_no: Mapped[int] = mapped_column(Integer)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    rects: Mapped[list[Any]] = mapped_column(JSON, default=list)


class Fact(Base):
    """One extracted field for one document in one run, with its evidence."""

    __tablename__ = "fact"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), index=True)
    field: Mapped[str] = mapped_column(String(100))
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    cited_page: Mapped[int | None] = mapped_column(Integer, nullable=True)  # page the model gave
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)  # page the quote is on
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rects: Mapped[list[Any]] = mapped_column(JSON, default=list)
    state: Mapped[EvidenceState] = mapped_column(String(20), default=EvidenceState.PENDING)
    reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    repointed_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passage_id: Mapped[int | None] = mapped_column(ForeignKey("passage.id"), nullable=True)
    # T14 (long documents, pipeline.merge): the same value found on other pages,
    # and different values the document also states (each {"value", "page_no", "passage_id"}).
    extra_passage_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    conflicts: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="facts")
    passage: Mapped[Passage | None] = relationship()


class Artifact(Base):
    __tablename__ = "artifact"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(50), default="report")
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="artifact")
    versions: Mapped[list[ArtifactVersion]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan", order_by="ArtifactVersion.version"
    )
    feedback: Mapped[list[Feedback]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan"
    )

    def version_row(self, version: int) -> ArtifactVersion:
        for v in self.versions:
            if v.version == version:
                return v
        raise LookupError(f"artifact {self.id} has no version {version}")

    def latest_content(self) -> ArtifactContent:
        return ArtifactContent.model_validate(self.version_row(self.current_version).content)


class ArtifactVersion(Base):
    __tablename__ = "artifact_version"
    __table_args__ = (UniqueConstraint("artifact_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifact.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    artifact: Mapped[Artifact] = relationship(back_populates="versions")


DISCREPANCY_STATUSES = ("open", "resolved")  # D2: no "accepted"; what Policy Check accepts (D10, pinned)
# Slice 4 (D1, D10): certificate claims share the table; "removed" is valid for certificate runs only
# (the route enforces it per workflow), "resolved" on a certificate is an override with a reason.
CLAIM_STATUSES = ("open", "resolved", "removed")


class DiscrepancyState(Base):
    """The broker's decision on one Policy Check discrepancy (slice 3 T6, spec section 4), or on
    one certificate claim (slice 4, D1: same rules, plus status "removed").

    Kept outside the artifact, so a regenerate never wipes a decision. `item_id` is the
    DiscrepancyBlock id (derived from the canonical key, stable across regeneration).
    `fingerprint` is the hash of the item's compared slot values in the current version;
    `resolved_fingerprint` the one the broker resolved. Reconciled on every new version
    (`reconcile_discrepancy_states`):

        resolved (or removed) and current values differ from the decided ones ──► open,
            changed_since_resolved, reset_from = the old status
        open after such a reset and the values are back (version restore) ──► reset_from again
        an item missing from the current version: row untouched (it may come back)
    """

    __tablename__ = "discrepancy_state"
    __table_args__ = (
        UniqueConstraint("run_id", "item_id"),
        CheckConstraint("status IN ('open', 'resolved', 'removed')", name="ck_discrepancy_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="open")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    resolved_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changed_since_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    # Slice 4: the decision a fingerprint change reset (resolved | removed), restored if the values return.
    reset_from: Mapped[str | None] = mapped_column(String(20), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="discrepancy_states")


TASK_STATUSES = ("open", "done")
MAX_TASK_TITLE_CHARS = 200
MAX_DISCREPANCY_TITLE_CHARS = 300


class Task(Base):
    """A broker's to-do (slice 3b, spec 1.2): title, optional due date, open | done, and an
    optional link to a run or to one discrepancy of a run.

    `run_id` is a plain integer with no foreign key (D2): it is checked on write, and a run row
    that goes away later leaves the link in place so the list can say `run_missing`.
    `discrepancy_item_id` needs `run_id` and must name an item of that run's current report when
    it is written (D3). `discrepancy_title` is the block title saved at link time and refreshed
    while the item is in the report (D8), so a dropped item still has a label. A task never
    changes a discrepancy state, nor the other way round (R5).
    """

    __tablename__ = "task"
    __table_args__ = (CheckConstraint("status IN ('open', 'done')", name="ck_task_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id"), index=True)
    title: Mapped[str] = mapped_column(String(MAX_TASK_TITLE_CHARS))
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # no FK (D2), indexed (P1)
    discrepancy_item_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    discrepancy_title: Mapped[str | None] = mapped_column(String(MAX_DISCREPANCY_TITLE_CHARS), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


MAX_HOLDER_NAME_CHARS = 120  # D6
MAX_HOLDER_ADDRESS_CHARS = 300
MAX_DESCRIPTION_CHARS = 1500


class Holder(Base):
    """A certificate holder remembered per client (slice 4, C2): saved at Issue, offered next time.
    `client_key` is the normalized client name (work_queue.client_key)."""

    __tablename__ = "holder"
    __table_args__ = (
        Index("ix_holder_workspace_client", "workspace_id", "client_key"),
        UniqueConstraint("workspace_id", "client_key", "name_key", name="uq_holder_client_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id"))
    client_key: Mapped[str] = mapped_column(String(MAX_CLIENT_NAME_CHARS))
    name: Mapped[str] = mapped_column(String(MAX_HOLDER_NAME_CHARS))
    name_key: Mapped[str] = mapped_column(String(MAX_HOLDER_NAME_CHARS))  # normalized name (one row per holder)
    address: Mapped[str] = mapped_column(String(MAX_HOLDER_ADDRESS_CHARS), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CertificateIssue(Base):
    """An issued certificate (slice 4, D5, D16): one artifact version frozen with its holder, its
    claims and their decisions (override reasons), and the PDF bytes, written in one transaction.
    Unique per (artifact, version): a second Issue of the same version returns this row."""

    __tablename__ = "certificate_issue"
    __table_args__ = (UniqueConstraint("artifact_id", "version", name="uq_certificate_issue_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifact.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    holder: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"name", "address"}
    claims: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{key, title, verdict, status, note, ...}]
    certificate: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # the frozen CertificateBlock
    pdf_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    pdf_sha256: Mapped[str] = mapped_column(String(64))


class Feedback(Base):
    """Thumbs up/down on one artifact version (spec section 4 step 5)."""

    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version"),
        CheckConstraint("rating IN (-1, 1)", name="ck_feedback_rating"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifact.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    rating: Mapped[int] = mapped_column(Integer)  # +1 or -1
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    artifact: Mapped[Artifact] = relationship(back_populates="feedback")


# --------------------------------------------------------------------- state machines


class InvalidTransition(ValueError):
    pass


_FACT_TRANSITIONS: dict[EvidenceState, frozenset[EvidenceState]] = {
    EvidenceState.PENDING: frozenset(
        {EvidenceState.VERIFIED, EvidenceState.SCAN_READ, EvidenceState.NOT_FOUND,
         EvidenceState.ABSENT, EvidenceState.FAILED, EvidenceState.CONFLICTING}
    ),
    EvidenceState.FAILED: frozenset({EvidenceState.PENDING}),  # retry
    EvidenceState.VERIFIED: frozenset(),
    EvidenceState.SCAN_READ: frozenset(),
    EvidenceState.NOT_FOUND: frozenset(),
    EvidenceState.ABSENT: frozenset(),
    EvidenceState.CONFLICTING: frozenset(),
}

_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset({RunStatus.COMPOSING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.COMPOSING: frozenset(
        {RunStatus.DONE, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.DONE: frozenset(),
    RunStatus.PARTIAL: frozenset({RunStatus.QUEUED}),  # retry failed documents
    RunStatus.FAILED: frozenset({RunStatus.QUEUED}),  # retry (also after startup reconcile, M6)
    RunStatus.CANCELLED: frozenset({RunStatus.QUEUED}),
}

_RD = RunDocumentStatus
_RUN_DOC_TRANSITIONS: dict[RunDocumentStatus, frozenset[RunDocumentStatus]] = {
    _RD.QUEUED: frozenset({_RD.READING, _RD.FAILED}),
    _RD.READING: frozenset({_RD.EXTRACTING, _RD.FAILED}),
    _RD.EXTRACTING: frozenset({_RD.VERIFYING, _RD.FAILED}),
    _RD.VERIFYING: frozenset({_RD.DONE, _RD.FAILED}),
    _RD.DONE: frozenset(),
    _RD.FAILED: frozenset({_RD.QUEUED}),  # retry
}


def _transition(obj: Any, attr: str, table: dict, enum: type, new: Any) -> None:
    current = enum(getattr(obj, attr))
    new = enum(new)
    if new not in table[current]:
        raise InvalidTransition(
            f"{type(obj).__name__}.{attr}: {current.value} -> {new.value} is not allowed"
        )
    setattr(obj, attr, new)


def transition_fact(fact: Fact, new: EvidenceState) -> None:
    _transition(fact, "state", _FACT_TRANSITIONS, EvidenceState, new)


def transition_run(run: Run, new: RunStatus) -> None:
    _transition(run, "status", _RUN_TRANSITIONS, RunStatus, new)


# Slice 4 (D2): Add document reopens a certificate draft. Never through transition_run, whose table keeps
# DONE terminal for every workflow; only this guarded function, for certificate runs only.
_CERTIFICATE_REOPEN = {RunStatus.DONE: RunStatus.QUEUED, RunStatus.PARTIAL: RunStatus.QUEUED}


def reopen_certificate_run(run: Run, certificate_slug: str) -> None:
    """DONE or PARTIAL -> QUEUED for a certificate draft that gets a new document. Raises
    InvalidTransition for any other workflow or status (the caller checks "not issued")."""
    current = RunStatus(run.status)
    if run.workflow is None or run.workflow.slug != certificate_slug:
        raise InvalidTransition("only a certificate draft can take a new document")
    if current not in _CERTIFICATE_REOPEN:
        raise InvalidTransition(f"Run.status: {current.value} cannot take a new document")
    run.status = _CERTIFICATE_REOPEN[current]


def transition_run_document(rd: RunDocument, new: RunDocumentStatus) -> None:
    _transition(rd, "status", _RUN_DOC_TRANSITIONS, RunDocumentStatus, new)


_DISPLAY = {
    EvidenceState.ABSENT: "Not specified",
    EvidenceState.PENDING: "Not specified",
    EvidenceState.NOT_FOUND: "Couldn't verify",
    EvidenceState.FAILED: "Extraction failed",
    EvidenceState.CONFLICTING: "Conflicting values",  # T14: the values are on the cell's notice and chips
}


def display_value(state: EvidenceState, value: str | None) -> str:
    """Text a cell shows for a fact in this state (M1). Only cited states show the value."""
    state = EvidenceState(state)
    if state in (EvidenceState.VERIFIED, EvidenceState.SCAN_READ):
        return value if value else "Not specified"
    return _DISPLAY[state]


# --------------------------------------------------------------------- helpers


def document_letter(position: int) -> str:
    if not 0 <= position < MAX_DOCUMENTS_PER_RUN:
        raise ValueError(f"position {position} outside 0..{MAX_DOCUMENTS_PER_RUN - 1}")
    return _LETTERS[position]


def upsert_passage(
    session: Session, document_id: int, page_no: int, *, verified: bool, rects: Iterable[Any]
) -> Passage:
    """Get or create the (document, page) passage and merge a fact's evidence in."""
    passage = session.scalar(
        select(Passage).where(Passage.document_id == document_id, Passage.page_no == page_no)
    )
    if passage is None:
        passage = Passage(document_id=document_id, page_no=page_no, verified=False, rects=[])
        session.add(passage)
    merged = list(passage.rects or [])
    for rect in rects:
        rect = [float(c) for c in rect]
        if rect not in merged:
            merged.append(rect)
    passage.rects = merged  # reassign: JSON columns do not track in-place mutation
    passage.verified = bool(passage.verified) or verified
    session.flush()
    return passage


def attach_merged_evidence(session: Session, fact: Fact, document_id: int, merged: Any) -> None:
    """Store a merged long-document fact's extra passages and conflicting values (T14).

    `merged` is a pipeline.merge.MergedFact (duck-typed: `extras` and
    `conflicts` are sequences of verify results with state, page_no, rects).
    """

    def passage_for(r: Any) -> int | None:
        state = EvidenceState(r.state)
        if r.page_no is None or state not in (EvidenceState.VERIFIED, EvidenceState.SCAN_READ):
            return None
        return upsert_passage(session, document_id, r.page_no, verified=state == EvidenceState.VERIFIED,
                              rects=r.rects).id

    extras: list[int] = []
    for r in getattr(merged, "extras", ()) or ():
        pid = passage_for(r)
        if pid is not None and pid != fact.passage_id and pid not in extras:
            extras.append(pid)
    conflicts = [{"value": r.value, "page_no": r.page_no, "passage_id": passage_for(r)}
                 for r in getattr(merged, "conflicts", ()) or ()]
    fact.extra_passage_ids = extras
    fact.conflicts = conflicts


def assign_chip_labels(entries: Iterable[tuple[int, str, int]]) -> dict[int, str]:
    """Derive chip labels from (passage_id, document letter, page_no) references.

    Labels are the document letter plus the passage's rank by page within that
    document, for example "B2" (DG11). Duplicate references share one label,
    which is how one chip number repeats across cells of a column.
    """
    by_doc: dict[str, dict[int, int]] = {}
    for passage_id, letter, page_no in entries:
        by_doc.setdefault(letter, {})[passage_id] = page_no
    labels: dict[int, str] = {}
    for letter, passages in by_doc.items():
        ordered = sorted(passages.items(), key=lambda item: (item[1], item[0]))
        for rank, (passage_id, _) in enumerate(ordered, start=1):
            labels[passage_id] = f"{letter}{rank}"
    return labels


def record_model_call(
    session: Session,
    run: Run,
    *,
    purpose: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    document_id: int | None = None,
    ok: bool = True,
) -> ModelCall:
    call = ModelCall(
        run=run,
        document_id=document_id,
        purpose=purpose,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        ok=ok,
    )
    session.add(call)
    run.tokens_in = (run.tokens_in or 0) + tokens_in
    run.tokens_out = (run.tokens_out or 0) + tokens_out
    run.cost_usd = (run.cost_usd or 0.0) + cost_usd
    return call


def create_artifact(
    session: Session, run: Run, *, title: str, content: ArtifactContent, kind: str = "report",
    note: str = "generated",
) -> Artifact:
    artifact = Artifact(run=run, title=title, kind=kind, current_version=0)
    session.add(artifact)
    session.flush()
    snapshot_version(session, artifact, content, note=note)
    return artifact


def snapshot_version(
    session: Session, artifact: Artifact, content: ArtifactContent, *, note: str | None = None
) -> ArtifactVersion:
    """Store `content` as the next version and make it current."""
    content = ArtifactContent.model_validate(content)
    version = ArtifactVersion(
        artifact=artifact,
        version=artifact.current_version + 1,
        content=content.model_dump(mode="json"),
        note=note,
    )
    session.add(version)
    artifact.current_version = version.version
    session.flush()
    # T6: every new version (run, retry, regenerate, restore, edit) is the active comparison now.
    reconcile_discrepancy_states(session, artifact.run_id, content)
    return version


# --------------------------------------------------------------------- discrepancy state (slice 3 T6)


def discrepancy_fingerprint(block: DiscrepancyBlock) -> str:
    """Hash of what the item compares: item state and, per role, the slot's evidence state, value
    and conflicting values. Citations (passage ids) are left out, so re-reading a document that
    states the same values on another page keeps a decision."""
    slots = {
        str(role): [str(cell.state) if cell.state else None, cell.value,
                    [c.value for c in cell.conflicts]]
        for role, cell in sorted(block.slots.items(), key=lambda kv: str(kv[0]))
    }
    payload = json.dumps({"item_state": str(block.item_state), "slots": slots}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def discrepancy_blocks(content: ArtifactContent) -> list[DiscrepancyBlock]:
    return [b for _, b in content.iter_blocks() if isinstance(b, DiscrepancyBlock)]


def claim_fingerprint(block: ClaimBlock) -> str:
    """Hash of what a certificate claim's verdict rests on (slice 4): kind, verdict, reason, condition
    and its basis values. Citations are left out (as for a discrepancy), so a re-read that states the
    same values on another page keeps the broker's decision; any change of evidence reopens it."""
    payload = json.dumps({"kind": str(block.kind), "verdict": str(block.verdict), "reason": block.reason,
                          "condition": block.condition, "basis": list(block.basis)}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def claim_blocks(content: ArtifactContent) -> list[ClaimBlock]:
    return [b for _, b in content.iter_blocks() if isinstance(b, ClaimBlock)]


def item_fingerprint(block: DiscrepancyBlock | ClaimBlock) -> str:
    return claim_fingerprint(block) if isinstance(block, ClaimBlock) else discrepancy_fingerprint(block)


def reconciled_status(row: DiscrepancyState | None, fp: str) -> str:
    """The status `reconcile_discrepancy_states` gives a row for fingerprint `fp`, without changing it
    (pipeline.certificate applies removals before the version is stored, so content and rows agree)."""
    if row is None:
        return "open"
    if row.fingerprint == fp:
        return row.status
    if row.status in ("resolved", "removed") and fp != row.resolved_fingerprint:
        return "open"
    if row.status == "open" and row.changed_since_resolved and fp == row.resolved_fingerprint:
        return row.reset_from or "resolved"
    return row.status


def reconcile_discrepancy_states(session: Session, run_id: int, content: ArtifactContent) -> None:
    """Bring the run's decisions in line with `content`, its current version (see DiscrepancyState).
    A content without discrepancies or certificate claims (Compare, Proposal) touches nothing."""
    blocks: list[DiscrepancyBlock | ClaimBlock] = [*discrepancy_blocks(content), *claim_blocks(content)]
    if not blocks:
        return
    rows = {r.item_id: r for r in session.scalars(select(DiscrepancyState).where(DiscrepancyState.run_id == run_id))}
    now = utcnow()
    for block in blocks:
        fp = item_fingerprint(block)
        row = rows.get(block.id)
        if row is None:
            row = DiscrepancyState(run_id=run_id, item_id=block.id, status="open", fingerprint=fp,
                                   changed_since_resolved=False, updated_at=now)
            session.add(row)
            rows[block.id] = row
            continue
        if row.fingerprint == fp:
            continue
        status = reconciled_status(row, fp)
        if status != row.status:
            if status == "open":
                row.reset_from, row.changed_since_resolved = row.status, True
            else:
                row.reset_from, row.changed_since_resolved = None, False
            row.status, row.updated_at = status, now
        row.fingerprint = fp
    session.flush()


SEVERITY_KEYS = tuple(s.value for s in Severity)


@dataclass(frozen=True)
class DiscrepancyCounts:
    """Per-severity counts over the current report's items (R7, M6)."""

    counts: dict[str, int]  # every item
    open_counts: dict[str, int]  # items whose status is open (an item without a state row is open)
    total: int
    resolved: int

    @property
    def open_total(self) -> int:
        return sum(self.open_counts.values())


def discrepancy_counts(session: Session, run: Run, *, blocks: Iterable[DiscrepancyBlock] | None = None,
                       rows: dict[str, DiscrepancyState] | None = None) -> DiscrepancyCounts | None:
    """Counts for `run`'s current artifact version, or None when the run has no report.

    Iterates the current version's DiscrepancyBlocks joined to their state rows (M6), never all
    state rows: an item gone from the latest version is not counted, and counts again if it comes
    back. `blocks` and `rows` let a caller that already loaded them (list_discrepancies, the queue's
    batched load) skip the queries."""
    if blocks is None:
        if run.artifact is None:
            return None
        blocks = discrepancy_blocks(run.artifact.latest_content())
    if rows is None:
        rows = {r.item_id: r for r in session.scalars(select(DiscrepancyState)
                                                    .where(DiscrepancyState.run_id == run.id))}
    counts = dict.fromkeys(SEVERITY_KEYS, 0)
    open_counts = dict.fromkeys(SEVERITY_KEYS, 0)
    total = resolved = 0
    for block in blocks:
        severity = str(block.severity)
        row = rows.get(block.id)
        status = row.status if row else "open"
        total += 1
        counts[severity] += 1
        if status == "open":
            open_counts[severity] += 1
        elif status == "resolved":
            resolved += 1
    return DiscrepancyCounts(counts=counts, open_counts=open_counts, total=total, resolved=resolved)


_UNSET: Any = object()


def update_discrepancy_state(session: Session, run_id: int, item_id: str, *, status: str | None = None,
                             note: str | None = _UNSET, allowed: tuple[str, ...] = DISCREPANCY_STATUSES
                             ) -> DiscrepancyState:
    """Set an item's status and/or note (PATCH). A status change clears "changed since resolved";
    resolving (or removing, certificates) records the current fingerprint as the decided one. Raises
    ValueError for a status outside `allowed` (open | resolved; certificates pass CLAIM_STATUSES) and
    LookupError for an item with no state row."""
    if status is not None and status not in allowed:
        raise ValueError(f"status must be one of {allowed}")
    row = session.scalar(select(DiscrepancyState).where(DiscrepancyState.run_id == run_id,
                                                        DiscrepancyState.item_id == item_id))
    if row is None:
        raise LookupError(f"run {run_id} has no discrepancy {item_id!r}")
    was_open = row.status == "open"
    if status is not None:
        row.status = status
        row.changed_since_resolved = False
        row.reset_from = None
        row.resolved_fingerprint = row.fingerprint if status in ("resolved", "removed") else None
    if note is not _UNSET:
        row.note = note
    row.updated_at = utcnow()
    session.flush()
    if was_open and status == "resolved":
        # Work queue (D2): resolving the last open item of the current report is the moment the
        # check leaves Needs review, so Done recently counts its 14 days from now.
        run = session.get(Run, run_id)
        tally = discrepancy_counts(session, run) if run is not None else None
        if tally is not None and tally.total and tally.open_total == 0:
            run.queue_changed_at = row.updated_at
            session.flush()
    return row


def item_tasks(session: Session, run_id: int) -> dict[str, Task]:
    """The task each discrepancy item of the run shows (O4): the first open task linked to the
    item by created_at DESC, id DESC, else the latest one. Items without a task are absent."""
    picked: dict[str, Task] = {}
    rows = session.scalars(select(Task).where(Task.run_id == run_id, Task.discrepancy_item_id.is_not(None))
                           .order_by(Task.created_at.desc(), Task.id.desc()))
    for task in rows:
        current = picked.get(task.discrepancy_item_id)
        if current is None or (current.status != "open" and task.status == "open"):
            picked[task.discrepancy_item_id] = task
    return picked


def restore_version(session: Session, artifact: Artifact, version: int) -> ArtifactVersion:
    """Undo: copy an earlier version forward as a new snapshot (history is never rewritten)."""
    source = artifact.version_row(version)
    return snapshot_version(
        session, artifact, ArtifactContent.model_validate(source.content), note=f"restore v{version}"
    )
