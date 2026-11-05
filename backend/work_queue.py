r"""The broker work queue (spec docs/specs/2026-11-02-work-queue-design.md, ledger R1 to R11, M1 to M8).

`GET /api/queue?line=` groups a line's runs by what needs action. The rules live in the pure
`classify` below; `load_queue` selects and batch-loads the rows it needs; `routes/queue.py` is a
thin wrapper. (Named work_queue, not queue: tests run from backend/, where a `queue.py` would
shadow the standard library module of that name.)

Classification, first match wins (D2, D3, D4, D10 and M1 applied):

    run (line=L, selected by: active OR queue_state=open OR max(finished_at, queue_changed_at) >= now-14d; cap 500)
     |
     +- workflow == Proposal Builder ----------------------------> skip (Proposals / Activity)
     +- active (queued/running) --------------------------------> in_progress
     +- Compare with proposal_run_id and that proposal active ---> in_progress ("Building proposal")
     +- queue_state == open:
     |    +- status failed|partial|cancelled ------------------> needs_attention (reasons, D10; open_counts if a check)
     |    +- Policy Check, has report, open discrepancies > 0 --> needs_review
     |    +- Compare done, latest proposal failed|cancelled ------> needs_attention ("Proposal didn't finish"; D3 amended)
     |    +- Compare done, no proposal -------------------------> ready
     |    +- otherwise (check fully resolved, proposal done) -> done_recently if in window
     +- queue_state done|dismissed -----------------------------> done_recently if in window
     +- outside window -----------------------------------------> omitted (Activity only)

Selection (D4, M2): rows matching the filter above, actionable rows first (active, or open AND
(failed|partial|cancelled, or Policy Check, or Compare without a done proposal)), then the recent
rows that left the queue, newest first within each; at most 500, `truncated` when more matched.

A proposal counts as built ("done proposal") when its run is done or partial: both wrote a
proposal document. Failed or cancelled sends the comparison to Needs attention with "Proposal didn't
finish" and its "Retry proposal" (D3 amended by the owner 2026-11-04: a failure never reads as Ready).

Client name (W4, D1, M7): the broker's `client_name`, else the extracted verified `named_insured`
(Policy Check: renewal, then expiring, then quote; Compare Quotes: the value most quotes state,
a tie going to the value of the leftmost quote), else none (the UI shows "Unnamed client").

Bookkeeping the runner calls (M4, D2, D3, D8): `on_run_terminal` from every terminal transition
of a run, inside that transition's transaction, and `on_run_retry` from Retry.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from backend import models
from backend.schemas import ArtifactContent, EvidenceState, RunStatus
from backend.slugs import (
    ALL_SLUGS,
    CERTIFICATE_SLUG,
    COMPARE_QUOTES_SLUG,
    LOSS_RUN_SLUG,
    POLICY_CHECK_SLUG,
    PROPOSAL_BUILDER_SLUG,
)

GROUPS = ("in_progress", "needs_review", "ready", "needs_attention", "done_recently")
WINDOW = timedelta(days=14)
CAP = 500

ACTIVE = (str(RunStatus.QUEUED), str(RunStatus.RUNNING), str(RunStatus.COMPOSING))
NEEDS_ATTENTION = (str(RunStatus.FAILED), str(RunStatus.PARTIAL), str(RunStatus.CANCELLED))
PROPOSAL_BUILT = (str(RunStatus.DONE), str(RunStatus.PARTIAL))
PROPOSAL_UNFINISHED = (str(RunStatus.FAILED), str(RunStatus.CANCELLED))

# Policy Check: whose verified named_insured names the client, in order (M7)
POLICY_NAME_SLOTS = ("renewal", "expiring", "quote")

# Reasons copy (plain language, spec 1.2, D3, D10)
STOPPED = "Stopped before finishing"
INTERRUPTED = "Interrupted by a restart"
BUDGET = "Extraction stopped: budget reached"
PROPOSAL_FAILED = "Proposal didn't finish"
RUN_ERROR = "Stopped with an error"
_BUDGET_CODES = {"budget_exhausted", "document_budget_exhausted"}

# One next action per item (PRODUCT.md principle 1): the label of each workflow's primary action. Every workflow
# slug has one (T12 test), so a new workflow cannot reach the queue without a label.
QUEUE_ACTIONS: dict[str, str] = {
    COMPARE_QUOTES_SLUG: "Compare quotes",
    PROPOSAL_BUILDER_SLUG: "Open proposal",
    POLICY_CHECK_SLUG: "Review discrepancies",
    CERTIFICATE_SLUG: "Issue certificate",
    LOSS_RUN_SLUG: "Review losses",  # slice 5a
}
assert set(QUEUE_ACTIONS) == set(ALL_SLUGS), "every workflow slug needs a queue action label"


# --------------------------------------------------------------------- pure part


@dataclass(frozen=True)
class QueueRun:
    """What `classify` needs to know about one run (loaded by `load_queue`)."""

    run_id: int
    workflow_slug: str
    workflow_name: str
    line: str
    status: str
    active: bool
    queue_state: str
    created_at: datetime | None
    finished_at: datetime | None
    queue_changed_at: datetime | None
    document_count: int = 0
    failed_codes: tuple[str | None, ...] = ()  # error_code of each failed document
    has_report: bool = False
    client: str | None = None
    client_source: str | None = None  # "broker" | "extracted" | None
    proposal_run_id: int | None = None
    retryable: bool = False
    carriers: tuple[str | None, ...] = ()  # per document, column order: verified carrier_name or None
    open_checks: int | None = None  # slice 5a: a loss review's checks needing action (None without a report)


@dataclass(frozen=True)
class ProposalLink:
    """The Proposal Builder run a comparison's `proposal_run_id` names."""

    run_id: int
    status: str
    active: bool

    @property
    def built(self) -> bool:
        return not self.active and self.status in PROPOSAL_BUILT

    @property
    def unfinished(self) -> bool:
        return not self.active and self.status in PROPOSAL_UNFINISHED

    @property
    def retryable(self) -> bool:
        """`Run.retryable` of the proposal run: POST /api/runs/{id}/retry would be accepted."""
        return not self.active and self.status in NEEDS_ATTENTION


def aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes for what was written as UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def last_change(run: QueueRun) -> datetime | None:
    """The later of finished_at and queue_changed_at: the clock of Done recently (D2)."""
    stamps = [t for t in (aware(run.finished_at), aware(run.queue_changed_at)) if t is not None]
    return max(stamps) if stamps else None


def in_window(run: QueueRun, now: datetime) -> bool:
    changed = last_change(run)
    return changed is not None and changed >= aware(now) - WINDOW


def summary(run: QueueRun, *, building_proposal: bool = False) -> str:
    if building_proposal:
        return "Building proposal"
    if run.workflow_slug == POLICY_CHECK_SLUG:
        return "Renewal check"
    if run.workflow_slug == LOSS_RUN_SLUG:  # slice 5a
        if run.open_checks:
            return f"{run.open_checks} check{'' if run.open_checks == 1 else 's'} to resolve"
        n = run.document_count
        return f"Loss review, {n} file{'' if n == 1 else 's'}"
    if run.workflow_slug == COMPARE_QUOTES_SLUG:
        n = run.document_count
        return f"{n} carrier{'' if n == 1 else 's'}"
    return run.workflow_name


def reasons(run: QueueRun, proposal: ProposalLink | None = None) -> list[str]:
    """Plain-language reasons a run needs attention (spec 1.2, D3, D10). Document failures come
    from the run's documents' error codes; a cancel or a restart is named before them."""
    out: list[str] = []
    codes = list(run.failed_codes)
    if run.status == str(RunStatus.CANCELLED):
        out.append(STOPPED)
    if "interrupted" in codes:
        out.append(INTERRUPTED)
    budget = any(c in _BUDGET_CODES for c in codes)
    if budget or (run.status == str(RunStatus.PARTIAL) and not codes):
        out.append(BUDGET)  # a partial run with every document read stopped on the run budget
    unread = sum(1 for c in codes if c not in _BUDGET_CODES and c not in ("cancelled", "interrupted"))
    if unread:
        out.append(f"{unread} document{'' if unread == 1 else 's'} not read")
    if run.status == str(RunStatus.FAILED) and not out:
        out.append(RUN_ERROR)
    if proposal is not None and proposal.unfinished:
        out.append(PROPOSAL_FAILED)
    return out


