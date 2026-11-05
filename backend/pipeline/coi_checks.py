r"""Certificate checks (slice 4, C4): deterministic code, no model call (C5).

Inputs are the run's verified facts per document (pipeline.certificate builds them); outputs are
ClaimBlocks, one per claim the certificate makes, each Supported or Not supported with a reason and
the passages it rests on. A value counts only when it is VERIFIED (found in the PDF's own text layer):
read from a scan, couldn't verify, conflicting or not stated all make a dependent claim Not supported
(PRODUCT.md: never guessed).

    documents ──► per document: package policy? (line names several lines) ──► notice, no facts (C7)
        │         declarations? (a cited line of coverage, and, when the document prints
        │           endorsements, a fact only declarations print: insured, period, a limit)
        │                                                   ──► a policy row
        │         endorsements (any read document)           ──► joined below
        ▼
    endorsement join (D7): policy numbers compared upper-case without spaces, dashes, dots or slashes;
        an endorsement with no verified policy number joins the policy of its own document; two policies
        with one normalized number ──► notice, the endorsement joins neither; no match ──► notice
        ▼
    claims
      in_force:<letter>   effective <= certificate date <= expiration, both dates verified
      <wording>:<letter>  one per requested wording per printed policy line it applies to (D14):
                          additional insured, primary and non-contributory: general liability, auto,
                          umbrella; waiver of subrogation: those and workers compensation. Needs a joined
                          endorsement of that type whose form number is verified:
                            scheduled (it names a party) ──► the named party must be the holder
                            blanket                      ──► the cited form is enough
                            a condition ("where required by written contract") ──► Not supported,
                              "Applies only when ...; confirm and give the reason" (override, D14)
      insured             the named insured is verified and the same on every printed policy
                          (case, spacing, dots and commas ignored); cannot be removed (D13)
      description         the broker's text: always needs a sign-off (D11)
      edited:<col>:<key>  a printed value the broker edited: needs a sign-off (D12)

The endorsement type comes from its printed title (cited), else from a small table of ISO form
numbers; never from the model (schemas.CERTIFICATE_FIELDS).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from backend.pipeline import compose
from backend.schemas import (
    CITED_STATES,
    ClaimBlock,
    ClaimKind,
    ClaimVerdict,
    EvidenceState,
    Segment,
)

# --------------------------------------------------------------------- vocabulary

ADDITIONAL_INSURED = "additional_insured"
WAIVER = "waiver_of_subrogation"
PRIMARY_NONCONTRIBUTORY = "primary_noncontributory"
WORDINGS = (ADDITIONAL_INSURED, WAIVER, PRIMARY_NONCONTRIBUTORY)
WORDING_LABELS = {ADDITIONAL_INSURED: "Additional insured", WAIVER: "Waiver of subrogation",
                  PRIMARY_NONCONTRIBUTORY: "Primary and non-contributory"}
AI_BLANKET, AI_SCHEDULED = "additional_insured_blanket", "additional_insured_scheduled"
ENDORSEMENT_TYPES = (AI_BLANKET, AI_SCHEDULED, WAIVER, PRIMARY_NONCONTRIBUTORY)  # classified in code

GENERAL_LIABILITY, AUTO, UMBRELLA, WORKERS_COMP, PROPERTY, OTHER = (
    "general_liability", "auto", "umbrella", "workers_comp", "property", "other")
LINE_LABELS = {GENERAL_LIABILITY: "General liability", AUTO: "Automobile liability",
               UMBRELLA: "Umbrella liability", WORKERS_COMP: "Workers compensation and employers liability",
               PROPERTY: "Property", OTHER: "Other"}
WORDING_LINES = {
    ADDITIONAL_INSURED: frozenset({GENERAL_LIABILITY, AUTO, UMBRELLA}),
    WAIVER: frozenset({GENERAL_LIABILITY, AUTO, UMBRELLA, WORKERS_COMP}),
    PRIMARY_NONCONTRIBUTORY: frozenset({GENERAL_LIABILITY, AUTO, UMBRELLA}),
}

# Limits printed per line (the field map's coverage part), in print order.
LINE_LIMITS: dict[str, tuple[str, ...]] = {
    GENERAL_LIABILITY: ("cert:gl_each_occurrence", "cert:gl_damage_to_rented_premises", "cert:gl_medical_expense",
                        "cert:gl_personal_advertising_injury", "cert:gl_general_aggregate",
                        "cert:gl_products_completed_ops_aggregate"),
    AUTO: ("cert:auto_combined_single_limit",),
    UMBRELLA: ("cert:umbrella_each_occurrence", "cert:umbrella_aggregate"),
    WORKERS_COMP: ("cert:wc_statutory", "cert:wc_el_each_accident", "cert:wc_el_disease_each_employee",
                   "cert:wc_el_disease_policy_limit"),
    PROPERTY: (),
    OTHER: (),
}
# Facts only a declarations page prints. An endorsement form also prints the carrier, the coverage part it
# modifies and the policy number, so those do not tell a declarations document from an endorsement.
DECLARATIONS_ONLY: tuple[str, ...] = (
    "cert:named_insured", "cert:insured_address", "cert:effective_date", "cert:expiration_date",
    *dict.fromkeys(k for keys in LINE_LIMITS.values() for k in keys))

# --------------------------------------------------------------------- normalizers


def normalize_policy_number(value: str | None) -> str:
    """D7: upper case, spaces, dashes, dots and slashes dropped ("GL-1234567" == "gl 1234567")."""
    return re.sub(r"[\s\-./]", "", (value or "").upper())


def normalize_name(value: str | None) -> str:
    """Names compared case and spacing insensitive, dots and commas ignored ("L.L.C." == "LLC")."""
    return " ".join(re.sub(r"[.,]", "", value or "").casefold().split())


_LINE_RULES = (  # (line, pattern); matched spans are removed before the next rule
    (WORKERS_COMP, r"workers'?\s*comp(?:ensation)?(?:\s+(?:and|&)\s+employers'?\s+liability)?|employers'?\s+liability"),
    (UMBRELLA, r"umbrella|excess\s+liability|excess"),
    (AUTO, r"\bauto(?:mobile)?\b|business\s+auto|motor\s+vehicle"),
    (GENERAL_LIABILITY, r"general\s+liability|\bcgl\b"),
    (PROPERTY, r"\bproperty\b|building\s+and\s+personal\s+property"),
)
_PACKAGE = re.compile(r"\bpackage\b|businessowners?|\bbop\b", re.I)


def classify_line(value: str | None) -> tuple[str | None, bool]:
    """(line, package) for a line-of-coverage value. A value naming several lines, or a package or
    businessowners policy, is a package (C7: not supported this slice). An unknown line is "other"."""
    text = (value or "").strip()
    if not text:
        return None, False
    if _PACKAGE.search(text):
        return None, True
    found = []
    rest = text.lower()
    for line, pattern in _LINE_RULES:
        if re.search(pattern, rest, re.I):
            found.append(line)
            rest = re.sub(pattern, " ", rest, flags=re.I)
    if len(found) > 1:
        return None, True
    return (found[0] if found else OTHER), False


def parse_date(value: str | None) -> date | None:
    """A single printed date (the compose date patterns: 01/01/2027, 2027-01-01, January 1, 2027)."""
    text = value or ""
    dates = []
    for rx in compose._DATE_RES:
        for m in rx.finditer(text):
            parts = compose._date_value(m)
            if parts is not None:
                try:
                    dates.append((m.start(), date(*parts)))
                except ValueError:
                    continue
    if len({d for _, d in dates}) != 1:
        return None  # none, or two different dates in one value: not one date
    return dates[0][1]


# --------------------------------------------------------------------- evidence


@dataclass(frozen=True)
class Evidence:
    """One fact's value and evidence state, as a cell shows it (pipeline.certificate)."""

    value: str | None = None
    state: EvidenceState = EvidenceState.ABSENT
    passage_ids: tuple[int, ...] = ()
    conflicts: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        return EvidenceState(self.state) == EvidenceState.VERIFIED and bool(self.value)

    @property
    def cited(self) -> bool:
        return EvidenceState(self.state) in CITED_STATES and bool(self.value)

    @property
    def stated(self) -> bool:
        """The document states something here, verified or not (a condition counts even unverified)."""
        return bool(self.value) or bool(self.conflicts) or EvidenceState(self.state) in (
            EvidenceState.NOT_FOUND, EvidenceState.CONFLICTING)

    def why_not(self, what: str) -> str:
        """Why this value does not support a claim, in plain words."""
        state = EvidenceState(self.state)
        if state == EvidenceState.SCAN_READ:
            return f"{what} is read from a scan, unverified"
        if state == EvidenceState.CONFLICTING:
            return f"{what} has conflicting values ({', '.join(v for v in self.conflicts if v)})"
        if state == EvidenceState.NOT_FOUND:
            return f"{what} could not be verified"
        if state == EvidenceState.FAILED:
            return f"{what} was not read"
        return f"{what} is not stated"

    def basis(self, name: str) -> str:
        return f"{name}={self.value or ''}|{EvidenceState(self.state).value}|{'/'.join(self.conflicts)}"


