"""AMS export ingestion for Policy Check (slice 3 T1; R1 D4 A, R2 D5 A).

An AMS export is a flat record of one policy, uploaded as JSON or CSV. It is
untrusted input, so it is screened like document text, rendered to a small PDF
at upload, and from then on takes the normal parse, verify, passage and viewer
path unchanged. Facts come from a key table in code, never from a model.

    bytes ─► caps: size ─► not PDF, UTF-8 text, no NUL ─► sniff: "{" or "[" = JSON, else CSV
          ─► JSON: one flat object of scalars (no nesting, no duplicate names, no NaN)
             CSV:  header row + exactly one record row, or a two-column field,value layout
          ─► screen every name and value: control, format (bidi, zero-width) and line-break
             characters removed or turned into spaces, whitespace collapsed; a name holds
             no ":" so a value can never forge a second "name: value" line; characters the
             PDF font cannot draw become "?" (notice)
          ─► caps: field count, name length, value length (AmsError names the limit)
          ─► render_pdf: one printed line "name: value" per field, wrapped by words,
             byte-identical for the same input (no dates, no ids, no links, no scripts)
          ─► map_facts: KEY_TABLE maps a normalized field name to a canonical fact key;
             each fact's quote is its printed line, so verify checks it against the
             rendered PDF like any other document fact (its directive guard included);
             unmapped fields are named in a notice; two fields giving different values
             for one key are a conflict (Needs review), never a guess

The original file is kept beside the rendered PDF (uploads.store_original), so
facts can be re-derived at run time with facts_from_original.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

import fitz

from backend.schemas import POLICY_CHECK_FIELDS, ExtractedFact, scalar_fields

AmsFormat = Literal["json", "csv"]
# Display label of each canonical key, for notices ("Coverage E (Personal Liability)", not the raw key).
FIELD_LABELS: dict[str, str] = {f.key: f.label for f in POLICY_CHECK_FIELDS}
_KB = 1024


@dataclass(frozen=True)
class AmsLimits:
    max_bytes: int = 512 * _KB
    max_fields: int = 400
    max_name_chars: int = 100
    max_value_chars: int = 1000


DEFAULT_AMS_LIMITS = AmsLimits()


class AmsError(Exception):
    """An AMS file cannot be used. `message` is safe to show to the user.

    Same attributes as pipeline.parse.ParseError, so the upload route reports both alike.
    Codes: empty, too_large, not_ams, invalid_ams, too_many_fields, field_too_long.
    """

    def __init__(self, code: str, message: str, limit: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.limit = limit


NOT_AMS_MESSAGE = "Not an AMS export. Only JSON or CSV files can be added as the AMS record."


@dataclass(frozen=True)
class AmsField:
    name: str
    value: str  # "" when the export leaves the field blank


@dataclass(frozen=True)
class AmsRecord:
    format: AmsFormat
    fields: tuple[AmsField, ...]
    notices: tuple[str, ...] = ()


@dataclass(frozen=True)
class AmsLine:
    name: str
    value: str
    page: int  # 1-based page of the rendered PDF
    quote: str  # the printed line, "name: value"


@dataclass(frozen=True)
class RenderedAms:
    pdf: bytes
    lines: tuple[AmsLine, ...]
    page_count: int
    record: AmsRecord


@dataclass(frozen=True)
class MappedFacts:
    facts: list[ExtractedFact]  # one per canonical key, in file order
    unmapped: list[str]  # field names with no key in KEY_TABLE (ignored, D5)
    conflicts: dict[str, list[ExtractedFact]] = field(default_factory=dict)  # key -> disagreeing facts
    notices: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- screening

_LINE_BREAKS = set("\r\n\t\v\f\x85  ")


@lru_cache(maxsize=1)
def _font() -> fitz.Font:
    return fitz.Font("helv")


def _screen(raw: str) -> str:
    out: list[str] = []
    for ch in raw:
        if ch in _LINE_BREAKS:
            out.append(" ")
            continue
        cat = unicodedata.category(ch)
        if cat[0] == "C":  # control, format (bidi, zero-width), surrogate, private, unassigned
            continue
        out.append(" " if cat[0] == "Z" else ch)
    return " ".join("".join(out).split())


def _drawable(text: str) -> tuple[str, int]:
    font = _font()
    missing = 0
    out = []
    for ch in text:
        if ch == " " or font.has_glyph(ord(ch)):
            out.append(ch)
        else:
            out.append("?")
            missing += 1
    return "".join(out), missing


def normalize_name(name: str) -> str:
    """Key-table form of a field name: camelCase split, NFKC, lower case, runs of other chars -> "_"."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", unicodedata.normalize("NFKC", name))
    return re.sub(r"[^a-z0-9]+", "_", s.casefold()).strip("_")


