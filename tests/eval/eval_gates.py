"""Eval scoring and gate computation (task T9, decision D7 / R6, mechanic M5).

Pure functions only: no file, network or model access. The live eval and the
offline unit tests both feed ground-truth dicts (``*.truth.json``) and
pipeline output into these functions.

Definitions
-----------
Evidence states (M1): ``verified``, ``scan_read``, ``not_found``, ``failed``.
A ``not_found`` fact that carries a value is a *rejected* fact (the model
proposed it and the verifier could not find it, UI "Couldn't verify"); a
``not_found`` fact with no value is *absent* ("Not specified").

Displayed value: state ``verified`` or ``scan_read`` with a non-null value.

Field accuracy (M5): among fields that are present (non-null) in ground
truth, the share whose displayed value matches the truth value. Fields that
are null in truth are excluded from the denominator and must return no
displayed value; a displayed value there is an ``absent_violation``.

Hard fail (R6): any ``verified`` fact whose value does not match the source:
the value differs from truth, the field is null in truth, or the cited page
is not a page where truth says the value occurs. Must be 0. ``scan_read``
facts are labeled unverified in the UI (D5) and are excluded.

Citation verification rate: verified / (verified + rejected), excluding
scan_read and failed facts (D5).

Hallucinated-value rate: displayed facts whose value does not match truth
(including fields null in truth) / displayed facts.

Gates (R6, initial bars, tuned after the first live run):
  * hard fails == 0 across every fixture (gated)
  * field accuracy on the clean bucket >= 0.90 (gated)
  * field accuracy on the multi-column bucket >= 0.80 (gated)
  * field accuracy on the long bucket (120 and 300 page documents) >= 0.85, and
    zero hard fails in that bucket (gated; required when the full suite runs)
  * field accuracy on the scanned bucket: reported, not gated
  * max input tokens per run <= 600,000 (D3 budget, gated when reported)
  * unlisted fields == 0: fail-closed fixtures (``"fail_closed": true``, the
    proposal fixtures) report any output field their truth does not list
Everything else is reported, not gated.

Proposal Builder (slice 2, T1): ``score_proposal`` and
``evaluate_proposal_gates`` score table cells and prose of a proposal run (see
the section comment there).

Policy Check (slice 3, T7, D16): ``score_policy_case`` and
``evaluate_policy_gates`` at the end of this module score the discrepancy items
of a Policy Check run against seeded ground truth (see the section comment
there).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

# ---------------------------------------------------------------------------
# Gate numbers
# ---------------------------------------------------------------------------

HARD_FAIL_MAX = 0
FIELD_ACCURACY_CLEAN_MIN = 0.90
FIELD_ACCURACY_MULTI_COLUMN_MIN = 0.80
FIELD_ACCURACY_LONG_MIN = 0.85
RUN_INPUT_TOKEN_BUDGET = 1_500_000

GATED_BUCKETS = {"clean": FIELD_ACCURACY_CLEAN_MIN, "multi_column": FIELD_ACCURACY_MULTI_COLUMN_MIN,
                 "long": FIELD_ACCURACY_LONG_MIN}
# compare_formats: the slice 6 PDF / Word / Excel scenarios (tests/fixtures/compare, compare_gate.py).
REPORTED_BUCKETS = ("scanned", "report_only", "parse_only", "compare_formats")

STATES = ("verified", "scan_read", "not_found", "failed")
DISPLAYED = ("verified", "scan_read")

# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9

_ABBREV = {
    "street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd", "drive": "dr",
    "suite": "ste", "building": "bldg", "incorporated": "inc", "company": "co",
    "corporation": "corp", "limited": "ltd", "&": "and",
}
_CONNECTORS = {"to", "through", "thru", "until"}
_STOPWORDS = {"of", "the"}
_SCALE = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "mm": 1_000_000,
          "million": 1_000_000, "b": 1_000_000_000, "billion": 1_000_000_000}

_DATE_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DATE_MDY = re.compile(r"\b([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_DATE_DMY = re.compile(r"\b(\d{1,2})\s+([a-z]{3,9})\.?,?\s+(\d{4})\b")
_NUMBER = re.compile(
    r"(?<![a-z0-9@.,])\$?\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?(thousand|million|billion|mm|k|m|b)?(\s?%)?(?![a-z0-9])")
_TOKEN = re.compile(r"[a-z0-9]+(?:\+\+|\+|-(?=\s|$|[(),]))?")


def _date_token(y: int, m: int, d: int) -> str | None:
    if 1 <= m <= 12 and 1 <= d <= 31:
        return f" @d{y:04d}{m:02d}{d:02d} "
    return None


def _sub_dates(s: str) -> str:
    def numeric(mo: re.Match) -> str:
        return _date_token(int(mo.group(3)), int(mo.group(1)), int(mo.group(2))) or mo.group(0)

    def iso(mo: re.Match) -> str:
        return _date_token(int(mo.group(1)), int(mo.group(2)), int(mo.group(3))) or mo.group(0)

    def mdy(mo: re.Match) -> str:
        m = _MONTHS.get(mo.group(1))
        return (_date_token(int(mo.group(3)), m, int(mo.group(2))) if m else None) or mo.group(0)

    def dmy(mo: re.Match) -> str:
        m = _MONTHS.get(mo.group(2))
        return (_date_token(int(mo.group(3)), m, int(mo.group(1))) if m else None) or mo.group(0)

    s = _DATE_NUMERIC.sub(numeric, s)
    s = _DATE_ISO.sub(iso, s)
    s = _DATE_MDY.sub(mdy, s)
    s = _DATE_DMY.sub(dmy, s)
    return s


def _sub_numbers(s: str) -> str:
    def num(mo: re.Match) -> str:
        try:
            val = Decimal(mo.group(1).replace(",", "") + (mo.group(2) or ""))
        except InvalidOperation:
            return mo.group(0)
        if mo.group(3):
            val *= _SCALE[mo.group(3)]
        val = val.normalize()
        text = format(val, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return f" @n{text}{'%' if mo.group(4) else ''} "

    return _NUMBER.sub(num, s)


def normalize_value(value: str | None) -> tuple[str, ...]:
    """Canonical token tuple for comparing extracted values to truth.

    Dates in common US formats become ``@dYYYYMMDD``; money and plain numbers
    become ``@n<decimal>`` (``$12,500.00`` == ``12500`` == ``$12.5k``); words
    are lowercased, periods inside abbreviations dropped (``L.L.C.`` ==
    ``LLC``), a few address and company words abbreviated, and rating signs
    kept (``A+`` != ``A`` != ``A-``). Range connectors (``to``, ``-``) are
    dropped between two dates.
    """
    if value is None:
        return ()
    s = str(value).lower().strip()
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    s = _sub_dates(s)
    s = re.sub(r"(?<=[a-z])\.(?=[a-z])", "", s)  # l.l.c -> llc
    s = re.sub(r"(?<=[a-z])\.(?=\s|$|,)", "", s)  # inc. -> inc
    s = _sub_numbers(s)
    s = s.replace("&", " and ")
    tokens: list[str] = []
    for part in re.split(r"\s+", s):
        if part.startswith("@"):
            tokens.append(part)
            continue
        for t in _TOKEN.findall(part + " "):
            if t not in _STOPWORDS:
                tokens.append(_ABBREV.get(t, t))
    if sum(1 for t in tokens if t.startswith("@d")) >= 2:
        tokens = [t for t in tokens if t not in _CONNECTORS]
    return tuple(tokens)


def _is_amount(tok: str) -> bool:
    """A plain number or money token (not a percentage, not a date)."""
    return tok.startswith("@n") and not tok.endswith("%")


def _equal_or_unit_suffixed(p: tuple[str, ...], t: tuple[str, ...]) -> bool:
    """``p`` equals ``t``, or ``t`` is one plain amount and ``p`` is that same
    amount followed only by words (``$10,000 per occurrence`` vs ``$10,000``).
    A second number, a date or a percent sign in the trailing part does not
    qualify, and ``25`` never matches ``25%``."""
    if p == t:
        return True
    if len(t) != 1 or not _is_amount(t[0]) or len(p) < 2 or p[0] != t[0]:
        return False
    return not any(tok.startswith("@") for tok in p[1:])


# Last word of a short qualifier that may precede a date ("Valid until", "Expires on",
# "Effective", "Good through", "Must be bound by"). Matched on normalized tokens.
_DATE_QUALIFIER_END = frozenset({
    "until", "till", "on", "through", "thru", "by", "to", "from", "as", "at",
    "effective", "expires", "expiring", "expiration", "expiry", "dated", "date",
})
_DATE_QUALIFIER_MAX_WORDS = 3


def _date_with_qualifier(p: tuple[str, ...], t: tuple[str, ...]) -> bool:
    """``t`` is exactly one date and ``p`` is that same date (compared as
    parsed dates) preceded by at most three plain words, the last of which is a
    qualifier or connector (``Valid until 12/15/2026`` vs ``December 15, 2026``).
    Nothing may follow the date, and no number, date or percent may appear in
    the leading words. Non-date truth never gets this tolerance."""
    if len(t) != 1 or not t[0].startswith("@d") or len(p) < 2 or p[-1] != t[0]:
        return False
    lead = p[:-1]
    return (len(lead) <= _DATE_QUALIFIER_MAX_WORDS
            and not any(tok.startswith("@") for tok in lead)
            and lead[-1] in _DATE_QUALIFIER_END)


def _close_enough(p: tuple[str, ...], t: tuple[str, ...]) -> bool:
    return _equal_or_unit_suffixed(p, t) or _date_with_qualifier(p, t)


def values_match(predicted: str | None, truth: str | None,
                 accepted: Iterable[str] = ()) -> bool:
    """True when ``predicted`` equals truth (or an accepted alias) after
    normalization, allowing a trailing unit or qualifier after a single
    amount, or a short leading qualifier before a single date. Two nulls
    match; null against a value does not."""
    if predicted is None or truth is None:
        return predicted is None and truth is None
    p = normalize_value(predicted)
    if not p:
        return False
    return (_close_enough(p, normalize_value(truth))
            or any(_close_enough(p, normalize_value(a)) for a in accepted))


# ---------------------------------------------------------------------------
# Pipeline output model
# ---------------------------------------------------------------------------


@dataclass
class PredictedFact:
    field: str
    value: str | None
    page: int | None = None
    quote: str | None = None
    state: str = "verified"

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown evidence state {self.state!r}")
        if isinstance(self.value, str) and not self.value.strip():
            self.value = None

    @property
    def displayed(self) -> bool:
        return self.state in DISPLAYED and self.value is not None


@dataclass
class DocResult:
    """What the pipeline produced for one document."""

    fixture_id: str
    facts: list[PredictedFact] = field(default_factory=list)
    status: str = "ok"  # ok | failed
    failure_reason: str | None = None
    option_count: int | None = None
    ocr_pages: list[int] | None = None

    @classmethod
    def from_dict(cls, d: Mapping) -> "DocResult":
        return cls(
            fixture_id=d["fixture_id"],
            facts=[PredictedFact(**f) for f in d.get("facts", [])],
            status=d.get("status", "ok"),
            failure_reason=d.get("failure_reason"),
            option_count=d.get("option_count"),
            ocr_pages=d.get("ocr_pages"),
        )


# ---------------------------------------------------------------------------
# Per-document scoring
# ---------------------------------------------------------------------------


@dataclass
class FieldOutcome:
    field: str
    truth: str | None
    predicted: str | None
    state: str | None
    page: int | None
    correct: bool
    outcome: str  # correct | wrong | missing | absent_ok | absent_violation
    hard_fail: str | None = None  # reason when a verified fact is wrong
    decoy_kind: str | None = None
    miss_reason: str | None = None  # set for present fields that are not correct, see MISS_REASONS


@dataclass
class DocScore:
    fixture_id: str
    gate_bucket: str
    present: int = 0
    correct: int = 0
    absent: int = 0
    absent_violations: int = 0
    verified: int = 0
    rejected: int = 0
    scan_read: int = 0
    failed: int = 0
    displayed: int = 0
    displayed_wrong: int = 0
    verified_page_ok: int = 0
    hard_fails: int = 0
    decoy_hits: dict[str, int] = field(default_factory=dict)
    canary_hits: int = 0
    option_count_ok: bool | None = None
    parse_ok: bool | None = None
    ocr_pages_ok: bool | None = None
    outcomes: list[FieldOutcome] = field(default_factory=list)
    # Fail-closed fixtures only: fields in the output that the truth does not list.
    unlisted: list[dict] = field(default_factory=list)


MISS_REASONS = ("missing", "wrong_value", "rejected", "scan_read")


def _miss_reason(p: PredictedFact | None) -> str:
    """Why a field present in truth was not scored correct.

    ``missing``: no fact, a null value, or a failed fact. ``rejected``: the
    model proposed a value the verifier could not find (``not_found`` with a
    value). ``scan_read``: a scan_read value that differs from truth.
    ``wrong_value``: a verified value that differs from truth."""
    if p is None or p.value is None:
        return "missing"
    if p.state == "not_found":
        return "rejected"
    if p.state == "scan_read":
        return "scan_read"
    if p.displayed:
        return "wrong_value"
    return "missing"


def _dedupe(facts: Iterable[PredictedFact]) -> dict[str, PredictedFact]:
    """One fact per field. Prefer a displayed fact over a non-displayed one,
    and the first displayed one among several (the pipeline should never emit
    two, so duplicates are scored on the first)."""
    out: dict[str, PredictedFact] = {}
    for f in facts:
        cur = out.get(f.field)
        if cur is None or (not cur.displayed and f.displayed):
            out[f.field] = f
    return out


def score_document(truth: Mapping, result: DocResult) -> DocScore:
    """Score one document's pipeline output against its truth dict."""
    expect = truth.get("expect", {})
    score = DocScore(truth["fixture_id"], truth.get("gate_bucket", "report_only"))

    exp_parse = expect.get("parse", "ok")
    if exp_parse == "failed":
        score.parse_ok = (result.status == "failed"
                          and result.failure_reason == expect.get("failure_reason"))
    else:
        score.parse_ok = result.status == "ok"
    if result.ocr_pages is not None and result.status == "ok":
        score.ocr_pages_ok = sorted(result.ocr_pages) == sorted(expect.get("ocr_pages", []))
    if result.option_count is not None:
        score.option_count_ok = result.option_count == expect.get("option_count", 1)

    decoys: dict[str, list[dict]] = {}
    for d in truth.get("decoys", []) or []:
        decoys.setdefault(d["field"], []).append(d)

    predicted = _dedupe(result.facts)
    truth_facts: Mapping = truth.get("facts", {}) or {}
    # Only the eval's scored fields count. For slice 1 fixtures a predicted field
    # the truth does not list (subjectivities, key_exclusions, ... exist in the
    # backend and in the PDFs but have no truth here) is ignored: not a hard
    # fail, absent violation or hallucination. Fail-closed fixtures (the
    # proposal fixtures, ``"fail_closed": true``) report every such field as
    # ``unlisted`` unless the truth names it in ``unscored_fields``; the
    # ``unlisted_fields`` gate then fails.
    fields = list(truth_facts)
    if truth.get("fail_closed"):
        known = set(truth_facts) | set(truth.get("unscored_fields") or {})
        for fname in sorted({f.field for f in result.facts} - known):
            p = predicted[fname]
            score.unlisted.append({"fixture_id": score.fixture_id, "field": fname,
                                   "predicted": p.value, "state": p.state, "where": "fact"})

    for fname in fields:
        t = truth_facts.get(fname)
        p = predicted.get(fname)
        t_val = t["value"] if t else None
        accepted = t.get("accepted_values", []) if t else []
        p_val = p.value if p else None
        state = p.state if p else None

        if p is not None:
            if p.state == "verified" and p.value is not None:
                score.verified += 1
            elif p.state == "not_found" and p.value is not None:
                score.rejected += 1
            elif p.state == "scan_read" and p.value is not None:
                score.scan_read += 1
            elif p.state == "failed":
                score.failed += 1

        shown = p is not None and p.displayed
        matches = shown and values_match(p_val, t_val, accepted)
        if shown:
            score.displayed += 1
            if not matches:
                score.displayed_wrong += 1

        decoy_kind = None
        if shown and not matches:
            for d in decoys.get(fname, []):
                if values_match(p_val, d["value"]):
                    decoy_kind = d.get("kind", "decoy")
                    score.decoy_hits[decoy_kind] = score.decoy_hits.get(decoy_kind, 0) + 1
                    if decoy_kind == "injection_canary":
                        score.canary_hits += 1
                    break

        hard = None
        if p is not None and p.state == "verified" and p.value is not None:
            if t is None:
                hard = "verified value for a field absent from the source"
            elif not matches:
                hard = "verified value differs from the source"
            elif p.page not in (t.get("pages") or [t.get("page")]):
                hard = "verified citation points at a page without the value"
            else:
                score.verified_page_ok += 1
        if hard:
            score.hard_fails += 1

        if t is None:
            score.absent += 1
            outcome = "absent_violation" if shown else "absent_ok"
            if shown:
                score.absent_violations += 1
            correct = not shown
        else:
            score.present += 1
            correct = bool(matches)
            if correct:
                score.correct += 1
            outcome = "correct" if correct else ("wrong" if shown else "missing")
        score.outcomes.append(FieldOutcome(fname, t_val, p_val, state, p.page if p else None,
                                           correct, outcome, hard, decoy_kind,
                                           None if (t is None or correct) else _miss_reason(p)))
    return score


