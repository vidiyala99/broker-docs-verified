"""Step 3, verify: a fact is cited only if its quote is really in the document
and its value is really inside that quote (spec section 4 step 3, D5, D6, M1,
M2, M8).

Decision order for one candidate fact {field, value, page, quote}:

    value empty? ──yes──► ABSENT ("Not specified", no chip)
        │no
    quote empty? ──yes──► NOT_FOUND, reason no_quote
        │no
    for layer in (digital text layer, model-OCR text):        D5: digital first
      for tier in (strict, fuzzy):                            strict anywhere beats fuzzy
        for page in (cited page, then others nearest first):  D6: re-point, tie -> earlier page
          quote match on page?            strict: token sequence equal after
            │yes                          normalization (case, whitespace, typographic
            ▼                             quotes and dashes, edge punctuation,
          value inside the MATCHED        line-break hyphens); fuzzy: difflib
          SOURCE span? (M2)               token-sequence ratio >= 0.90 (M8)
            │yes ──► digital: VERIFIED with word-box rects
            │        OCR:     SCAN_READ, no rects (page-level highlight)
            │no  ──► remember "value_not_in_quote", keep searching
    nothing passed ──► NOT_FOUND (reason value_not_in_quote or quote_not_found)

The value is compared with the source text that matched, never with the
model's own quote, so a fuzzy match whose differing token is the value itself
cannot verify (the D7 hard rule). Numbers compare by value ("1000000" equals
"$1,000,000"), but a percent never equals a plain number.

Works on any page objects with `page_no`, `text`, `words` and `ocr_text`
(pipeline.parse.ParsedPage or models.Page rows). Word boxes may be Word
tuples or JSON lists in the same order.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol

from backend.schemas import EvidenceState, ExtractedFact

log = logging.getLogger("brokerdocs.verify")


@dataclass(frozen=True)
class VerifyConfig:
    fuzzy_threshold: float = 0.90  # M8
    min_fuzzy_tokens: int = 4  # shorter quotes must match strictly


DEFAULT_CONFIG = VerifyConfig()


class CandidateFact(NamedTuple):
    """One fact as the model returned it (page is 1-based, local to the document)."""

    field: str
    value: str | None
    page: int | None
    quote: str | None

    @classmethod
    def from_extracted(cls, fact: ExtractedFact) -> CandidateFact:
        return cls(fact.field, fact.value, fact.page, fact.quote)


@dataclass(frozen=True)
class VerifyResult:
    field: str
    value: str | None
    state: EvidenceState
    cited_page: int | None  # page the model gave
    page_no: int | None  # page the quote was found on (None if not found)
    quote: str | None  # the model's quote
    matched_text: str | None  # source text that matched
    method: str | None  # exact | normalized | hyphen | fuzzy
    score: float  # 1.0 for strict matches, difflib ratio for fuzzy, 0.0 otherwise
    rects: list[list[float]] = field(default_factory=list)  # one box per matched line
    repointed_from: int | None = None  # cited page, when the quote was on another page
    reason: str | None = None  # no_value | no_quote | quote_not_found | value_not_in_quote

    @property
    def cited(self) -> bool:
        return self.state in (EvidenceState.VERIFIED, EvidenceState.SCAN_READ)


class PageLike(Protocol):
    page_no: int
    text: str
    words: Sequence[Sequence[Any]] | None
    ocr_text: str | None


# --------------------------------------------------------------------- normalization

_TRANSLATE = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
        "―": "-", "−": "-", "­": "",
        " ": " ",
    }
)
_EDGE = ".,;:!?\"'()[]{}<>*"
_NUM = re.compile(r"^(?:usd)?\$?(-?\d[\d,]*(?:\.\d+)?)(%?)$")


def _norm_text(s: str) -> str:
    return unicodedata.normalize("NFKC", s).translate(_TRANSLATE).lower()


def _norm_token(raw: str) -> str:
    return _norm_text(raw).strip().strip(_EDGE)


def _tokens_of(s: str) -> list[str]:
    return [t for t in (_norm_token(p) for p in _norm_text(s).split()) if t]


def _number(tok: str) -> tuple[float, str] | None:
    m = _NUM.match(tok)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "")), m.group(2)
    except ValueError:
        return None


def _eq_plain(a: str, b: str) -> bool:
    return a == b


def _eq_value(a: str, b: str) -> bool:
    if a == b:
        return True
    na, nb = _number(a), _number(b)
    return na is not None and na == nb


# --------------------------------------------------------------------- page token streams


@dataclass(frozen=True)
class _Tok:
    norm: str
    raw: str
    line: tuple
    rect: tuple[float, float, float, float] | None
    joinable: bool = False  # ends a line with "letter-" and the next line starts with a letter


def _mark_joinable(toks: list[_Tok]) -> list[_Tok]:
    out = []
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        joinable = (
            nxt is not None
            and nxt.line != t.line
            and len(t.norm) > 1
            and t.norm.endswith("-")
            and t.norm[-2].isalpha()
            and nxt.norm[:1].isalpha()
        )
        out.append(_Tok(t.norm, t.raw, t.line, t.rect, joinable) if joinable else t)
    return out


def _stream_from_words(words: Iterable[Sequence[Any]]) -> list[_Tok]:
    toks = []
    for w in words:
        norm = _norm_token(str(w[4]))
        if norm:
            toks.append(_Tok(norm, str(w[4]), (w[5], w[6]),
                             (float(w[0]), float(w[1]), float(w[2]), float(w[3]))))
    return _mark_joinable(toks)


def _stream_from_text(text: str) -> list[_Tok]:
    toks = []
    for li, line in enumerate(text.splitlines()):
        for raw in line.split():
            norm = _norm_token(raw)
            if norm:
                toks.append(_Tok(norm, raw, (li,), None))
    return _mark_joinable(toks)


@dataclass
class _Layer:
    state: EvidenceState
    raw_text: str
    toks: list[_Tok]


@dataclass
class _PageIndex:
    page_no: int
    digital: _Layer | None
    ocr: _Layer | None


def _index_page(page: PageLike) -> _PageIndex:
    digital = None
    words = getattr(page, "words", None)
    text = getattr(page, "text", "") or ""
    if words:
        digital = _Layer(EvidenceState.VERIFIED, text, _stream_from_words(words))
    elif words is None and text.strip():
        # No word boxes (seam for a later local OCR, D12): still the document's own text.
        digital = _Layer(EvidenceState.VERIFIED, text, _stream_from_text(text))
    ocr_text = getattr(page, "ocr_text", None)
    ocr = _Layer(EvidenceState.SCAN_READ, ocr_text, _stream_from_text(ocr_text)) if ocr_text else None
    return _PageIndex(int(page.page_no), digital, ocr)


# --------------------------------------------------------------------- matching

_Eq = Callable[[str, str], bool]


def _match_at(q: list[str], toks: list[_Tok], start: int, eq: _Eq) -> tuple[int, bool] | None:
    """Match q against toks from `start`. Returns (end index, used a hyphen join)."""
    j, k, hyphen = 0, start, False
    while j < len(q):
        if k >= len(toks):
            return None
        t = toks[k]
        if eq(q[j], t.norm):
            j, k = j + 1, k + 1
            continue
        if t.joinable and k + 1 < len(toks):
            nxt = toks[k + 1].norm
            if eq(q[j], t.norm[:-1] + nxt) or eq(q[j], t.norm + nxt):
                j, k, hyphen = j + 1, k + 2, True
                continue
        return None
    return k, hyphen


def _contains(span: list[_Tok], value_tokens: list[str]) -> bool:
    if not value_tokens:
        return False
    return any(_match_at(value_tokens, span, i, _eq_value) for i in range(len(span)))


@dataclass
class _Hit:
    page_no: int
    start: int
    end: int
    method: str
    score: float
    value_ok: bool


def _strict_hits(layer: _Layer, q: list[str], quote: str, v: list[str], page_no: int):
    exact = quote.strip() in layer.raw_text
    for i in range(len(layer.toks)):
        m = _match_at(q, layer.toks, i, _eq_plain)
        if m is None:
            continue
        end, hyphen = m
        method = "hyphen" if hyphen else ("exact" if exact else "normalized")
        yield _Hit(page_no, i, end, method, 1.0, _contains(layer.toks[i:end], v))


def _fuzzy_hits(layer: _Layer, q: list[str], v: list[str], page_no: int, cfg: VerifyConfig):
    n = len(q)
    if n < cfg.min_fuzzy_tokens:
        return
    norms = [t.norm for t in layer.toks]
    sm = difflib.SequenceMatcher(None, autojunk=False)
    sm.set_seq2(q)
    found = []
    for width in sorted({n, n - 1, n + 1}):
        if width <= 0:
            continue
        for i in range(0, max(0, len(norms) - width) + 1):
            window = norms[i : i + width]
            if not window:
                continue
            sm.set_seq1(window)
            if sm.real_quick_ratio() < cfg.fuzzy_threshold or sm.quick_ratio() < cfg.fuzzy_threshold:
                continue
            ratio = sm.ratio()
            if ratio >= cfg.fuzzy_threshold:
                found.append((ratio, i, i + width))
    for ratio, i, end in sorted(found, key=lambda h: (-h[0], h[1])):
        yield _Hit(page_no, i, end, "fuzzy", round(ratio, 4), _contains(layer.toks[i:end], v))


def _rects(span: list[_Tok]) -> list[list[float]]:
    by_line: dict[tuple, list[float]] = {}
    for t in span:
        if t.rect is None:
            continue
        r = by_line.get(t.line)
        if r is None:
            by_line[t.line] = list(t.rect)
        else:
            r[0], r[1] = min(r[0], t.rect[0]), min(r[1], t.rect[1])
            r[2], r[3] = max(r[2], t.rect[2]), max(r[3], t.rect[3])
    return [[round(c, 2) for c in r] for r in by_line.values()]


def _page_order(index: list[_PageIndex], cited: int | None) -> list[_PageIndex]:
    if cited is None:
        return sorted(index, key=lambda p: p.page_no)
    return sorted(index, key=lambda p: (abs(p.page_no - cited), p.page_no))


# --------------------------------------------------------------------- public API


def build_index(pages: Iterable[PageLike]) -> list[_PageIndex]:
    """Tokenize a document's pages once; pass the result to verify_fact for many facts."""
    return [_index_page(p) for p in pages]