# --------------------------------------------------------------------- parsing


def sniff_format(data: bytes) -> AmsFormat:
    """Format from content only: JSON when the first non-blank character opens an object or array."""
    head = data[:64].lstrip(b"\xef\xbb\xbf \t\r\n")
    return "json" if head[:1] in (b"{", b"[") else "csv"


def _invalid(message: str) -> AmsError:
    return AmsError("invalid_ams", message)


class _Pairs(list):
    """A JSON object kept as its ordered (name, value) pairs."""


def _json_scalar(name: str, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return value
    raise _invalid(f'Field "{_screen(name)[:60]}" holds a list or an object; an AMS export must be one '
                   "flat record of single values.")


def _reject_constant(token: str) -> object:
    raise _invalid(f"This JSON file holds {token}, which is not a value an AMS export can use.")


def _parse_json(text: str) -> list[tuple[str, str]]:
    try:
        obj = json.loads(text, object_pairs_hook=_Pairs, parse_constant=_reject_constant)
    except AmsError:
        raise
    except (ValueError, RecursionError):
        raise _invalid("This JSON file could not be read; it is not valid JSON.") from None
    if not isinstance(obj, _Pairs):
        raise _invalid("This JSON file must hold one object of field names and values.")
    return [(str(k), _json_scalar(str(k), v)) for k, v in obj]


_KEY_HEADERS = {"field", "name", "key", "field_name", "code", "field_code"}
_VALUE_HEADERS = {"value", "field_value"}


def _parse_csv(text: str, limits: AmsLimits) -> list[tuple[str, str]]:
    try:
        rows = [r for r in csv.reader(io.StringIO(text, newline=""), strict=True) if any(c.strip() for c in r)]
    except csv.Error:
        raise _invalid("This CSV file could not be read; check its quotes and commas.") from None
    if not rows:
        raise AmsError("empty", "This file is empty.")
    header, records = rows[0], rows[1:]
    norm = [normalize_name(h) for h in header]
    if len(header) == 2 and norm[0] in _KEY_HEADERS and norm[1] in _VALUE_HEADERS:
        if len(records) > limits.max_fields:
            raise _too_many(limits)
        for r in records:
            if len(r) != 2:
                raise _invalid("Every row of a field,value CSV must have exactly two cells.")
        if not records:
            raise _invalid("This CSV file has no fields.")
        return [(r[0], r[1]) for r in records]
    if len(header) > limits.max_fields:
        raise _too_many(limits)
    if not records:
        raise _invalid("This CSV file has a header row but no record row.")
    if len(records) > 1:
        raise _invalid(f"This CSV file has {len(records)} record rows; an AMS export for one policy has "
                       "exactly one row under the header.")
    if len(records[0]) != len(header):
        raise _invalid("The record row has a different number of cells than the header row.")
    return list(zip(header, records[0], strict=True))


def _too_many(limits: AmsLimits) -> AmsError:
    return AmsError("too_many_fields", f"This AMS export has more than {limits.max_fields} fields; the limit is "
                    f"{limits.max_fields} fields.", limit=limits.max_fields)


def parse_ams(data: bytes, limits: AmsLimits = DEFAULT_AMS_LIMITS) -> AmsRecord:
    """Parse and screen an AMS export. Raises AmsError (message names the limit) for a rejection."""
    if len(data) > limits.max_bytes:
        kb = f"{limits.max_bytes / _KB:g} KB"
        raise AmsError("too_large", f"This file is larger than {kb}; the limit is {kb} per AMS export.",
                       limit=limits.max_bytes)
    if not data.strip():
        raise AmsError("empty", "This file is empty.")
    if b"%PDF-" in data[:1024]:
        raise AmsError("not_ams", NOT_AMS_MESSAGE)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _invalid("This file is not UTF-8 text. Export the AMS record as UTF-8 JSON or CSV.") from None
    if "\x00" in text:
        raise AmsError("not_ams", NOT_AMS_MESSAGE)
    fmt = sniff_format(data)
    pairs = _parse_json(text) if fmt == "json" else _parse_csv(text, limits)
    if not pairs:
        raise _invalid("This AMS export has no fields.")
    if len(pairs) > limits.max_fields:
        raise _too_many(limits)

    fields: list[AmsField] = []
    seen: dict[str, str] = {}
    replaced = 0
    for raw_name, raw_value in pairs:
        name = _screen(raw_name.replace(":", " "))
        value = _screen(raw_value)
        if not name:
            raise _invalid("A field name is blank once control characters are removed.")
        for what, s, cap in (("name", name, limits.max_name_chars), ("value", value, limits.max_value_chars)):
            if len(s) > cap:
                raise AmsError("field_too_long", f'A field {what} is longer than {cap} characters; the limit is '
                               f"{cap} characters per field {what}.", limit=cap)
        name, n1 = _drawable(name)
        value, n2 = _drawable(value)
        replaced += n1 + n2
        key = normalize_name(name) or name
        if key in seen:
            raise _invalid(f'Field "{seen[key]}" appears more than once in this AMS export.')
        seen[key] = name
        fields.append(AmsField(name, value))
    notices = (f"{replaced} characters could not be shown in the AMS record and were replaced with ?.",) \
        if replaced else ()
    return AmsRecord(fmt, tuple(fields), notices)


# --------------------------------------------------------------------- render to PDF

PAGE_W, PAGE_H = 612.0, 792.0
MARGIN = 54.0
FONT_SIZE = 10.0
LEADING = 14.0
_TEXT_W = PAGE_W - 2 * MARGIN


def _wrap(text: str) -> list[str]:
    font = _font()

    def width(s: str) -> float:
        return font.text_length(s, fontsize=FONT_SIZE)

    lines: list[str] = []
    cur = ""
    for word in text.split(" "):
        while width(word) > _TEXT_W:  # a single over-long token: break by characters
            cut = len(word)
            while cut > 1 and width(word[:cut]) > _TEXT_W:
                cut -= 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:cut])
            word = word[cut:]
        trial = f"{cur} {word}" if cur else word
        if width(trial) <= _TEXT_W:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def render_pdf(record: AmsRecord) -> RenderedAms:
    """One printed line per field ("name: value"), wrapped by words, flowing onto new pages."""
    font = _font()
    doc = fitz.open()
    try:
        bottom = PAGE_H - MARGIN
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        writer = fitz.TextWriter(page.rect)
        y = MARGIN + FONT_SIZE
        heading = f"AMS export ({record.format.upper()}), {len(record.fields)} fields"
        writer.append((MARGIN, y), heading, font=font, fontsize=FONT_SIZE)
        y += 2 * LEADING
        out: list[AmsLine] = []
        for f in record.fields:
            quote = f"{f.name}: {f.value}" if f.value else f"{f.name}:"
            wrapped = _wrap(quote)
            if y + (len(wrapped) - 1) * LEADING > bottom:
                writer.write_text(page)
                page = doc.new_page(width=PAGE_W, height=PAGE_H)
                writer = fitz.TextWriter(page.rect)
                y = MARGIN + FONT_SIZE
            for line in wrapped:
                writer.append((MARGIN, y), line, font=font, fontsize=FONT_SIZE)
                y += LEADING
            out.append(AmsLine(f.name, f.value, page.number + 1, quote))
            y += LEADING / 2
        writer.write_text(page)
        doc.set_metadata({})
        pdf = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
        pages = doc.page_count
    finally:
        doc.close()
    return RenderedAms(pdf, tuple(out), pages, record)


