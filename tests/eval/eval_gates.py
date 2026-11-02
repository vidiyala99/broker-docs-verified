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
Everything else is reported, not gated.
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
REPORTED_BUCKETS = ("scanned", "report_only", "parse_only")

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
    # Only the eval's scored fields count. A predicted field the truth does not
    # list (subjectivities, key_exclusions, ... exist in the backend and in the
    # PDFs but have no truth here) is ignored: not a hard fail, absent violation
    # or hallucination.
    fields = list(truth_facts)

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