def verify_fact(
    candidate: CandidateFact,
    pages: Iterable[PageLike] | list[_PageIndex],
    config: VerifyConfig = DEFAULT_CONFIG,
) -> VerifyResult:
    pages = list(pages)
    index = pages if pages and isinstance(pages[0], _PageIndex) else build_index(pages)
    field_, value, cited, quote = candidate

    def result(state, **kw) -> VerifyResult:
        base = dict(field=field_, value=value, state=state, cited_page=cited, page_no=None,
                    quote=quote, matched_text=None, method=None, score=0.0)
        base.update(kw)
        return VerifyResult(**base)

    if value is None or not str(value).strip():
        return result(EvidenceState.ABSENT, reason="no_value")
    q = _tokens_of(quote or "")
    if not q:
        log.info("verify: %s has a value but no quote", field_)
        return result(EvidenceState.NOT_FOUND, reason="no_quote")
    v = _tokens_of(str(value))

    first_value_miss: tuple[_Hit, _Layer] | None = None
    ordered = _page_order(index, cited)
    for layer_name in ("digital", "ocr"):
        for tier in ("strict", "fuzzy"):
            for page in ordered:
                layer: _Layer | None = getattr(page, layer_name)
                if layer is None or not layer.toks:
                    continue
                hits = (
                    _strict_hits(layer, q, quote or "", v, page.page_no)
                    if tier == "strict"
                    else _fuzzy_hits(layer, q, v, page.page_no, config)
                )
                for hit in hits:
                    if not hit.value_ok:
                        if first_value_miss is None:
                            first_value_miss = (hit, layer)
                        continue
                    span = layer.toks[hit.start : hit.end]
                    repointed = cited if cited is not None and hit.page_no != cited else None
                    if repointed is not None:
                        log.info("verify: %s repointed from page %s to page %s",
                                 field_, cited, hit.page_no)
                    return result(
                        layer.state,
                        page_no=hit.page_no,
                        matched_text=" ".join(t.raw for t in span),
                        method=hit.method,
                        score=hit.score,
                        rects=_rects(span) if layer.state == EvidenceState.VERIFIED else [],
                        repointed_from=repointed,
                    )

    if first_value_miss is not None:
        hit, layer = first_value_miss
        log.info("verify: %s quote found on page %s but value %r is not inside it",
                 field_, hit.page_no, value)
        return result(
            EvidenceState.NOT_FOUND,
            page_no=hit.page_no,
            matched_text=" ".join(t.raw for t in layer.toks[hit.start : hit.end]),
            method=hit.method,
            score=hit.score,
            reason="value_not_in_quote",
        )
    log.info("verify: %s quote not found in the document", field_)
    return result(EvidenceState.NOT_FOUND, reason="quote_not_found")


def verify_facts(
    candidates: Iterable[CandidateFact | ExtractedFact],
    pages: Iterable[PageLike],
    config: VerifyConfig = DEFAULT_CONFIG,
) -> list[VerifyResult]:
    """Verify every fact of one document against that document's pages, in order."""
    index = build_index(pages)
    out = []
    for c in candidates:
        cand = CandidateFact.from_extracted(c) if isinstance(c, ExtractedFact) else CandidateFact(*c)
        out.append(verify_fact(cand, index, config))
    return out