# --------------------------------------------------------------------- key table (D5)

# Canonical fact keys an AMS field can fill: the policy-check scalar fields (schemas.POLICY_CHECK_FIELDS
# is the truth, so the AMS role and the PDF roles align on one key list). Forms are not in an AMS flat
# record, so no form:<n> keys here. Fields with no policy-check key (effective date and expiration date
# on their own, carrier, agent notes) are unmapped and named in a notice (D5), never mapped to a near key:
# an effective date alone compared with a printed policy term would be a false mismatch.
CANONICAL_KEYS: tuple[str, ...] = tuple(f.key for f in scalar_fields(POLICY_CHECK_FIELDS))

_ALIASES: dict[str, tuple[str, ...]] = {
    "policy_number": ("policy number", "policy no", "policy num", "policy #", "pol no", "policy id", "policy"),
    "policy_term": ("policy term", "term", "policy period"),
    "named_insured": ("named insured", "insured", "insured name", "primary insured", "client name"),
    "insured_address": ("address", "insured address", "mailing address", "property address", "location address",
                        "risk address", "residence premises"),
    "coverage_a_limit": ("coverage a", "coverage a limit", "cov a", "cov a limit", "coverage a dwelling",
                         "dwelling", "dwelling limit"),
    "coverage_b_limit": ("coverage b", "coverage b limit", "cov b", "cov b limit", "coverage b other structures",
                         "other structures", "other structures limit"),
    "coverage_c_limit": ("coverage c", "coverage c limit", "cov c", "cov c limit", "coverage c personal property",
                         "personal property", "personal property limit"),
    "coverage_d_limit": ("coverage d", "coverage d limit", "cov d", "cov d limit", "coverage d loss of use",
                         "loss of use", "loss of use limit"),
    "coverage_e_limit": ("coverage e", "coverage e limit", "cov e", "cov e limit", "coverage e personal liability",
                         "personal liability", "personal liability limit"),
    "coverage_f_limit": ("coverage f", "coverage f limit", "cov f", "cov f limit", "coverage f medical payments",
                         "medical payments", "medical payments limit", "med pay"),
    "deductible_all_peril": ("deductible", "all peril deductible", "aop deductible", "deductible all peril", "aop"),
    "deductible_wind_hail": ("wind hail deductible", "deductible wind hail", "wind deductible", "wh deductible",
                             "windstorm hail deductible"),
    "deductible_hurricane": ("hurricane deductible", "deductible hurricane"),
    "total_premium": ("total premium", "premium", "annual premium", "written premium", "policy premium"),
    "discount_multi_policy": ("multi policy discount", "multi-policy discount", "multipolicy discount",
                              "package discount", "bundle discount"),
    "discount_protective_device": ("protective device discount", "alarm discount", "security discount",
                                   "sprinkler discount"),
    "discount_claims_free": ("claims free discount", "claim free discount", "loss free discount"),
    "discount_loyalty": ("loyalty discount", "years insured discount", "renewal discount"),
}

