r"""Merge verified window candidates into one fact per field (T14). Deterministic code only.

A long document is extracted window by window (pipeline.windows), so one
field can come back several times. Every candidate has already been verified
against the FULL document (pipeline.verify). This module picks what the cell
shows, per field:

    candidates ──► tier: VERIFIED if any, else SCAN_READ (lower tier, D5)
        │            none cited ──► NOT_FOUND if any candidate had a value
        │                           ("Couldn't verify", M1), else ABSENT
        ▼
    group the tier by normalized value (verify's tokens; numbers by value,
    so "$10,000" equals "10000", but "$10,000 per occurrence" does not)
        │
        ├─ one group ──► earliest page wins; the same value on other pages
        │                becomes extra passages (more chips on the cell)
        │
        └─ several groups (conflicting values)
              endorsement rule (below) picks one ──► that value; the others
              │                                       are kept as `superseded`
              │                                       (logged, not shown)
              └─ ambiguous ──► earliest value, and every other value is kept
                               in `conflicts`; compose puts a
                               conflicting_values notice on the cell that
                               names all of them (never picked silently)

Endorsement rule (kept simple on purpose):
1. A candidate "amends" when its quote or matched source text contains
   endorsement wording (AMEND_RE: endorsement, amend / amended / amendment /
   amendatory, deleted and replaced, replaced with / by, supersedes). If no
   candidate amends by its own quote, the page text is used instead (the
   page carries an endorsement heading).
2. The amending candidates must all state one value, and that value must
   first appear on a later page than every other value (an endorsement comes
   after the declarations it changes).
3. Otherwise the conflict is ambiguous.

Extension point: a page-scoring pass that skips windows with no likely
facts would plug in before extraction (pipeline.windows.select_windows);
it is off for now.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from backend.pipeline import verify
from backend.schemas import EvidenceState, FactField

log = logging.getLogger("brokerdocs.merge")

AMEND_RE = re.compile(
    r"\bendorsements?\b|\bamend(?:s|ed|ment|ments|atory)?\b|\bdeleted\s+and\s+replaced\b"
    r"|\breplaced\s+(?:with|by)\b|\bsupersed(?:e|es|ed|ing)\b",
    re.I,
)


@dataclass(frozen=True)
class MergedFact(verify.VerifyResult):
    """The fact a cell shows, plus what the other windows found for the field."""

    extras: tuple[verify.VerifyResult, ...] = ()  # same value on other pages (extra passages)
    conflicts: tuple[verify.VerifyResult, ...] = ()  # other values, unresolved (conflicting_values)
    superseded: tuple[verify.VerifyResult, ...] = ()  # other values an endorsement replaced
    resolution: str = "single"  # absent | not_found | single | same_value | endorsement | ambiguous
    candidates: int = 0  # how many window candidates had a value


def value_key(value: str | None) -> tuple[str, ...]:
    """Normalized value for grouping: verify's tokens, numbers compared by value."""
    out = []
    for tok in verify._tokens_of(value or ""):
        num = verify._number(tok)
        out.append(f"#{num[0]:g}{num[1]}" if num is not None else tok)
    return tuple(out)


def _page(r: verify.VerifyResult) -> int:
    return r.page_no if r.page_no is not None else 10**9


def _amends_by_quote(r: verify.VerifyResult) -> bool:
    return any(AMEND_RE.search(t) for t in (r.quote, r.matched_text) if t)


def _from(r: verify.VerifyResult, **kw) -> MergedFact:
    base = {f: getattr(r, f) for f in verify.VerifyResult.__dataclass_fields__}
    base.update(kw)
    return MergedFact(**base)


def _resolve_endorsement(groups: list[list[verify.VerifyResult]],
                         page_text: Mapping[int, str]) -> int | None:
    """Index of the group an endorsement makes effective, or None when ambiguous."""
    amending = [i for i, g in enumerate(groups) if any(_amends_by_quote(r) for r in g)]
    if not amending:
        amending = [i for i, g in enumerate(groups)
                    if any(r.page_no is not None and AMEND_RE.search(page_text.get(r.page_no, "")) for r in g)]
    if len(amending) != 1:
        return None
    win = amending[0]
    first = min(_page(r) for r in groups[win])
    if all(min(_page(r) for r in g) < first for i, g in enumerate(groups) if i != win):
        return win
    return None


