r"""Header synonyms for loss-run tables (spec section 4 step 1, L7). The table is data.

    printed header ─► normalize (case, "#" -> number, "no." -> number, "&" -> and, punctuation -> space)
                   ─► SYNONYMS[column]  ─hit─► our column
                   └─ miss ─► unknown: one small model call per sheet may map it, only to our own columns
                              (sheets.map_unknown_headers); anything left unmapped is shown, never guessed

The PDF row check uses the same table to find each amount column's header on a page (O1).
`SYNONYMS_HASH` is part of the extraction cache key (R8): a synonym change never serves an old mapping.
"""

from __future__ import annotations

import hashlib
import json
import re

# Our columns, in display order. Money columns are in integer cents.
COLUMNS: dict[str, str] = {
    "claim_number": "Claim number",
    "claimant": "Claimant",
    "date_of_loss": "Date of loss",
    "status": "Status",
    "paid": "Paid",
    "reserve": "Reserve",
    "incurred": "Incurred",
    "recovery": "Recovery",
    "deductible": "Deductible",
    "policy_number": "Policy number",
    "line": "Line of business",
    "policy_year": "Policy year",
    "carrier": "Carrier",
}
MONEY_COLUMNS = ("paid", "reserve", "incurred", "recovery", "deductible")

SYNONYMS: dict[str, tuple[str, ...]] = {
    "claim_number": ("claim number", "claim no", "claim num", "claim", "claim id", "claim ref", "claim reference",
                     "clm number", "clm no", "clm", "claim nbr", "clm nbr", "file number", "claim file number"),
    "claimant": ("claimant", "claimant name", "injured party", "injured employee", "injured worker",
                 "employee name", "claimant employee"),
    "date_of_loss": ("date of loss", "loss date", "dol", "accident date", "date of accident", "date of injury",
                     "injury date", "incident date", "occurrence date", "loss dt"),
    "status": ("status", "claim status", "clm status", "open closed", "o c", "stat"),
    "paid": ("paid", "total paid", "paid loss", "paid losses", "loss paid", "losses paid", "amount paid",
             "paid to date", "paid amount", "total paid amount", "paid total"),
    "reserve": ("reserve", "reserves", "outstanding", "outstanding reserve", "outstanding reserves", "os reserve",
                "o s reserve", "open reserve", "case reserve", "reserve amount", "total reserve", "total outstanding",
                "outstanding amount"),
    "incurred": ("incurred", "total incurred", "incurred loss", "incurred losses", "total incurred loss",
                 "gross incurred", "incurred amount", "total incurred amount", "incurred total"),
    "recovery": ("recovery", "recoveries", "subrogation", "subro", "salvage", "salvage subrogation",
                 "salvage and subrogation", "total recovery", "total recoveries", "recovered"),
    "deductible": ("deductible", "deductible amount", "ded", "deductibles", "ded amount"),
    "policy_number": ("policy number", "policy no", "policy num", "policy", "pol number", "pol no", "pol",
                      "policy nbr", "pol nbr"),
    "line": ("line", "line of business", "lob", "coverage", "coverage line", "line of coverage", "policy type",
             "coverage type"),
    "policy_year": ("policy year", "py", "pol yr", "policy yr", "pol year", "year"),
    "carrier": ("carrier", "insurer", "insurance company", "writing company", "carrier name", "company"),
}

_LOOKUP: dict[str, str] = {syn: col for col, syns in SYNONYMS.items() for syn in syns}
SYNONYMS_HASH = hashlib.sha256(json.dumps(SYNONYMS, sort_keys=True).encode("utf-8")).hexdigest()


def normalize_header(raw: object) -> str:
    text = str(raw or "").lower()
    text = text.replace("#", " number ").replace("&", " and ")
    text = re.sub(r"\bno\.", " no ", text)
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return " ".join(text.split())


def known_column(raw: object) -> str | None:
    """Our column for a printed header, or None when the table does not know it."""
    return _LOOKUP.get(normalize_header(raw))


def map_headers(cells: list[object]) -> tuple[dict[int, str], list[tuple[int, str]]]:
    """(column index -> our column for known headers, [(index, header text)] unknown). The first header for a
    column wins; a later duplicate is listed as unknown (shown, never guessed)."""
    mapped: dict[int, str] = {}
    unknown: list[tuple[int, str]] = []
    taken: set[str] = set()
    for i, cell in enumerate(cells):
        text = " ".join(str(cell).split()) if cell is not None else ""
        if not text:
            continue
        col = known_column(text)
        if col is not None and col not in taken:
            mapped[i] = col
            taken.add(col)
        else:
            unknown.append((i, text))
    return mapped, unknown


def known_count(cells: list[object]) -> int:
    return len(map_headers(cells)[0])


__all__ = ["COLUMNS", "MONEY_COLUMNS", "SYNONYMS", "SYNONYMS_HASH", "known_column", "known_count", "map_headers",
           "normalize_header"]