def classify(run: QueueRun, counts: Mapping[str, int] | None, proposal: ProposalLink | None,
             now: datetime) -> tuple[str, dict[str, Any]] | None:
    """(group, item) for one run, or None when the run is not in the queue. `counts` is the
    run's open discrepancy counts per severity (Policy Check with a report), `proposal` the run
    its `proposal_run_id` names (None when unset or gone). See the module docstring."""
    if run.workflow_slug in (PROPOSAL_BUILDER_SLUG, CERTIFICATE_SLUG):  # certificates: their own page (C8)
        return None
    is_check = run.workflow_slug == POLICY_CHECK_SLUG
    is_loss = run.workflow_slug == LOSS_RUN_SLUG
    is_compare = run.workflow_slug == COMPARE_QUOTES_SLUG
    open_total = sum(counts.values()) if counts else 0
    open_counts = dict(counts) if (is_check and run.has_report and counts is not None) else None

    def item(group: str, *, why: list[str] | None = None, building: bool = False) -> tuple[str, dict[str, Any]]:
        return group, {
            "run_id": run.run_id,
            "workflow_slug": run.workflow_slug,
            "workflow_name": run.workflow_name,
            "line": run.line,
            # Queue navigation (2026-11-03): rows that share a client are told apart by date and carriers.
            "created_at": _iso(run.created_at),
            "carriers": list(run.carriers),
            "client": run.client,
            "client_source": run.client_source,
            "summary": summary(run, building_proposal=building),
            "open_counts": open_counts,
            "reasons": why or None,
            "status": run.status,
            "retryable": run.retryable,
            "finished_at": _iso(run.finished_at),
            "queue_state": run.queue_state,
            "queue_changed_at": _iso(run.queue_changed_at),
            "proposal_run_id": run.proposal_run_id,
            # Work queue UI: "Retry proposal" on a comparison whose proposal did not finish (Needs attention, D3 amended).
            "proposal_retryable": proposal.retryable if proposal is not None else None,
            "action": QUEUE_ACTIONS.get(run.workflow_slug),
            "open_checks": run.open_checks if is_loss else None,
        }

    if run.active:
        return item("in_progress")
    if is_compare and proposal is not None and proposal.active:
        return item("in_progress", building=True)
    if run.queue_state == "open":
        if run.status in NEEDS_ATTENTION:
            return item("needs_attention", why=reasons(run))
        if is_check and run.has_report and open_total > 0:
            return item("needs_review")
        # Slice 5a: a loss review needs review while any check, conflict or unverified row is open, and is ready
        # when none is; Done when the broker marks it done (queue_state, like every workflow).
        if is_loss and run.has_report:
            return item("needs_review" if run.open_checks else "ready")
        if is_compare and run.status == str(RunStatus.DONE) and (proposal is None or not proposal.built):
            # D3 amended (owner 2026-11-04): a proposal that failed or was cancelled is a failure like any other,
            # so the comparison needs attention ("Proposal didn't finish", primary action "Retry proposal").
            if proposal is not None and proposal.unfinished:
                return item("needs_attention", why=reasons(run, proposal))
            return item("ready")
    if in_window(run, now):
        return item("done_recently")
    return None