MISSING = Evidence()

# Public (slice 6, R1): pipeline.compare_rules classifies a Compare quote's printed endorsement wording with these.
PNC_RE = re.compile(r"primary\s+(?:and|&)\s+non\s*-?\s*contributory", re.I)
WAIVER_RE = re.compile(r"waiver|transfer\s+of\s+rights\s+of\s+recovery|right\s+to\s+recover", re.I)
AI_RE = re.compile(r"additional\s+insureds?", re.I)
SCHEDULED_RE = re.compile(r"schedul|designated", re.I)

# ISO forms by number (compact), used only when the title is not cited.
FORM_KINDS: dict[str, frozenset[str]] = {
    "CG2010": frozenset({ADDITIONAL_INSURED}), "CG2026": frozenset({ADDITIONAL_INSURED}),
    "CG2037": frozenset({ADDITIONAL_INSURED}), "CG2011": frozenset({ADDITIONAL_INSURED}),
    "CG2033": frozenset({ADDITIONAL_INSURED}), "CG2038": frozenset({ADDITIONAL_INSURED}),
    "CA2048": frozenset({ADDITIONAL_INSURED}), "CG2404": frozenset({WAIVER}), "WC000313": frozenset({WAIVER}),
    "CA0444": frozenset({WAIVER}), "CG2001": frozenset({PRIMARY_NONCONTRIBUTORY}),
}
SCHEDULED_FORMS = frozenset({"CG2010", "CG2026", "CG2037", "CG2011", "CA2048", "CG2404", "WC000313"})


