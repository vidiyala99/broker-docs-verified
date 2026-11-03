r"""Certificate draft (slice 4, C4): facts + parameters + the broker's decisions -> ArtifactContent.

Pure code, no model call (C5). The runner builds the draft when a certificate run composes; every
later change (Remove, a cell edit, Undo, Add document) rebuilds it with this same function, so the
printed certificate always agrees with the check list.

    run facts ──► per document: Evidence per cert: key (compose._table_row cell rules: chips,
        │         conflicts, a cited state without a value shows nothing)
        ▼
    coi_checks.join ──► policies (declarations), endorsements joined to them (D7), notices
        ▼
    claims: in_force + one per requested wording per printed policy line (D14), description (D11),
        │   one per edited printed value (D12)
        ▼
    decisions: each claim's state row as reconcile will leave it (models.reconciled_status), so a
        │   removal whose evidence changed is reopened before anything prints (no second pass)
        ▼
    Remove (D13): in_force removed  ──► the policy's coverage row leaves, with its wording claims
        │         wording removed    ──► that wording leaves the policy's line
        │         description removed ──► the description is cleared
        │         edited removed     ──► the extracted value is restored
        ▼
    insured claim over the printed policies (not removable: override it, or remove the mismatching
        policy's row)
        ▼
    ArtifactContent: section "certificate" [CertificateBlock], section "checks" [ClaimBlock...],
        notices (packages, unmatched endorsements, unread documents)

The field map (FIELD_MAP, data) says what each printed row shows: the own one-page layout ("own-v1",
C6); an ACORD 25 map would be a second map. A value prints only when VERIFIED, otherwise "Not stated"
(the cell keeps its state and chips, so the draft shows why).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import models
from backend.pipeline import coi_checks, compose
from backend.pipeline.coi_checks import Endorsement, Evidence, PolicyDoc, Request
from backend.schemas import (
    CITED_STATES,
    ArtifactContent,
    CertificateBlock,
    CertificateField,
    CertificateInsurer,
    CertificatePolicy,
    Cell,
    ClaimBlock,
    ClaimKind,
    EvidenceState,
    FactField,
    Notice,
    RunDocumentStatus,
    Section,
    is_endorsement_key,
    scalar_fields,
    split_endorsement_key,
)

NOT_STATED = "Not stated"
LAYOUT = "own-v1"
CERTIFICATE_SECTION, CHECKS_SECTION = "certificate", "checks"

# Field map (C5/C6): printed rows of the own layout, (field, source fact, label). Limits per line come
# from coi_checks.LINE_LIMITS; every row prints the source fact only when verified.
INSURED_MAP = (("cert:named_insured", "Insured"), ("cert:insured_address", "Insured address"))
PRODUCER_MAP = ("cert:producer", "Producer")
POLICY_MAP = (("cert:carrier", "Insurer"), ("cert:naic", "NAIC"), ("cert:policy_number", "Policy number"),
              ("cert:effective_date", "Effective"), ("cert:expiration_date", "Expiration"))
FIELD_LABELS: dict[str, str] = {}  # filled from the workflow fields at build time


# --------------------------------------------------------------------- parameters (D6)


class Wording(BaseModel):
    model_config = ConfigDict(extra="forbid")

    additional_insured: StrictBool = False
    waiver_of_subrogation: StrictBool = False
    primary_noncontributory: StrictBool = False

    def requested(self) -> tuple[str, ...]:
        return tuple(w for w in coi_checks.WORDINGS if getattr(self, w))


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _clean_text(value: str) -> str:
    """Control characters dropped (tab and line breaks kept as line breaks), runs of spaces collapsed."""
    lines = ["".join(ch for ch in line if ch >= " " or ch == "\t").replace("\t", " ") for line in value.splitlines()]
    return "\n".join(" ".join(line.split()) for line in lines).strip()


class CertificateParameters(BaseModel):
    """`POST /api/runs` parameters for the certificate workflow. Free text is bounded (D6: holder name
    120, address 300, description 1,500 characters; 422 names the field) and escaped when printed."""

    model_config = ConfigDict(extra="forbid")

    holder_name: str = Field(..., min_length=1, max_length=models.MAX_HOLDER_NAME_CHARS)
    holder_address: str = Field("", max_length=models.MAX_HOLDER_ADDRESS_CHARS)
    wording: Wording = Field(default_factory=Wording)
    certificate_date: dt.date | None = None  # default: today, fixed at run creation
    description: str = Field("", max_length=models.MAX_DESCRIPTION_CHARS)

    @field_validator("holder_name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        return _one_line(v) if isinstance(v, str) else v

    @field_validator("holder_address", "description", mode="before")
    @classmethod
    def _text(cls, v: Any) -> Any:
        return _clean_text(v) if isinstance(v, str) else v

    def stored(self, today: dt.date | None = None) -> dict[str, Any]:
        """What run.parameters holds (the date fixed, so a rebuild never moves it)."""
        out = self.model_dump(mode="json")
        out["certificate_date"] = (self.certificate_date or today or dt.date.today()).isoformat()
        return out


def stored_parameters(params: Mapping[str, Any] | None) -> CertificateParameters:
    return CertificateParameters.model_validate(dict(params or {}))


def request_of(params: CertificateParameters) -> Request:
    return Request(certificate_date=params.certificate_date or dt.date.today(), holder_name=params.holder_name,
                   wording=params.wording.requested(), description=params.description or None)


# --------------------------------------------------------------------- evidence from the run


def _evidence(cell: Cell) -> Evidence:
    state = EvidenceState(cell.state) if cell.state else EvidenceState.ABSENT
    return Evidence(value=cell.value if state in CITED_STATES else None, state=state,
                    passage_ids=tuple(cell.passage_ids), conflicts=tuple(c.value or "" for c in cell.conflicts))


@dataclass(frozen=True)
class RunInputs:
    """A run's documents as coi_checks sees them, plus the cells the certificate prints."""

    documents: list[PolicyDoc]
    cells: dict[tuple[str, str], Cell]  # (document letter, fact key) -> cell
    unread: list[tuple[str, str]]  # (letter, filename) of documents that failed
    fields: list[FactField]