def group_items(classified: Iterable[tuple[QueueRun, tuple[str, dict[str, Any]] | None]]) -> dict[str, list]:
    """Items per group, newest change first (then the higher run id)."""
    groups: dict[str, list[tuple[Any, dict[str, Any]]]] = {g: [] for g in GROUPS}
    floor = datetime.min.replace(tzinfo=UTC)
    for run, result in classified:
        if result is None:
            continue
        group, it = result
        key = (max([t for t in (last_change(run), aware(run.created_at)) if t is not None], default=floor),
               run.run_id)
        groups[group].append((key, it))
    return {g: [it for _, it in sorted(rows, key=lambda r: r[0], reverse=True)] for g, rows in groups.items()}


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def extracted_client(workflow_slug: str, facts: Sequence[tuple[str, int, str]]) -> str | None:
    """The client name a run's verified `named_insured` facts give, or None (M7, D1).

    `facts` holds (slot, document position, value) per verified fact. Policy Check takes the
    renewal's name, else the expiring policy's, else the quote's. Compare Quotes takes the name most
    quotes state (compared ignoring case and spacing); on a tie, the one whose first quote is
    leftmost (lowest position) wins, and its spelling there is shown."""
    values = [(slot, pos, " ".join(v.split())) for slot, pos, v in facts if v and v.strip()]
    if not values:
        return None
    if workflow_slug in (CERTIFICATE_SLUG, LOSS_RUN_SLUG):  # slices 4, 5a: the first document naming the insured
        return sorted((pos, v) for _, pos, v in values)[0][1]
    if workflow_slug == POLICY_CHECK_SLUG:
        for slot in POLICY_NAME_SLOTS:
            hit = sorted((pos, v) for s, pos, v in values if s == slot)
            if hit:
                return hit[0][1]
        return None
    if workflow_slug == COMPARE_QUOTES_SLUG:
        tally: dict[str, int] = defaultdict(int)
        first: dict[str, tuple[int, str]] = {}
        for _, pos, v in values:
            k = _norm(v)
            tally[k] += 1
            if k not in first or pos < first[k][0]:
                first[k] = (pos, v)
        best = max(tally, key=lambda k: (tally[k], -first[k][0]))
        return first[best][1]
    return None


def resolve_client(broker: str | None, extracted: str | None) -> tuple[str | None, str | None]:
    """(client, client_source): the broker's name beats the extracted one (W4)."""
    if broker:
        return broker, "broker"
    if extracted:
        return extracted, "extracted"
    return None, None


def carriers_of(positions: Sequence[int], facts: Sequence[tuple[int, str]]) -> tuple[str | None, ...]:
    """One entry per document of the run, in column order (`positions`): the document's verified `carrier_name`
    (spacing normalised; `facts` holds (position, value)), or None when it has none (not read, read from a scan,
    or no carrier printed). The first verified value of a document wins."""
    named: dict[int, str] = {}
    for pos, value in facts:
        name = " ".join((value or "").split())
        if name and pos not in named:
            named[pos] = name
    return tuple(named.get(p) for p in sorted(positions))


def run_client(session: Session, run: models.Run) -> tuple[str | None, str | None]:
    """(client, client_source) of one run by the queue's rule (W4, M7): `GET /api/runs/{id}` shows the same
    name, with the same source, as the run's queue row."""
    if run.client_name:
        return run.client_name, "broker"
    where = {rd.document_id: (rd.slot, rd.position) for rd in run.documents}
    slug = run.workflow.slug if run.workflow else ""
    rows = session.execute(select(models.Fact.document_id, models.Fact.value)
                           .where(models.Fact.run_id == run.id, models.Fact.field == insured_field(slug),
                                  models.Fact.state == str(EvidenceState.VERIFIED)))
    facts = [(*where[d], v) for d, v in rows if d in where and v]
    return resolve_client(None, extracted_client(slug, facts))


def insured_field(workflow_slug: str) -> str:
    """The fact whose verified value names the client (slice 4: certificates read cert: keys)."""
    if workflow_slug == LOSS_RUN_SLUG:
        return "lr:named_insured"
    return "cert:named_insured" if workflow_slug == CERTIFICATE_SLUG else "named_insured"


