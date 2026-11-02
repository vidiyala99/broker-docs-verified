"""SQLAlchemy ORM models (spec section 3 data model, M1 and M7).

Tables: workspace, workflow, document, page, run, run_document, model_call,
message, fact, passage, artifact, artifact_version, feedback.

Key rules:
- `fact` is persisted (M7); each fact carries an EvidenceState (M1).
- `passage` is the citation target, keyed by (document, page) (M7). A chip
  number is derived from passages, never stored (`assign_chip_labels`).
- Artifact content is JSON validated by `schemas.ArtifactContent`; every
  change snapshots a new `artifact_version` (never update a version in place).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from backend.schemas import (
    ArtifactContent,
    EvidenceState,
    RunDocumentStatus,
    RunStatus,
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

    document: Mapped[Document] = relationship(back_populates="pages")


class Run(Base):
    __tablename__ = "run"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id"), index=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflow.id"))
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


class RunDocument(Base):
    """The run's doc-to-slot map: one row per document column (A to H)."""

    __tablename__ = "run_document"
    __table_args__ = (
        UniqueConstraint("run_id", "document_id"),
        UniqueConstraint("run_id", "position"),
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
    return version


def restore_version(session: Session, artifact: Artifact, version: int) -> ArtifactVersion:
    """Undo: copy an earlier version forward as a new snapshot (history is never rewritten)."""
    source = artifact.version_row(version)
    return snapshot_version(
        session, artifact, ArtifactContent.model_validate(source.content), note=f"restore v{version}"
    )