@dataclass(frozen=True)
class Endorsement:
    document_letter: str
    number: str  # compact form number (the key's part)
    form: Evidence  # "<number> <edition>" as printed
    type: Evidence = MISSING  # the printed title, which names what the endorsement does
    policy_number: Evidence = MISSING
    named_party: Evidence = MISSING
    condition: Evidence = MISSING

    @property
    def label(self) -> str:
        return self.form.value or self.number

    @property
    def kinds(self) -> frozenset[str]:
        if self.type.cited:
            text = self.type.value or ""
            found = set()
            if AI_RE.search(text):
                found.add(ADDITIONAL_INSURED)
            if WAIVER_RE.search(text):
                found.add(WAIVER)
            if PNC_RE.search(text):
                found.add(PRIMARY_NONCONTRIBUTORY)
            if found:
                return frozenset(found)
        return FORM_KINDS.get(self.number, frozenset())

    @property
    def types(self) -> list[str]:
        """ENDORSEMENT_TYPES this endorsement is classified as (from its title, in code)."""
        out = []
        if ADDITIONAL_INSURED in self.kinds:
            out.append(AI_SCHEDULED if self.scheduled else AI_BLANKET)
        out += [k for k in (WAIVER, PRIMARY_NONCONTRIBUTORY) if k in self.kinds]
        return out

    @property
    def scheduled(self) -> bool:
        if self.named_party.stated:
            return True
        if self.type.cited:
            return bool(SCHEDULED_RE.search(self.type.value or ""))
        return self.number in SCHEDULED_FORMS

    def passage_ids(self) -> list[int]:
        ids: list[int] = []
        for ev in (self.form, self.type, self.policy_number, self.named_party, self.condition):
            ids.extend(ev.passage_ids)
        return list(dict.fromkeys(ids))

    def basis(self) -> list[str]:
        return [self.form.basis(f"form:{self.number}"), self.type.basis("type"),
                self.policy_number.basis("policy_number"), self.named_party.basis("named_party"),
                self.condition.basis("condition")]