def client_key(name: str | None) -> str:
    """Normalized client name: what holders are remembered by (slice 4, C2), as names are compared here."""
    return _norm(name or "")


# --------------------------------------------------------------------- certificate queue (slice 4, C8)
#
#     certificate run (any line), selected like the work queue (active, open, or changed in 14 days)
#      +- active (queued/running/composing) -------------------------> in_progress
#      +- current version issued -------------------------------------> done_recently if in window ("Issued")
#      +- queue_state done|dismissed ---------------------------------> done_recently if in window
#      +- failed | cancelled, or no draft ----------------------------> needs_attention (reasons)
#      +- draft with claims needing action (open, not supported) ------> needs_attention ("N checks to resolve")
#      +- draft with every claim supported, overridden or removed ----> ready ("Ready to issue")
# A partial run (a document not read) keeps its draft: classified by its claims, with the reason listed.

CERTIFICATE_GROUPS = ("in_progress", "needs_attention", "ready", "done_recently")


def classify_certificate(run: QueueRun, *, open_claims: int | None, issued_at: datetime | None,
                         now: datetime) -> tuple[str, dict[str, Any]] | None:
    def item(group: str, summary_text: str, why: list[str] | None = None) -> tuple[str, dict[str, Any]]:
        return group, {
            "run_id": run.run_id, "workflow_slug": run.workflow_slug, "workflow_name": run.workflow_name,
            "line": run.line, "created_at": _iso(run.created_at), "client": run.client,
            "client_source": run.client_source, "summary": summary_text, "reasons": why or None,
            "open_claims": open_claims, "status": run.status, "retryable": run.retryable,
            "finished_at": _iso(run.finished_at), "queue_state": run.queue_state,
            "queue_changed_at": _iso(run.queue_changed_at), "issued_at": _iso(issued_at),
        }

    if run.active:
        return item("in_progress", "Reading documents")
    stamps = [t for t in (last_change(run), aware(issued_at)) if t is not None]
    recent = bool(stamps) and max(stamps) >= aware(now) - WINDOW
    if issued_at is not None:
        return item("done_recently", "Issued") if recent else None
    if run.queue_state != "open":
        return item("done_recently", "Closed") if recent else None
    why = reasons(run) if run.status in NEEDS_ATTENTION else []
    if run.status in (str(RunStatus.FAILED), str(RunStatus.CANCELLED)) or open_claims is None:
        return item("needs_attention", "Draft not ready", why)
    if open_claims > 0:
        return item("needs_attention", f"{open_claims} check{'' if open_claims == 1 else 's'} to resolve", why)
    return item("ready", "Ready to issue", why)


