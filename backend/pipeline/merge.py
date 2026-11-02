r"""Merge verified window candidates into one fact per field (T14). Deterministic code only.

A long document is extracted window by window (pipeline.windows), so one
field can come back several times. Every candidate has already been verified
against the FULL document (pipeline.verify). This module picks what the cell
shows, per field:

    candidates ──► plausibility filter (below): a value that is only the
        │            field's label, a heading or a table-of-contents entry is
        │            dropped (kept in `dropped`, logged)
        ▼
    tier: VERIFIED if any, else SCAN_READ (lower tier, D5)
        │            none cited ──► NOT_FOUND if any candidate had a value
        │                           ("Couldn't verify", M1; reason
        │                           implausible_value when only dropped ones
        │                           were left), else ABSENT
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
              ├─ containment rule (below): a value that is a less specific
              │    mention of a longer one is folded into it; one group left
              │    ──► the longer value, its earliest page, the mentions as
              │        extra passages (resolution "contained")
              └─ ambiguous ──► state CONFLICTING, no value: every value is
                               kept in `conflicts` (earliest page first) with
                               its own verified evidence; compose shows
                               "Conflicting values", a chip per value and a
                               conflicting_values notice. Nothing is picked
                               (owner decision 2026-11-02: the old rule showed
                               the earliest as verified, which let a running
                               header or heading on page 1 beat the real value)

Plausibility filter (implausible_reason, deliberately tiny). A candidate is
dropped when its value:
1. is made only of the field label's words plus generic label and heading
   words (GENERIC_LABEL_WORDS: of, record, name, section, policy, coverage,
   definitions, continued, ...) and contains every word of the label, for
   example "Producer", "Producer of record", "Coverage Territory";
2. ends with "Definition(s)" (a definitions heading);
3. ends with a dot leader and a page number (a table-of-contents entry);
4. is pagination only ("Page 27 of 300", "Page 4", "27 / 300"; live eval
   long_300.policy_term, 2026-11-02, a page header's "Page 27 of 300" came
   back as the policy term); a bare number such as "12" is not dropped;
5. is shorter than 4 characters, has no digit and is not an upper-case code
   (so "A-", "A++" and "USA" pass, "of" and "Ab" do not).
False-positive risk: a real value that is literally the label's words (for
example a producer whose registered name is "Producer Of Record") is
dropped; every word of the label must be present, so one-word values such
as "Admitted" for Admitted Status still pass. A short lower-case value such
as "No" is dropped, and so is a real value that ends with "Definitions".
The filter runs on windowed (long) documents only; a single-call quote
never reaches merge.

Running header rule (RUNNING_HEADER_FIELDS, only policy_form for now). A
candidate whose value is a whole line on 3 or more pages (page numbering such
as "Page 40 of 120" and spacing ignored) is a running page header and is
dropped (reason running_header), but only when the field has another cited
candidate left; a header that is the only value is kept. Live eval run 3:
long_120 windows returned the header "Commercial General Liability Policy"
as the policy form. False-positive risk: a real value printed as its own line
on every page is dropped when some other value also came back. That is why
identity fields (carrier, insured, producer, address), which legitimately
sit in page headers, are not in the set. The containment rule below does not
cover this case: the live header is not a substring of the real form line.
The same goes for a candidate whose stating line (the whole line or lines its
matched text spans) is printed verbatim on 3 or more pages (repeated_line_pages,
reason running_header too): live eval 2026-11-02, a long_300 window returned
"Commercial General Liability Coverage Part" from "This endorsement modifies
insurance provided under the Commercial General Liability Coverage Part.",
a formula line on every endorsement page. Such a boilerplate line also never
counts as an amendment (endorsement rule, any field).

Containment rule (contained_in, after the endorsement rule). Live eval, 3
runs, long_120 and long_300 policy_form: page 2 states "CG 00 01 04 13
Commercial General Liability Coverage Form (Occurrence)", later boilerplate
pages state "Commercial General Liability Coverage Form"; both verified, so
the cell was "Conflicting values" although the real value was found. When a
value's normalized tokens (value_key: verify's tokens, case and spacing
ignored) are a contiguous run inside a longer value's tokens, the shorter is
a less specific mention of the longer and is merged into it like a repeated
value: the longer value is shown, its earliest page is the primary passage,
the shorter candidates become extra passages. Guards, so a shorter value
that means something else still conflicts:
1. the match is on whole tokens ("Form" never matches "Forms");
2. the shorter value has at least 3 tokens (single words such as
   "Admitted" against "Non-Admitted", or "Harborline Specialty" against the
   full carrier name, keep conflicting);
3. the shorter value has no number, so limits, deductibles and other amounts
   ("$5,000" vs "$5,000 per occurrence", "$1,000,000" vs "$2,000,000") keep
   their current behavior;
4. the tokens the longer value adds hold no negation (NEGATION_TOKENS: no,
   non, not, without, except, excluding, ... or a "non-" / "not-" prefix),
   so "Admitted Carrier In California" vs "Not Admitted Carrier In
   California" conflicts;
5. a mention contained in two unrelated longer values (an Occurrence and a
   Claims-Made form) is not folded into either.
An endorsement winner is decided first and is never undone by this rule.
False-positive risk: a 3+ word value that is a real, different value but
reads as a run inside a longer one without a negation word (for example a
qualifier the guards do not know, such as "Commercial General Liability
Coverage Form" vs "... Coverage Form Excess Layer Only") is merged and the
longer value is shown; its passage still appears as an extra chip.

Endorsement rule (kept simple on purpose):
1. A candidate "amends" when its quote or matched source text contains
   endorsement wording (AMEND_RE: endorsement, amend / amended / amendment /
   amendatory, deleted and replaced, replaced with / by, supersedes). If no
   candidate amends by its own quote, the lines of its page that state it
   plus the one line directly above (an endorsement heading over the changed
   line) are used instead, never the whole page: a later page that merely
   mentions an endorsement somewhere does not make an unrelated candidate
   (a running header, live eval run 3) an amendment.
2. The amending candidates must all state one value, and that value must
   first appear on a later page than every other value (an endorsement comes
   after the declarations it changes).
3. A candidate on a boilerplate line (the same line on 3 or more pages) never
   amends: an amendment states a new value once. Live eval 2026-11-02:
   "This endorsement modifies insurance provided under the Commercial General
   Liability Coverage Part" (every endorsement page of long_300) carries the
   word "endorsement" and was picked over the real form on page 2.
4. Otherwise the conflict is ambiguous.

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
    # state CONFLICTING: every value the document states, earliest page first (conflicting_values)
    conflicts: tuple[verify.VerifyResult, ...] = ()
    superseded: tuple[verify.VerifyResult, ...] = ()  # other values an endorsement replaced
    dropped: tuple[verify.VerifyResult, ...] = ()  # candidates the plausibility filter removed
    # absent | not_found | single | same_value | contained | endorsement | ambiguous
    resolution: str = "single"
    candidates: int = 0  # how many window candidates had a value


GENERIC_LABEL_WORDS = frozenset({
    "a", "an", "and", "the", "of", "or", "for", "to", "in", "on", "per", "see",
    "record", "name", "names", "info", "information", "detail", "details", "item", "items",
    "section", "schedule", "summary", "page", "continued", "conditions", "provisions",
    "definition", "definitions", "policy", "coverage", "form", "forms", "limit", "limits",
})
_DEFINITIONS_RE = re.compile(r"\bdefinitions?\W*$", re.I)
_TOC_RE = re.compile(r"(?:\.\s*){4,}\d+\s*$")
# pagination only: "Page 27 of 300", "Page 4", "p. 12", "27 / 300", "- 27 -"
_PAGINATION_RE = re.compile(r"^\W*(?:(?:page|pg|p)\.?\s*)?\d+(?:\s*(?:of|/)\s*\d+)?\W*$", re.I)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def implausible_reason(fdef: FactField | str, value: str | None) -> str | None:
    """Why a value cannot be the field's real value (a label, heading or TOC entry), else None."""
    v = (value or "").strip()
    if not v:
        return None
    label = fdef.label if isinstance(fdef, FactField) else str(fdef).replace("_", " ")
    label_words = set(_words(label))
    words = set(_words(v))
    if label_words and label_words <= words <= label_words | GENERIC_LABEL_WORDS:
        return "label_text"
    if _DEFINITIONS_RE.search(v):
        return "definitions_heading"
    if _TOC_RE.search(v):
        return "table_of_contents"
    if _PAGINATION_RE.match(v) and re.search(r"[a-z/]", v, re.I):
        return "page_number"
    if len(v) < 4 and not any(ch.isdigit() for ch in v) and v != v.upper():
        return "too_short"
    return None


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


