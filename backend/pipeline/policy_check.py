r"""Policy Check report (slice 3 integration): verified facts per role -> diff -> DiscrepancyBlocks.

Pure code, no model call (D14), so the same facts give a byte-identical report, and
Regenerate rebuilds it for free. The runner calls `build_content` instead of compose.

    run facts ──► compose cells (compose._table_row: the same evidence-state rules as every
                  report table, conflicts and chips included), one row per key: the scalar
                  fields plus every form:<n> key a document stated
              ──► role inputs: slot_states (runner) decides per role
                      absent   ──► no input              (diff: no_document, shown N/A)
                      not_read ──► RoleInput(read=False) (diff: not_read, "Not read", D11)
                      read     ──► RoleFact.from_cell per key of the role's document
              ──► diff.diff (D10 renewal-centered, D15 needs review, severity table)
              ──► one DiscrepancyBlock per item (slot rules in schemas.py):
                      no_document  ──► role left out of `slots`        (N/A)
                      not_stated   ──► Cell state absent               (N/A)
                      not_read     ──► Cell state failed               (Not read)
                      needs_review ──► Cell state conflicting / not_found, conflicts listed
                      value        ──► Cell value, state, chips
              ──► ArtifactContent: one "Discrepancies" section (a paragraph when the list is
                  empty), run notices (documents failed, budget, AMS fields not compared)

AMS facts (D5): `verify_ams_facts` verifies the key-table facts of an AMS export against its
rendered PDF; two AMS fields that disagree for one key become one CONFLICTING fact (each value
with its own chip), never a guess.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from backend.ams import MappedFacts
from backend.pipeline import compose, diff, merge, verify
from backend.schemas import (
    CITED_STATES,
    ArtifactContent,
    Cell,
    ConflictValue,
    DiscrepancyBlock,
    DiscrepancyItemState,
    EvidenceState,
    FactField,
    Notice,
    ParagraphBlock,
    PolicyRole,
    Section,
    Segment,
    Severity,
    scalar_fields,
)

SECTION_ID = "discrepancies"
SECTION_TITLE = "Discrepancies"
NO_DISCREPANCIES = ("No discrepancies found: every value the renewal states agrees with the other documents "
                    "after normalization.")


# --------------------------------------------------------------------- AMS facts


def verify_ams_facts(mapped: MappedFacts, pages: Sequence[verify.PageLike],
                     config: verify.VerifyConfig = verify.DEFAULT_CONFIG) -> list[verify.VerifyResult]:
    """Verify the AMS key-table facts against the rendered AMS PDF. Thread-pool safe."""
    index = verify.build_index(pages)
    out: list[verify.VerifyResult] = [verify.verify_fact(verify.CandidateFact.from_extracted(f), index, config)
                                      for f in mapped.facts]
    for key, candidates in mapped.conflicts.items():
        checked = [verify.verify_fact(verify.CandidateFact.from_extracted(c), index, config) for c in candidates]
        cited = [r for r in checked if r.state in CITED_STATES]
        state = EvidenceState.CONFLICTING if len({r.value for r in cited}) >= 2 else EvidenceState.NOT_FOUND
        out.append(merge.MergedFact(
            field=key, value=None, state=state, cited_page=None, page_no=None, quote=None, matched_text=None,
            method=None, score=0.0, rects=[], reason="conflicting_values",
            conflicts=tuple(cited) if state == EvidenceState.CONFLICTING else (), resolution="ambiguous",
            candidates=len(candidates)))
    return out


# --------------------------------------------------------------------- role inputs


def report_keys(inp: compose.ComposeInput) -> list[str]:
    """Scalar fields in schema order, then every other key a document has a fact for, sorted."""
    keys = [f.key for f in scalar_fields(inp.fields)]
    known = set(keys)
    keys += sorted({f.field for f in inp.facts if f.field and f.field not in known})
    return keys


def role_inputs(inp: compose.ComposeInput, states: Mapping[str, str]) -> dict[str, diff.RoleInput]:
    """Diff input per role from the run's report cells and its slot states (runner.slot_states)."""
    keys = report_keys(inp)
    rows = {k: compose._table_row(inp, FactField(key=k, label=k)) for k in keys}
    letter = {}
    for col in inp.columns:
        if col.slot in diff.ROLES and not col.failed:
            letter.setdefault(col.slot, col.key)
    out: dict[str, diff.RoleInput] = {}
    for role in diff.ROLES:
        state = states.get(role, "absent")
        if state == "absent":
            continue
        if state != "read" or role not in letter:
            out[role] = diff.RoleInput(facts={}, read=False)
            continue
        col = letter[role]
        out[role] = diff.RoleInput(facts={k: diff.RoleFact.from_cell(rows[k].cells[col]) for k in keys
                                          if col in rows[k].cells})
    return out