def merge_field(fdef: FactField | str, candidates: Sequence[verify.VerifyResult],
                page_text: Mapping[int, str] | None = None) -> MergedFact:
    """One fact for one field from all of its window candidates (already verified)."""
    key = fdef.key if isinstance(fdef, FactField) else str(fdef)
    page_text = page_text or {}
    valued = [c for c in candidates if c.value is not None and str(c.value).strip()]
    n = len(valued)
    tier = [c for c in valued if c.state == EvidenceState.VERIFIED] or \
        [c for c in valued if c.state == EvidenceState.SCAN_READ]
    if not tier:
        rejected = [c for c in valued if c.state == EvidenceState.NOT_FOUND]
        if rejected:
            return _from(min(rejected, key=lambda c: (c.cited_page or 10**9)), resolution="not_found",
                         candidates=n)
        return MergedFact(field=key, value=None, state=EvidenceState.ABSENT, cited_page=None, page_no=None,
                          quote=None, matched_text=None, method=None, score=0.0, reason="no_value",
                          resolution="absent", candidates=n)

    groups: dict[tuple[str, ...], list[verify.VerifyResult]] = {}
    for c in sorted(tier, key=_page):  # stable: window order breaks page ties
        groups.setdefault(value_key(c.value), []).append(c)
    ordered = sorted(groups.values(), key=lambda g: _page(g[0]))

    def with_extras(group: list[verify.VerifyResult], **kw) -> MergedFact:
        primary, seen, extras = group[0], {group[0].page_no}, []
        for r in group[1:]:
            if r.page_no not in seen:
                seen.add(r.page_no)
                extras.append(r)
        resolution = kw.pop("resolution", "same_value" if extras else "single")
        return _from(primary, extras=tuple(extras), resolution=resolution, candidates=n, **kw)

    if len(ordered) == 1:
        return with_extras(ordered[0])
    win = _resolve_endorsement(ordered, page_text)
    if win is not None:
        others = tuple(g[0] for i, g in enumerate(ordered) if i != win)
        log.info("merge: %s endorsement on page %s replaces %s", key, ordered[win][0].page_no,
                 [(r.value, r.page_no) for r in others])
        return with_extras(ordered[win], superseded=others, resolution="endorsement")
    conflicts = tuple(g[0] for g in ordered[1:])
    log.info("merge: %s has conflicting values %s; showing the earliest", key,
             [(r.value, r.page_no) for r in (ordered[0][0], *conflicts)])
    return with_extras(ordered[0], conflicts=conflicts, resolution="ambiguous")


def merge_document(fields: Sequence[FactField | str], candidates: Iterable[verify.VerifyResult],
                   page_text: Mapping[int, str] | None = None) -> list[MergedFact]:
    """One MergedFact per field, in field order."""
    by_field: dict[str, list[verify.VerifyResult]] = {}
    for c in candidates:
        by_field.setdefault(c.field, []).append(c)
    out = []
    for f in fields:
        k = f.key if isinstance(f, FactField) else str(f)
        out.append(merge_field(f, by_field.get(k, []), page_text))
    return out


def conflict_message(label: str, shown: tuple[str | None, int | None],
                     conflicts: Sequence[tuple[str | None, int | None]]) -> str:
    """Text of the conflicting_values notice: every (value, page), the shown (earliest) one first."""
    def one(v, p):
        return f"{v} (page {p})" if p is not None else f"{v}"
    values = [one(*shown), *(one(v, p) for v, p in conflicts)]
    listed = ", ".join(values[:-1]) + f" and {values[-1]}"
    return (f"This document states different values for {label}: {listed}. "
            "The earliest is shown; check which one applies.")


__all__ = ["AMEND_RE", "MergedFact", "conflict_message", "merge_document", "merge_field", "value_key"]