def load_certificate_queue(session: Session, *, is_active: Callable[[int], bool] = lambda _id: False,
                           now: datetime | None = None, cap: int = CAP) -> dict[str, Any]:
    """The Certificates page queue (`GET /api/certificates/queue`): certificate runs only, every line."""
    from backend.pipeline import certificate  # the pipeline imports this module

    now = aware(now) or models.utcnow()
    cutoff = now - WINDOW
    Run, Workflow = models.Run, models.Workflow
    query = (select(Run).join(Workflow, Workflow.id == Run.workflow_id)
             .where(Workflow.slug == CERTIFICATE_SLUG,
                    or_(Run.status.in_(ACTIVE), Run.queue_state == "open", Run.finished_at >= cutoff,
                        Run.queue_changed_at >= cutoff))
             .order_by(Run.id.desc()).limit(cap + 1)
             .options(selectinload(Run.workflow), selectinload(Run.documents)))
    runs = list(session.scalars(query))
    truncated = len(runs) > cap
    runs = runs[:cap]
    ids = [r.id for r in runs]
    drafts: dict[int, tuple[int, int, list]] = {}  # run -> (artifact id, version, claim blocks)
    issued: dict[tuple[int, int], datetime] = {}
    states: dict[int, dict[str, models.DiscrepancyState]] = defaultdict(dict)
    names: dict[int, list[tuple[int, str]]] = defaultdict(list)
    if ids:
        for run_id, art_id, version, content in session.execute(
                select(models.Artifact.run_id, models.Artifact.id, models.Artifact.current_version,
                       models.ArtifactVersion.content)
                .join(models.ArtifactVersion, and_(models.ArtifactVersion.artifact_id == models.Artifact.id,
                                                   models.ArtifactVersion.version == models.Artifact.current_version))
                .where(models.Artifact.run_id.in_(ids))):
            drafts[run_id] = (art_id, version, models.claim_blocks(ArtifactContent.model_validate(content)))
        for art_id, version, at in session.execute(
                select(models.CertificateIssue.artifact_id, models.CertificateIssue.version,
                       models.CertificateIssue.issued_at)
                .where(models.CertificateIssue.run_id.in_(ids))):
            issued[(art_id, version)] = at
        for row in session.scalars(select(models.DiscrepancyState).where(models.DiscrepancyState.run_id.in_(ids))):
            states[row.run_id][row.item_id] = row
        for run_id, doc_id, value in session.execute(
                select(models.Fact.run_id, models.Fact.document_id, models.Fact.value)
                .where(models.Fact.run_id.in_(ids), models.Fact.field == "cert:named_insured",
                       models.Fact.state == str(EvidenceState.VERIFIED))):
            names[run_id].append((doc_id, value))
    groups: dict[str, list[tuple[Any, dict[str, Any]]]] = {g: [] for g in CERTIFICATE_GROUPS}
    floor = datetime.min.replace(tzinfo=UTC)
    for run in runs:
        status = str(run.status)
        active = is_active(run.id) or status in ACTIVE
        where = {rd.document_id: (rd.slot, rd.position) for rd in run.documents}
        facts = [(*where[d], v) for d, v in names.get(run.id, ()) if d in where and v]
        client, source = resolve_client(run.client_name, extracted_client(CERTIFICATE_SLUG, facts))
        qr = QueueRun(run_id=run.id, workflow_slug=CERTIFICATE_SLUG, workflow_name=run.workflow.name,
                      line=run.line or "commercial", status=status, active=active,
                      queue_state=run.queue_state or "open", created_at=run.created_at, finished_at=run.finished_at,
                      queue_changed_at=run.queue_changed_at, document_count=len(run.documents),
                      failed_codes=tuple(rd.error_code for rd in run.documents if str(rd.status) == "failed"),
                      has_report=run.id in drafts, client=client, client_source=source,
                      retryable=not active and status in NEEDS_ATTENTION)
        draft = drafts.get(run.id)
        open_claims = None
        issued_at = None
        if draft is not None:
            art_id, version, claims = draft
            open_claims = certificate.claim_counts(claims, states.get(run.id, {})).open
            issued_at = issued.get((art_id, version))
        result = classify_certificate(qr, open_claims=open_claims, issued_at=issued_at, now=now)
        if result is None:
            continue
        group, it = result
        stamps = [t for t in (last_change(qr), aware(issued_at), aware(run.created_at)) if t is not None]
        groups[group].append(((max(stamps, default=floor), run.id), it))
    return {**{g: [it for _, it in sorted(rows, key=lambda r: r[0], reverse=True)] for g, rows in groups.items()},
            "truncated": truncated}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# --------------------------------------------------------------------- loading (D4, M2, section 4)