def _match_context(r: verify.VerifyResult, page_text: Mapping[int, str]) -> str | None:
    """The page lines that state the candidate (its matched text, quote or value) plus the
    line directly above them (an endorsement heading over the changed line), or None when
    the candidate cannot be located on its page."""
    text = page_text.get(r.page_no, "") if r.page_no is not None else ""
    if not text:
        return None
    for needle in (r.matched_text, r.quote, r.value):
        words = (needle or "").split()
        if not words:
            continue
        mo = re.search(r"\s+".join(re.escape(w) for w in words), text, re.I)
        if mo:
            start = text.rfind("\n", 0, mo.start())  # start of the first matched line, minus 1
            start = text.rfind("\n", 0, start) + 1 if start > 0 else 0  # one line above
            end = text.find("\n", mo.end())
            return text[start:end if end != -1 else len(text)]
    return None


def _amends_near_match(r: verify.VerifyResult, page_text: Mapping[int, str]) -> bool:
    ctx = _match_context(r, page_text)
    return bool(ctx and AMEND_RE.search(ctx))


# Fields whose value is not expected to repeat on many pages; a value that is a whole
# running page header line there is a decoy (live eval run 3, long_120.policy_form).
# Identity fields (carrier, insured, producer, address) legitimately sit in headers.
RUNNING_HEADER_FIELDS = frozenset({"policy_form"})
RUNNING_HEADER_MIN_PAGES = 3
_PAGE_NUMBER_RE = re.compile(r"\bpage\s+\d+(?:\s+of\s+\d+)?\b", re.I)


