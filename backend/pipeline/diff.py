r"""Policy Check diff (slice 3, T4): renewal-centered discrepancies, deterministic code only.

Input: per role (expiring, quote, renewal, ams) the facts of its one document, keyed by
canonical key (schemas.POLICY_CHECK_FIELDS keys, form:<number> keys, anything else lands in
the Other bucket). Output: one Discrepancy per key that needs the broker's attention, with
a slot per role, a severity from the table below, and a title and explanation built in code
from the slots (D14: no model call, so reruns are byte-identical).

Slot state of one role for one key:

    role not uploaded ─────────────────────────────► no_document   (shown N/A)
    document failed / budget ran out (read=False) ──► not_read      (never N/A, D11)
    fact FAILED ────────────────────────────────────► not_read
    no fact, ABSENT, or no value ───────────────────► not_stated    (shown N/A)
    VERIFIED or SCAN_READ with a value ─────────────► value         (cited, passage ids)
    CONFLICTING, NOT_FOUND, PENDING ────────────────► needs_review  (no value shown; the
                                                       conflict list when CONFLICTING, D15)

Compare rule for one key (D10 renewal-centered, D15 needs review):

    expiring or renewal slot needs_review / not_read? ──yes──► Needs review item, severity Low,
        │no                                                   never "removed", never equal
        ▼
    expiring vs renewal (skipped for NOT_COMPARED_WITH_EXPIRING: the term always moves)
        both values, differ after normalization ──► grade by fact type (increase, decrease,
        │                                           decrease_over_10pct, changed, edition_changed)
        expiring value, renewal not stated ───────► removed
        expiring not stated, renewal value ───────► added
        ▼
    quote and AMS vs renewal: only a slot with a value counts (absent in quote or AMS never
    counts; needs_review and not_read never count); differs, or renewal does not state it
        ──► mismatch, flat High
        ▼
    no finding ──► no item (equal after normalization is suppressed)
    else one item: severity = the highest of its findings; change = the expiring grade if
    any, else "mismatch"

Normalization by fact type: money and percent amounts by value (Decimal: "$300,000.00",
"300,000" and "$300K" are equal; "$1 million" is 1,000,000; a percent never equals a
money amount, and a value with two amounts compares by its text); dates in a term as ISO
dates; text by case, spacing and punctuation (addresses also expand street abbreviations);
forms by edition (merge.form_edition), so an edition change aligns on the form number and
is one change, not an add plus a remove.

Severity table (SEVERITY_TABLE, starting values to tune against fixtures, spec section 3):
limit down more than 10% Critical (exactly 10% is High), other limit change High, limit
removed High; deductible increase or added Critical; discount dropped High; form edition
change Medium; quote or AMS mismatch flat High; Needs review Low; the Other bucket Low.

Scope: single-location policies (D17). Canonical keys such as coverage_a_limit carry no
location or schedule, so a multi-location policy cannot align (TODOS.md).
Output order: severity, then key. to_json gives the canonical bytes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction
from typing import Any

from backend.pipeline.merge import form_edition, split_form_value
from backend.schemas import (
    CITED_STATES,
    FORM_KEY_PREFIX,
    POLICY_CHECK_FIELDS,
    Cell,
    ConflictValue,
    EvidenceState,
    FactField,
    is_form_key,
    scalar_fields,
)

ROLES = ("expiring", "quote", "renewal", "ams")  # slot order
REQUIRED_ROLES = ("expiring", "renewal")
OPTIONAL_ROLES = ("quote", "ams")
_ROLE_WORD = {"expiring": "expiring", "quote": "quote", "renewal": "renewal", "ams": "AMS"}


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_RANK = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}


class ItemState(StrEnum):
    DISCREPANCY = "discrepancy"
    NEEDS_REVIEW = "needs_review"  # D15


class SlotState(StrEnum):
    VALUE = "value"
    NOT_STATED = "not_stated"
    NO_DOCUMENT = "no_document"
    NOT_READ = "not_read"
    NEEDS_REVIEW = "needs_review"


# --------------------------------------------------------------------- fact types and the table

FACT_TYPES: dict[str, str] = {
    "policy_number": "identifier",
    "policy_term": "term",
    "named_insured": "text",
    "insured_address": "address",
    "coverage_a_limit": "limit",
    "coverage_b_limit": "limit",
    "coverage_c_limit": "limit",
    "coverage_d_limit": "limit",
    "coverage_e_limit": "limit",
    "coverage_f_limit": "limit",
    "deductible_all_peril": "deductible",
    "deductible_wind_hail": "deductible",
    "deductible_hurricane": "deductible",
    "total_premium": "premium",
    "discount_multi_policy": "discount",
    "discount_protective_device": "discount",
    "discount_claims_free": "discount",
    "discount_loyalty": "discount",
}
NUMERIC_TYPES = frozenset({"limit", "deductible", "premium", "discount", "other"})
# The policy term always moves forward at renewal: compared with quote and AMS only.
NOT_COMPARED_WITH_EXPIRING = frozenset({"policy_term"})
LIMIT_DROP_CRITICAL = Fraction(1, 10)  # "down more than 10%" is Critical; exactly 10% is High

C, H, M, L = Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW
SEVERITY_TABLE: dict[str, dict[str, Severity]] = {
    "limit": {"decrease_over_10pct": C, "decrease": H, "increase": H, "changed": H, "removed": H, "added": M},
    "deductible": {"increase": C, "added": C, "decrease": M, "changed": H, "removed": M},
    "premium": {"increase": M, "decrease": L, "changed": M, "removed": M, "added": L},
    "discount": {"removed": H, "increase": L, "decrease": L, "changed": L, "added": L},
    "form": {"edition_changed": M, "changed": M, "removed": H, "added": M},
    "text": {"changed": M, "removed": M, "added": L},
    "address": {"changed": M, "removed": M, "added": L},
    "identifier": {"changed": L, "removed": L, "added": L},
    "term": {},  # never compared with expiring
    "other": {"increase": L, "decrease": L, "changed": L, "removed": L, "added": L},  # spec section 8
}
UNIVERSAL_SEVERITY: dict[str, Severity] = {"mismatch": H, "needs_review": L}  # D10 flat High, D15
CHANGES_BY_TYPE: dict[str, tuple[str, ...]] = {t: tuple(v) for t, v in SEVERITY_TABLE.items()}
_PUBLIC_CHANGE = {"decrease_over_10pct": "decrease"}


def fact_type(key: str) -> str:
    if is_form_key(key):
        return "form"
    return FACT_TYPES.get(key, "other")


def severity(ftype: str, change: str) -> Severity:
    if change in UNIVERSAL_SEVERITY:
        return UNIVERSAL_SEVERITY[change]
    return SEVERITY_TABLE[ftype][change]


# --------------------------------------------------------------------- input


@dataclass(frozen=True)
class RoleFact:
    """One role's fact for one key: what a report cell holds."""

    value: str | None
    state: EvidenceState
    passage_ids: tuple[int, ...] = ()
    fact_ids: tuple[int, ...] = ()
    conflicts: tuple[ConflictValue, ...] = ()  # state CONFLICTING: every stated value

    @classmethod
    def from_cell(cls, cell: Cell) -> RoleFact:
        """A report cell as diff input. A user-edited cell compares its extracted original (the
        diff compares documents, and only the original is cited)."""
        value = cell.original_value if cell.edited else cell.value
        pids = cell.original_passage_ids if cell.edited else cell.passage_ids
        return cls(value=value, state=cell.state or EvidenceState.ABSENT, passage_ids=tuple(pids),
                   fact_ids=tuple(cell.fact_ids), conflicts=tuple(cell.conflicts))