# ---------------------------------------------------------------------------
# Aggregation and gates
# ---------------------------------------------------------------------------


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


@dataclass
class BucketMetrics:
    bucket: str
    documents: int
    present: int
    correct: int
    field_accuracy: float | None
    absent: int
    absent_violations: int
    verified: int
    rejected: int
    scan_read: int
    citation_verification_rate: float | None
    displayed: int
    displayed_wrong: int
    hallucinated_value_rate: float | None
    hard_fails: int


@dataclass
class SuiteMetrics:
    buckets: dict[str, BucketMetrics]
    overall: BucketMetrics
    hard_fails: int
    canary_hits: int
    decoy_hits: dict[str, int]
    parse_mismatches: list[str]
    ocr_mismatches: list[str]
    option_count_mismatches: list[str]
    max_run_input_tokens: int | None
    hard_fail_details: list[dict]
    # One entry per field present in truth but not scored correct.
    misses: list[dict] = field(default_factory=list)
    # Set (to the group names that ran) when only a subset of the live runs executed.
    subset_groups: list[str] | None = None
    # Fail-closed fixtures: output fields the truth does not list (gated at 0).
    unlisted_fields: list[dict] = field(default_factory=list)


def _bucket(name: str, scores: list[DocScore]) -> BucketMetrics:
    s = lambda attr: sum(getattr(x, attr) for x in scores)  # noqa: E731
    return BucketMetrics(
        bucket=name, documents=len(scores),
        present=s("present"), correct=s("correct"),
        field_accuracy=_ratio(s("correct"), s("present")),
        absent=s("absent"), absent_violations=s("absent_violations"),
        verified=s("verified"), rejected=s("rejected"), scan_read=s("scan_read"),
        citation_verification_rate=_ratio(s("verified"), s("verified") + s("rejected")),
        displayed=s("displayed"), displayed_wrong=s("displayed_wrong"),
        hallucinated_value_rate=_ratio(s("displayed_wrong"), s("displayed")),
        hard_fails=s("hard_fails"),
    )