def _header_line_key(line: str) -> str:
    return " ".join(_PAGE_NUMBER_RE.sub(" ", line).lower().split())


def running_header_pages(value: str | None, page_text: Mapping[int, str]) -> list[int]:
    """Pages with a line that is exactly ``value`` once page numbering ("Page 4 of 120")
    and spacing are ignored. Three or more such pages mark a running header."""
    key = _header_line_key(value or "")
    if not key:
        return []
    return sorted(p for p, text in page_text.items()
                  if any(_header_line_key(ln) == key for ln in (text or "").splitlines()))


def _stating_lines(r: verify.VerifyResult, page_text: Mapping[int, str]) -> list[str]:
    """The whole lines of the candidate's page that its matched text (else quote, else value) spans."""
    text = page_text.get(r.page_no, "") if r.page_no is not None else ""
    if not text:
        return []
    for needle in (r.matched_text, r.quote, r.value):
        words = (needle or "").split()
        if not words:
            continue
        mo = re.search(r"\s+".join(re.escape(w) for w in words), text, re.I)
        if mo:
            start = text.rfind("\n", 0, mo.start()) + 1
            end = text.find("\n", mo.end())
            return [ln for ln in text[start:end if end != -1 else len(text)].splitlines() if ln.strip()]
    return []


def repeated_line_pages(r: verify.VerifyResult, page_text: Mapping[int, str]) -> list[int]:
    """Pages that print every line stating the candidate, verbatim (page numbering and spacing
    ignored). Three or more mark boilerplate: a running header or a fixed formula such as the
    line on every endorsement, "This endorsement modifies insurance provided under the
    Commercial General Liability Coverage Part." (live eval long_300.policy_form, 2026-11-02)."""
    keys = {k for k in (_header_line_key(ln) for ln in _stating_lines(r, page_text)) if k}
    if not keys:
        return []
    return sorted(p for p, text in page_text.items()
                  if keys <= {_header_line_key(ln) for ln in (text or "").splitlines()})