def run_inputs(run: models.Run) -> RunInputs:
    fields = [FactField.model_validate(f) for f in run.workflow.fact_schema]
    facts_by_doc: dict[int, list[models.Fact]] = defaultdict(list)
    for f in run.facts:
        facts_by_doc[f.document_id].append(f)
    columns, facts, unread = [], [], []
    for rd in run.documents:
        failed = RunDocumentStatus(rd.status) == RunDocumentStatus.FAILED
        done = RunDocumentStatus(rd.status) == RunDocumentStatus.DONE
        if failed:
            unread.append((rd.letter, rd.document.filename))
        if not done:
            continue
        columns.append(compose.ComposeColumn(key=rd.letter, label=rd.document.filename, document_id=rd.document_id,
                                             filename=rd.document.filename, slot=rd.slot))
        facts.extend(compose.ComposeFact(
            column_key=rd.letter, field=f.field, value=f.value, state=EvidenceState(f.state), passage_id=f.passage_id,
            page_no=f.page_no, fact_id=f.id, extra_passage_ids=tuple(f.extra_passage_ids or ()),
            conflicts=tuple((c["value"], c.get("page_no"), c.get("passage_id")) for c in f.conflicts or ()))
            for f in facts_by_doc.get(rd.document_id, []))
    inp = compose.ComposeInput(title="", fields=fields, sections=[], columns=columns, facts=facts)
    keys = [f.key for f in scalar_fields(fields)]
    keys += sorted({f.field for f in facts if is_endorsement_key(f.field)})
    labels = {f.key: f.label for f in fields}
    cells: dict[tuple[str, str], Cell] = {}
    for key in keys:
        row = compose._table_row(inp, FactField(key=key, label=labels.get(key, key)))
        for letter, cell in row.cells.items():
            cells[(letter, key)] = cell
    documents = []
    for col in columns:
        scalars = {k: _evidence(cells[(col.key, k)]) for k in [f.key for f in scalar_fields(fields)]
                   if (col.key, k) in cells}
        parts: dict[str, dict[str, Evidence]] = defaultdict(dict)
        for (letter, key), cell in cells.items():
            if letter == col.key and is_endorsement_key(key):
                number, attribute = split_endorsement_key(key)
                parts[number][attribute] = _evidence(cell)
        endorsements = tuple(
            Endorsement(document_letter=col.key, number=n, form=p.get("form_number", coi_checks.MISSING),
                        type=p.get("type", coi_checks.MISSING), policy_number=p.get("policy_number", coi_checks.MISSING),
                        named_party=p.get("named_party", coi_checks.MISSING),
                        condition=p.get("condition", coi_checks.MISSING))
            for n, p in sorted(parts.items()) if any(ev.stated for ev in p.values()))
        documents.append(PolicyDoc(letter=col.key, document_id=col.document_id, filename=col.filename,
                                   facts=scalars, endorsements=endorsements))
    return RunInputs(documents=documents, cells=cells, unread=unread, fields=fields)


# --------------------------------------------------------------------- build


@dataclass(frozen=True)
class Analysis:
    """Everything one build decided (eval_hook and the issue route read it too)."""

    content: ArtifactContent
    claims: list[ClaimBlock]
    printed: list[PolicyDoc]
    joined: coi_checks.Joined
    statuses: dict[str, str]  # claim key -> effective status (open | resolved | removed)