def aggregate(scores: list[DocScore], run_input_tokens: Iterable[int] = ()) -> SuiteMetrics:
    by: dict[str, list[DocScore]] = {}
    for sc in scores:
        by.setdefault(sc.gate_bucket, []).append(sc)
    decoys: dict[str, int] = {}
    for sc in scores:
        for k, v in sc.decoy_hits.items():
            decoys[k] = decoys.get(k, 0) + v
    tokens = list(run_input_tokens)
    return SuiteMetrics(
        buckets={k: _bucket(k, v) for k, v in sorted(by.items())},
        overall=_bucket("overall", scores),
        hard_fails=sum(sc.hard_fails for sc in scores),
        canary_hits=sum(sc.canary_hits for sc in scores),
        decoy_hits=decoys,
        parse_mismatches=[sc.fixture_id for sc in scores if sc.parse_ok is False],
        ocr_mismatches=[sc.fixture_id for sc in scores if sc.ocr_pages_ok is False],
        option_count_mismatches=[sc.fixture_id for sc in scores if sc.option_count_ok is False],
        max_run_input_tokens=max(tokens) if tokens else None,
        hard_fail_details=[
            {"fixture_id": sc.fixture_id, "field": o.field, "predicted": o.predicted,
             "truth": o.truth, "page": o.page, "reason": o.hard_fail}
            for sc in scores for o in sc.outcomes if o.hard_fail
        ],
        misses=[
            {"fixture_id": sc.fixture_id, "field": o.field, "truth": o.truth,
             "predicted": o.predicted, "state": o.state, "reason": o.miss_reason}
            for sc in scores for o in sc.outcomes if o.miss_reason
        ],
        unlisted_fields=[u for sc in scores for u in sc.unlisted],
    )


@dataclass
class GateResult:
    name: str
    value: float | int | None
    threshold: str
    gated: bool
    passed: bool
    note: str = ""


@dataclass
class GateReport:
    results: list[GateResult]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results if r.gated)

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if r.gated and not r.passed]


def evaluate_gates(m: SuiteMetrics, *, require_buckets: Iterable[str] = ("clean", "multi_column")
                   ) -> GateReport:
    """Apply the R6 bars. A gated bucket with no scored fields fails (no data
    is not a pass) when it is listed in ``require_buckets``."""
    required = set(require_buckets)
    res: list[GateResult] = [
        GateResult("hard_fails (verified value not matching source)", m.hard_fails,
                   f"== {HARD_FAIL_MAX}", True, m.hard_fails <= HARD_FAIL_MAX),
        GateResult("unlisted_fields (fail-closed fixtures)", len(m.unlisted_fields), "== 0", True,
                   not m.unlisted_fields, "output field missing from a fail-closed truth"),
    ]
    for bucket, bar in GATED_BUCKETS.items():
        b = m.buckets.get(bucket)
        acc = b.field_accuracy if b else None
        if acc is None:
            res.append(GateResult(f"field_accuracy[{bucket}]", None, f">= {bar:.2f}",
                                  bucket in required, bucket not in required, "no data"))
        else:
            res.append(GateResult(f"field_accuracy[{bucket}]", acc, f">= {bar:.2f}", True,
                                  acc >= bar))
    lb = m.buckets.get("long")
    if lb is not None:
        res.append(GateResult("hard_fails[long]", lb.hard_fails, f"== {HARD_FAIL_MAX}", True,
                              lb.hard_fails <= HARD_FAIL_MAX))
    elif "long" in required:
        res.append(GateResult("hard_fails[long]", None, f"== {HARD_FAIL_MAX}", True, False,
                              "no data"))
    for bucket in REPORTED_BUCKETS:
        b = m.buckets.get(bucket)
        if b is not None:
            res.append(GateResult(f"field_accuracy[{bucket}]", b.field_accuracy, "reported",
                                  False, True))
    if m.max_run_input_tokens is not None:
        res.append(GateResult("max_run_input_tokens", m.max_run_input_tokens,
                              f"<= {RUN_INPUT_TOKEN_BUDGET}", True,
                              m.max_run_input_tokens <= RUN_INPUT_TOKEN_BUDGET))
    o = m.overall
    res += [
        GateResult("citation_verification_rate", o.citation_verification_rate, "reported",
                   False, True, "verified / (verified + rejected); scan_read excluded"),
        GateResult("hallucinated_value_rate", o.hallucinated_value_rate, "reported", False, True),
        GateResult("absent_violations", o.absent_violations, "reported", False, True,
                   "value shown for a field null in truth"),
        GateResult("injection_canary_hits", m.canary_hits, "reported", False, True,
                   "verified canaries are already counted as hard fails"),
        GateResult("parse_mismatches", len(m.parse_mismatches), "reported", False, True,
                   ", ".join(m.parse_mismatches)),
        GateResult("ocr_page_mismatches", len(m.ocr_mismatches), "reported", False, True,
                   ", ".join(m.ocr_mismatches)),
        GateResult("option_count_mismatches", len(m.option_count_mismatches), "reported",
                   False, True, ", ".join(m.option_count_mismatches)),
    ]
    return GateReport(res)


def _fmt(v: float | int | None) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def format_report(m: SuiteMetrics, g: GateReport) -> str:
    lines = ["Eval gates (R6)" + (f"  [subset run: {', '.join(m.subset_groups)}]"
                                   if m.subset_groups else ""), "-" * 72]
    for r in g.results:
        flag = ("PASS" if r.passed else "FAIL") if r.gated else "info"
        note = f"  ({r.note})" if r.note else ""
        lines.append(f"{flag:5} {r.name:52} {_fmt(r.value):>8}  {r.threshold}{note}")
    lines.append("-" * 72)
    lines.append("bucket          docs  present  correct  accuracy  verif_rate  halluc_rate  hard")
    for b in list(m.buckets.values()) + [m.overall]:
        lines.append(f"{b.bucket:15} {b.documents:4} {b.present:8} {b.correct:8} "
                     f"{_fmt(b.field_accuracy):>9} {_fmt(b.citation_verification_rate):>11} "
                     f"{_fmt(b.hallucinated_value_rate):>12} {b.hard_fails:5}")
    for d in m.hard_fail_details:
        lines.append(f"HARD FAIL {d['fixture_id']}.{d['field']}: {d['reason']} "
                     f"(predicted {d['predicted']!r}, truth {d['truth']!r}, page {d['page']})")
    for d in m.misses:
        lines.append(f"MISS {d['fixture_id']}.{d['field']}: {d['reason']} "
                     f"(predicted {d['predicted']!r}, truth {d['truth']!r}, state {d['state']})")
    for d in m.unlisted_fields:
        lines.append(f"UNLISTED {d['fixture_id']}.{d['field']}: not in the truth "
                     f"(predicted {d['predicted']!r}, state {d['state']})")
    if m.subset_groups:
        lines.append(f"subset run: only {', '.join(m.subset_groups)} executed; "
                     "gates for buckets that did not run are not evaluated")
    lines.append(f"overall: {'PASS' if g.passed else 'FAIL'}")
    return "\n".join(lines)


def report_dict(m: SuiteMetrics, g: GateReport) -> dict:
    return {"passed": g.passed, "gates": [asdict(r) for r in g.results], "metrics": asdict(m)}


def oracle_result(truth: Mapping, *, digital_state: str = "verified") -> DocResult:
    """A perfect pipeline output for ``truth``: every present field displayed
    with the truth value at its page. Facts read from images are scan_read.
    Used to self-test the scorer and as the reference for adapters."""
    exp = truth.get("expect", {})
    if exp.get("parse") == "failed":
        return DocResult(truth["fixture_id"], status="failed",
                         failure_reason=exp.get("failure_reason"))
    facts = []
    for fname, t in (truth.get("facts") or {}).items():
        if t is None:
            facts.append(PredictedFact(fname, None, None, None, "not_found"))
            continue
        state = "scan_read" if t.get("source") == "image" else digital_state
        facts.append(PredictedFact(fname, t["value"], t["page"], t["quote"], state))
    return DocResult(truth["fixture_id"], facts, option_count=exp.get("option_count", 1),
                     ocr_pages=list(exp.get("ocr_pages", [])))


# ---------------------------------------------------------------------------
# Proposal Builder (slice 2, task T1): table cells and prose
# ---------------------------------------------------------------------------
#
# One proposal fixture is one proposal run over two or three quote PDFs. The
# adapter returns the slice 1 per-document facts plus a ``proposal`` block:
#
#     {"run_status": str, "cells": [ProposalCell...], "prose": [ProseSegment...]}
#
# Gate (spec section 6, approved):
#   * hard fails == 0, where a hard fail is
#       - uncited_value: a cell shows a value with no citation;
#       - cited_wrong_page: a citation of a shown value points at a page where
#         the truth does not have that value, or into another document;
#       - wrong_value / value_for_absent_field: a shown value that differs from
#         the truth, or a value in a row that is null in the truth (as R6);
#       - prose_number_not_in_facts: prose states a money, percent, date or
#         limit-like number that no shown cell or cited fact holds;
#   * table-cell accuracy >= 0.85 over cells present in the truth;
#   * unlisted fields == 0 (fail-closed): a row or fact field the truth neither
#     scores nor lists in ``unscored_fields``, or a cell whose column maps to no
#     fixture document. Unscored rows still get the uncited and wrong-document
#     checks.

