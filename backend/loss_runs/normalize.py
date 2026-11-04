r"""Normalization rules for loss runs (spec section 4 step 4, R7). Rules are data; the functions are pure.

    money      "$1,200.00" | "1200" | "(1,200.00)" | "-1,200.00" | "-" | Decimal / int / float -> integer cents
               via Decimal(str(value)) rounded half-up (never float math); "-" alone is zero; blank is None
    dates      Excel serial | datetime | "06/30/2025" | "6/30/25" | "2025-06-30" | "30-Jun-2025" | "Jun 30, 2025"
               -> ISO "2025-06-30"; anything else -> None (the raw text stays in the source cell)
    status     O / Open / Opened -> open; C / Closed -> closed; Re-opened / Reopen / RO -> reopened; else unknown
    claim key  case and whitespace only (no leading-zero stripping: that could merge two claims, section 7)
    carrier    case, spacing and punctuation only
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------- status (data)

STATUS_MAP: dict[str, str] = {  # spec section 4 step 4, exactly: any other spelling stays raw, status unknown
    "o": "open", "open": "open", "opened": "open",
    "c": "closed", "closed": "closed",
    "re-opened": "reopened", "reopened": "reopened", "reopen": "reopened", "ro": "reopened",
}
OPEN_STATUSES = frozenset({"open", "reopened"})


def normalize_status(raw: Any) -> str:
    """open | closed | reopened | unknown (an unknown spelling keeps its raw text, shown)."""
    if raw is None:
        return "unknown"
    return STATUS_MAP.get(" ".join(str(raw).split()).lower(), "unknown")


# ---------------------------------------------------------------- keys


def claim_key(raw: str | None) -> str:
    """Case and whitespace only. "gl 2024 017" -> "GL2024017"; "017" and "17" stay two claims."""
    return "".join((raw or "").split()).upper()


def carrier_key(raw: str | None) -> str:
    text = unicodedata.normalize("NFKC", raw or "").casefold()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())


# ---------------------------------------------------------------- money

_CENT = Decimal("0.01")
_AMOUNT_RE = re.compile(r"(\d{1,3}(,\d{3})+|\d+)(\.\d+)?|\.\d+")


class MoneyError(ValueError):
    """A cell or printed value that is not an amount."""


def to_cents(value: Any) -> int | None:
    """Integer cents, None for blank. Raises MoneyError for text that is not an amount."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100
    if isinstance(value, (float, Decimal)):
        try:
            d = Decimal(str(value))
        except InvalidOperation as exc:  # pragma: no cover - NaN / inf
            raise MoneyError(str(value)) from exc
        if not d.is_finite():
            raise MoneyError(str(value))
        return int((d.quantize(_CENT, rounding=ROUND_HALF_UP) * 100).to_integral_value())
    text = str(value).strip()
    for dash in ("−", "–", "—"):
        text = text.replace(dash, "-")
    text = text.replace("USD", "").strip()
    if not text:
        return None
    s = text.replace("$", "").replace(" ", "")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.startswith("-"):
        negative, s = not negative, s[1:]
    elif s.endswith("-") and len(s) > 1:
        negative, s = True, s[:-1]
    if s in ("", "-"):
        if "-" in text:
            return 0  # a dash alone prints a zero
        raise MoneyError(text)
    if not _AMOUNT_RE.fullmatch(s):
        raise MoneyError(text)
    d = Decimal(s.replace(",", ""))
    cents = int((d.quantize(_CENT, rounding=ROUND_HALF_UP) * 100).to_integral_value())
    return -cents if negative else cents


def money_or_none(value: Any) -> int | None:
    try:
        return to_cents(value)
    except MoneyError:
        return None


def format_cents(cents: int | None) -> str:
    if cents is None:
        return "none"
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"


# ---------------------------------------------------------------- dates

_EXCEL_EPOCH = dt.date(1899, 12, 30)
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d-%b-%y", "%b %d, %Y", "%B %d, %Y",
                 "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%m-%d-%Y", "%m-%d-%y", "%m.%d.%Y")


def to_iso_date(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (int, float, Decimal)):
        serial = float(value)
        if 1 <= serial < 100_000:
            return (_EXCEL_EPOCH + dt.timedelta(days=int(serial))).isoformat()
        return None
    text = " ".join(str(value).split()).strip().rstrip(".")
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ].*", text):
        text = text[:10]
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    if re.fullmatch(r"\d{5}(\.\d+)?", text):  # an Excel serial written as text
        return to_iso_date(float(text))
    return None


def policy_year_of(value: Any) -> int | None:
    """"2024", "2024-2025", "2024/25", 2024, 2024.0 -> 2024; None otherwise."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        n = int(value)
        return n if 1900 <= n <= 2100 and n == float(value) else None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", str(value))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- free text (section 7, slice 4 A3)

MAX_TEXT_CHARS = 200


def clean_text(value: Any, limit: int = MAX_TEXT_CHARS) -> str | None:
    """Control, format (bidi, zero-width) characters removed, whitespace collapsed, bounded. Data, never
    instructions: nothing here is ever sent back to a model as a directive."""
    if value is None:
        return None
    out = []
    for ch in str(value):
        cat = unicodedata.category(ch)
        if cat[0] == "C":
            out.append(" " if ch in "\t\n\r\v\f" else "")
            continue
        out.append(" " if cat[0] == "Z" else ch)
    text = " ".join("".join(out).split())
    return text[:limit] or None


__all__ = ["MoneyError", "OPEN_STATUSES", "STATUS_MAP", "carrier_key", "claim_key", "clean_text", "format_cents",
           "money_or_none", "normalize_status", "policy_year_of", "to_cents", "to_iso_date"]