# --------------------------------------------------------------------- blocks


def slot_cell(slot: diff.Slot) -> Cell | None:
    """A diff slot as a report cell (schemas.py slot conventions). None: the role is left out (N/A)."""
    S = diff.SlotState
    if slot.state == S.NO_DOCUMENT:
        return None
    fact_ids = list(slot.fact_ids)
    if slot.state == S.NOT_STATED:
        return Cell(state=EvidenceState.ABSENT, fact_ids=fact_ids)
    if slot.state == S.NOT_READ:
        return Cell(state=EvidenceState.FAILED, fact_ids=fact_ids)
    if slot.state == S.NEEDS_REVIEW:
        conflicting = slot.evidence_state == EvidenceState.CONFLICTING and bool(slot.conflicts)
        return Cell(state=EvidenceState.CONFLICTING if conflicting else EvidenceState.NOT_FOUND,
                    passage_ids=list(slot.passage_ids) if conflicting else [], fact_ids=fact_ids,
                    conflicts=[ConflictValue(value=c.value, page_no=c.page_no, passage_id=c.passage_id)
                               for c in slot.conflicts] if conflicting else [])
    return Cell(value=slot.value, state=slot.evidence_state, passage_ids=list(slot.passage_ids), fact_ids=fact_ids)


def to_block(item: diff.Discrepancy) -> DiscrepancyBlock:
    slots: dict[PolicyRole, Cell] = {}
    for s in item.slots:
        cell = slot_cell(s)
        if cell is not None:
            slots[PolicyRole(s.role)] = cell
    return DiscrepancyBlock(
        id=item.id, key=item.key, title=item.title, severity=Severity(str(item.severity)),
        item_state=DiscrepancyItemState(str(item.state)), lob=item.lob,
        prose=[Segment(text=item.explanation)], slots=slots)


def build_content(inp: compose.ComposeInput, states: Mapping[str, str], *,
                  extra_notices: Iterable[Notice] = ()) -> ArtifactContent:
    """The Policy Check report. Raises ValueError when a required role is missing or not read
    (the runner fails such a run before it gets here, D11)."""
    items = diff.diff(role_inputs(inp, states), inp.fields)
    blocks = [to_block(i) for i in items]
    if not blocks:
        blocks = [ParagraphBlock(id="discrepancies-none", segments=[Segment(text=NO_DISCREPANCIES)])]
    section = Section(id=SECTION_ID, number="1", title=SECTION_TITLE, blocks=blocks)
    return ArtifactContent(sections=[section], notices=[*inp.notices, *extra_notices])


def ams_notices(mapped: MappedFacts) -> list[Notice]:
    """Run notices from an AMS export: fields not compared, conflicting fields, replaced characters."""
    return [Notice(kind="info", message=m) for m in mapped.notices]


__all__ = ["NO_DISCREPANCIES", "SECTION_ID", "ams_notices", "build_content", "report_keys", "role_inputs",
           "slot_cell", "to_block", "verify_ams_facts"]