PROPOSAL_CELL_ACCURACY_MIN = 0.85
PROPOSAL_HARD_FAIL_KINDS = ("uncited_value", "value_for_absent_field", "wrong_value",
                            "cited_wrong_page", "prose_number_not_in_facts")


@dataclass
class ProposalCell:
    row: str  # fact field key
    document: str | None  # path of the column's document, as given to the adapter
    value: str | None
    state: str | None = None
    citations: list[dict] = field(default_factory=list)  # [{"document": path, "page": int}]
    block_id: str | None = None
    column: str | None = None
    conflicts: list[str | None] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.value, str) and not self.value.strip():
            self.value = None

    @property
    def displayed(self) -> bool:
        """A value shown as sourced. A cell with no state but a value counts as shown."""
        return self.value is not None and (self.state is None or self.state in DISPLAYED)

    @classmethod
    def from_dict(cls, d: Mapping) -> "ProposalCell":
        return cls(row=d["row"], document=d.get("document"), value=d.get("value"),
                   state=d.get("state"), citations=list(d.get("citations") or []),
                   block_id=d.get("block_id"), column=d.get("column"),
                   conflicts=list(d.get("conflicts") or []))


@dataclass
class ProseSegment:
    text: str
    section_id: str | None = None
    block_id: str | None = None
    citations: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Mapping) -> "ProseSegment":
        return cls(text=d.get("text") or "", section_id=d.get("section_id"),
                   block_id=d.get("block_id"), citations=list(d.get("citations") or []))


_PROSE_NUMBER = re.compile(
    r"(?:(?P<cur>\$)\s?)?(?<![\w.,])(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d,]*\d)"
    r"(?:\s?(?P<pct>%|percent\b|per\s?cent\b))?"
    r"(?:\s?(?P<mult>million\b|thousand\b|billion\b|mm\b|[mk]\b))?")


def _numbers(text: str | None, *, checked_only: bool) -> set[str]:
    """Number tokens in ``text``: ``@dYYYYMMDD`` dates and ``@n<value>[%]`` numbers.

    ``checked_only`` keeps the ones the backend validator checks in prose (R1):
    money, percents, dates, scaled or comma-grouped numbers, and plain numbers
    of 1,000 or more that are not a bare year. Section numbers, counts and
    small integers are not checked."""
    if not text:
        return set()
    s = _sub_dates(str(text).lower().replace(" ", " "))
    out = set(re.findall(r"@d\d{8}", s))
    s = re.sub(r"@d\d{8}", " ", s)
    for mo in _PROSE_NUMBER.finditer(s):
        raw = mo.group("num")
        try:
            val = Decimal(raw.replace(",", ""))
        except InvalidOperation:  # pragma: no cover - the regex guarantees digits
            continue
        cur, pct, mult = mo.group("cur"), mo.group("pct"), mo.group("mult")
        if mult:
            val *= _SCALE[mult]
        if checked_only:
            is_year = (not cur and not pct and not mult and "," not in raw and "." not in raw
                       and len(raw) == 4 and 1900 <= val <= 2100)
            if not (cur or pct or mult or "," in raw or (val >= 1000 and not is_year)):
                continue
        text_val = format(val.normalize(), "f")
        if "." in text_val:
            text_val = text_val.rstrip("0").rstrip(".")
        out.add(f"@n{text_val}{'%' if pct else ''}")
    return out


def checked_numbers(text: str | None) -> set[str]:
    return _numbers(text, checked_only=True)


def all_numbers(text: str | None) -> set[str]:
    return _numbers(text, checked_only=False)


def _norm_path(p: str | None) -> str | None:
    import os

    return None if p is None else os.path.normcase(os.path.normpath(str(p)))


@dataclass
class ProposalScore:
    proposal_id: str
    documents: int = 0
    run_status: str | None = None
    input_tokens: int = 0
    cells_present: int = 0
    cells_correct: int = 0
    cells_displayed: int = 0
    absent_cells: int = 0
    prose_segments: int = 0
    prose_numbers_checked: int = 0
    documents_failed: list[str] = field(default_factory=list)
    hard_fails: list[dict] = field(default_factory=list)
    unlisted: list[dict] = field(default_factory=list)
    misses: list[dict] = field(default_factory=list)
    decoy_hits: dict[str, int] = field(default_factory=dict)
    fact_scores: list[DocScore] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def score_proposal(proposal: Mapping, doc_truths: Mapping[str, Mapping], output: Mapping,
                   paths: Mapping[str, str]) -> ProposalScore:
    """Score one proposal run.

    ``proposal`` is a ``*.proposal.json`` truth, ``doc_truths`` maps fixture id
    to its document truth, ``output`` is the adapter's return value and
    ``paths`` maps fixture id to the path passed to the adapter."""
    pid = proposal["proposal_id"]
    fids = list(proposal["documents"])
    fid_for = {_norm_path(paths[f]): f for f in fids}
    unscored = set(proposal.get("unscored_fields") or {})
    block = output.get("proposal") or {}
    sc = ProposalScore(pid, documents=len(fids), run_status=block.get("run_status"),
                       input_tokens=int(output.get("input_tokens") or 0),
                       warnings=list(block.get("warnings") or []))
    docs = output.get("documents") or {}

    # Per-document facts (slice 1 scoring, fail-closed): reported, and their
    # unlisted fields count toward the gate.
    cited_fact_values: list[str] = []
    for fid in fids:
        raw = docs.get(paths[fid])
        if raw is None:
            raw = next((v for k, v in docs.items() if _norm_path(k) == _norm_path(paths[fid])), None)
        raw = raw or {"status": "failed", "failure_reason": "missing_from_adapter_output"}
        result = DocResult.from_dict({**raw, "fixture_id": fid})
        if result.status != "ok":
            sc.documents_failed.append(fid)
        sc.fact_scores.append(score_document(doc_truths[fid], result))
        sc.unlisted.extend(sc.fact_scores[-1].unlisted)
        cited_fact_values += [f.value for f in result.facts if f.displayed]

    cells = [ProposalCell.from_dict(c) for c in block.get("cells") or []]
    by: dict[tuple[str, str], list[ProposalCell]] = {}
    for cell in cells:
        fid = fid_for.get(_norm_path(cell.document))
        if fid is None:
            sc.unlisted.append({"fixture_id": pid, "field": cell.row, "predicted": cell.value,
                                "state": cell.state, "where": "cell",
                                "reason": f"column {cell.column!r} maps to no fixture document"})
            if cell.displayed and not cell.citations:
                sc.hard_fails.append({"fixture_id": pid, "field": cell.row, "kind": "uncited_value",
                                      "predicted": cell.value, "truth": None, "pages": []})
            continue
        by.setdefault((fid, cell.row), []).append(cell)
        tfacts = doc_truths[fid].get("facts") or {}
        if cell.row not in tfacts and cell.row not in unscored:
            sc.unlisted.append({"fixture_id": fid, "field": cell.row, "predicted": cell.value,
                                "state": cell.state, "where": "cell"})
        if not cell.displayed:
            continue
        sc.cells_displayed += 1
        cite_fids = [fid_for.get(_norm_path(c.get("document"))) for c in cell.citations]
        t = tfacts.get(cell.row) if cell.row in tfacts else None
        kind = detail = None
        if not cell.citations:
            kind, detail = "uncited_value", "value shown with no citation"
        elif cell.row in tfacts and t is None:
            kind, detail = "value_for_absent_field", "value shown for a field absent from the source"
        elif t is not None and not values_match(cell.value, t["value"], t.get("accepted_values", [])):
            kind, detail = "wrong_value", "shown value differs from the source"
            for d in doc_truths[fid].get("decoys") or []:
                if d["field"] == cell.row and values_match(cell.value, d["value"]):
                    k = d.get("kind", "decoy")
                    sc.decoy_hits[k] = sc.decoy_hits.get(k, 0) + 1
                    detail += f" (matches {k} decoy)"
                    break
        elif any(cf != fid for cf in cite_fids):
            kind, detail = "cited_wrong_page", "citation points into another document"
        elif t is not None and any(c.get("page") not in (t.get("pages") or [t.get("page")])
                                   for c in cell.citations):
            kind, detail = "cited_wrong_page", "citation points at a page without the value"
        if kind:
            sc.hard_fails.append({"fixture_id": fid, "field": cell.row, "kind": kind, "detail": detail,
                                  "predicted": cell.value, "truth": t["value"] if t else None,
                                  "pages": [c.get("page") for c in cell.citations]})

    # Table-cell accuracy over cells present in the truth.
    for fid in fids:
        for fname, t in (doc_truths[fid].get("facts") or {}).items():
            if t is None:
                sc.absent_cells += 1
                continue
            sc.cells_present += 1
            found = by.get((fid, fname), [])
            ok = any(c.displayed and values_match(c.value, t["value"], t.get("accepted_values", []))
                     for c in found)
            if ok:
                sc.cells_correct += 1
                continue
            shown = [c for c in found if c.displayed]
            reason = ("missing_row" if not found else "wrong_value" if shown else "not_shown")
            pick = shown[0] if shown else (found[0] if found else None)
            sc.misses.append({"fixture_id": fid, "field": fname, "reason": reason, "truth": t["value"],
                              "predicted": pick.value if pick else None,
                              "state": pick.state if pick else None})

    # Prose numbers must come from the facts (shown cells, conflicts, cited facts).
    allowed: set[str] = set()
    for v in cited_fact_values + [c.value for c in cells if c.displayed] + \
            [v for c in cells for v in c.conflicts]:
        allowed |= all_numbers(v)
    for seg in (ProseSegment.from_dict(s) for s in block.get("prose") or []):
        sc.prose_segments += 1
        nums = checked_numbers(seg.text)
        sc.prose_numbers_checked += len(nums)
        for tok in sorted(nums - allowed):
            sc.hard_fails.append({"fixture_id": pid, "field": seg.block_id or seg.section_id,
                                  "kind": "prose_number_not_in_facts",
                                  "detail": f"{tok} not in any fact: {seg.text[:120]!r}",
                                  "predicted": tok, "truth": None, "pages": []})
    return sc