def _boilerplate(r: verify.VerifyResult, page_text: Mapping[int, str]) -> bool:
    return (len(running_header_pages(r.value, page_text)) >= RUNNING_HEADER_MIN_PAGES
            or len(repeated_line_pages(r, page_text)) >= RUNNING_HEADER_MIN_PAGES)


# Containment rule: a value that is a token-boundary run inside a longer value is a less
# specific mention of it (see the module docstring for the guards and the false-positive risk).
CONTAINMENT_MIN_TOKENS = 3
NEGATION_TOKENS = frozenset({"no", "non", "not", "never", "none", "without", "except", "excluding", "excluded"})


def _is_number_key(tok: str) -> bool:
    return tok.startswith("#")  # value_key writes numbers as "#<value><suffix>"


def contained_in(short: Sequence[str], long: Sequence[str]) -> bool:
    """True when ``short`` (a value_key) is a less specific mention of ``long``: a proper,
    contiguous token run of it, with at least CONTAINMENT_MIN_TOKENS tokens and no number in
    the shorter value, and no negation word ("non", "not", "no", ...) among the tokens the
    longer value adds."""
    n, m = len(short), len(long)
    if n < CONTAINMENT_MIN_TOKENS or n >= m or any(_is_number_key(t) for t in short):
        return False
    for i in range(m - n + 1):
        if tuple(long[i:i + n]) == tuple(short):
            added = [*long[:i], *long[i + n:]]
            if not any(t in NEGATION_TOKENS or t.startswith(("non-", "not-")) for t in added):
                return True
    return False


def _fold_contained(groups: list[list[verify.VerifyResult]]) -> list[list[verify.VerifyResult]]:
    """Fold each group whose value is contained in exactly one maximal longer value into that
    value's group (its candidates go after the longer value's own, earliest page first). A
    value contained in two unrelated longer values is left alone (still a conflict)."""
    keys = [value_key(g[0].value) for g in groups]
    inside = {i: [j for j in range(len(groups)) if j != i and contained_in(keys[i], keys[j])]
              for i in range(len(groups))}
    roots = {i for i in range(len(groups)) if not inside[i]}
    folded: dict[int, list[verify.VerifyResult]] = {i: list(groups[i]) for i in sorted(roots)}
    absorbed: dict[int, list[verify.VerifyResult]] = {i: [] for i in roots}
    for i in range(len(groups)):
        if i in roots:
            continue
        top = [j for j in inside[i] if j in roots]  # containment is transitive: the maximal ones
        if len(top) == 1:
            absorbed[top[0]].extend(groups[i])
        else:
            folded[i], absorbed[i] = list(groups[i]), []
    out = [folded[i] + sorted(absorbed[i], key=_page) for i in folded]
    return sorted(out, key=lambda g: _page(g[0]))


def _from(r: verify.VerifyResult, **kw) -> MergedFact:
    base = {f: getattr(r, f) for f in verify.VerifyResult.__dataclass_fields__}
    base.update(kw)
    return MergedFact(**base)


