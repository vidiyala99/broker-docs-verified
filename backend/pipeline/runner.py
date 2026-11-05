r"""Run orchestration (T6) and cancel (T13): asyncio tasks, SSE events, reconcile.

State machine of one run (statuses from schemas.RunStatus, transitions
enforced by models.transition_run):

    submit ──► QUEUED ──start──► RUNNING ──all documents settled──► COMPOSING ──► DONE
                 │                  │  \                               │      └──► PARTIAL
                 │                  │   └─ no document usable ──► FAILED (no compose call)
                 └── cancel ────────┴── cancel ──────────────────────────┴──► CANCELLED
    process restart: QUEUED / RUNNING / COMPOSING ──reconcile──► FAILED (retryable, M6)
    retry: FAILED / PARTIAL / CANCELLED ──► QUEUED (only documents not DONE run again)

Per document, in parallel (one asyncio task each, inside a TaskGroup so a
cancel reaches every child and waits for it):

    READING     load bytes + parse (thread pool); OCR pages that trip M3
                (render in the thread pool, vision call on the loop)
    EXTRACTING  pipeline.extract (one strict call); over 15 pages, pipeline.windows
                (page windows, progress "Extracting window k/N", T14)
    VERIFYING   pipeline.verify (thread pool), then fact + passage rows
    DONE / FAILED (ParseError code, ProviderError code, cancelled, internal_error)

Policy Check (slice 3, `_Plan.policy_check`): an AMS document skips EXTRACTING's model
call; its facts come from ams.facts_from_original (the kept original, D5) and verify
against its rendered PDF. COMPOSING builds the report in code (policy_check_content:
diff -> DiscrepancyBlocks, D14), so the run makes no compose call.

Concurrency (M6): at most 4 model calls at once is enforced inside the
Provider (its semaphore); parse, render and verify run in this manager's
thread pool. The database is only touched from the event loop thread, in
short sessions that never span an await, so threads do pure computation.

Budget (D3, M4): one RunBudget per execution, limited to the run's budget
minus tokens the run already spent (retries count against the same cap).
Before any document starts, `compose_headroom` tokens are reserved and held
back so extraction cannot starve the report; the hold is released just
before compose. A document whose call does not fit fails with
budget_exhausted and the run completes PARTIAL with a budget notice. Each
document spends through its own DocumentBudget child (T14, 500k ceiling by
default) so one long document cannot starve the rest; a document over its
ceiling fails with document_budget_exhausted.

Idempotency: `submit` returns the active run for the same workflow,
documents and parameters instead of creating a second one (double-click
Run), and `start` never launches a second task for a run.

Events (for SSE): see `EventBus` and `RunManager.events`; `format_sse`
renders one event as a text/event-stream frame.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import itertools
import json
import logging
import re
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend import ams, db, models, work_queue
from backend.loss_runs import pdf_rows, sheets
from backend.loss_runs import report as loss_report
from backend.pipeline import cache, certificate, compose, extract, parse, policy_check, verify, windows
from backend.providers import CallRecord, Provider, ProviderError, RunBudget
from backend.schemas import (
    CITED_STATES,
    ArtifactContent,
    EvidenceState,
    FactField,
    Notice,
    OutlineBlock,
    RunDocumentStatus,
    RunStatus,
    SectionPlanItem,
    StoredProposal,
    outline_errors,
)

log = logging.getLogger("brokerdocs.runner")

ACTIVE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.COMPOSING)
RETRYABLE_STATUSES = (RunStatus.FAILED, RunStatus.PARTIAL, RunStatus.CANCELLED)
_TERMINAL_DOC = (RunDocumentStatus.DONE, RunDocumentStatus.FAILED)
DEFAULT_COMPOSE_HEADROOM = 40_000  # compose input (facts) plus its 16k reserved output, with margin
LABEL_FIELDS = ("carrier_name", "carrier")  # fact whose cited value names a column

_current_document: contextvars.ContextVar[int | None] = contextvars.ContextVar("brokerdocs_document", default=None)

# --------------------------------------------------------------------- failure reasons

_MESSAGES = {
    "budget_exhausted": "Budget exhausted: the run's token budget ran out before this document was read.",
    "document_budget_exhausted": "This document needs more tokens than one document may use in a run.",
    "refusal": "The model declined to read this document.",
    "schema_invalid": "The model's answer could not be read, twice in a row.",
    "truncated": "The model's answer was cut off before it finished.",
    "timeout": "The model service timed out. Retry later.",
    "unavailable": "The model service is unavailable right now. Retry later.",
    "context_overflow": "This document is too long for the model.",
    "request_error": "The model request was rejected. Check the model settings and API key.",
    "missing_key": "No API key is configured for the model service.",
    "cancelled": "Cancelled.",
    "interrupted": "Interrupted by a restart. Retry to continue.",
    "internal_error": "Something went wrong while processing this document.",
}


def failure_message(code: str) -> str:
    """User-facing reason for a failure code (parse failures carry their own message)."""
    return _MESSAGES.get(code, "This document could not be processed.")


def _page_ranges(pages: Iterable[int]) -> str:
    """[3, 4, 5, 9] -> "3 to 5, 9"."""
    ps = sorted(set(pages))
    out: list[str] = []
    start = None
    for i, p in enumerate(ps):
        start = p if start is None else start
        if i + 1 == len(ps) or ps[i + 1] != p + 1:
            out.append(str(p) if start == p else f"{start} to {p}")
            start = None
    return ", ".join(out)


# --------------------------------------------------------------------- events


def format_sse(event: dict[str, Any]) -> str:
    """One event as a text/event-stream frame. The data line is the whole event as JSON."""
    head = f"id: {event['id']}\n" if event.get("id") is not None else ""
    return f"{head}event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'), default=str)}\n\n"


class EventBus:
    """In-memory event fan-out per run with bounded replay history.

    Event shape: {"id": int, "run_id": int, "execution": int, "type": str, "data": dict};
    ids are increasing across the process, so an SSE Last-Event-ID resumes a
    stream. `execution` counts task launches of the run (1 for the first run,
    2 after the first retry, ...; 0 for events of a run never started in this
    process), so a reader can tell the run.end of an earlier execution from the
    current one.
    """

    def __init__(self, max_history: int = 1000):
        self._ids = itertools.count(1)
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=max_history))
        self._subscribers: dict[int, set[asyncio.Queue]] = defaultdict(set)
        self._executions: dict[int, int] = defaultdict(int)

    def begin_execution(self, run_id: int) -> int:
        """A new task is about to run for `run_id`; later events belong to it."""
        self._executions[run_id] += 1
        return self._executions[run_id]

    def execution(self, run_id: int) -> int:
        return self._executions.get(run_id, 0)

    def publish(self, run_id: int, type_: str, data: dict[str, Any]) -> dict[str, Any]:
        event = {"id": next(self._ids), "run_id": run_id, "execution": self.execution(run_id),
                 "type": type_, "data": data}
        self._history[run_id].append(event)
        for q in list(self._subscribers.get(run_id, ())):
            q.put_nowait(event)
        return event

    def history(self, run_id: int, after: int | None = None) -> list[dict[str, Any]]:
        return [e for e in self._history.get(run_id, ()) if after is None or e["id"] > after]

    def subscribe(self, run_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers[run_id].add(q)
        return q

    def unsubscribe(self, run_id: int, q: asyncio.Queue) -> None:
        self._subscribers.get(run_id, set()).discard(q)


# --------------------------------------------------------------------- helpers


@dataclass(frozen=True)
class DocumentRef:
    """What the byte loader gets (never an ORM object, it may run in a thread)."""

    id: int
    filename: str
    stored_name: str
    kind: str = "quote"  # models.Document.kind; "ams" for an AMS export (slice 3)


@dataclass
class _PageData:
    page_no: int
    text: str
    words: list | None
    ocr_text: str | None
    needs_ocr: bool

    @property
    def ocr(self) -> bool:
        return self.ocr_text is not None


@dataclass(frozen=True)
class ComposePlan:
    """What a run composes from: the workflow's fields, title and sections, or for a
    proposal the run's outline and brief (slice 2). Built by `compose_plan`, the one
    helper used by the run, retry recompose and Regenerate prose (S2)."""

    title: str
    fields: list[FactField]
    sections: list[SectionPlanItem]
    instructions: str
    outline: tuple[OutlineBlock, ...] = ()
    fact_sections: tuple[SectionPlanItem, ...] = ()
    brief: compose.ProposalBrief | None = None
    compare: bool = False  # slice 6: Compare Quotes (marks and the Key difference)
    additional_context: str = ""  # slice 6: the broker's Additional context (Compare Quotes, BRIEF channel only)


def is_compare(run: models.Run) -> bool:
    return run.workflow is not None and run.workflow.slug == db.COMPARE_QUOTES_SLUG


def is_policy_check(run: models.Run) -> bool:
    return run.workflow.slug == db.POLICY_CHECK_SLUG


def is_certificate(run: models.Run) -> bool:
    return run.workflow is not None and run.workflow.slug == db.CERTIFICATE_SLUG


def is_loss_run(run: models.Run) -> bool:
    return run.workflow is not None and run.workflow.slug == db.LOSS_RUN_SLUG


def loss_cache_parts() -> dict[str, Any]:
    """Slice 5a (R8): what a loss-run read depends on beyond the fact pass."""
    return {"rows": pdf_rows.cache_parts(), "sheets": sheets.cache_parts()}


def proposal_title(client_name: str) -> str:
    return (f"Insurance Proposal for {client_name}" if client_name else "Insurance Proposal")[:200]


def compose_plan(run: models.Run) -> ComposePlan:
    """The run's compose plan. Only the proposal-builder workflow reads an outline from
    run.parameters, re-validated here (it reaches the prompt); a stored outline that
    fails validation raises ValueError (the run fails, nothing is composed from it)."""
    wf = run.workflow
    fields = [FactField.model_validate(f) for f in wf.fact_schema]
    static = [SectionPlanItem.model_validate(x) for x in wf.section_plan]
    params = run.parameters or {}
    if wf.slug == db.COMPARE_QUOTES_SLUG:
        context = params.get("additional_context")
        return ComposePlan(title=wf.name, fields=fields, sections=static, instructions=wf.instructions or "",
                           compare=True, additional_context=context if isinstance(context, str) else "")
    if wf.slug != db.PROPOSAL_BUILDER_SLUG or "outline" not in params:
        return ComposePlan(title=wf.name, fields=fields, sections=static, instructions=wf.instructions or "")
    stored = StoredProposal.model_validate(params)
    problems = outline_errors(stored.outline, {x.id for x in static})
    if problems:
        raise ValueError(f"stored proposal outline is invalid: {problems[0][1]}")
    outline = tuple(stored.outline)
    brief = compose.ProposalBrief(
        title=proposal_title(stored.client_name), client_name=stored.client_name, broker_name=stored.broker_name,
        brokerage_name=stored.brokerage_name, additional_context=stored.additional_context,
        no_ai_prose=stored.no_ai_prose)
    return ComposePlan(title=brief.title, fields=fields, sections=compose.outline_sections(outline),
                       instructions=wf.instructions or "", outline=outline, fact_sections=tuple(static),
                       brief=brief)


@dataclass(frozen=True)
class _Plan:
    run_id: int
    compose: ComposePlan
    todo: list[tuple[int, DocumentRef]]  # (run_document id, document)
    budget_limit: int
    policy_check: bool = False  # slice 3: AMS facts from code, report from the diff (no compose call)
    certificate: bool = False  # slice 4: the draft is built in code (pipeline.certificate, no compose call)
    loss_run: bool = False  # slice 5a: rows from sheets (code) and PDF row windows; the review is built in code
    cache_key: str | None = None  # slice 4: extraction cache key (pipeline.cache); None = cache off

    @property
    def fields(self) -> list[FactField]:
        return self.compose.fields

    @property
    def instructions(self) -> str:
        return self.compose.instructions

    @property
    def title(self) -> str:
        return self.compose.title


def files_dir() -> Path:
    """Where uploads are stored under their generated UUID names (D4); T7 writes here."""
    path = db.data_dir() / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def load_stored_bytes(doc: DocumentRef) -> bytes:
    if not _SAFE_NAME.match(doc.stored_name):
        raise ValueError("stored name is not a generated name")
    return (files_dir() / doc.stored_name).read_bytes()


def load_stored_original(doc: DocumentRef) -> bytes:
    """The kept original of an AMS export (uploads.store_original), for ams.facts_from_original."""
    from backend import uploads  # uploads imports this module (files_dir)

    return uploads.load_original(doc.stored_name)


# --------------------------------------------------------------------- slots (slice 3, D8 / D11)

DEFAULT_SLOT = "quotes"  # RunDocument.slot column default; every run before slice 3 used it


class SlotError(ValueError):
    """The run's slot assignment does not fit the workflow's input slots (D8). The
    message is user-safe; the route answers 400 invalid_run."""


def _slot_defs(input_slots: Sequence[Any] | None) -> list[dict[str, Any]]:
    return [d for d in input_slots or () if isinstance(d, dict) and d.get("key")]


def _label(d: dict[str, Any]) -> str:
    return str(d.get("label") or d["key"])


def resolve_slots(input_slots: Sequence[Any] | None, ids: Sequence[int],
                  slots: Mapping[int, str] | None) -> list[str]:
    """The slot key of each document in `ids` order, validated against the workflow.

    No `slots` (every run before slice 3): a workflow with one input slot puts every
    document in it, exactly as before; a workflow with several slots needs `slots`.
    With `slots`: every document has exactly one known slot key, no other document is
    named, and each slot's document count is within its min and max.
    """
    defs = _slot_defs(input_slots)
    if slots is None:
        if len(defs) > 1:
            raise SlotError("Assign each document to a slot: " + ", ".join(_label(d) for d in defs) + ".")
        return [defs[0]["key"] if defs else DEFAULT_SLOT] * len(ids)
    given = {int(k): v for k, v in slots.items()}
    extra = [d for d in given if d not in set(ids)]
    if extra:
        raise SlotError(f"Document {extra[0]} has a slot but is not in this run.")
    if any(d not in given for d in ids):
        raise SlotError("Assign a slot to every document in the run.")
    known = {d["key"]: d for d in defs}
    for d in ids:
        key = given[d]
        if key not in known:
            raise SlotError(f"Unknown slot '{str(key)[:50]}'. This workflow's slots are: "
                            + ", ".join(known) + ".")
    for key, d in known.items():
        n = sum(1 for v in given.values() if v == key)
        low, high = int(d.get("min", 0) or 0), d.get("max")
        if n < low:
            raise SlotError(f"{_label(d)} needs {low} document{'s' if low != 1 else ''}; {n} assigned.")
        if high is not None and n > int(high):
            raise SlotError(f"{_label(d)} takes at most {high} document{'s' if high != 1 else ''}; "
                            f"{n} assigned.")
    return [given[d] for d in ids]


def slot_states(run: models.Run) -> dict[str, str]:
    """Per workflow slot: "read" (a document in it is DONE), "not_read" (its documents
    failed, budget exhaustion included: D11 "Not read", never N/A), "absent" (no document
    was given: N/A), or "pending" (still running). Slots in workflow order; a slot key a
    run uses that the workflow no longer defines is appended."""
    keys = [d["key"] for d in _slot_defs(run.workflow.input_slots)]
    keys += [k for k in dict.fromkeys(rd.slot for rd in run.documents) if k not in keys]
    out = {}
    for key in keys:
        statuses = [RunDocumentStatus(rd.status) for rd in run.documents if rd.slot == key]
        if not statuses:
            out[key] = "absent"
        elif RunDocumentStatus.DONE in statuses:
            out[key] = "read"
        elif all(st == RunDocumentStatus.FAILED for st in statuses):
            out[key] = "not_read"
        else:
            out[key] = "pending"
    return out


_AMS_TYPES = ("application/json", "text/csv")
_SHEET_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SPREADSHEET_KIND = "spreadsheet"  # slice 5a: an uploaded .xlsx / .csv loss run (no PDF; sheet pages on demand)


def _accepts(d: dict[str, Any]) -> tuple[bool, bool]:
    """(takes PDFs, takes AMS exports) for one slot definition; no `accepts` takes PDFs only."""
    accepts = [a.strip().lower() for a in str(d.get("accepts") or "application/pdf").split(",")]
    return "application/pdf" in accepts, any(a in accepts for a in _AMS_TYPES)


def _accepts_sheets(d: dict[str, Any]) -> bool:
    return _SHEET_TYPE in [a.strip().lower() for a in str(d.get("accepts") or "").split(",")]


def _reads_sheet_rows(d: dict[str, Any]) -> bool:
    """A slot that reads spreadsheets as claim rows (the loss review): it takes .csv as well as .xlsx. Compare Quotes
    and Proposal Builder take .xlsx only as a quote rendered to a PDF record (slice 6, R5)."""
    accepts = [a.strip().lower() for a in str(d.get("accepts") or "").split(",")]
    return _SHEET_TYPE in accepts and "text/csv" in accepts


# Slice 6 (R5): a Word or Excel quote (a rendered PDF record) fits only a slot that lists its type.
_OFFICE_TYPES = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "xlsx": _SHEET_TYPE}


def check_slot_kinds(input_slots: Sequence[Any] | None, kinds: Sequence[str], slot_keys: Sequence[str],
                     formats: Sequence[str | None] | None = None) -> None:
    """Each document fits its slot's `accepts` (slice 3): an AMS export only in a slot that takes
    JSON or CSV, a spreadsheet loss run (slice 5a) only in a slot that reads spreadsheet rows, a Word or
    Excel quote (slice 6, `formats` = Document.source_format) only in a slot that lists its type and does
    not read spreadsheet rows, any other document only in a slot that takes PDFs. Raises SlotError."""
    defs = {d["key"]: d for d in _slot_defs(input_slots)}
    formats = list(formats) if formats is not None else [None] * len(kinds)
    for kind, key, fmt in zip(kinds, slot_keys, formats, strict=True):
        d = defs.get(key)
        if d is None:
            continue
        pdf_ok, ams_ok = _accepts(d)
        if kind == SPREADSHEET_KIND:
            if not _reads_sheet_rows(d):
                raise SlotError(f"{_label(d)} takes PDF documents; a spreadsheet is read only by a loss review.")
            continue
        if kind != "ams" and fmt in _OFFICE_TYPES:
            accepts = [a.strip().lower() for a in str(d.get("accepts") or "").split(",")]
            if _reads_sheet_rows(d):
                raise SlotError(f"{_label(d)} reads spreadsheets as loss runs; upload this file again as a loss "
                                "run.")
            if _OFFICE_TYPES[fmt] not in accepts:
                raise SlotError(f"{_label(d)} takes PDF documents.")
            continue
        if kind == "ams" and not ams_ok:
            raise SlotError(f"{_label(d)} takes PDF documents; an AMS export goes in the AMS slot.")
        if kind != "ams" and not pdf_ok:
            raise SlotError(f"{_label(d)} takes an AMS export (JSON or CSV), not a PDF document.")


def unread_required_slots(run: models.Run) -> list[str]:
    """Labels of required slots (min >= 1) with fewer readable documents than their min (D11)."""
    short = []
    for d in _slot_defs(run.workflow.input_slots):
        low = int(d.get("min", 0) or 0)
        done = sum(1 for rd in run.documents
                   if rd.slot == d["key"] and RunDocumentStatus(rd.status) == RunDocumentStatus.DONE)
        if low >= 1 and done < low:
            short.append(_label(d))
    return short


def _doc_payload(rd: models.RunDocument) -> dict[str, Any]:
    return {
        "run_document_id": rd.id, "document_id": rd.document_id, "position": rd.position,
        "letter": rd.letter, "slot": rd.slot, "filename": rd.document.filename,
        "source_format": rd.document.source_format or "pdf",  # slice 6: pdf | docx | xlsx column label
        "status": str(rd.status),
        "progress": rd.progress, "error": rd.error, "error_code": rd.error_code,
        "option_count": rd.option_count, "option_label": rd.option_label, "document_kind": rd.document_kind,
        # slice 4 extraction cache: the run whose read was copied, and when the facts were first read
        "reused_from_run_id": rd.reused_from_run_id,
        "read_at": rd.read_at.isoformat() if rd.read_at is not None else None,
    }


def is_retryable(status: RunStatus | str, *, active: bool = False) -> bool:
    """True exactly when `RunManager.retry` would accept the run: FAILED, PARTIAL or
    CANCELLED, and no task is running for it."""
    return not active and RunStatus(status) in RETRYABLE_STATUSES


def _run_payload(run: models.Run, *, active: bool = False) -> dict[str, Any]:
    return {"id": run.id, "status": str(run.status), "error": run.error,
            "retryable": is_retryable(run.status, active=active),
            "tokens_in": run.tokens_in or 0, "tokens_out": run.tokens_out or 0,
            "cost_usd": run.cost_usd or 0.0}


def _fail_open_documents(run: models.Run, code: str, message: str) -> list[models.RunDocument]:
    changed = []
    for rd in run.documents:
        if RunDocumentStatus(rd.status) not in _TERMINAL_DOC:
            models.transition_run_document(rd, RunDocumentStatus.FAILED)
            rd.error_code, rd.error, rd.progress = code, message, None
            changed.append(rd)
    return changed


def reconcile_interrupted_runs(factory: sessionmaker[Session]) -> list[int]:
    """Startup (M6): runs left queued, running or composing by a dead process become
    FAILED and retryable; their unfinished documents fail as interrupted. Idempotent."""
    ids = []
    with db.session_scope(factory) as s:
        runs = s.scalars(select(models.Run).where(models.Run.status.in_([str(x) for x in ACTIVE_STATUSES])))
        for run in runs:
            _fail_open_documents(run, "interrupted", failure_message("interrupted"))
            models.transition_run(run, RunStatus.FAILED)
            run.error = "Interrupted by a restart. Retry to continue."
            run.retryable = True
            run.finished_at = models.utcnow()
            work_queue.on_run_terminal(s, run, run.finished_at)
            ids.append(run.id)
    if ids:
        log.warning("reconciled %d interrupted run(s): %s", len(ids), ids)
    return sorted(ids)


def build_compose_input(run: models.Run, *, title: str, fields: Sequence[FactField],
                        sections: Sequence[SectionPlanItem], instructions: str,
                        edits: Sequence[compose.ComposeEdit] = (), outline: Sequence[OutlineBlock] = (),
                        fact_sections: Sequence[SectionPlanItem] = (),
                        brief: compose.ProposalBrief | None = None, compare: bool = False,
                        additional_context: str = "") -> compose.ComposeInput:
    """Compose input from a run's documents and persisted facts (shared by the run and by
    Regenerate prose, which passes the user's cell edits, D9)."""
    facts_by_doc: dict[int, list[models.Fact]] = defaultdict(list)
    for f in run.facts:
        facts_by_doc[f.document_id].append(f)
    columns, facts = [], []
    budget_failed = other_failed = 0
    for rd in run.documents:
        failed = RunDocumentStatus(rd.status) == RunDocumentStatus.FAILED
        if failed:
            if rd.error_code == "budget_exhausted":
                budget_failed += 1
            else:
                other_failed += 1
        label = rd.document.filename
        doc_facts = [] if failed else facts_by_doc.get(rd.document_id, [])
        for f in doc_facts:
            if f.field in LABEL_FIELDS and EvidenceState(f.state) in CITED_STATES and f.value:
                label = f.value
                break
        columns.append(compose.ComposeColumn(
            key=rd.letter, label=label, document_id=rd.document_id, filename=rd.document.filename,
            slot=rd.slot, failed=failed, failure_message=rd.error, option_count=rd.option_count or 1,
            option_label=rd.option_label, document_kind=rd.document_kind if compare else None))
        facts.extend(compose.ComposeFact(column_key=rd.letter, field=f.field, value=f.value,
                                         state=EvidenceState(f.state), passage_id=f.passage_id,
                                         page_no=f.page_no, fact_id=f.id,
                                         extra_passage_ids=tuple(f.extra_passage_ids or ()),
                                         conflicts=tuple((c["value"], c.get("page_no"), c.get("passage_id"))
                                                         for c in f.conflicts or ())) for f in doc_facts)
    notices = []
    if budget_failed:
        notices.append(Notice(kind="budget_exhausted", message=(
            f"The run's token budget ran out, so {budget_failed} of {len(columns)} documents were not "
            "read. The report covers the rest.")))
    if other_failed:
        notices.append(Notice(kind="documents_failed", retryable=True, message=(
            f"{other_failed} of {len(columns)} documents could not be read; the Upload panel shows why.")))
    return compose.ComposeInput(title=title, fields=list(fields), sections=list(sections),
                                columns=columns, facts=facts, instructions=instructions,
                                notices=notices, edits=list(edits), outline=tuple(outline),
                                fact_sections=tuple(fact_sections), brief=brief, compare=compare,
                                additional_context=additional_context)