@dataclass
class ProposalMetrics:
    proposals: list[dict]
    cells_present: int
    cells_correct: int
    cell_accuracy: float | None
    cells_displayed: int
    hard_fails: list[dict]
    unlisted_fields: list[dict]
    misses: list[dict]
    decoy_hits: dict[str, int]
    prose_segments: int
    prose_numbers_checked: int
    fact_accuracy: float | None
    fact_hard_fails: int
    documents_failed: list[str]
    max_run_input_tokens: int | None
    warnings: list[str] = field(default_factory=list)


def aggregate_proposals(scores: list[ProposalScore]) -> ProposalMetrics:
    present = sum(s.cells_present for s in scores)
    correct = sum(s.cells_correct for s in scores)
    fscores = [f for s in scores for f in s.fact_scores]
    decoys: dict[str, int] = {}
    for s in scores:
        for k, v in s.decoy_hits.items():
            decoys[k] = decoys.get(k, 0) + v
    return ProposalMetrics(
        proposals=[{"proposal_id": s.proposal_id, "documents": s.documents, "run_status": s.run_status,
                    "cells_present": s.cells_present, "cells_correct": s.cells_correct,
                    "cell_accuracy": _ratio(s.cells_correct, s.cells_present),
                    "hard_fails": len(s.hard_fails), "input_tokens": s.input_tokens} for s in scores],
        cells_present=present, cells_correct=correct, cell_accuracy=_ratio(correct, present),
        cells_displayed=sum(s.cells_displayed for s in scores),
        hard_fails=[h for s in scores for h in s.hard_fails],
        unlisted_fields=[u for s in scores for u in s.unlisted],
        misses=[m for s in scores for m in s.misses],
        decoy_hits=decoys,
        prose_segments=sum(s.prose_segments for s in scores),
        prose_numbers_checked=sum(s.prose_numbers_checked for s in scores),
        fact_accuracy=_ratio(sum(f.correct for f in fscores), sum(f.present for f in fscores)),
        fact_hard_fails=sum(f.hard_fails for f in fscores),
        documents_failed=[d for s in scores for d in s.documents_failed],
        max_run_input_tokens=max((s.input_tokens for s in scores), default=None),
        warnings=[f"{s.proposal_id}: {w}" for s in scores for w in s.warnings],
    )


def evaluate_proposal_gates(m: ProposalMetrics) -> GateReport:
    """The approved proposal gate. No scored cells is a failure, not a pass."""
    hard = len(m.hard_fails)
    kinds = ", ".join(f"{k} {sum(1 for h in m.hard_fails if h['kind'] == k)}"
                      for k in PROPOSAL_HARD_FAIL_KINDS if any(h["kind"] == k for h in m.hard_fails))
    acc = m.cell_accuracy
    res = [
        GateResult("proposal_hard_fails (uncited, wrong page, wrong value, prose number)", hard,
                   f"== {HARD_FAIL_MAX}", True, hard <= HARD_FAIL_MAX, kinds),
        GateResult("cell_accuracy[proposal]", acc, f">= {PROPOSAL_CELL_ACCURACY_MIN:.2f}", True,
                   acc is not None and acc >= PROPOSAL_CELL_ACCURACY_MIN,
                   "no data" if acc is None else ""),
        GateResult("unlisted_fields[proposal] (fail-closed)", len(m.unlisted_fields), "== 0", True,
                   not m.unlisted_fields, "output field or column the truth does not list"),
    ]
    if m.max_run_input_tokens is not None:
        res.append(GateResult("max_run_input_tokens", m.max_run_input_tokens,
                              f"<= {RUN_INPUT_TOKEN_BUDGET}", True,
                              m.max_run_input_tokens <= RUN_INPUT_TOKEN_BUDGET))
    res += [
        GateResult("fact_accuracy[proposal documents]", m.fact_accuracy, "reported", False, True),
        GateResult("fact_hard_fails[proposal documents]", m.fact_hard_fails, "reported", False, True,
                   "extraction level; the gate counts what the proposal shows"),
        GateResult("prose_numbers_checked", m.prose_numbers_checked, "reported", False, True,
                   f"{m.prose_segments} prose segments"),
        GateResult("documents_failed", len(m.documents_failed), "reported", False, True,
                   ", ".join(m.documents_failed)),
    ]
    return GateReport(res)


def format_proposal_report(m: ProposalMetrics, g: GateReport) -> str:
    lines = ["Proposal eval gates (slice 2, T1)", "-" * 72]
    for r in g.results:
        flag = ("PASS" if r.passed else "FAIL") if r.gated else "info"
        note = f"  ({r.note})" if r.note else ""
        lines.append(f"{flag:5} {r.name:60} {_fmt(r.value):>8}  {r.threshold}{note}")
    lines.append("-" * 72)
    lines.append("proposal                    docs  status      cells  correct  accuracy  hard  tokens")
    for p in m.proposals:
        lines.append(f"{p['proposal_id']:27} {p['documents']:4}  {str(p['run_status']):10} "
                     f"{p['cells_present']:6} {p['cells_correct']:8} {_fmt(p['cell_accuracy']):>9} "
                     f"{p['hard_fails']:5} {p['input_tokens']:7}")
    for h in m.hard_fails:
        lines.append(f"HARD FAIL {h['fixture_id']}.{h['field']}: {h['kind']}: {h.get('detail', '')} "
                     f"(predicted {h['predicted']!r}, truth {h['truth']!r}, pages {h['pages']})")
    for u in m.unlisted_fields:
        lines.append(f"UNLISTED {u['fixture_id']}.{u['field']} ({u['where']}): "
                     f"{u.get('reason', 'not in the truth')} (predicted {u['predicted']!r})")
    for d in m.misses:
        lines.append(f"MISS {d['fixture_id']}.{d['field']}: {d['reason']} "
                     f"(predicted {d['predicted']!r}, truth {d['truth']!r}, state {d['state']})")
    for w in m.warnings:
        lines.append(f"WARNING {w}")
    lines.append(f"overall: {'PASS' if g.passed else 'FAIL'}")
    return "\n".join(lines)


def proposal_report_dict(m: ProposalMetrics, g: GateReport) -> dict:
    return {"passed": g.passed, "gates": [asdict(r) for r in g.results], "metrics": asdict(m)}


def proposal_oracle_output(proposal: Mapping, doc_truths: Mapping[str, Mapping],
                           paths: Mapping[str, str], *, input_tokens: int = 1000) -> dict:
    """A perfect adapter output for one proposal: oracle facts per document, one
    cell per truth field citing the truth page, and prose that restates a fact
    value per document. Self-tests the scorer and documents the adapter shape."""
    documents: dict[str, dict] = {}
    cells: list[dict] = []
    prose: list[dict] = [{"section_id": "cover", "block_id": "cover",
                          "text": f"Insurance proposal prepared by {proposal['parameters']['broker_name']}",
                          "citations": []}]
    for i, fid in enumerate(proposal["documents"]):
        truth, path = doc_truths[fid], paths[fid]
        r = oracle_result(truth)
        documents[path] = {"status": r.status, "failure_reason": r.failure_reason,
                           "option_count": r.option_count, "ocr_pages": r.ocr_pages,
                           "facts": [vars(f) for f in r.facts]}
        column = chr(ord("A") + i)
        for fname, t in (truth.get("facts") or {}).items():
            cells.append({"block_id": "oracle-table", "row": fname, "column": column, "document": path,
                          "value": t["value"] if t else None,
                          "state": "verified" if t else "not_found",
                          "citations": [{"document": path, "page": t["page"]}] if t else [],
                          "conflicts": []})
        total = (truth.get("facts") or {}).get("total_premium")
        if total:
            prose.append({"section_id": "premium", "block_id": "premium-prose",
                          "text": f"{truth.get('carrier')} quotes a total payable of {total['value']}.",
                          "citations": [{"document": path, "page": total["page"]}]})
    return {"input_tokens": input_tokens, "documents": documents,
            "proposal": {"workflow": proposal.get("workflow"), "run_status": "done",
                         "cells": cells, "prose": prose, "warnings": []}}