@dataclass(frozen=True)
class PolicyDoc:
    """One read document: its scalar cert: facts and its endorsements."""

    letter: str
    document_id: int | None
    filename: str
    facts: Mapping[str, Evidence] = field(default_factory=dict)
    endorsements: tuple[Endorsement, ...] = ()

    def fact(self, key: str) -> Evidence:
        return self.facts.get(key, MISSING)

    @property
    def line_info(self) -> tuple[str | None, bool]:
        line = self.fact("cert:line")
        if not line.cited:
            if EvidenceState(line.state) == EvidenceState.CONFLICTING:
                values = {classify_line(v) for v in line.conflicts if v}
                if len(values) > 1 or any(pkg for _, pkg in values):
                    return None, True
            return None, False
        return classify_line(line.value)

    @property
    def line(self) -> str | None:
        return self.line_info[0]

    @property
    def package(self) -> bool:
        return self.line_info[1]

    @property
    def declarations(self) -> bool:
        """Does this document read as a policy's declarations (a policy row on the certificate)?

        It needs a line of coverage. A document that prints endorsements must also state a fact only
        declarations print (named insured, address, policy period or a limit), stated in any state:
        an endorsement form names the coverage part and the policy number it modifies ("This
        endorsement forms part of Policy No. X"), so a line and a number alone do not make it a
        policy. Its endorsements join the policy they name instead (D7)."""
        if self.line is None:
            return False
        if not self.endorsements:
            return True
        return any(self.fact(k).stated for k in DECLARATIONS_ONLY)

    @property
    def policy_number_label(self) -> str:
        pn = self.fact("cert:policy_number")
        return pn.value if pn.cited else f"in document {self.letter}"


@dataclass(frozen=True)
class Request:
    certificate_date: date
    holder_name: str
    wording: tuple[str, ...] = ()
    description: str | None = None


@dataclass(frozen=True)
class CertNotice:
    kind: str  # package | not_used | endorsement_unmatched | endorsement_collision
    message: str
    document_letter: str | None = None


@dataclass
class Joined:
    policies: list[PolicyDoc]  # declarations documents, in document order
    endorsements: dict[str, list[Endorsement]]  # policy letter -> joined endorsements
    notices: list[CertNotice]


def _sentence(text: str) -> str:
    text = text.strip()
    return text[0].upper() + text[1:] + ("" if text.endswith(".") else ".") if text else text


def join(documents: Sequence[PolicyDoc]) -> Joined:
    """Policies, endorsements joined to them (D7), and notices for packages and unmatched forms."""
    notices: list[CertNotice] = []
    policies: list[PolicyDoc] = []
    sources: list[PolicyDoc] = []
    for d in documents:
        if d.package:
            notices.append(CertNotice("package", f"Document {d.letter} ({d.filename}) covers several lines; package "
                                  "policies are not supported yet, so nothing from it is used.", d.letter))
            continue
        if d.declarations:
            policies.append(d)
        sources.append(d)
        if not d.declarations and not d.endorsements:
            notices.append(CertNotice("not_used", f"Document {d.letter} ({d.filename}) shows no declarations and "
                                  "no endorsement a certificate uses.", d.letter))
    by_number: dict[str, list[PolicyDoc]] = {}
    for p in policies:
        pn = p.fact("cert:policy_number")
        if pn.verified:
            by_number.setdefault(normalize_policy_number(pn.value), []).append(p)
    for number, same in by_number.items():
        if len(same) > 1:
            notices.append(CertNotice("endorsement_collision", f"Documents {' and '.join(p.letter for p in same)} "
                                  f"show the same policy number ({same[0].fact('cert:policy_number').value}); "
                                  "endorsements for it join neither.", same[0].letter))
    joined: dict[str, list[Endorsement]] = {p.letter: [] for p in policies}
    own = {p.letter: p for p in policies}
    for d in sources:
        for e in d.endorsements:
            if e.policy_number.verified:
                hits = by_number.get(normalize_policy_number(e.policy_number.value), [])
                if len(hits) == 1:
                    joined[hits[0].letter].append(e)
                elif not hits:
                    notices.append(CertNotice("endorsement_unmatched", f"Endorsement {e.label} (document {d.letter}) "
                                              f"modifies policy {e.policy_number.value}, which is not on this "
                                              "certificate.", d.letter))
                else:
                    notices.append(CertNotice("endorsement_collision", f"Endorsement {e.label} (document {d.letter}) "
                                              f"modifies policy {e.policy_number.value}, which two documents show; it "
                                              "joins neither.", d.letter))
            elif d.letter in own:
                joined[d.letter].append(e)  # no verified number: it modifies its own document's policy
            else:
                notices.append(CertNotice("endorsement_unmatched", f"Endorsement {e.label} (document {d.letter}) does "
                                          "not state a verified policy number, so it joins no policy.", d.letter))
    return Joined(policies=policies, endorsements=joined, notices=notices)