def _input_from_plan(run: models.Run, plan: ComposePlan,
                     edits: Sequence[compose.ComposeEdit] = ()) -> compose.ComposeInput:
    return build_compose_input(run, title=plan.title, fields=plan.fields, sections=plan.sections,
                               instructions=plan.instructions, edits=edits, outline=plan.outline,
                               fact_sections=plan.fact_sections, brief=plan.brief, compare=plan.compare,
                               additional_context=plan.additional_context)


def compose_input_for_run(run: models.Run, edits: Sequence[compose.ComposeEdit] = ()) -> compose.ComposeInput:
    """Compose input for retry and Regenerate prose: same plan as the run (S2)."""
    return _input_from_plan(run, compose_plan(run), edits)


# --------------------------------------------------------------------- manager

MergeEdits = Callable[[ArtifactContent, ArtifactContent], ArtifactContent]
"""(previous latest content, freshly composed content) -> content to snapshot."""


class RunManager:
    """Owns the run tasks, the thread pool and the event bus for one process."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider: Provider,
        *,
        load_bytes: Callable[[DocumentRef], bytes] = load_stored_bytes,
        load_original: Callable[[DocumentRef], bytes] = load_stored_original,
        limits: parse.ParseLimits = parse.DEFAULT_LIMITS,
        verify_config: verify.VerifyConfig = verify.DEFAULT_CONFIG,
        budget_limit: int | None = None,
        document_budget_limit: int | None = None,
        compose_headroom: int = DEFAULT_COMPOSE_HEADROOM,
        max_workers: int = 4,
        bus: EventBus | None = None,
        merge_edits: MergeEdits | None = None,
        extraction_cache: bool | None = None,
    ):
        """`merge_edits`, when given, is applied on a retry that recomposes an artifact
        that already exists, so the user's cell edits (D9) survive the new version.
        `extraction_cache` (slice 4): None follows BROKERDOCS_EXTRACTION_CACHE (on unless "off"),
        False always reads live (evals, D9)."""
        self._merge_edits = merge_edits
        self._extraction_cache = extraction_cache
        self._factory = session_factory
        self.provider = provider
        self._load_bytes = load_bytes
        self._load_original = load_original
        self._limits = limits
        self._verify_config = verify_config
        self._budget_limit = budget_limit or provider.config.run_token_budget
        self._document_budget_limit = document_budget_limit or provider.config.document_token_budget
        self._headroom = max(0, compose_headroom)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="brokerdocs-pipeline")
        self._tasks: dict[int, asyncio.Task] = {}
        self.bus = bus or EventBus()

    # ------------------------------------------------------------ public API

    @property
    def budget_limit(self) -> int:
        """Per-run token budget (D3); calls after the run (chat, prose) count against it too (M4)."""
        return self._budget_limit

    def is_active(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    def active_run_ids(self) -> list[int]:
        return sorted(r for r in self._tasks if self.is_active(r))

    def submit(self, workflow_id: int, document_ids: Sequence[int],
               parameters: dict[str, Any] | None = None, *,
               slots: Mapping[int, str] | None = None, line: str | None = None,
               client_name: str | None = None) -> tuple[int, bool]:
        """Create and start a run, or return the active identical one. Returns (run_id, created).

        `slots` maps each document id to one of the workflow's input slot keys (D8);
        without it every document goes to the workflow's only slot ("quotes"). The
        active run is identical when workflow, documents in order, their slots and
        parameters all match.

        `line` (slice 3b, "commercial" or "personal", validated by the caller) is a tag outside
        that identity: a new run gets it (default commercial), and a reused active run takes
        it when it is given (D4). `client_name` (work queue, cleaned by the caller) follows the
        same rule: outside the identity, set on a new run, applied to a reused one when given.

        Raises SlotError for a slot assignment that does not fit the workflow, ValueError
        for an invalid document list (messages are user-safe) and LookupError for an
        unknown workflow or document.
        """
        ids = [int(i) for i in document_ids]
        params = dict(parameters or {})
        if not ids:
            raise ValueError("Add at least one document to run a comparison.")
        if len(set(ids)) != len(ids):
            raise ValueError("Each document can appear only once in a run.")
        try:
            parse.check_document_count(len(ids), self._limits)
        except parse.ParseError as exc:
            raise ValueError(exc.message) from None
        with db.session_scope(self._factory) as s:
            workflow = s.get(models.Workflow, workflow_id)
            if workflow is None:
                raise LookupError(f"no workflow {workflow_id}")
            found = set(s.scalars(select(models.Document.id).where(models.Document.id.in_(ids))))
            missing = [i for i in ids if i not in found]
            if missing:
                raise LookupError(f"no document {missing[0]}")
            slot_keys = resolve_slots(workflow.input_slots, ids, slots)
            kinds = {i: (k, f) for i, k, f in s.execute(
                select(models.Document.id, models.Document.kind, models.Document.source_format)
                .where(models.Document.id.in_(ids))).all()}
            check_slot_kinds(workflow.input_slots, [kinds[i][0] or "quote" for i in ids], slot_keys,
                             [kinds[i][1] for i in ids])
            wanted = list(zip(ids, slot_keys))
            active = s.scalars(select(models.Run).where(
                models.Run.workflow_id == workflow_id,
                models.Run.status.in_([str(x) for x in ACTIVE_STATUSES]))).all()
            for run in active:
                if [(rd.document_id, rd.slot) for rd in run.documents] == wanted \
                        and (run.parameters or {}) == params:
                    existing = run.id
                    if line is not None:
                        run.line = line  # D4: reuse the run, apply the requested line
                    if client_name is not None:
                        run.client_name = client_name  # same rule as line (work queue)
                    break
            else:
                existing = None
            if existing is None:
                run = models.Run(workspace_id=workflow.workspace_id, workflow_id=workflow_id, parameters=params,
                                 line=line or "commercial", client_name=client_name, queue_state="open",
                                 status=RunStatus.QUEUED, retryable=False, tokens_in=0, tokens_out=0, cost_usd=0.0)
                for pos, (doc_id, slot) in enumerate(wanted):
                    run.documents.append(models.RunDocument(document_id=doc_id, position=pos, slot=slot,
                                                            status=RunDocumentStatus.QUEUED, option_count=1))
                s.add(run)
                s.flush()
                new_id = run.id
            work_queue.link_proposal(s, s.get(models.Run, existing if existing is not None else new_id))
        if existing is not None:
            if not self.is_active(existing):
                self.start(existing)
            return existing, False
        self.start(new_id)
        return new_id, True

    def start(self, run_id: int) -> bool:
        """Launch the run's task if it is QUEUED and not already running. Needs a running loop."""
        if self.is_active(run_id):
            return False
        with self._factory() as s:
            run = s.get(models.Run, run_id)
            if run is None:
                raise LookupError(f"no run {run_id}")
            if RunStatus(run.status) != RunStatus.QUEUED:
                return False
        loop = asyncio.get_running_loop()
        self.bus.begin_execution(run_id)
        task = loop.create_task(self._execute(run_id), name=f"brokerdocs-run-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(functools.partial(self._task_done, run_id))
        return True

    def retry(self, run_id: int) -> bool:
        """Re-run the documents that did not finish, then recompose (new artifact version)."""
        if self.is_active(run_id):
            return False
        with db.session_scope(self._factory) as s:
            run = s.get(models.Run, run_id)
            if run is None:
                raise LookupError(f"no run {run_id}")
            if RunStatus(run.status) not in RETRYABLE_STATUSES:
                return False
            models.transition_run(run, RunStatus.QUEUED)
            run.error, run.retryable, run.finished_at = None, False, None
            work_queue.on_run_retry(run)  # D8: a retried item is open work again
            redo = set()
            for rd in run.documents:
                if RunDocumentStatus(rd.status) == RunDocumentStatus.DONE:
                    continue
                if RunDocumentStatus(rd.status) == RunDocumentStatus.FAILED:
                    models.transition_run_document(rd, RunDocumentStatus.QUEUED)
                else:  # pragma: no cover - reconcile leaves no open document behind
                    rd.status = RunDocumentStatus.QUEUED
                rd.error = rd.error_code = rd.progress = None
                redo.add(rd.document_id)
            run.facts = [f for f in run.facts if f.document_id not in redo]
        return self.start(run_id)

    def add_documents(self, run_id: int, document_ids: Sequence[int]) -> bool:
        """Add document (slice 4, D2): append documents to a certificate draft and read only them.

        Certificate runs only, DONE or PARTIAL, current version not issued (the caller checks it):
        the run moves to QUEUED through models.reopen_certificate_run (never transition_run, so every
        other workflow's DONE stays terminal), existing facts are kept, and the rebuild makes a new
        artifact version whose claims keep their decisions (reconcile). Raises LookupError for an
        unknown run or document, ValueError (user-safe) for a bad document list, and
        models.InvalidTransition when the run cannot take a document. Returns whether it started."""
        ids = [int(i) for i in document_ids]
        if not ids:
            raise ValueError("Add at least one document.")
        if len(set(ids)) != len(ids):
            raise ValueError("Each document can appear only once in a run.")
        if self.is_active(run_id):
            raise models.InvalidTransition("the run is still going")
        with db.session_scope(self._factory) as s:
            run = s.get(models.Run, run_id)
            if run is None:
                raise LookupError(f"no run {run_id}")
            found = {d.id: d for d in s.scalars(select(models.Document).where(models.Document.id.in_(ids)))}
            missing = [i for i in ids if i not in found]
            if missing:
                raise LookupError(f"no document {missing[0]}")
            if any(found[i].kind == "ams" for i in ids):
                raise ValueError("A certificate reads PDF documents, not an AMS export.")
            slot = _slot_defs(run.workflow.input_slots)[0]["key"]
            check_slot_kinds(run.workflow.input_slots, [found[i].kind or "quote" for i in ids], [slot] * len(ids),
                             [found[i].source_format for i in ids])  # slice 6 (R5): no Word or Excel quotes
            have = {rd.document_id for rd in run.documents}
            if any(i in have for i in ids):
                raise ValueError("That document is already on this certificate.")
            try:
                parse.check_document_count(len(have) + len(ids), self._limits)
            except parse.ParseError as exc:
                raise ValueError(exc.message) from None
            models.reopen_certificate_run(run, db.CERTIFICATE_SLUG)
            run.error, run.retryable, run.finished_at = None, False, None
            work_queue.on_run_retry(run)
            start = max((rd.position for rd in run.documents), default=-1) + 1
            for offset, doc_id in enumerate(ids):
                run.documents.append(models.RunDocument(document_id=doc_id, position=start + offset,
                                                        slot=_slot_defs(run.workflow.input_slots)[0]["key"],
                                                        status=RunDocumentStatus.QUEUED, option_count=1))
        return self.start(run_id)

    async def wait(self, run_id: int) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.wait({task})

    async def cancel(self, run_id: int) -> bool:
        """Cancel a run (T13). Child tasks are cancelled and awaited; no task outlives this call.

        Returns True when the run was active (it ends CANCELLED), False otherwise.
        """
        task = self._tasks.get(run_id)
        was_task = task is not None and not task.done()
        if was_task:
            task.cancel()
            await asyncio.wait({task})
        # Covers a task cancelled before its first step, and a queued run with no task.
        return self._mark_cancelled(run_id) or was_task

    async def shutdown(self) -> None:
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks)
        for run_id in list(self._tasks):
            self._mark_cancelled(run_id)
        await asyncio.to_thread(self._executor.shutdown, True, cancel_futures=True)

    def policy_check_content(self, run: models.Run, inp: compose.ComposeInput | None = None) -> ArtifactContent:
        """The Policy Check report of a run from its stored facts (slice 3): the diff as
        DiscrepancyBlocks, deterministic and free (no model call), so the run, a retry and
        Regenerate all build it the same way. Needs an open session on `run`."""
        inp = inp or compose_input_for_run(run)
        extra: list[Notice] = []
        for rd in run.documents:
            if rd.document.kind != "ams" or RunDocumentStatus(rd.status) != RunDocumentStatus.DONE:
                continue
            ref = DocumentRef(rd.document_id, rd.document.filename, rd.document.stored_name, rd.document.kind)
            try:
                mapped = ams.facts_from_original(self._load_original(ref))
            except (OSError, ValueError, ams.AmsError):  # the facts are stored; only the notice is lost
                log.warning("run %s: AMS notices unavailable for document %s", run.id, rd.document_id)
                continue
            extra.extend(policy_check.ams_notices(mapped))
        return policy_check.build_content(inp, slot_states(run), extra_notices=extra)

    def snapshot(self, run_id: int) -> dict[str, Any]:
        """Current state from the database (first SSE frame after a reconnect or restart)."""
        with self._factory() as s:
            run = s.get(models.Run, run_id)
            if run is None:
                raise LookupError(f"no run {run_id}")
            art = run.artifact
            return {
                "run": _run_payload(run, active=self.is_active(run_id)),
                "documents": [_doc_payload(rd) for rd in run.documents],
                "artifact": {"id": art.id, "version": art.current_version} if art else None,
            }

    async def events(self, run_id: int, last_event_id: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Replay history after `last_event_id`, then live events until the run.end
        of the current execution.

        A retried run's history holds the run.end of every earlier execution;
        those are superseded and skipped (never yielded), so a client that
        stops at run.end follows the retry to its own end. The other events
        of earlier executions are replayed as they happened.

        A run that is not active and has nothing to replay yields one
        run.snapshot event (id None) built from the database, then ends; so
        does an idle run whose replay has no current run.end to close on.
        """

        def superseded_end(e: dict[str, Any]) -> bool:
            return e["type"] == "run.end" and e.get("execution", 0) < self.bus.execution(run_id)

        q = self.bus.subscribe(run_id)
        try:
            seen = last_event_id or 0
            for e in self.bus.history(run_id, after=last_event_id):
                seen = e["id"]
                if superseded_end(e):
                    continue
                yield e
                if e["type"] == "run.end":
                    return
            while True:
                try:
                    e = q.get_nowait()
                except asyncio.QueueEmpty:
                    if not self.is_active(run_id):
                        yield {"id": None, "run_id": run_id, "execution": self.bus.execution(run_id),
                               "type": "run.snapshot", "data": self.snapshot(run_id)}
                        return
                    e = await q.get()
                if e["id"] <= seen:
                    continue
                seen = e["id"]
                if superseded_end(e):
                    continue
                yield e
                if e["type"] == "run.end":
                    return
        finally:
            self.bus.unsubscribe(run_id, q)

    # ------------------------------------------------------------ internals: plumbing

    def _task_done(self, run_id: int, task: asyncio.Task) -> None:
        if self._tasks.get(run_id) is task:
            del self._tasks[run_id]
        if not task.cancelled() and task.exception() is not None:  # pragma: no cover - _execute catches
            log.error("run %s task crashed", run_id, exc_info=task.exception())

    async def _in_thread(self, fn: Callable, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, functools.partial(fn, *args, **kwargs))

    def _emit(self, run_id: int, type_: str, data: dict[str, Any]) -> None:
        self.bus.publish(run_id, type_, data)

    def _emit_docs(self, run_id: int, payloads: Iterable[dict[str, Any]]) -> None:
        for p in payloads:
            self._emit(run_id, "document.status", p)

    def _end(self, run_id: int, run_payload: dict[str, Any]) -> None:
        self._emit(run_id, "run.status", {k: run_payload[k] for k in ("status", "error", "retryable")})
        self._emit(run_id, "run.end", {"status": run_payload["status"]})

    def _recorder(self, run_id: int) -> Callable[[CallRecord], None]:
        def hook(rec: CallRecord) -> None:
            with db.session_scope(self._factory) as s:
                run = s.get(models.Run, run_id)
                models.record_model_call(
                    s, run, purpose=rec.role.value, model=rec.model, tokens_in=rec.prompt_tokens,
                    tokens_out=rec.completion_tokens, cost_usd=rec.cost_usd,
                    document_id=_current_document.get(), ok=rec.outcome == "ok")
                usage = {"tokens_in": run.tokens_in, "tokens_out": run.tokens_out, "cost_usd": run.cost_usd}
            self._emit(run_id, "run.usage", usage)
        return hook

    def _update_doc(self, run_id: int, rd_id: int, status: RunDocumentStatus | None = None,
                    **fields: Any) -> None:
        with db.session_scope(self._factory) as s:
            rd = s.get(models.RunDocument, rd_id)
            if status is not None and RunDocumentStatus(rd.status) != status:
                models.transition_run_document(rd, status)
            for k, v in fields.items():
                setattr(rd, k, v)
            s.flush()
            payload = _doc_payload(rd)
        self._emit(run_id, "document.status", payload)

    def _mark_cancelled(self, run_id: int) -> bool:
        with db.session_scope(self._factory) as s:
            run = s.get(models.Run, run_id)
            if run is None or RunStatus(run.status) not in ACTIVE_STATUSES:
                return False
            changed = _fail_open_documents(run, "cancelled", failure_message("cancelled"))
            models.transition_run(run, RunStatus.CANCELLED)
            run.retryable, run.finished_at = True, models.utcnow()
            work_queue.on_run_terminal(s, run, run.finished_at)
            s.flush()
            docs = [_doc_payload(rd) for rd in changed]
            payload = _run_payload(run)
        self._emit_docs(run_id, docs)
        self._end(run_id, payload)
        return True

    def _mark_failed(self, run_id: int, message: str) -> None:
        with db.session_scope(self._factory) as s:
            run = s.get(models.Run, run_id)
            if run is None or RunStatus(run.status) not in ACTIVE_STATUSES:
                return
            changed = _fail_open_documents(run, "internal_error", failure_message("internal_error"))
            models.transition_run(run, RunStatus.FAILED)
            run.error, run.retryable, run.finished_at = message, True, models.utcnow()
            work_queue.on_run_terminal(s, run, run.finished_at)
            s.flush()
            docs = [_doc_payload(rd) for rd in changed]
            payload = _run_payload(run)
        self._emit_docs(run_id, docs)
        self._end(run_id, payload)

    # ------------------------------------------------------------ internals: the run

    def _begin(self, run_id: int) -> _Plan:
        with db.session_scope(self._factory) as s:
            run = s.get(models.Run, run_id)
            models.transition_run(run, RunStatus.RUNNING)
            run.started_at = run.started_at or models.utcnow()
            todo = [(rd.id, DocumentRef(rd.document_id, rd.document.filename, rd.document.stored_name,
                                        rd.document.kind or "quote"))
                    for rd in run.documents if RunDocumentStatus(rd.status) == RunDocumentStatus.QUEUED]
            spent = (run.tokens_in or 0) + (run.tokens_out or 0)
            plan_compose = compose_plan(run)
            loss = is_loss_run(run)
            limit = max(1, self._budget_limit - spent)
            if loss:
                # O5/D11: every document this execution reads gets a fresh loss-run allowance, so a Retry after a
                # budget stop can finish the pages it did not read.
                cfg = self.provider.config
                limit = max(cfg.loss_run_token_budget - spent, cfg.loss_document_token_budget * max(1, len(todo)))
            plan = _Plan(
                run_id=run_id,
                compose=plan_compose,
                todo=todo,
                budget_limit=limit,
                policy_check=is_policy_check(run),
                certificate=is_certificate(run),
                loss_run=loss,
                cache_key=cache.extraction_key(plan_compose.fields, plan_compose.instructions, self.provider.config,
                                               extra=loss_cache_parts() if loss else None),
            )
            payload = _run_payload(run)
        self._emit(run_id, "run.status", {k: payload[k] for k in ("status", "error", "retryable")})
        return plan

    async def _execute(self, run_id: int) -> None:
        budget: RunBudget | None = None
        hold = None
        try:
            plan = self._begin(run_id)
            budget = RunBudget(plan.budget_limit, on_record=self._recorder(run_id))
            headroom = min(self._headroom, budget.remaining)
            if headroom > 0:
                hold = budget.reserve(headroom, label="compose-headroom")
            async with asyncio.TaskGroup() as tg:
                for rd_id, ref in plan.todo:
                    tg.create_task(self._process_document(run_id, rd_id, ref, plan, budget),
                                   name=f"brokerdocs-run-{run_id}-doc-{ref.id}")
            if hold is not None:
                budget.release(hold)
                hold = None
            await self._compose(run_id, plan, budget)
        except asyncio.CancelledError:
            self._mark_cancelled(run_id)
            raise
        except Exception:
            log.exception("run %s failed", run_id)
            self._mark_failed(run_id, "Something went wrong while running this comparison. Retry to try again.")
        finally:
            if hold is not None and hold.open and budget is not None:
                budget.release(hold)

    async def _process_document(self, run_id: int, rd_id: int, ref: DocumentRef, plan: _Plan,
                                budget: RunBudget) -> None:
        token = _current_document.set(ref.id)
        ceiling = self.provider.config.loss_document_token_budget if plan.loss_run else self._document_budget_limit
        budget = budget.child(ceiling, label=f"document:{ref.id}")  # T14 ceiling (slice 5a: the loss-run cap)
        try:
            if ref.kind != "ams" and self._reuse_read(run_id, rd_id, ref, plan):
                return  # slice 4: an earlier read of the same file was copied (pipeline.cache)
            if plan.loss_run:
                await self._read_loss_document(run_id, rd_id, ref, plan, budget)
                return
            self._update_doc(run_id, rd_id, RunDocumentStatus.READING, progress="Reading pages")
            pages = await self._read_pages(run_id, rd_id, ref, budget)
            if plan.policy_check and ref.kind == "ams":
                # D5: an AMS export's facts come from the key table, no model call; they verify
                # against its rendered PDF like any document fact.
                self._update_doc(run_id, rd_id, RunDocumentStatus.EXTRACTING, progress="Mapping AMS fields")
                original = await self._in_thread(self._load_original, ref)
                mapped = await self._in_thread(ams.facts_from_original, original)
                self._update_doc(run_id, rd_id, RunDocumentStatus.VERIFYING, progress="Verifying")
                verified = await self._in_thread(policy_check.verify_ams_facts, mapped, pages, self._verify_config)
                self._store_facts(run_id, ref.id, verified)
                self._update_doc(run_id, rd_id, RunDocumentStatus.DONE, progress=None, option_count=1,
                                 option_label=None)
                return
            self._update_doc(run_id, rd_id, RunDocumentStatus.EXTRACTING, progress="Extracting")
            result = await windows.extract_document(
                self.provider, budget, plan.fields, pages, document_name=ref.filename,
                instructions=plan.instructions, label=f"extract:{ref.id}",
                on_progress=lambda k, n: self._update_doc(run_id, rd_id, progress=f"Extracting window {k}/{n}"))
            self._update_doc(run_id, rd_id, RunDocumentStatus.VERIFYING, progress="Verifying")
            verified = await self._in_thread(windows.verify_document, result, pages, self._verify_config)
            self._store_facts(run_id, ref.id, verified)
            self._update_doc(run_id, rd_id, RunDocumentStatus.DONE, progress=None,
                             option_count=result.option_count, option_label=result.option_label,
                             document_kind=getattr(result, "document_kind", None),
                             # Off means no reuse either way: a read made with the cache off stores no
                             # key, so it never serves a later run (keeps dev-seed reads out of e2e).
                             extraction_key=plan.cache_key if self.cache_enabled() else None,
                             reused_from_run_id=None, read_at=models.utcnow())
        except asyncio.CancelledError:
            raise
        except (ams.AmsError, sheets.SheetError) as exc:
            self._update_doc(run_id, rd_id, RunDocumentStatus.FAILED, progress=None,
                             error_code=str(exc.code), error=exc.message)
        except parse.ParseError as exc:
            self._record_parse_failure(ref.id, exc)
            self._update_doc(run_id, rd_id, RunDocumentStatus.FAILED, progress=None,
                             error_code=str(exc.code), error=exc.message)
        except ProviderError as exc:
            log.warning("run %s document %s failed: %s", run_id, ref.id, exc.code)
            self._update_doc(run_id, rd_id, RunDocumentStatus.FAILED, progress=None,
                             error_code=exc.code, error=failure_message(exc.code))
        except Exception:
            log.exception("run %s document %s crashed", run_id, ref.id)
            self._update_doc(run_id, rd_id, RunDocumentStatus.FAILED, progress=None,
                             error_code="internal_error", error=failure_message("internal_error"))
        finally:
            _current_document.reset(token)

    # ------------------------------------------------------------ slice 5a: loss runs

    def _done_read(self, run_id: int, rd_id: int, plan: _Plan) -> None:
        self._update_doc(run_id, rd_id, RunDocumentStatus.DONE, progress=None, option_count=1, option_label=None,
                         extraction_key=plan.cache_key if self.cache_enabled() else None,
                         reused_from_run_id=None, read_at=models.utcnow())

    def _load_sheet_book(self, ref: DocumentRef) -> sheets.Workbook:
        """The parsed rows kept at upload (<uuid>.rows.jsonl), else the original parsed again (eval, old upload)."""
        path = files_dir() / f"{ref.stored_name}{sheets.CACHE_SUFFIX}"
        if _SAFE_NAME.match(ref.stored_name) and path.is_file():
            return sheets.read_book(path)
        return sheets.parse_workbook(self._load_original(ref), ref.filename)

    async def _read_loss_document(self, run_id: int, rd_id: int, ref: DocumentRef, plan: _Plan, budget) -> None:
        if ref.kind == SPREADSHEET_KIND:
            await self._read_sheet(run_id, rd_id, ref, plan, budget)
        else:
            await self._read_loss_pdf(run_id, rd_id, ref, plan, budget)

    async def _read_sheet(self, run_id: int, rd_id: int, ref: DocumentRef, plan: _Plan, budget) -> None:
        """A spreadsheet loss run: rows and totals in code; one model call per sheet with unknown headers."""
        self._update_doc(run_id, rd_id, RunDocumentStatus.READING, progress="Reading sheets")
        book = await self._in_thread(self._load_sheet_book, ref)
        self._update_doc(run_id, rd_id, RunDocumentStatus.EXTRACTING, progress="Mapping columns")
        mapped, unknown = sheets.known_mappings(book)
        model_mapped: dict[int, set[int]] = {}
        for s in book.sheets:
            if unknown.get(s.index):
                got = await sheets.map_unknown_headers(self.provider, budget, s, mapped[s.index], unknown[s.index],
                                                       label=f"headers:{ref.id}:s{s.index}",
                                                       document_name=ref.filename)
                mapped[s.index].update(got)
                model_mapped[s.index] = set(got)
        self._update_doc(run_id, rd_id, RunDocumentStatus.VERIFYING, progress="Checking rows")
        ext = await self._in_thread(sheets.extract, book, mapped, model_mapped=model_mapped)
        meta = await self._in_thread(sheets.layout, book)
        with db.session_scope(self._factory) as s:
            for old in s.scalars(select(models.Fact).where(models.Fact.run_id == run_id,
                                                           models.Fact.document_id == ref.id)):
                s.delete(old)
            loss_report.replace_extracted(s, run_id, ref.id)
            s.flush()
            for f in ext.facts:
                page = sheets.page_of(meta, f.sheet, f.row)
                s.add(models.Fact(run_id=run_id, document_id=ref.id, field=f.field, value=f.value, cited_page=page,
                                  page_no=page, quote=f.quote, matched_text=f.value, method="sheet", score=1.0,
                                  rects=[], state=EvidenceState.VERIFIED))
            s.add(models.Fact(run_id=run_id, document_id=ref.id, field=loss_report.READ_NOTES_FIELD,
                              value=json.dumps({"notices": ext.notices, "mappings": ext.mappings}), rects=[],
                              state=EvidenceState.ABSENT))
            carrier = next((f.value for f in ext.facts if f.field == "lr:carrier"), None)
            for r in ext.rows:
                r["page_no"] = sheets.page_of(meta, int(r["location"][1:r["location"].index(":")]), r["sheet_row"])
            for t in ext.totals:
                t["page_no"] = sheets.page_of(meta, int(t["location"][1:t["location"].index(":")]), t["sheet_row"])
            loss_report.insert_rows(s, run_id, ref.id, ext.rows, carrier)
            loss_report.insert_totals(s, run_id, ref.id, ext.totals)
        self._done_read(run_id, rd_id, plan)

    async def _read_loss_pdf(self, run_id: int, rd_id: int, ref: DocumentRef, plan: _Plan, budget) -> None:
        """A PDF loss run: the lr: fact pass, then claim rows per 2-page window (R2), verified in code (R6, O1).
        Pages already read by an earlier execution are skipped (O5); a budget stop or a one-page cut-off fails the
        document with the pages it did not read, keeping the rows it did."""
        self._update_doc(run_id, rd_id, RunDocumentStatus.READING, progress="Reading pages")
        pages = await self._read_pages(run_id, rd_id, ref, budget)
        self._update_doc(run_id, rd_id, RunDocumentStatus.EXTRACTING, progress="Reading the header")
        result = await windows.extract_document(self.provider, budget, plan.fields, pages, document_name=ref.filename,
                                                instructions=plan.instructions, label=f"extract:{ref.id}")
        verified = await self._in_thread(windows.verify_document, result, pages, self._verify_config)
        self._store_facts(run_id, ref.id, verified)
        with db.session_scope(self._factory) as s:
            already = loss_report.read_page_set(s, run_id, ref.id)
            if not already:  # a fresh read replaces the document's rows (D4); a resumed one keeps them
                loss_report.replace_extracted(s, run_id, ref.id)
                s.execute(models.LossWindow.__table__.delete().where(models.LossWindow.run_id == run_id,
                                                                     models.LossWindow.document_id == ref.id))
            carrier = loss_report.document_facts(s, run_id).get(ref.id, {}).get("lr:carrier")
        unread = {p.page_no for p in pages} - already
        read = await pdf_rows.read_rows(
            self.provider, budget, pages, document_name=ref.filename, label=f"rows:{ref.id}", unread=unread,
            on_progress=lambda k, n: self._update_doc(run_id, rd_id, progress=f"Reading claims {k}/{n}"))
        self._update_doc(run_id, rd_id, RunDocumentStatus.VERIFYING, progress="Checking rows")
        rows, totals = await self._in_thread(pdf_rows.verify_read, read, pages)
        with db.session_scope(self._factory) as s:
            for o in read.outcomes:
                s.add(models.LossWindow(run_id=run_id, document_id=ref.id, first_page=o.window.first,
                                        last_page=o.window.last, status=o.status, error_code=o.error_code))
            have_rows, have_totals = loss_report.stored_locations(s, run_id, ref.id)
            loss_report.insert_rows(s, run_id, ref.id, rows, carrier, skip_locations=have_rows)
            loss_report.insert_totals(s, run_id, ref.id, totals, skip_locations=have_totals)
            for page_no, ok in sorted({(r["page_no"], r["verify_state"] == "verified") for r in rows}):
                models.upsert_passage(s, ref.id, page_no, verified=ok, rects=[])  # D13: the PDF viewer path
            s.flush()
            missing = loss_report.unread_pages(s, run_id, ref.id, len(pages))
        if not missing:
            self._done_read(run_id, rd_id, plan)
            return
        code = read.stopped_code() or "truncated"
        ranges = _page_ranges(missing)
        if code == "truncated":
            message = f"Page{'s' if len(missing) != 1 else ''} {ranges} not fully read. Retry reads {'them' if len(missing) != 1 else 'it'} again."
        elif code in ("budget_exhausted", "document_budget_exhausted"):
            last = min(missing) - 1
            message = (f"Stopped at page {last}: budget reached. Retry reads pages {ranges}." if last >= 1
                       else f"Budget reached before the claims were read. Retry reads pages {ranges}.")
        else:
            message = f"{failure_message(code)} Pages {ranges} were not read; Retry reads them."
        self._update_doc(run_id, rd_id, RunDocumentStatus.FAILED, progress=None, error_code=code, error=message)

    def cache_enabled(self) -> bool:
        return cache.enabled_from_env() if self._extraction_cache is None else bool(self._extraction_cache)

    def _reuse_read(self, run_id: int, rd_id: int, ref: DocumentRef, plan: _Plan) -> bool:
        """Copy an earlier read of the same file under the same key (pipeline.cache). True on a hit."""
        if plan.cache_key is None or not self.cache_enabled():
            return False
        current_ocr = cache.ocr_key(self.provider.config)
        try:
            with db.session_scope(self._factory) as s:
                source = cache.find_source(s, run_id=run_id, document_id=ref.id, key=plan.cache_key)
                if source is None:
                    return False
                rd = s.get(models.RunDocument, rd_id)
                try:
                    cache.copy_read(s, source, rd, current_ocr=current_ocr)
                except cache.OcrMismatch:  # D17: text layer copied (committed), OCR and extraction run live
                    log.info("run %s document %s: cached read used another OCR method; reading live",
                             run_id, ref.id)
                    return False
                payload = _doc_payload(rd)
        except Exception:  # a cache problem never fails the document: read it live instead
            log.exception("run %s document %s: extraction cache copy failed; reading live", run_id, ref.id)
            return False
        log.info("run %s document %s: reused the read of run %s", run_id, ref.id, source.run_id)
        self._emit(run_id, "document.status", payload)
        return True

    def _record_parse_failure(self, doc_id: int, exc: parse.ParseError) -> None:
        with db.session_scope(self._factory) as s:
            doc = s.get(models.Document, doc_id)
            doc.parse_error, doc.parse_message = str(exc.code), exc.message

    def _cached_pages(self, doc_id: int) -> list[_PageData] | None:
        with self._factory() as s:
            doc = s.get(models.Document, doc_id)
            if doc.parse_error or not doc.page_count or len(doc.pages) != doc.page_count:
                return None
            return [_PageData(p.page_no, p.text or "", p.words, p.ocr_text, bool(p.needs_ocr))
                    for p in doc.pages]

    def _store_pages(self, doc_id: int, parsed: parse.ParsedDocument) -> list[_PageData]:
        with db.session_scope(self._factory) as s:
            doc = s.get(models.Document, doc_id)
            doc.pages = []
            s.flush()
            doc.pages = [
                models.Page(page_no=p.page_no, text=p.text,
                            words=[list(w) for w in p.words] if p.words is not None else None,
                            width=p.width, height=p.height, char_count=p.char_count, image_ratio=p.image_ratio,
                            needs_ocr=p.needs_ocr, ocr_reason=p.ocr_reason, ocr=False, ocr_text=None)
                for p in parsed.pages
            ]
            doc.page_count, doc.parse_error, doc.parse_message = parsed.page_count, None, None
        return [_PageData(p.page_no, p.text, [list(w) for w in p.words] if p.words is not None else None,
                          None, p.needs_ocr) for p in parsed.pages]

    def _store_ocr(self, doc_id: int, page_no: int, text: str) -> None:
        with db.session_scope(self._factory) as s:
            page = s.scalar(select(models.Page).where(models.Page.document_id == doc_id,
                                                      models.Page.page_no == page_no))
            page.ocr_text, page.ocr = text, True
            page.ocr_key = cache.ocr_key(self.provider.config)  # D17: which OCR method read it

    async def _read_pages(self, run_id: int, rd_id: int, ref: DocumentRef, budget: RunBudget) -> list[_PageData]:
        data: bytes | None = None
        pages = self._cached_pages(ref.id)
        if pages is None:
            data = await self._in_thread(self._load_bytes, ref)
            parsed = await self._in_thread(parse.parse_pdf, data, self._limits)
            pages = self._store_pages(ref.id, parsed)
        pending = [p for p in pages if p.needs_ocr and not p.ocr]
        total = len(pages)
        done = total - len(pending)
        self._update_doc(run_id, rd_id, progress=f"Reading pages {done}/{total}")
        for page in pending:
            if data is None:
                data = await self._in_thread(self._load_bytes, ref)
            png = await self._in_thread(parse.render_page_png, data, page.page_no, self._limits.ocr_dpi)
            page.ocr_text = await extract.ocr_page(self.provider, budget, png, page_no=page.page_no,
                                                   label=f"vision:{ref.id}:p{page.page_no}")
            self._store_ocr(ref.id, page.page_no, page.ocr_text)
            done += 1
            self._update_doc(run_id, rd_id, progress=f"Reading pages {done}/{total}")
        if all(not extract.page_text(p).strip() for p in pages):
            raise parse.ParseError(parse.ParseErrorCode.EMPTY, "No readable text was found in this PDF.")
        return pages

    def _store_facts(self, run_id: int, doc_id: int, results: Sequence[verify.VerifyResult]) -> None:
        with db.session_scope(self._factory) as s:
            for old in s.scalars(select(models.Fact).where(models.Fact.run_id == run_id,
                                                           models.Fact.document_id == doc_id)):
                s.delete(old)
            s.flush()
            for r in results:
                fact = models.Fact(
                    run_id=run_id, document_id=doc_id, field=r.field, value=r.value, cited_page=r.cited_page,
                    page_no=r.page_no, quote=r.quote, matched_text=r.matched_text, method=r.method,
                    score=r.score, rects=list(r.rects), state=EvidenceState.PENDING, reason=r.reason,
                    repointed_from=r.repointed_from)
                models.transition_fact(fact, r.state)
                if r.cited and r.page_no is not None:
                    passage = models.upsert_passage(s, doc_id, r.page_no,
                                                    verified=r.state == EvidenceState.VERIFIED, rects=r.rects)
                    fact.passage_id = passage.id
                    models.attach_merged_evidence(s, fact, doc_id, r)  # T14: no-op for single-call facts
                elif getattr(r, "conflicts", ()):  # T14 CONFLICTING: no value, a passage per stated value
                    models.attach_merged_evidence(s, fact, doc_id, r)
                s.add(fact)

    def _compose_input(self, run: models.Run, plan: _Plan, budget: RunBudget) -> compose.ComposeInput:
        return _input_from_plan(run, plan.compose)

    async def _compose(self, run_id: int, plan: _Plan, budget: RunBudget) -> None:
        built: ArtifactContent | None = None
        with db.session_scope(self._factory) as s:
            run = s.get(models.Run, run_id)
            usable = [rd for rd in run.documents if RunDocumentStatus(rd.status) == RunDocumentStatus.DONE]
            if plan.loss_run:  # O5: a document stopped part-way keeps the rows it read; the review shows them
                usable = [rd for rd in run.documents if loss_report.usable(s, rd)]
            # D11: a required slot without a readable document fails the run like "none
            # readable" does. A one-slot workflow (Compare Quotes, Proposal) can only reach
            # this through the first branch, so its behavior and message are unchanged.
            unread = unread_required_slots(run) if usable and not plan.loss_run else []
            if not usable or unread:
                models.transition_run(run, RunStatus.FAILED)
                reasons = sorted({rd.error for rd in run.documents if rd.error})
                if usable:
                    names = " and ".join(name.lower() for name in unread)
                    run.error = (f"The {names} could not be read, so this check cannot run. "
                                 "Retry to try again. " + " ".join(reasons)).strip()
                else:
                    run.error = "No document could be read. " + " ".join(reasons)
                run.retryable = True
                run.finished_at = models.utcnow()
                work_queue.on_run_terminal(s, run, run.finished_at)
                s.flush()
                payload = _run_payload(run)
                inp = None
            else:
                models.transition_run(run, RunStatus.COMPOSING)
                s.flush()
                inp = self._compose_input(run, plan, budget)
                payload = _run_payload(run)
                # Slice 3: the Policy Check report is the diff, built in code (D14, no model call).
                built = self.policy_check_content(run, inp) if plan.policy_check else None
                if plan.certificate:  # slice 4: the draft and its checks, built in code (C5)
                    built = certificate.content_for_run(s, run)
                if plan.loss_run:  # slice 5a: totals, checks, notices from the ledger (no compose call, D2)
                    built = loss_report.content_for_run(s, run)
        if inp is None:
            self._end(run_id, payload)
            return
        self._emit(run_id, "run.status", {k: payload[k] for k in ("status", "error", "retryable")})

        if built is not None:
            result = compose.ComposeResult(content=built, fallback=False, attempts=0)
        else:
            result = await compose.compose_report(self.provider, budget, inp, label=f"compose:{run_id}")

        with db.session_scope(self._factory) as s:
            run = s.get(models.Run, run_id)
            note = "template fallback" if result.fallback else "generated"
            if run.artifact is None:
                art = models.create_artifact(s, run, title=plan.title, content=result.content, note=note)
            else:
                art = run.artifact
                content = result.content
                if self._merge_edits is not None and not plan.certificate and not plan.loss_run:
                    content = self._merge_edits(art.latest_content(), content)  # certificates/loss runs: own edits
                models.snapshot_version(s, art, content,
                                        note="rebuilt" if plan.certificate or plan.loss_run else f"retry, {note}")
            any_failed = any(RunDocumentStatus(rd.status) == RunDocumentStatus.FAILED for rd in run.documents)
            final = RunStatus.PARTIAL if any_failed or budget.exhausted else RunStatus.DONE
            models.transition_run(run, final)
            run.retryable = final == RunStatus.PARTIAL
            run.finished_at = models.utcnow()
            work_queue.on_run_terminal(s, run, run.finished_at)
            s.flush()
            artifact = {"artifact_id": art.id, "version": art.current_version, "fallback": result.fallback}
            payload = _run_payload(run)
        self._emit(run_id, "artifact.ready", artifact)
        self._end(run_id, payload)