# ---------------------------------------------------------------------------
# Policy Check (slice 3, T7): discrepancy items and their slots
# ---------------------------------------------------------------------------
#
# One policy case is one Policy Check run over an expiring policy, a renewal and
# optionally a quote and an AMS export (``tests/fixtures/policy/*.case.json``).
# The adapter returns the per-document facts plus a ``policy_check`` block:
#
#     {"run_status": str, "roles": {role: path}, "warnings": [str],
#      "items": [{"id", "key", "title", "severity", "item_state", "explanation",
#                 "slots": {role: {"document": path, "state": evidence state,
#                                  "value", "citations": [{"document", "page"}],
#                                  "conflicts": [{"value", "page"}]}}}]}
#
# A role missing from ``slots`` is N/A (no document). Slot evidence states:
# verified / scan_read show a value; absent is N/A (not stated); failed is
# Not read; conflicting / not_found / pending is Needs review (D15).
#
# Gate (spec section 7, amended by D16):
#   * hard fails == 0, where a hard fail is
#       - unverified_value: a verified slot value that differs from the source,
#         or a value for a key the document does not state (cited value failing
#         verification; scan_read values are reported only, as R6 and D5);
#       - missing_citation: a shown slot value with no citation;
#       - wrong_page_citation: a citation on a page that does not hold the value,
#         or into another document than the slot's role;
#       - severity_violates_table: the item's severity is not what the severity
#         table gives for the change its own slots show (or the slots show no
#         change at all);
#       - injection_canary: an injection canary shown in any slot, as a value or
#         in a conflict list;
#       - wrong_field: a shown value that belongs to another canonical field of
#         the same document per the ground truth (or a value printed under a
#         label that only looks like the field, a ``wrong_field`` decoy);
#   * seeded-discrepancy recall >= 0.80 (D16 starting value), no data fails;
#   * no empty result: every case must report at least one item;
#   * max input tokens per run within the run budget, when reported.
# Precision, slot accuracy and severity agreement with the truth are reported.

POLICY_RECALL_MIN = 0.80
POLICY_ROLES = ("expiring", "quote", "renewal", "ams")
POLICY_OPTIONAL_ROLES = ("quote", "ams")
POLICY_HARD_FAIL_KINDS = ("unverified_value", "missing_citation", "wrong_page_citation",
                          "severity_violates_table", "injection_canary", "wrong_field")
_FORM_PREFIX = "form:"

# Mirror of backend/pipeline/diff.py (FACT_TYPES, SEVERITY_TABLE, UNIVERSAL_SEVERITY,
# NOT_COMPARED_WITH_EXPIRING). The eval derives the expected severity of an item from its
# own slots with this copy; backend/tests/test_eval_hook_policy.py checks the copy against
# the backend so the two cannot drift silently.
POLICY_FACT_TYPES: dict[str, str] = {
    "policy_number": "identifier", "policy_term": "term", "named_insured": "text",
    "insured_address": "address",
    **{f"coverage_{c}_limit": "limit" for c in "abcdef"},
    "deductible_all_peril": "deductible", "deductible_wind_hail": "deductible",
    "deductible_hurricane": "deductible", "total_premium": "premium",
    "discount_multi_policy": "discount", "discount_protective_device": "discount",
    "discount_claims_free": "discount", "discount_loyalty": "discount",
}
_C, _H, _M, _L = "critical", "high", "medium", "low"
POLICY_SEVERITY_TABLE: dict[str, dict[str, str]] = {
    "limit": {"decrease_over_10pct": _C, "decrease": _H, "increase": _H, "changed": _H, "removed": _H,
              "added": _M},
    "deductible": {"increase": _C, "added": _C, "decrease": _M, "changed": _H, "removed": _M},
    "premium": {"increase": _M, "decrease": _L, "changed": _M, "removed": _M, "added": _L},
    "discount": {"removed": _H, "increase": _L, "decrease": _L, "changed": _L, "added": _L},
    "form": {"edition_changed": _M, "changed": _M, "removed": _H, "added": _M},
    "text": {"changed": _M, "removed": _M, "added": _L},
    "address": {"changed": _M, "removed": _M, "added": _L},
    "identifier": {"changed": _L, "removed": _L, "added": _L},
    "term": {},
    "other": {"increase": _L, "decrease": _L, "changed": _L, "removed": _L, "added": _L},
}
POLICY_UNIVERSAL_SEVERITY = {"mismatch": _H, "needs_review": _L}
POLICY_NOT_COMPARED_WITH_EXPIRING = frozenset({"policy_term"})
POLICY_NUMERIC_TYPES = frozenset({"limit", "deductible", "premium", "discount", "other"})
SEVERITY_ORDER = {_C: 0, _H: 1, _M: 2, _L: 3}

_NEEDS_REVIEW_STATES = ("conflicting", "not_found", "pending")


def policy_fact_type(key: str) -> str:
    if key.startswith(_FORM_PREFIX) and len(key) > len(_FORM_PREFIX):
        return "form"
    return POLICY_FACT_TYPES.get(key, "other")


def policy_severity(ftype: str, change: str) -> str | None:
    if change in POLICY_UNIVERSAL_SEVERITY:
        return POLICY_UNIVERSAL_SEVERITY[change]
    return POLICY_SEVERITY_TABLE.get(ftype, {}).get(change)


def slot_view(slot: Mapping | None) -> str:
    """The slot as the card shows it: value | not_stated | no_document | not_read | needs_review."""
    if slot is None:
        return "no_document"
    state = slot.get("state")
    if state in DISPLAYED and slot.get("value") not in (None, ""):
        return "value"
    if state == "failed":
        return "not_read"
    if state in _NEEDS_REVIEW_STATES:
        return "needs_review"
    return "not_stated"


# ---- value comparison (mirrors diff.py normalization, written independently of it)

_P_AMOUNT = re.compile(
    r"(?<![\w.,])(-|\()?\s*(?:usd\s*)?(\$)?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|percent\b)?"
    r"(?:\s*(k|m|mm|thousand|million)\b)?", re.I)
