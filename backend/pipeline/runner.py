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
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend import db, models
from backend.pipeline import compose, extract, parse, verify, windows
from backend.providers import CallRecord, Provider, ProviderError, RunBudget
from backend.schemas import (
    CITED_STATES,
    ArtifactContent,
    EvidenceState,
    FactField,
    Notice,
    RunDocumentStatus,
    RunStatus,
    SectionPlanItem,
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


# --------------------------------------------------------------------- events


def format_sse(event: dict[str, Any]) -> str:
    """One event as a text/event-stream frame. The data line is the whole event as JSON."""
    head = f"id: {event['id']}\n" if event.get("id") is not None else ""
    return f"{head}event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'), default=str)}\n\n"


class EventBus:
    """In-memory event fan-out per run with bounded replay history.

    Event shape: {"id": int, "run_id": int, "type": str, "data": dict}; ids are
    increasing across the process, so an SSE Last-Event-ID resumes a stream.
    """

    def __init__(self, max_history: int = 1000):
        self._ids = itertools.count(1)
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=max_history))
        self._subscribers: dict[int, set[asyncio.Queue]] = defaultdict(set)

    def publish(self, run_id: int, type_: str, data: dict[str, Any]) -> dict[str, Any]:
        event = {"id": next(self._ids), "run_id": run_id, "type": type_, "data": data}
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
class _Plan:
    run_id: int
    fields: list[FactField]
    sections: list[SectionPlanItem]
    instructions: str
    title: str
    todo: list[tuple[int, DocumentRef]]  # (run_document id, document)
    budget_limit: int


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


def _doc_payload(rd: models.RunDocument) -> dict[str, Any]:
    return {
        "run_document_id": rd.id, "document_id": rd.document_id, "position": rd.position,
        "letter": rd.letter, "filename": rd.document.filename, "status": str(rd.status),
        "progress": rd.progress, "error": rd.error, "error_code": rd.error_code,
        "option_count": rd.option_count, "option_label": rd.option_label,
    }


def _run_payload(run: models.Run) -> dict[str, Any]:
    return {"id": run.id, "status": str(run.status), "error": run.error, "retryable": bool(run.retryable),
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
            ids.append(run.id)
    if ids:
        log.warning("reconciled %d interrupted run(s): %s", len(ids), ids)
    return sorted(ids)


def build_compose_input(run: models.Run, *, title: str, fields: Sequence[FactField],
                        sections: Sequence[SectionPlanItem], instructions: str,
                        edits: Sequence[compose.ComposeEdit] = ()) -> compose.ComposeInput:
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
            failed=failed, failure_message=rd.error, option_count=rd.option_count or 1,
            option_label=rd.option_label))
        facts.extend(compose.ComposeFact(column_key=rd.letter, field=f.field, value=f.value,
                                         state=EvidenceState(f.state), passage_id=f.passage_id,
                                         page_no=f.page_no, fact_id=f.id,
                                         extra_passage_ids=tuple(f.extra_passage_ids or ()),
                                         conflicts=tuple((c["value"], c.get("page_no"))
                                                         for c in f.conflicts or ())) for f in doc_facts)
    notices = []
    if budget_failed:
        notices.append(Notice(kind="budget_exhausted", message=(
            f"The run's token budget ran out, so {budget_failed} of {len(columns)} documents were not "
            "read. The report covers the rest.")))
    if other_failed:
        notices.append(Notice(kind="documents_failed", retryable=True, message=(
            f"{other_failed} of {len(columns)} documents could not be read; their columns show why.")))
    return compose.ComposeInput(title=title, fields=list(fields), sections=list(sections),
                                columns=columns, facts=facts, instructions=instructions,
                                notices=notices, edits=list(edits))