def load_queue(session: Session, line: str, *, is_active: Callable[[int], bool] = lambda _id: False,
               now: datetime | None = None, cap: int = CAP) -> dict[str, Any]:
    """The `GET /api/queue` body for one line. Batched: the runs with their workflow and documents
    (selectinload), one query for linked proposals, one for the current report of the Policy Check
    runs, one for their discrepancy states and one for verified named_insured facts."""
    now = aware(now) or models.utcnow()
    cutoff = now - WINDOW
    Run, Workflow = models.Run, models.Workflow
    Proposal = aliased(models.Run)
    no_built_proposal = or_(Run.proposal_run_id.is_(None), Proposal.id.is_(None),
                            Proposal.status.not_in(PROPOSAL_BUILT))
    actionable = or_(
        Run.status.in_(ACTIVE),
        Proposal.status.in_(ACTIVE),
        and_(Run.queue_state == "open",
             or_(Run.status.in_(NEEDS_ATTENTION), Workflow.slug.in_((POLICY_CHECK_SLUG, LOSS_RUN_SLUG)),
                 and_(Workflow.slug == COMPARE_QUOTES_SLUG, no_built_proposal))),
    )
    query = (
        select(Run)
        .join(Workflow, Workflow.id == Run.workflow_id)
        .outerjoin(Proposal, Proposal.id == Run.proposal_run_id)
        .where(Run.line == line, Workflow.slug.not_in((PROPOSAL_BUILDER_SLUG, CERTIFICATE_SLUG)),
               or_(Run.status.in_(ACTIVE), Proposal.status.in_(ACTIVE), Run.queue_state == "open",
                   Run.finished_at >= cutoff, Run.queue_changed_at >= cutoff))
        .order_by(case((actionable, 0), else_=1), Run.id.desc())
        .limit(cap + 1)
        .options(selectinload(Run.workflow), selectinload(Run.documents))
    )
    runs = list(session.scalars(query))
    truncated = len(runs) > cap
    runs = runs[:cap]

    check_ids = [r.id for r in runs if r.workflow.slug == POLICY_CHECK_SLUG]
    loss_ids = [r.id for r in runs if r.workflow.slug == LOSS_RUN_SLUG]
    proposal_ids = {r.proposal_run_id for r in runs if r.proposal_run_id is not None}

    proposals: dict[int, ProposalLink] = {}
    if proposal_ids:
        for pid, status in session.execute(select(Run.id, Run.status).where(Run.id.in_(proposal_ids))):
            proposals[pid] = ProposalLink(pid, str(status), is_active(pid) or str(status) in ACTIVE)

    reports: dict[int, list] = {}
    if check_ids:
        rows = session.execute(
            select(models.Artifact.run_id, models.ArtifactVersion.content)
            .join(models.ArtifactVersion, and_(models.ArtifactVersion.artifact_id == models.Artifact.id,
                                               models.ArtifactVersion.version == models.Artifact.current_version))
            .where(models.Artifact.run_id.in_(check_ids)))
        for run_id, content in rows:
            reports[run_id] = models.discrepancy_blocks(ArtifactContent.model_validate(content))
    loss_open: dict[int, int] = {}
    if loss_ids:  # slice 5a: the report's checks carry their computed status (needs_action)
        for run_id, content in session.execute(
                select(models.Artifact.run_id, models.ArtifactVersion.content)
                .join(models.ArtifactVersion, and_(models.ArtifactVersion.artifact_id == models.Artifact.id,
                                                   models.ArtifactVersion.version == models.Artifact.current_version))
                .where(models.Artifact.run_id.in_(loss_ids))):
            blocks = models.loss_check_blocks(ArtifactContent.model_validate(content))
            loss_open[run_id] = models.loss_open_checks(blocks)
    states: dict[int, dict[str, models.DiscrepancyState]] = defaultdict(dict)
    if reports:
        for row in session.scalars(select(models.DiscrepancyState)
                                   .where(models.DiscrepancyState.run_id.in_(list(reports)))):
            states[row.run_id][row.item_id] = row

    # One statement for both: named_insured (runs without a broker name) and carrier_name (every run).
    unnamed = {r.id for r in runs if not r.client_name}
    names: dict[int, list[tuple[int, str]]] = defaultdict(list)  # run -> (document id, value)
    carrier_facts: dict[int, list[tuple[int, str]]] = defaultdict(list)
    if runs:
        for run_id, doc_id, field, value in session.execute(
                select(models.Fact.run_id, models.Fact.document_id, models.Fact.field, models.Fact.value)
                .where(models.Fact.run_id.in_([r.id for r in runs]),
                       models.Fact.field.in_(("named_insured", "carrier_name", "lr:named_insured", "lr:carrier")),
                       models.Fact.state == str(EvidenceState.VERIFIED))):
            if field in ("carrier_name", "lr:carrier"):
                carrier_facts[run_id].append((doc_id, value))
            elif run_id in unnamed:
                names[run_id].append((doc_id, value))

    classified = []
    for run in runs:
        status = str(run.status)
        active = is_active(run.id) or status in ACTIVE
        where = {rd.document_id: (rd.slot, rd.position) for rd in run.documents}
        facts = [(*where[d], v) for d, v in names.get(run.id, ()) if d in where and v]
        client, source = resolve_client(run.client_name, extracted_client(run.workflow.slug, facts))
        qr = QueueRun(
            run_id=run.id, workflow_slug=run.workflow.slug, workflow_name=run.workflow.name,
            line=run.line or "commercial", status=status, active=active, queue_state=run.queue_state or "open",
            created_at=run.created_at, finished_at=run.finished_at, queue_changed_at=run.queue_changed_at,
            document_count=len(run.documents),
            failed_codes=tuple(rd.error_code for rd in run.documents
                               if str(rd.status) == "failed"),
            has_report=run.id in reports or run.id in loss_open, client=client, client_source=source,
            proposal_run_id=run.proposal_run_id, open_checks=loss_open.get(run.id),
            retryable=not active and status in NEEDS_ATTENTION,
            carriers=carriers_of([pos for _, pos in where.values()],
                                 [(where[d][1], v) for d, v in carrier_facts.get(run.id, ()) if d in where]),
        )
        counts = None
        if run.id in reports:
            counts = models.discrepancy_counts(session, run, blocks=reports[run.id], rows=states.get(run.id, {})).open_counts
        proposal = proposals.get(run.proposal_run_id) if run.proposal_run_id is not None else None
        classified.append((qr, classify(qr, counts, proposal, now)))
    return {"line": line, **group_items(classified), "truncated": truncated}