def _resolve_endorsement(groups: list[list[verify.VerifyResult]],
                         page_text: Mapping[int, str]) -> int | None:
    """Index of the group an endorsement makes effective, or None when ambiguous. A candidate
    stated on a boilerplate line (the same line on 3+ pages, _boilerplate) never amends: an
    amendment states a new value once, while "This endorsement modifies insurance provided
    under the ... Coverage Part" is printed on every endorsement and changes no value."""
    def one_off(r: verify.VerifyResult) -> bool:
        return not page_text or not _boilerplate(r, page_text)

    amending = [i for i, g in enumerate(groups) if any(_amends_by_quote(r) and one_off(r) for r in g)]
    if not amending:  # an endorsement heading on, or just above, the line that states the value
        amending = [i for i, g in enumerate(groups)
                    if any(_amends_near_match(r, page_text) and one_off(r) for r in g)]
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
    plausible, dropped = [], []
    reasons: dict[int, str] = {}
    for c in valued:
        why = implausible_reason(fdef, c.value)
        if why:
            reasons[id(c)] = why
        (dropped if why else plausible).append(c)
    if key in RUNNING_HEADER_FIELDS and page_text:
        headers = [c for c in plausible if _boilerplate(c, page_text)]
        header_ids = {id(c) for c in headers}
        rest = [c for c in plausible if id(c) not in header_ids]
        if headers and any(c.state in (EvidenceState.VERIFIED, EvidenceState.SCAN_READ) for c in rest):
            for c in headers:
                reasons[id(c)] = "running_header"
            plausible, dropped = rest, dropped + headers
    if dropped:
        log.info("merge: %s dropped implausible values %s", key,
                 [(r.value, r.page_no or r.cited_page, reasons[id(r)]) for r in dropped])
    dropped_t = tuple(dropped)
    tier = [c for c in plausible if c.state == EvidenceState.VERIFIED] or \
        [c for c in plausible if c.state == EvidenceState.SCAN_READ]
    if not tier:
        rejected = [c for c in plausible if c.state == EvidenceState.NOT_FOUND]
        if rejected:
            return _from(min(rejected, key=lambda c: (c.cited_page or 10**9)), resolution="not_found",
                         candidates=n, dropped=dropped_t)
        if dropped:  # only labels or headings came back: "Couldn't verify", no passage
            first = min(dropped, key=lambda c: (c.cited_page or c.page_no or 10**9))
            return _from(first, state=EvidenceState.NOT_FOUND, page_no=None, matched_text=None, method=None,
                         score=0.0, rects=[], repointed_from=None, reason="implausible_value",
                         cited_page=first.cited_page if first.cited_page is not None else first.page_no,
                         resolution="not_found", candidates=n, dropped=dropped_t)
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
        return _from(primary, extras=tuple(extras), resolution=resolution, candidates=n, dropped=dropped_t,
                     **kw)

    if len(ordered) == 1:
        return with_extras(ordered[0])
    win = _resolve_endorsement(ordered, page_text)
    if win is not None:
        others = tuple(g[0] for i, g in enumerate(ordered) if i != win)
        log.info("merge: %s endorsement on page %s replaces %s", key, ordered[win][0].page_no,
                 [(r.value, r.page_no) for r in others])
        return with_extras(ordered[win], superseded=others, resolution="endorsement")
    folded = _fold_contained(ordered)  # after the endorsement rule, before reporting a conflict
    if len(folded) < len(ordered):
        log.info("merge: %s folded less specific mentions into %s", key,
                 [(g[0].value, g[0].page_no) for g in folded])
    if len(folded) == 1:
        return with_extras(folded[0], resolution="contained")
    ordered = folded
    conflicts = tuple(g[0] for g in ordered)
    log.info("merge: %s has conflicting values %s; none is picked", key,
             [(r.value, r.page_no) for r in conflicts])
    return MergedFact(field=key, value=None, state=EvidenceState.CONFLICTING, cited_page=None, page_no=None,
                      quote=None, matched_text=None, method=None, score=0.0, rects=[],
                      reason="conflicting_values", conflicts=conflicts, dropped=dropped_t,
                      resolution="ambiguous", candidates=n)


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
                     conflicts: Sequence[tuple[str | None, int | None]], *, none_shown: bool = False) -> str:
    """Text of the conflicting_values notice: every (value, page), earliest first.

    none_shown: the cell is in state CONFLICTING (no value picked, `shown` is
    just the first value); otherwise `shown` is the value the cell shows
    (facts stored before 2026-11-02).
    """
    def one(v, p):
        return f"{v} (page {p})" if p is not None else f"{v}"
    values = [one(*shown), *(one(v, p) for v, p in conflicts)]
    listed = ", ".join(values[:-1]) + f" and {values[-1]}"
    tail = ("No value is shown as verified; check which one applies." if none_shown
            else "The earliest is shown; check which one applies.")
    return f"This document states different values for {label}: {listed}. {tail}"


__all__ = ["AMEND_RE", "GENERIC_LABEL_WORDS", "NEGATION_TOKENS", "RUNNING_HEADER_FIELDS", "MergedFact",
           "conflict_message", "contained_in", "implausible_reason", "merge_document", "merge_field",
           "repeated_line_pages", "running_header_pages", "value_key"]