def _printed_cell(cell: Cell | None) -> Cell:
    """What prints: the value when verified, else "Not stated" (state, chips and conflicts kept)."""
    if cell is None:
        return Cell(value=NOT_STATED, state=EvidenceState.ABSENT)
    out = cell.model_copy(deep=True)
    out.notice = None
    if EvidenceState(cell.state or EvidenceState.ABSENT) != EvidenceState.VERIFIED or not cell.value:
        out.value = NOT_STATED
    return out


def _field(cells, letter: str, key: str, label: str) -> CertificateField:
    return CertificateField(key=key, label=label, cell=_printed_cell(cells.get((letter, key))))


def _previous_edits(base: ArtifactContent | None) -> dict[tuple[str, str], Cell]:
    cert = base.find_certificate() if base is not None else None
    if cert is None:
        return {}
    return {(col, f.key): f.cell for col, f in cert.iter_fields() if f.cell.edited}


def build(inputs: RunInputs, params: CertificateParameters, rows: Mapping[str, models.DiscrepancyState],
          *, base: ArtifactContent | None = None) -> Analysis:
    """The certificate draft. `rows` are the run's claim state rows by item id; `base` the version whose
    edits carry over (the latest one, or the restored one for Undo)."""
    request = request_of(params)
    labels = {f.key: f.label for f in inputs.fields}
    joined = coi_checks.join(inputs.documents)
    unread = len(inputs.unread)

    def status(claim: ClaimBlock) -> str:
        return models.reconciled_status(rows.get(claim.id), models.claim_fingerprint(claim))

    per_policy: list[tuple[PolicyDoc, ClaimBlock, list[ClaimBlock]]] = []
    for p in joined.policies:
        in_force = coi_checks.in_force_claim(p, request.certificate_date)
        wording = [coi_checks.wording_claim(p, w, joined.endorsements.get(p.letter, []), request, unread=unread)
                   for w in coi_checks.applicable_wording(p.line, request)]
        per_policy.append((p, in_force, wording))

    claims: list[ClaimBlock] = []
    statuses: dict[str, str] = {}
    printed: list[tuple[PolicyDoc, list[str]]] = []
    for p, in_force, wording in per_policy:
        claims.append(in_force)
        statuses[in_force.key] = status(in_force)
        if statuses[in_force.key] == "removed":
            continue  # D13: the coverage row leaves, and with it the wording printed on it
        shown = []
        for c in wording:
            claims.append(c)
            statuses[c.key] = status(c)
            if statuses[c.key] != "removed":
                shown.append(c.wording)
        printed.append((p, shown))

    insured = coi_checks.insured_claim([p for p, _ in printed])
    if insured is not None:
        claims.append(insured)
        statuses[insured.key] = status(insured)

    description = request.description
    desc_claim = coi_checks.description_claim(description)
    if desc_claim is not None:
        claims.append(desc_claim)
        statuses[desc_claim.key] = status(desc_claim)
        if statuses[desc_claim.key] == "removed":
            description = None

    # ---- the printed certificate
    cells = inputs.cells
    first = printed[0][0] if printed else None
    producer = next((p for p, _ in printed if p.fact("cert:producer").verified), first)
    insurers: list[CertificateInsurer] = []
    insurer_of: dict[str, str] = {}
    for p, _ in printed:
        carrier = p.fact("cert:carrier")
        if not carrier.verified:
            continue
        name_key = coi_checks.normalize_name(carrier.value)
        known = next((i for i in insurers if coi_checks.normalize_name(i.name) == name_key), None)
        if known is None:
            naic = p.fact("cert:naic")
            known = CertificateInsurer(letter="ABCDEFGH"[len(insurers)], name=carrier.value,
                                       naic=naic.value if naic.verified else None)
            insurers.append(known)
        elif known.naic is None and p.fact("cert:naic").verified:
            known.naic = p.fact("cert:naic").value
        insurer_of[p.letter] = known.letter
    policies = []
    for p, shown in printed:
        line = p.line or coi_checks.OTHER
        keys = [*POLICY_MAP, *((k, labels.get(k, k)) for k in coi_checks.LINE_LIMITS[line])]
        policies.append(CertificatePolicy(
            letter=p.letter, insurer=insurer_of.get(p.letter), document_id=p.document_id, line=line,
            line_label=coi_checks.LINE_LABELS[line], wording=shown,
            fields=[_field(cells, p.letter, k, label) for k, label in keys]))
    block = CertificateBlock(
        layout=LAYOUT, certificate_date=request.certificate_date.isoformat(), holder_name=params.holder_name,
        holder_address=params.holder_address, description=description,
        producer=_field(cells, producer.letter, PRODUCER_MAP[0], PRODUCER_MAP[1]) if producer else
        CertificateField(key=PRODUCER_MAP[0], label=PRODUCER_MAP[1], cell=_printed_cell(None)),
        insured=[_field(cells, first.letter, k, label) if first else
                 CertificateField(key=k, label=label, cell=_printed_cell(None)) for k, label in INSURED_MAP],
        insurers=insurers, policies=policies)

    # ---- edits carry over (D12); a removed edit restores the extracted value (D13)
    for (col, key), old in _previous_edits(base).items():
        try:
            target = block.find_field(key, col)
        except KeyError:
            continue  # the row no longer prints (its policy was removed)
        claim = coi_checks.edited_claim(col, key, target.label, old.value, target.cell.value)
        if status(claim) == "removed":
            continue
        target.cell.original_value = target.cell.value
        target.cell.original_passage_ids = list(target.cell.passage_ids)
        target.cell.value, target.cell.passage_ids, target.cell.edited = old.value, [], True
        claims.append(claim)
        statuses[claim.key] = status(claim)

    notices = [Notice(kind="info", message=n.message) for n in joined.notices]
    if unread:
        names = ", ".join(f"{letter} ({name})" for letter, name in inputs.unread)
        notices.append(Notice(kind="documents_failed", retryable=True, message=(
            f"{unread} document{'s' if unread != 1 else ''} could not be read ({names}); claims that need "
            "them are not supported.")))
    if not joined.policies:
        notices.append(Notice(kind="info", message="No policy declarations were found, so nothing can be "
                                                   "certified yet. Add a declarations page."))
    content = ArtifactContent(sections=[
        Section(id=CERTIFICATE_SECTION, number="1", title="Certificate", blocks=[block]),
        Section(id=CHECKS_SECTION, number="2", title="Checks", blocks=list(claims)),
    ], notices=notices)
    return Analysis(content=content, claims=claims, printed=[p for p, _ in printed], joined=joined,
                    statuses=statuses)