# --------------------------------------------------------------------- runner bookkeeping (M4)


def source_run_id(run: models.Run) -> int | None:
    """The comparison a Proposal Builder run was built from (carried in parameters, M3)."""
    value = (run.parameters or {}).get("source_run_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def link_proposal(session: Session, run: models.Run) -> None:
    """At submit (new or reused run): a proposal built from a comparison becomes that
    comparison's `proposal_run_id`, so the comparison shows "Building proposal" while it runs."""
    sid = source_run_id(run)
    if sid is None:
        return
    source = session.get(models.Run, sid)
    if source is not None:
        source.proposal_run_id = run.id


def on_run_terminal(session: Session, run: models.Run, now: datetime | None = None) -> None:
    """Queue bookkeeping for a run that just reached a terminal status (done, partial, failed,
    cancelled, or failed by the startup reconcile). Call it inside the transition's transaction.

    A proposal run built from a comparison (D3): done or partial means the comparison's proposal
    exists, so the comparison leaves Ready now and Done recently counts from this moment (D2).
    Failed or cancelled changes nothing stored: the comparison is classified into Needs attention
    with "Proposal didn't finish" (D3 amended 2026-11-04). Only the comparison's latest proposal (its `proposal_run_id`)
    counts."""
    sid = source_run_id(run)
    if sid is None:
        return
    source = session.get(models.Run, sid)
    if source is None or source.proposal_run_id != run.id:
        return
    if str(run.status) in PROPOSAL_BUILT:
        source.queue_changed_at = now or models.utcnow()


def on_run_retry(run: models.Run) -> None:
    """Retry reopens the item (D8): queue_state open, and its leave time cleared."""
    run.queue_state = "open"
    run.queue_changed_at = None