# --------------------------------------------------------------------- claims


def _claim(key: str, kind: ClaimKind, title: str, verdict: ClaimVerdict, reason: str | None,
           passage_ids: Iterable[int], basis: list[str], **extra) -> ClaimBlock:
    ids = list(dict.fromkeys(passage_ids))
    text = reason or title
    return ClaimBlock(id=f"claim-{key.replace(':', '-')}", key=key, kind=kind, title=title, verdict=verdict,
                      reason=reason, prose=[Segment(text=_sentence(text), passage_ids=ids)], basis=basis, **extra)


def in_force_claim(p: PolicyDoc, on: date) -> ClaimBlock:
    eff, exp = p.fact("cert:effective_date"), p.fact("cert:expiration_date")
    title = f"{LINE_LABELS[p.line or OTHER]} policy {p.policy_number_label} in force on {on.isoformat()}"
    basis = [f"date={on.isoformat()}", eff.basis("effective"), exp.basis("expiration")]
    ids = [*eff.passage_ids, *exp.passage_ids]
    problems = [ev.why_not(name) for ev, name in ((eff, "the effective date"), (exp, "the expiration date"))
                if not ev.verified]
    if problems:
        return _claim(f"in_force:{p.letter}", ClaimKind.IN_FORCE, title, ClaimVerdict.NOT_SUPPORTED,
                      "; ".join(problems), ids, basis, policy_letter=p.letter)
    start, end = parse_date(eff.value), parse_date(exp.value)
    if start is None or end is None:
        bad = "the effective date" if start is None else "the expiration date"
        return _claim(f"in_force:{p.letter}", ClaimKind.IN_FORCE, title, ClaimVerdict.NOT_SUPPORTED,
                      f"{bad} could not be read as one date", ids, basis, policy_letter=p.letter)
    if on < start:
        reason = f"the policy starts on {eff.value}, after the certificate date"
    elif on > end:
        reason = f"the policy expired on {exp.value}, before the certificate date"
    else:
        return _claim(f"in_force:{p.letter}", ClaimKind.IN_FORCE, title, ClaimVerdict.SUPPORTED,
                      f"in force from {eff.value} to {exp.value}", ids, basis, policy_letter=p.letter)
    return _claim(f"in_force:{p.letter}", ClaimKind.IN_FORCE, title, ClaimVerdict.NOT_SUPPORTED, reason, ids, basis,
                  policy_letter=p.letter)


def _judge(e: Endorsement, wording: str, holder: str) -> tuple[ClaimVerdict, str, str | None]:
    """(verdict, reason, condition) of one endorsement for one wording."""
    label = WORDING_LABELS[wording].lower()
    if not e.form.verified:
        return ClaimVerdict.NOT_SUPPORTED, e.form.why_not(f"the {label} endorsement {e.label}"), None
    if e.scheduled:
        if not e.named_party.verified:
            return (ClaimVerdict.NOT_SUPPORTED,
                    f"endorsement {e.label} applies to the party its schedule names, and "
                    + e.named_party.why_not("that name"), None)
        if normalize_name(e.named_party.value) != normalize_name(holder):
            return (ClaimVerdict.NOT_SUPPORTED,
                    f"endorsement {e.label} names {e.named_party.value}, not the holder {holder}", None)
    if e.condition.stated:
        cond = e.condition.value if e.condition.cited else None
        if cond:
            return ClaimVerdict.NOT_SUPPORTED, f"applies only when {cond}; confirm and give the reason", cond
        return (ClaimVerdict.NOT_SUPPORTED, f"endorsement {e.label} may limit when it applies; check the cited "
                "page and give the reason", None)
    how = "names the holder" if e.scheduled else "applies to any holder (blanket)"
    return ClaimVerdict.SUPPORTED, f"endorsement {e.label} {how}", None


_RANK = {ClaimVerdict.SUPPORTED: 0, ClaimVerdict.NOT_SUPPORTED: 1}