def state_rows(session: Session, run_id: int) -> dict[str, models.DiscrepancyState]:
    return {r.item_id: r for r in session.scalars(select(models.DiscrepancyState)
                                                 .where(models.DiscrepancyState.run_id == run_id))}


def analyse(session: Session, run: models.Run, *, base: ArtifactContent | None = None,
            use_latest: bool = True) -> Analysis:
    if base is None and use_latest and run.artifact is not None:
        base = run.artifact.latest_content()
    return build(run_inputs(run), stored_parameters(run.parameters), state_rows(session, run.id), base=base)


def content_for_run(session: Session, run: models.Run, *, base: ArtifactContent | None = None) -> ArtifactContent:
    """The draft of a certificate run from its stored facts (the runner, retry, Add document)."""
    return analyse(session, run, base=base).content


def rebuild(session: Session, run: models.Run, *, note: str, base: ArtifactContent | None = None
            ) -> models.ArtifactVersion:
    """Store a rebuilt draft as the next version (Remove, an edit, Undo). Needs an artifact."""
    content = content_for_run(session, run, base=base)
    return models.snapshot_version(session, run.artifact, content, note=note[:200])


# --------------------------------------------------------------------- state for the API


def claim_needs_action(block: ClaimBlock, status: str) -> bool:
    """An open claim that is not supported (or needs a sign-off) blocks Issue (D5 claims_open)."""
    return status == "open" and str(block.verdict) != "supported"


@dataclass(frozen=True)
class ClaimCounts:
    total: int
    open: int  # needing action
    supported: int
    overridden: int
    removed: int


def claim_counts(blocks: Sequence[ClaimBlock], rows: Mapping[str, models.DiscrepancyState]) -> ClaimCounts:
    open_ = supported = overridden = removed = 0
    for b in blocks:
        row = rows.get(b.id)
        st = row.status if row else "open"
        if st == "removed":
            removed += 1
        elif st == "resolved":
            overridden += 1
        elif claim_needs_action(b, st):
            open_ += 1
        else:
            supported += 1
    return ClaimCounts(total=len(blocks), open=open_, supported=supported, overridden=overridden, removed=removed)


def in_force_coverage(content: ArtifactContent) -> bool:
    """D13 no_coverage: Issue needs at least one printed policy whose in-force claim is Supported."""
    cert = content.find_certificate()
    if cert is None:
        return False
    supported = {b.policy_letter for b in models.claim_blocks(content)
                 if b.kind == ClaimKind.IN_FORCE and str(b.verdict) == "supported"}
    return any(p.letter in supported for p in cert.policies)


__all__ = ["Analysis", "CertificateParameters", "ClaimCounts", "RunInputs", "analyse", "build", "claim_counts",
           "claim_needs_action", "content_for_run", "in_force_coverage", "rebuild", "run_inputs", "state_rows",
           "stored_parameters"]