KEY_TABLE: dict[str, str] = {normalize_name(alias): key for key, aliases in _ALIASES.items() for alias in aliases}


def canonical_key(field_name: str) -> str | None:
    return KEY_TABLE.get(normalize_name(field_name))


def map_facts(rendered: RenderedAms) -> MappedFacts:
    """Facts from the key table; each quote is the field's printed line on the rendered PDF."""
    grouped: dict[str, list[ExtractedFact]] = {}
    names: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for line in rendered.lines:
        key = canonical_key(line.name)
        if key is None:
            if line.value:
                unmapped.append(line.name)
            continue
        if not line.value:
            continue
        grouped.setdefault(key, []).append(
            ExtractedFact(field=key, value=line.value, page=line.page, quote=line.quote))
        names.setdefault(key, []).append(line.name)
    facts: list[ExtractedFact] = []
    conflicts: dict[str, list[ExtractedFact]] = {}
    notices = list(rendered.record.notices)
    for key, candidates in grouped.items():
        if len({c.value for c in candidates}) == 1:
            facts.append(candidates[0])
        else:
            conflicts[key] = candidates
            notices.append(f"AMS fields {', '.join(names[key])} give different values for "
                           f"{FIELD_LABELS.get(key, key)}; shown as Needs review.")
    if unmapped:
        notices.append(f"AMS fields not compared (no matching policy field): {', '.join(unmapped)}.")
    return MappedFacts(facts, unmapped, conflicts, notices)


def facts_from_original(data: bytes, limits: AmsLimits = DEFAULT_AMS_LIMITS) -> MappedFacts:
    """Re-derive the facts from the kept original file (the render is deterministic, so pages match)."""
    return map_facts(render_pdf(parse_ams(data, limits)))