@dataclass(frozen=True)
class RoleInput:
    """One role's document. read=False: it failed or its budget ran out (D11 "Not read")."""

    facts: Mapping[str, RoleFact]
    read: bool = True


# --------------------------------------------------------------------- output


@dataclass(frozen=True)
class Slot:
    role: str
    state: SlotState
    value: str | None = None  # only for state value: a verified (or scan-read) value
    evidence_state: EvidenceState | None = None
    passage_ids: tuple[int, ...] = ()
    fact_ids: tuple[int, ...] = ()
    conflicts: tuple[ConflictValue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "state": str(self.state),
            "value": self.value,
            "evidence_state": str(self.evidence_state) if self.evidence_state is not None else None,
            "passage_ids": list(self.passage_ids),
            "fact_ids": list(self.fact_ids),
            "conflicts": [{"value": c.value, "page_no": c.page_no, "passage_id": c.passage_id}
                          for c in self.conflicts],
        }


@dataclass(frozen=True)
class Discrepancy:
    id: str  # derived from the key: stable across regeneration
    key: str
    label: str
    fact_type: str
    state: ItemState
    change: str  # increase | decrease | changed | edition_changed | removed | added | mismatch | needs_review
    severity: Severity
    title: str
    explanation: str
    slots: tuple[Slot, ...]
    lob: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "key": self.key, "label": self.label, "fact_type": self.fact_type,
            "state": str(self.state), "change": self.change, "severity": str(self.severity),
            "title": self.title, "explanation": self.explanation, "lob": self.lob,
            "slots": [s.to_dict() for s in self.slots],
        }