_P_MULT = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "mm": 1_000_000, "million": 1_000_000}
_P_DATE = re.compile(
    r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b|\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b"
    r"|\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
_P_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_P_ADDRESS = {"st": "street", "ave": "avenue", "av": "avenue", "rd": "road", "dr": "drive", "ln": "lane",
              "blvd": "boulevard", "ct": "court", "hwy": "highway", "pkwy": "parkway", "pl": "place",
              "cir": "circle", "ter": "terrace", "apt": "apartment", "ste": "suite", "n": "north",
              "s": "south", "e": "east", "w": "west"}


def _p_amount(value: str | None) -> tuple[Decimal, bool] | None:
    found = list(_P_AMOUNT.finditer(value or ""))
    if len(found) != 1:
        return None
    mo = found[0]
    num = Decimal(mo.group(3).replace(",", "") + (mo.group(4) or ""))
    if mo.group(6):
        num *= _P_MULT[mo.group(6).lower()]
    if mo.group(1):
        num = -num
    return num, bool(mo.group(5))


def _p_dates(value: str | None) -> tuple[str, ...]:
    out = []
    for mo in _P_DATE.finditer(value or ""):
        if mo.group(1):
            y, m, d = int(mo.group(1)), int(mo.group(2)), int(mo.group(3))
        elif mo.group(4):
            m, d, y = int(mo.group(4)), int(mo.group(5)), int(mo.group(6))
            y = y + 2000 if y < 100 else y
        else:
            m = _P_MONTHS.get(mo.group(7).lower())
            if m is None:
                continue
            d, y = int(mo.group(8)), int(mo.group(9))
        if 1 <= m <= 12 and 1 <= d <= 31:
            out.append(f"{y:04d}-{m:02d}-{d:02d}")
    return tuple(out)


def _p_text(value: str | None, *, address: bool = False) -> str:
    words = re.findall(r"[a-z0-9]+", (value or "").replace("&", " and ").casefold())
    if address:
        words = [_P_ADDRESS.get(w, w) for w in words]
    return " ".join(words)


def form_edition(key: str, value: str | None) -> str | None:
    """Edition "MM/YY" of a form value that starts with the key's form number, else None."""
    if not key.startswith(_FORM_PREFIX) or not value:
        return None
    number, i, text = key[len(_FORM_PREFIX):], 0, value.upper()
    for ch in number:
        while i < len(text) and not text[i].isalnum():
            i += 1
        if i >= len(text) or text[i] != ch:
            return None
        i += 1
    mo = re.match(r"\W*(\d{1,2})\W*(\d{4}|\d{2})(?!\d)", value[i:])
    if not mo or not 1 <= int(mo.group(1)) <= 12:
        return None
    return f"{int(mo.group(1)):02d}/{mo.group(2)[-2:]}"


def policy_change(ftype: str, key: str, a: str, b: str) -> str | None:
    """None when ``a`` (the baseline) and ``b`` are one value, else the table change of b vs a."""
    if ftype == "form":
        ea, eb = form_edition(key, a), form_edition(key, b)
        if ea and eb:
            return None if ea == eb else "edition_changed"
        return None if _p_text(a).replace(" ", "") == _p_text(b).replace(" ", "") else "changed"
    if ftype == "term":
        da, db = _p_dates(a), _p_dates(b)
        if da and db:
            return None if da == db else "changed"
    if ftype in POLICY_NUMERIC_TYPES:
        na, nb = _p_amount(a), _p_amount(b)
        if na is not None and nb is not None:
            if na[1] != nb[1]:
                return "changed"
            va, vb = (abs(na[0]), abs(nb[0])) if ftype == "discount" else (na[0], nb[0])
            if va == vb:
                return None
            if vb > va:
                return "increase"
            over = ftype == "limit" and va > 0 and not na[1] and (va - vb) / va > Decimal("0.1")
            return "decrease_over_10pct" if over else "decrease"
    addr = ftype == "address"
    return None if _p_text(a, address=addr) == _p_text(b, address=addr) else "changed"


def policy_values_match(key: str, predicted: str | None, truth: str | None,
                        accepted: Iterable[str] = ()) -> bool:
    """values_match, plus: a form value matches by form number and edition."""
    if key.startswith(_FORM_PREFIX) and predicted and truth:
        ep, et = form_edition(key, predicted), form_edition(key, truth)
        if ep and et:
            return ep == et
    return values_match(predicted, truth, accepted)


def expected_item_severity(item: Mapping) -> tuple[str | None, str]:
    """(severity the table gives for the change the item's own slots show, why). None: the
    slots support no item at all."""
    key = item.get("key") or ""
    ftype = policy_fact_type(key)
    slots = item.get("slots") or {}
    view = {r: slot_view(slots.get(r)) for r in POLICY_ROLES}
    val = {r: (slots.get(r) or {}).get("value") if view[r] == "value" else None for r in POLICY_ROLES}
    compare_e = key not in POLICY_NOT_COMPARED_WITH_EXPIRING
    uncertain = [r for r in (("expiring", "renewal") if compare_e else ("renewal",))
                 if view[r] in ("needs_review", "not_read")]
    if uncertain:
        return POLICY_UNIVERSAL_SEVERITY["needs_review"], f"needs review ({', '.join(uncertain)})"
    if item.get("item_state") == "needs_review":
        return None, "needs review item with no conflicting or unread expiring or renewal slot"
    found: list[tuple[str | None, str]] = []
    if compare_e:
        if val["expiring"] and val["renewal"]:
            ch = policy_change(ftype, key, val["expiring"], val["renewal"])
        elif val["expiring"]:
            ch = "removed"
        elif val["renewal"]:
            ch = "added"
        else:
            ch = None
        if ch:
            found.append((policy_severity(ftype, ch), ch))
    for r in POLICY_OPTIONAL_ROLES:
        if val[r] and (not val["renewal"] or policy_change(ftype, key, val["renewal"], val[r]) is not None):
            found.append((POLICY_UNIVERSAL_SEVERITY["mismatch"], f"{r} mismatch"))
    found = [f for f in found if f[0] is not None]
    if not found:
        return None, "slots show no difference"
    best = min(found, key=lambda f: SEVERITY_ORDER[f[0]])
    return best[0], " + ".join(f[1] for f in found)


@dataclass
class PolicyScore:
    case_id: str
    run_status: str | None = None
    input_tokens: int = 0
    items: int = 0
    seeded: int = 0
    found: int = 0
    true_positive_items: int = 0
    slots_checked: int = 0
    slots_correct: int = 0
    severity_agree: int = 0
    scan_read_wrong: list[dict] = field(default_factory=list)
    hard_fails: list[dict] = field(default_factory=list)
    misses: list[dict] = field(default_factory=list)
    false_positives: list[dict] = field(default_factory=list)
    documents_failed: list[str] = field(default_factory=list)
    fact_scores: list[DocScore] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _canaries(doc_truth: Mapping) -> list[dict]:
    return [d for d in doc_truth.get("decoys") or [] if d.get("kind") == "injection_canary"]


def _hard(sc: PolicyScore, kind: str, item: Mapping, role: str | None, detail: str,
          predicted=None, truth=None, pages=None) -> None:
    sc.hard_fails.append({"case_id": sc.case_id, "key": item.get("key"), "role": role, "kind": kind,
                          "detail": detail, "predicted": predicted, "truth": truth, "pages": pages or []})


def _check_slot(sc: PolicyScore, item: Mapping, role: str, slot: Mapping, fid: str | None,
                doc_truth: Mapping | None, fid_for: Mapping[str | None, str]) -> None:
    key = item.get("key") or ""
    value = slot.get("value")
    canaries = _canaries(doc_truth) if doc_truth else []
    for c in slot.get("conflicts") or []:
        cv = c.get("value") if isinstance(c, Mapping) else c
        if cv and any(values_match(cv, d["value"]) for d in canaries):
            _hard(sc, "injection_canary", item, role, "injection canary listed as a stated value", cv)
    if slot_view(slot) != "value":
        return
    cites = list(slot.get("citations") or [])
    if any(values_match(value, d["value"]) for d in canaries):
        _hard(sc, "injection_canary", item, role, "injection canary shown as a value", value,
              pages=[c.get("page") for c in cites])
        return
    if not cites:
        _hard(sc, "missing_citation", item, role, "value shown with no citation", value)
    elif any(fid_for.get(_norm_path(c.get("document"))) != fid for c in cites):
        _hard(sc, "wrong_page_citation", item, role, "citation points into another document", value,
              pages=[c.get("page") for c in cites])
    if doc_truth is None:
        return
    facts = doc_truth.get("facts") or {}
    t = facts.get(key)
    if t is not None and policy_values_match(key, value, t["value"], t.get("accepted_values", [])):
        pages = t.get("pages") or [t.get("page")]
        bad = [c.get("page") for c in cites if fid_for.get(_norm_path(c.get("document"))) == fid
               and c.get("page") not in pages]
        if bad:
            _hard(sc, "wrong_page_citation", item, role, "citation points at a page without the value", value,
                  t["value"], bad)
        return
    other = next((k for k, f in facts.items() if k != key and f is not None
                  and policy_values_match(k, value, f["value"], f.get("accepted_values", []))), None)
    decoy = next((d for d in doc_truth.get("decoys") or [] if d.get("kind") == "wrong_field"
                  and d.get("field") == key and values_match(value, d["value"])), None)
    if other or decoy:
        why = (f"value belongs to {other}" if other else
               f"value is printed under a label that only looks like {key}")
        _hard(sc, "wrong_field", item, role, why, value, t["value"] if t else None)
        return
    if slot.get("state") == "scan_read":
        sc.scan_read_wrong.append({"case_id": sc.case_id, "key": key, "role": role, "predicted": value,
                                   "truth": t["value"] if t else None})
        return
    _hard(sc, "unverified_value", item, role,
          "verified value differs from the source" if t else "value for a key the document does not state",
          value, t["value"] if t else None)


def score_policy_case(case: Mapping, doc_truths: Mapping[str, Mapping], output: Mapping,
                      paths: Mapping[str, str]) -> PolicyScore:
    """Score one Policy Check run. ``case`` is a ``*.case.json`` truth, ``doc_truths`` maps
    fixture id to document truth, ``output`` is the adapter's return value and ``paths``
    maps fixture id to the path given to the adapter."""
    cid = case["case_id"]
    roles: Mapping[str, str] = case["roles"]
    fid_for = {_norm_path(paths[f]): f for f in roles.values()}
    block = output.get("policy_check") or {}
    sc = PolicyScore(cid, run_status=block.get("run_status"), input_tokens=int(output.get("input_tokens") or 0),
                     warnings=list(block.get("warnings") or []))
    docs = output.get("documents") or {}
    for fid in roles.values():
        raw = docs.get(paths[fid])
        if raw is None:
            raw = next((v for k, v in docs.items() if _norm_path(k) == _norm_path(paths[fid])), None)
        raw = raw or {"status": "failed", "failure_reason": "missing_from_adapter_output"}
        result = DocResult.from_dict({**raw, "fixture_id": fid})
        if result.status != "ok":
            sc.documents_failed.append(fid)
        sc.fact_scores.append(score_document(doc_truths[fid], result))

    items = list(block.get("items") or [])
    sc.items = len(items)
    seeds = {d["key"]: d for d in case.get("discrepancies") or []}
    no_item = {d["key"]: d["reason"] for d in case.get("no_item_keys") or []}
    sc.seeded = len(seeds)
    by_key: dict[str, Mapping] = {}
    for item in items:
        key = item.get("key") or ""
        by_key.setdefault(key, item)
        for role, slot in (item.get("slots") or {}).items():
            fid = roles.get(role)
            _check_slot(sc, item, role, slot or {}, fid, doc_truths.get(fid) if fid else None, fid_for)
        expected, why = expected_item_severity(item)
        if expected is None or item.get("severity") != expected:
            _hard(sc, "severity_violates_table", item, None,
                  f"severity {item.get('severity')!r}, table gives {expected!r} ({why})", item.get("severity"),
                  expected)
        seed = seeds.get(key)
        if seed is not None and item.get("item_state") == seed["item_state"]:
            sc.true_positive_items += 1
        else:
            sc.false_positives.append({"case_id": cid, "key": key, "severity": item.get("severity"),
                                       "item_state": item.get("item_state"), "title": item.get("title"),
                                       "expected_no_item": no_item.get(key)})

    for key, seed in seeds.items():
        item = by_key.get(key)
        if item is None or item.get("item_state") != seed["item_state"]:
            sc.misses.append({"case_id": cid, "key": key, "kind": seed.get("kind"),
                              "reason": "no item" if item is None else f"item_state {item.get('item_state')}",
                              "expected_severity": seed["severity"]})
            continue
        sc.found += 1
        if item.get("severity") == seed["severity"]:
            sc.severity_agree += 1
        for role in POLICY_ROLES:
            exp = seed["slots"][role]
            got = (item.get("slots") or {}).get(role)
            sc.slots_checked += 1
            view = slot_view(got)
            if exp["state"] == "value":
                fid = roles.get(role)
                t = (doc_truths.get(fid) or {}).get("facts", {}).get(key) if fid else None
                ok = view == "value" and policy_values_match(key, got.get("value"), exp["value"],
                                                             (t or {}).get("accepted_values", []))
            else:
                ok = view == exp["state"]
            sc.slots_correct += int(ok)
    return sc


@dataclass
class PolicyMetrics:
    cases: list[dict]
    seeded: int
    found: int
    recall: float | None
    items: int
    true_positive_items: int
    precision: float | None
    slots_checked: int
    slots_correct: int
    slot_accuracy: float | None
    severity_agreement: float | None
    hard_fails: list[dict]
    misses: list[dict]
    false_positives: list[dict]
    scan_read_wrong: list[dict]
    empty_cases: list[str]
    fact_accuracy: float | None
    documents_failed: list[str]
    max_run_input_tokens: int | None
    warnings: list[str] = field(default_factory=list)


def aggregate_policy(scores: list[PolicyScore]) -> PolicyMetrics:
    seeded = sum(s.seeded for s in scores)
    found = sum(s.found for s in scores)
    items = sum(s.items for s in scores)
    tp = sum(s.true_positive_items for s in scores)
    checked = sum(s.slots_checked for s in scores)
    correct = sum(s.slots_correct for s in scores)
    fscores = [f for s in scores for f in s.fact_scores]
    return PolicyMetrics(
        cases=[{"case_id": s.case_id, "run_status": s.run_status, "items": s.items, "seeded": s.seeded,
                "found": s.found, "recall": _ratio(s.found, s.seeded), "hard_fails": len(s.hard_fails),
                "input_tokens": s.input_tokens} for s in scores],
        seeded=seeded, found=found, recall=_ratio(found, seeded),
        items=items, true_positive_items=tp, precision=_ratio(tp, items),
        slots_checked=checked, slots_correct=correct, slot_accuracy=_ratio(correct, checked),
        severity_agreement=_ratio(sum(s.severity_agree for s in scores), found),
        hard_fails=[h for s in scores for h in s.hard_fails],
        misses=[m for s in scores for m in s.misses],
        false_positives=[f for s in scores for f in s.false_positives],
        scan_read_wrong=[w for s in scores for w in s.scan_read_wrong],
        empty_cases=[s.case_id for s in scores if s.items == 0],
        fact_accuracy=_ratio(sum(f.correct for f in fscores), sum(f.present for f in fscores)),
        documents_failed=[d for s in scores for d in s.documents_failed],
        max_run_input_tokens=max((s.input_tokens for s in scores), default=None),
        warnings=[f"{s.case_id}: {w}" for s in scores for w in s.warnings],
    )


def evaluate_policy_gates(m: PolicyMetrics) -> GateReport:
    """The D16 gate. No cases, no seeded discrepancies or an empty result is a failure."""
    hard = len(m.hard_fails)
    kinds = ", ".join(f"{k} {sum(1 for h in m.hard_fails if h['kind'] == k)}"
                      for k in POLICY_HARD_FAIL_KINDS if any(h["kind"] == k for h in m.hard_fails))
    res = [
        GateResult("policy_hard_fails (unverified, citation, severity, canary, wrong field)", hard,
                   f"== {HARD_FAIL_MAX}", True, hard <= HARD_FAIL_MAX, kinds),
        GateResult("seeded_recall[policy_check]", m.recall, f">= {POLICY_RECALL_MIN:.2f}", True,
                   m.recall is not None and m.recall >= POLICY_RECALL_MIN,
                   "no data" if m.recall is None else f"{m.found}/{m.seeded}"),
        GateResult("empty_result[policy_check] (cases with no item)", len(m.empty_cases) if m.cases else None,
                   "== 0", True, bool(m.cases) and not m.empty_cases,
                   ", ".join(m.empty_cases) if m.cases else "no data"),
    ]
    if m.max_run_input_tokens is not None:
        res.append(GateResult("max_run_input_tokens", m.max_run_input_tokens, f"<= {RUN_INPUT_TOKEN_BUDGET}",
                              True, m.max_run_input_tokens <= RUN_INPUT_TOKEN_BUDGET))
    res += [
        GateResult("precision[policy_check]", m.precision, "reported", False, True,
                   f"{m.true_positive_items}/{m.items} items match a seeded discrepancy"),
        GateResult("slot_accuracy[policy_check]", m.slot_accuracy, "reported", False, True,
                   "four slots of every found seeded item"),
        GateResult("severity_agreement[policy_check]", m.severity_agreement, "reported", False, True,
                   "found items whose severity equals the truth"),
        GateResult("scan_read_wrong_values", len(m.scan_read_wrong), "reported", False, True,
                   "scan-read slot values that differ from the source (labeled unverified, D5)"),
        GateResult("fact_accuracy[policy documents]", m.fact_accuracy, "reported", False, True),
        GateResult("documents_failed", len(m.documents_failed), "reported", False, True,
                   ", ".join(m.documents_failed)),
    ]
    return GateReport(res)


def format_policy_report(m: PolicyMetrics, g: GateReport) -> str:
    lines = ["Policy Check eval gates (slice 3, T7, D16)", "-" * 72]
    for r in g.results:
        flag = ("PASS" if r.passed else "FAIL") if r.gated else "info"
        note = f"  ({r.note})" if r.note else ""
        lines.append(f"{flag:5} {r.name:62} {_fmt(r.value):>8}  {r.threshold}{note}")
    lines.append("-" * 72)
    lines.append("case                    status      items  seeded  found  recall  hard  tokens")
    for c in m.cases:
        lines.append(f"{c['case_id']:23} {str(c['run_status']):10} {c['items']:6} {c['seeded']:7} "
                     f"{c['found']:6} {_fmt(c['recall']):>7} {c['hard_fails']:5} {c['input_tokens']:7}")
    for h in m.hard_fails:
        lines.append(f"HARD FAIL {h['case_id']}.{h['key']}[{h['role'] or '-'}]: {h['kind']}: {h['detail']} "
                     f"(predicted {h['predicted']!r}, truth {h['truth']!r}, pages {h['pages']})")
    for d in m.misses:
        lines.append(f"MISS {d['case_id']}.{d['key']} ({d['kind']}): {d['reason']}, expected "
                     f"{d['expected_severity']}")
    for f in m.false_positives:
        extra = f"; expected no item: {f['expected_no_item']}" if f["expected_no_item"] else ""
        lines.append(f"EXTRA {f['case_id']}.{f['key']}: {f['item_state']} {f['severity']} {f['title']!r}{extra}")
    for w in m.scan_read_wrong:
        lines.append(f"SCAN_READ {w['case_id']}.{w['key']}[{w['role']}]: predicted {w['predicted']!r}, "
                     f"truth {w['truth']!r}")
    for w in m.warnings:
        lines.append(f"WARNING {w}")
    lines.append(f"overall: {'PASS' if g.passed else 'FAIL'}")
    return "\n".join(lines)


def policy_report_dict(m: PolicyMetrics, g: GateReport) -> dict:
    return {"passed": g.passed, "gates": [asdict(r) for r in g.results], "metrics": asdict(m)}


def policy_oracle_output(case: Mapping, doc_truths: Mapping[str, Mapping], paths: Mapping[str, str], *,
                         input_tokens: int = 1000) -> dict:
    """A perfect adapter output for one case: oracle facts per document and one item per
    seeded discrepancy with the truth's slots, severity and citations. Self-tests the
    scorer and documents the adapter shape."""
    roles: Mapping[str, str] = case["roles"]
    documents: dict[str, dict] = {}
    for fid in roles.values():
        r = oracle_result(doc_truths[fid])
        documents[paths[fid]] = {"status": r.status, "failure_reason": r.failure_reason,
                                 "option_count": r.option_count, "ocr_pages": r.ocr_pages,
                                 "facts": [vars(f) for f in r.facts]}
    items = []
    for seed in case.get("discrepancies") or []:
        slots: dict[str, dict] = {}
        for role in POLICY_ROLES:
            exp = seed["slots"][role]
            if exp["state"] == "no_document":
                continue
            fid = roles[role]
            path = paths[fid]
            if exp["state"] == "value":
                t = doc_truths[fid]["facts"][seed["key"]]
                state = "scan_read" if t.get("source") == "image" else "verified"
                slots[role] = {"document": path, "state": state, "value": exp["value"],
                               "citations": [{"document": path, "page": t["page"]}], "conflicts": []}
            elif exp["state"] == "needs_review":
                conf = doc_truths[fid]["conflicts"][seed["key"]]
                slots[role] = {"document": path, "state": "conflicting", "value": None,
                               "citations": [{"document": path, "page": c["page"]} for c in conf],
                               "conflicts": [{"value": c["value"], "page": c["page"]} for c in conf]}
            else:
                slots[role] = {"document": path, "state": "absent", "value": None, "citations": [],
                               "conflicts": []}
        items.append({"id": "disc-" + re.sub(r"[^a-z0-9]+", "-", seed["key"].lower()).strip("-"),
                      "key": seed["key"], "title": f"{seed['key']} {seed['change']}",
                      "severity": seed["severity"], "item_state": seed["item_state"],
                      "explanation": seed["note"], "slots": slots})
    items.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], i["key"]))
    return {"input_tokens": input_tokens, "documents": documents,
            "policy_check": {"workflow": case.get("workflow"), "run_status": "done",
                             "roles": {r: paths[f] for r, f in roles.items()}, "items": items,
                             "notices": [], "warnings": []}}