def wording_claim(p: PolicyDoc, wording: str, endorsements: Sequence[Endorsement], request: Request,
                  *, unread: int = 0) -> ClaimBlock:
    label = WORDING_LABELS[wording]
    title = f"{label} on {LINE_LABELS[p.line or OTHER].lower()} policy {p.policy_number_label}"
    key = f"{wording}:{p.letter}"
    matching = [e for e in endorsements if wording in e.kinds]
    basis = [f"holder={normalize_name(request.holder_name)}", f"wording={wording}"]
    if not matching:
        reason = f"no {label.lower()} endorsement for policy {p.policy_number_label} was found in the documents"
        if unread:
            reason += f" ({unread} document{'s' if unread != 1 else ''} not read)"
        return _claim(key, ClaimKind.WORDING, title, ClaimVerdict.NOT_SUPPORTED, reason,
                      p.fact("cert:policy_number").passage_ids, basis + [f"unread={unread}"],
                      wording=wording, policy_letter=p.letter)
    judged = [(e, *_judge(e, wording, request.holder_name)) for e in matching]
    # supported first; then a conditional one (the broker can confirm the condition); then the rest
    judged.sort(key=lambda j: (_RANK[j[1]], 0 if j[3] else 1))
    e, verdict, reason, condition = judged[0]
    for other in matching:
        basis += other.basis()
    return _claim(key, ClaimKind.WORDING, title, verdict, reason, e.passage_ids(), basis, wording=wording,
                  policy_letter=p.letter, condition=condition)


def insured_claim(printed: Sequence[PolicyDoc]) -> ClaimBlock | None:
    """The named insured is verified and the same on every printed policy (not removable, D13)."""
    if not printed:
        return None
    names = [(p, p.fact("cert:named_insured")) for p in printed]
    basis = [ev.basis(f"insured:{p.letter}") for p, ev in names]
    ids = [pid for _, ev in names for pid in ev.passage_ids]
    title = "Named insured is the same on every policy"
    problems = [f"policy {p.policy_number_label}: " + ev.why_not("the named insured") for p, ev in names
                if not ev.verified]
    if problems:
        return _claim("insured", ClaimKind.INSURED, title, ClaimVerdict.NOT_SUPPORTED, "; ".join(problems), ids,
                      basis, removable=False)
    distinct = {}
    for p, ev in names:
        distinct.setdefault(normalize_name(ev.value), (p, ev))
    if len(distinct) > 1:
        listed = "; ".join(f"policy {p.policy_number_label} names {ev.value}" for p, ev in distinct.values())
        return _claim("insured", ClaimKind.INSURED, title, ClaimVerdict.NOT_SUPPORTED,
                      f"the named insured differs: {listed}", ids, basis, removable=False)
    return _claim("insured", ClaimKind.INSURED, title, ClaimVerdict.SUPPORTED,
                  f"every policy names {names[0][1].value}", ids, basis, removable=False)


def description_claim(description: str | None) -> ClaimBlock | None:
    if not description:
        return None
    return _claim("description", ClaimKind.DESCRIPTION, "Description of operations reviewed",
                  ClaimVerdict.NEEDS_SIGNOFF, "typed by the broker and printed as written; confirm it with a "
                  "reason, or remove it", (), [f"description={description}"])


def edited_claim(column: str, key: str, label: str, value: str | None, original: str | None) -> ClaimBlock:
    was = original if original is not None else "Not stated"
    return _claim(f"edited:{column}:{key}", ClaimKind.EDITED, f"Edited by broker: {label}",
                  ClaimVerdict.NEEDS_SIGNOFF, f"changed from {was} to {value or 'Not stated'}; confirm it with a "
                  "reason, or remove the edit", (), [f"value={value}", f"original={original}"],
                  policy_letter=None if column == "insured" else column)


def applicable_wording(line: str | None, request: Request) -> list[str]:
    return [w for w in WORDINGS if w in request.wording and (line or OTHER) in WORDING_LINES[w]]


__all__ = ["ADDITIONAL_INSURED", "AI_RE", "PNC_RE", "SCHEDULED_RE", "WAIVER_RE", "LINE_LABELS", "LINE_LIMITS", "PRIMARY_NONCONTRIBUTORY", "WAIVER", "WORDINGS",
           "WORDING_LABELS", "Endorsement", "Evidence", "Joined", "PolicyDoc", "Request", "applicable_wording",
           "classify_line", "description_claim", "edited_claim", "in_force_claim", "insured_claim", "join",
           "normalize_name", "normalize_policy_number", "parse_date", "wording_claim"]