def compose_input_for_run(run: models.Run, edits: Sequence[compose.ComposeEdit] = ()) -> compose.ComposeInput:
    wf = run.workflow
    return build_compose_input(
        run, title=wf.name, fields=[FactField.model_validate(f) for f in wf.fact_schema],
        sections=[SectionPlanItem.model_validate(x) for x in wf.section_plan],
        instructions=wf.instructions or "", edits=edits)


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
        limits: parse.ParseLimits = parse.DEFAULT_LIMITS,
        verify_config: verify.VerifyConfig = verify.DEFAULT_CONFIG,
        budget_limit: int | None = None,
        document_budget_limit: int | None = None,
        compose_headroom: int = DEFAULT_COMPOSE_HEADROOM,
        max_workers: int = 4,
        bus: EventBus | None = None,
        merge_edits: MergeEdits | None = None,
    ):
        """`merge_edits`, when given, is applied on a retry that recomposes an artifact
        that already exists, so the user's cell edits (D9) survive the new version."""
        self._merge_edits = merge_edits
        self._factory = session_factory
        self.provider = provider
        self._load_bytes = load_bytes
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
               parameters: dict[str, Any] | None = None) -> tuple[int, bool]:
        """Create and start a run, or return the active identical one. Returns (run_id, created).

        Raises ValueError for an invalid document list (message is user-safe)
        and LookupError for an unknown workflow or document.
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
            active = s.scalars(select(models.Run).where(
                models.Run.workflow_id == workflow_id,
                models.Run.status.in_([str(x) for x in ACTIVE_STATUSES]))).all()
            for run in active:
                if [rd.document_id for rd in run.documents] == ids and (run.parameters or {}) == params:
                    existing = run.id
                    break
            else:
                existing = None
            if existing is None:
                run = models.Run(workspace_id=workflow.workspace_id, workflow_id=workflow_id, parameters=params,
                                 status=RunStatus.QUEUED, retryable=False, tokens_in=0, tokens_out=0, cost_usd=0.0)
                for pos, doc_id in enumerate(ids):
                    run.documents.append(models.RunDocument(document_id=doc_id, position=pos,
                                                            status=RunDocumentStatus.QUEUED, option_count=1))
                s.add(run)
                s.flush()
                new_id = run.id
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
        task = asyncio.get_running_loop().create_task(self._execute(run_id), name=f"brokerdocs-run-{run_id}")
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

    def snapshot(self, run_id: int) -> dict[str, Any]:
        """Current state from the database (first SSE frame after a reconnect or restart)."""
        with self._factory() as s:
            run = s.get(models.Run, run_id)
            if run is None:
                raise LookupError(f"no run {run_id}")
            art = run.artifact
            return {
                "run": _run_payload(run),
                "documents": [_doc_payload(rd) for rd in run.documents],
                "artifact": {"id": art.id, "version": art.current_version} if art else None,
            }

    async def events(self, run_id: int, last_event_id: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Replay history after `last_event_id`, then live events until run.end.

        A run that is not active and has nothing to replay yields one
        run.snapshot event (id None) built from the database, then ends.
        """
        q = self.bus.subscribe(run_id)
        try:
            seen = last_event_id or 0
            yielded = False
            for e in self.bus.history(run_id, after=last_event_id):
                yield e
                yielded, seen = True, e["id"]
                if e["type"] == "run.end":
                    return
            while True:
                try:
                    e = q.get_nowait()
                except asyncio.QueueEmpty:
                    if not self.is_active(run_id):
                        if not yielded:
                            yield {"id": None, "run_id": run_id, "type": "run.snapshot",
                                   "data": self.snapshot(run_id)}
                        return
                    e = await q.get()
                if e["id"] <= seen:
                    continue
                yield e
                yielded, seen = True, e["id"]
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
            run.finished_at = models.utcnow()
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
            wf = run.workflow
            todo = [(rd.id, DocumentRef(rd.document_id, rd.document.filename, rd.document.stored_name))
                    for rd in run.documents if RunDocumentStatus(rd.status) == RunDocumentStatus.QUEUED]
            spent = (run.tokens_in or 0) + (run.tokens_out or 0)
            plan = _Plan(
                run_id=run_id,
                fields=[FactField.model_validate(f) for f in wf.fact_schema],
                sections=[SectionPlanItem.model_validate(x) for x in wf.section_plan],
                instructions=wf.instructions or "",
                title=wf.name,
                todo=todo,
                budget_limit=max(1, self._budget_limit - spent),
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
        budget = budget.child(self._document_budget_limit, label=f"document:{ref.id}")  # T14 ceiling
        try:
            self._update_doc(run_id, rd_id, RunDocumentStatus.READING, progress="Reading pages")
            pages = await self._read_pages(run_id, rd_id, ref, budget)
            self._update_doc(run_id, rd_id, RunDocumentStatus.EXTRACTING, progress="Extracting")
            result = await windows.extract_document(
                self.provider, budget, plan.fields, pages, document_name=ref.filename,
                instructions=plan.instructions, label=f"extract:{ref.id}",
                on_progress=lambda k, n: self._update_doc(run_id, rd_id, progress=f"Extracting window {k}/{n}"))
            self._update_doc(run_id, rd_id, RunDocumentStatus.VERIFYING, progress="Verifying")
            verified = await self._in_thread(windows.verify_document, result, pages, self._verify_config)
            self._store_facts(run_id, ref.id, verified)
            self._update_doc(run_id, rd_id, RunDocumentStatus.DONE, progress=None,
                             option_count=result.option_count, option_label=result.option_label)
        except asyncio.CancelledError:
            raise
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
                s.add(fact)

    def _compose_input(self, run: models.Run, plan: _Plan, budget: RunBudget) -> compose.ComposeInput:
        return build_compose_input(run, title=plan.title, fields=plan.fields, sections=plan.sections,
                                   instructions=plan.instructions)

    async def _compose(self, run_id: int, plan: _Plan, budget: RunBudget) -> None:
        with db.session_scope(self._factory) as s:
            run = s.get(models.Run, run_id)
            usable = [rd for rd in run.documents if RunDocumentStatus(rd.status) == RunDocumentStatus.DONE]
            if not usable:
                models.transition_run(run, RunStatus.FAILED)
                reasons = sorted({rd.error for rd in run.documents if rd.error})
                run.error = "No document could be read. " + " ".join(reasons)
                run.retryable = True
                run.finished_at = models.utcnow()
                s.flush()
                payload = _run_payload(run)
                inp = None
            else:
                models.transition_run(run, RunStatus.COMPOSING)
                s.flush()
                inp = self._compose_input(run, plan, budget)
                payload = _run_payload(run)
        if inp is None:
            self._end(run_id, payload)
            return
        self._emit(run_id, "run.status", {k: payload[k] for k in ("status", "error", "retryable")})

        result = await compose.compose_report(self.provider, budget, inp, label=f"compose:{run_id}")

        with db.session_scope(self._factory) as s:
            run = s.get(models.Run, run_id)
            note = "template fallback" if result.fallback else "generated"
            if run.artifact is None:
                art = models.create_artifact(s, run, title=plan.title, content=result.content, note=note)
            else:
                art = run.artifact
                content = result.content
                if self._merge_edits is not None:
                    content = self._merge_edits(art.latest_content(), content)
                models.snapshot_version(s, art, content, note=f"retry, {note}")
            any_failed = any(RunDocumentStatus(rd.status) == RunDocumentStatus.FAILED for rd in run.documents)
            final = RunStatus.PARTIAL if any_failed or budget.exhausted else RunStatus.DONE
            models.transition_run(run, final)
            run.finished_at = models.utcnow()
            s.flush()
            artifact = {"artifact_id": art.id, "version": art.current_version, "fallback": result.fallback}
            payload = _run_payload(run)
        self._emit(run_id, "artifact.ready", artifact)
        self._end(run_id, payload)