def item_id(key: str) -> str:
    return "disc-" + re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def to_json(items: Iterable[Discrepancy]) -> str:
    """Canonical bytes of a discrepancy list (same input, same bytes)."""
    return json.dumps([i.to_dict() for i in items], sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------- normalization

_AMOUNT_RE = re.compile(
    r"(?<![\w.,])(-|\()?\s*(?:usd\s*)?(\$)?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|percent\b)?"
    r"(?:\s*(k|m|mm|thousand|million)\b)?",
    re.I,
)
_MULT = {"k": Decimal(1_000), "thousand": Decimal(1_000), "m": Decimal(1_000_000), "mm": Decimal(1_000_000),
         "million": Decimal(1_000_000)}


@dataclass(frozen=True)
class Amount:
    value: Decimal
    percent: bool
    money: bool  # printed with a $ sign


def parse_amount(value: str | None) -> Amount | None:
    """The one money or percent amount a value states, or None when it states none or several."""
    found = list(_AMOUNT_RE.finditer(value or ""))
    if len(found) != 1:
        return None
    mo = found[0]
    try:
        num = Decimal(mo.group(3).replace(",", "") + (mo.group(4) or ""))
    except InvalidOperation:  # pragma: no cover - the pattern only admits digits
        return None
    if mo.group(6):
        num *= _MULT[mo.group(6).lower()]
    if mo.group(1):
        num = -num
    return Amount(num, percent=bool(mo.group(5)), money=bool(mo.group(2)))


_ADDRESS_WORDS = {"st": "street", "ave": "avenue", "av": "avenue", "rd": "road", "dr": "drive", "ln": "lane",
                  "blvd": "boulevard", "ct": "court", "hwy": "highway", "pkwy": "parkway", "pl": "place",
                  "cir": "circle", "ter": "terrace", "apt": "apartment", "ste": "suite", "n": "north",
                  "s": "south", "e": "east", "w": "west"}


def normalize_text(value: str | None, *, address: bool = False) -> str:
    words = re.findall(r"[a-z0-9]+", (value or "").replace("&", " and ").casefold())
    if address:
        words = [_ADDRESS_WORDS.get(w, w) for w in words]
    return " ".join(words)


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_DATE_RE = re.compile(
    r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"
    r"|\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b"
    r"|\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.I,
)


def parse_dates(value: str | None) -> tuple[str, ...]:
    """Every date a value states, as ISO dates, in order."""
    out = []
    for mo in _DATE_RE.finditer(value or ""):
        if mo.group(1):
            y, m, d = int(mo.group(1)), int(mo.group(2)), int(mo.group(3))
        elif mo.group(4):
            m, d, y = int(mo.group(4)), int(mo.group(5)), int(mo.group(6))
            y = y + 2000 if y < 100 else y
        else:
            m = _MONTHS.get(mo.group(7).lower())
            if m is None:
                continue
            d, y = int(mo.group(8)), int(mo.group(9))
        if 1 <= m <= 12 and 1 <= d <= 31:
            out.append(f"{y:04d}-{m:02d}-{d:02d}")
    return tuple(out)


@dataclass(frozen=True)
class _Grade:
    change: str  # a SEVERITY_TABLE change for the fact type
    expiring: Amount | None = None
    renewal: Amount | None = None
    editions: tuple[str, str] | None = None


def _compare(ftype: str, key: str, a: str, b: str) -> _Grade | None:
    """None when a (the baseline: expiring, or the renewal for a mismatch) and b are one value
    after normalization, else how b differs from a."""
    if ftype == "form":
        ea, eb = form_edition(key, a), form_edition(key, b)
        if ea and eb:
            return None if ea == eb else _Grade("edition_changed", editions=(ea, eb))
        return None if normalize_text(a).replace(" ", "") == normalize_text(b).replace(" ", "") else _Grade("changed")
    if ftype == "term":
        da, db = parse_dates(a), parse_dates(b)
        if da and db:
            return None if da == db else _Grade("changed")
    if ftype in NUMERIC_TYPES:
        na, nb = parse_amount(a), parse_amount(b)
        if na is not None and nb is not None:
            if na.percent != nb.percent:
                return _Grade("changed", na, nb)
            va, vb = (abs(na.value), abs(nb.value)) if ftype == "discount" else (na.value, nb.value)
            if va == vb:
                return None
            if vb > va:
                return _Grade("increase", na, nb)
            over = (ftype == "limit" and va > 0 and not na.percent
                    and Fraction(va - vb) / Fraction(va) > LIMIT_DROP_CRITICAL)
            return _Grade("decrease_over_10pct" if over else "decrease", na, nb)
    same = normalize_text(a, address=ftype == "address") == normalize_text(b, address=ftype == "address")
    return None if same else _Grade("changed")


# --------------------------------------------------------------------- slots

_UNCERTAIN = frozenset({EvidenceState.CONFLICTING, EvidenceState.NOT_FOUND, EvidenceState.PENDING})


def _slot(role: str, ri: RoleInput | None, key: str) -> Slot:
    if ri is None:
        return Slot(role, SlotState.NO_DOCUMENT)
    if not ri.read:
        return Slot(role, SlotState.NOT_READ)
    rf = ri.facts.get(key)
    if rf is None:
        return Slot(role, SlotState.NOT_STATED)
    state = EvidenceState(rf.state)
    if state == EvidenceState.FAILED:
        return Slot(role, SlotState.NOT_READ, evidence_state=state)
    if state in CITED_STATES and rf.value is not None and rf.value.strip():
        return Slot(role, SlotState.VALUE, value=rf.value, evidence_state=state,
                    passage_ids=tuple(rf.passage_ids), fact_ids=tuple(rf.fact_ids))
    if state in _UNCERTAIN:
        conflicting = state == EvidenceState.CONFLICTING
        return Slot(role, SlotState.NEEDS_REVIEW, evidence_state=state,
                    passage_ids=tuple(rf.passage_ids) if conflicting else (), fact_ids=tuple(rf.fact_ids),
                    conflicts=tuple(rf.conflicts) if conflicting else ())
    return Slot(role, SlotState.NOT_STATED, evidence_state=state)


# --------------------------------------------------------------------- text (D14)


def _fmt_number(d: Decimal) -> str:
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return f"{d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _and(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _pct(g: _Grade) -> str | None:
    """Change as a percent of the expiring amount, one decimal, for money amounts only."""
    a, b = g.expiring, g.renewal
    if a is None or b is None or a.percent or a.value == 0:
        return None
    va, vb = (abs(a.value), abs(b.value))
    if va == 0:  # pragma: no cover - guarded above
        return None
    pct = (abs(vb - va) / va * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{pct}"


def _delta(g: _Grade, ftype: str) -> str:
    a, b = g.expiring, g.renewal
    va, vb = (abs(a.value), abs(b.value)) if ftype == "discount" else (a.value, b.value)
    d = abs(vb - va)
    if a.percent:
        return f"{_fmt_number(d)} percentage point{'' if d == 1 else 's'}"
    return ("$" if a.money or b.money else "") + _fmt_number(d)


def _expiring_text(label: str, ftype: str, g: _Grade, e: Slot, r: Slot) -> tuple[str, str]:
    ch = g.change
    if ch in ("increase", "decrease", "decrease_over_10pct"):
        word = "increased" if ch == "increase" else "decreased"
        pct = _pct(g)
        title = f"{label} {word}" + (f" by {pct}%" if pct else "")
        noun = "an increase" if ch == "increase" else "a decrease"
        tail = f" ({pct}%)" if pct else ""
        return title, f"Expiring states {e.value}; renewal states {r.value}, {noun} of {_delta(g, ftype)}{tail}."
    if ch == "edition_changed":
        ea, eb = g.editions
        return (f"{label} edition changed",
                f"Expiring states {e.value}; renewal states {r.value} (edition {ea} to {eb}).")
    if ch == "removed":
        return f"{label} not stated in renewal", f"Expiring states {e.value}; renewal does not state it."
    if ch == "added":
        return f"{label} added at renewal", f"Renewal states {r.value}; expiring does not state it."
    return f"{label} changed", f"Expiring states {e.value}; renewal states {r.value}."


def _mismatch_text(r: Slot, mismatches: Sequence[Slot], *, after_expiring: bool) -> str:
    parts = [f"{_ROLE_WORD[s.role]} states {s.value}" for s in mismatches]
    if not after_expiring:
        if r.state == SlotState.VALUE:
            parts.insert(0, f"renewal states {r.value}")
        else:
            parts.append("renewal does not state it")
    return _cap("; ".join(parts)) + "."


def _review_text(uncertain: Sequence[Slot]) -> str:
    out = []
    for s in uncertain:
        role = _ROLE_WORD[s.role]
        if s.state == SlotState.NOT_READ:
            out.append(f"The {role} value was not read.")
        elif s.conflicts:
            vals = [(f"{c.value} (page {c.page_no})" if c.page_no is not None else f"{c.value}")
                    for c in s.conflicts if c.value is not None]
            out.append(f"The {role} document states different values: {_and(vals)}." if vals
                       else f"The {role} document states different values.")
        else:
            out.append(f"The {role} value could not be verified against its document.")
    out.append("Check the source before relying on this comparison.")
    return " ".join(out)


def _label(key: str, labels: Mapping[str, str], slots: Mapping[str, Slot]) -> str:
    if key in labels:
        return labels[key]
    if is_form_key(key):
        for role in ("renewal", "expiring", "quote", "ams"):
            s = slots[role]
            split = split_form_value(key, s.value) if s.state == SlotState.VALUE else None
            if split and split[0]:
                return f"Form {split[0]}"
        return f"Form {key[len(FORM_KEY_PREFIX):]}"
    return key.replace("_", " ").strip().title() or key


# --------------------------------------------------------------------- the diff


def _check_roles(roles: Mapping[str, RoleInput]) -> None:
    unknown = sorted(set(roles) - set(ROLES))
    if unknown:
        raise ValueError(f"unknown roles {unknown}; expected some of {list(ROLES)}")
    for role in REQUIRED_ROLES:
        if role not in roles:
            raise ValueError(f"the {role} document is required")
        if not roles[role].read:
            raise ValueError(f"the {role} document was not read; the run fails (D11)")


def _item(key: str, labels: Mapping[str, str], roles: Mapping[str, RoleInput], lob: str | None) -> Discrepancy | None:
    ftype = fact_type(key)
    slots = {role: _slot(role, roles.get(role), key) for role in ROLES}
    e, r = slots["expiring"], slots["renewal"]
    label = _label(key, labels, slots)
    ordered = tuple(slots[role] for role in ROLES)

    def make(state: ItemState, change: str, sev: Severity, title: str, text: str) -> Discrepancy:
        return Discrepancy(id=item_id(key), key=key, label=label, fact_type=ftype, state=state, change=change,
                           severity=sev, title=title, explanation=text, slots=ordered, lob=lob)

    compare_e = key not in NOT_COMPARED_WITH_EXPIRING
    review = (SlotState.NEEDS_REVIEW, SlotState.NOT_READ)
    uncertain = [s for s in ((e,) if compare_e else ()) + (r,) if s.state in review]
    if uncertain:  # D15: never removed, never equal
        return make(ItemState.NEEDS_REVIEW, "needs_review", severity(ftype, "needs_review"),
                    f"{label} needs review", _review_text(uncertain))

    grade: _Grade | None = None
    if compare_e:
        if e.state == SlotState.VALUE and r.state == SlotState.VALUE:
            grade = _compare(ftype, key, e.value, r.value)
        elif e.state == SlotState.VALUE:
            grade = _Grade("removed")
        elif r.state == SlotState.VALUE:
            grade = _Grade("added")
    mismatches = [s for s in (slots[x] for x in ROLES if x in OPTIONAL_ROLES)
                  if s.state == SlotState.VALUE
                  and (r.state != SlotState.VALUE or _compare(ftype, key, r.value, s.value) is not None)]
    if grade is None and not mismatches:
        return None

    severities = ([severity(ftype, grade.change)] if grade else []) + [severity(ftype, "mismatch")] * len(mismatches)
    sev = min(severities, key=SEVERITY_RANK.__getitem__)
    if grade is not None:
        title, text = _expiring_text(label, ftype, grade, e, r)
        if mismatches:
            text += " " + _mismatch_text(r, mismatches, after_expiring=True)
        change = _PUBLIC_CHANGE.get(grade.change, grade.change)
    else:
        title = f"{label} differs from {_and([_ROLE_WORD[s.role] for s in mismatches])}"
        text = _mismatch_text(r, mismatches, after_expiring=False)
        change = "mismatch"
    return make(ItemState.DISCREPANCY, change, sev, title, text)


def diff(roles: Mapping[str, RoleInput], fields: Sequence[FactField] = POLICY_CHECK_FIELDS, *,
         lob: str | None = None) -> list[Discrepancy]:
    """Discrepancies for one policy check, ordered by severity then key. Pure: same input, same
    output (to_json bytes too), whatever the order of the mappings.

    Raises ValueError when a role is unknown, or expiring or renewal is missing or not read
    (D11: a failed required document fails the run before the diff)."""
    _check_roles(roles)
    labels = {f.key: f.label for f in scalar_fields(fields)}
    keys = set(labels)
    for ri in roles.values():
        keys.update(k for k in ri.facts if k)
    items = [it for it in (_item(k, labels, roles, lob) for k in sorted(keys)) if it is not None]
    return sorted(items, key=lambda i: (SEVERITY_RANK[i.severity], i.key))


__all__ = ["CHANGES_BY_TYPE", "FACT_TYPES", "LIMIT_DROP_CRITICAL", "NOT_COMPARED_WITH_EXPIRING", "OPTIONAL_ROLES",
           "REQUIRED_ROLES", "ROLES", "SEVERITY_TABLE", "Amount", "Discrepancy", "Fraction", "ItemState", "RoleFact",
           "RoleInput", "Severity", "Slot", "SlotState", "diff", "fact_type", "item_id", "normalize_text",
           "parse_amount", "parse_dates", "severity", "to_json"]
