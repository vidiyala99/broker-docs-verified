"""Compare Quotes terms, marks and the Key difference (slice 6, spec sections 3.1 to 3.3, review D1, R1 to R3,
R9 to R11).

Three things live here, all deterministic:

1. Classification (R1, R9). The extraction call copies printed wording; code decides what it means.
   - additional_insured / waiver_of_subrogation: Blanket | Scheduled only | Not included, else Not stated;
     primary_noncontributory: Included | Not included, else Not stated. Wording that matches no pattern, or
     that is about another term only (the coi_checks regexes), is Not stated and never marked.
   - notable_exclusions: each verified exclusion title is matched to the fixed WATCH_LIST by synonyms. The cell
     lists the matches; "None of these" only when the document prints a verified schedule heading (the list is
     complete, R9) and at least one verified title, none on the watch list; otherwise Not stated.

2. Marks (R2): `mark_cells(tables)` -> {(row key, column key): Mark}. Computed on read (views.artifact_out) and
   inside compose (after the run's edits are applied, O3); never stored.

       eligible cell = verified or broker-edited; Not stated / unverified / conflicting / failed never count
       endorsement rows   rank Blanket > Scheduled only > Not included (P&NC: Included > Not included);
                          needs >= 2 eligible values; below the row's best -> worse "Others: <best>"
       notable_exclusions X is worse when it has a watch-list exclusion that another eligible cell Y lacks
                          and Y is complete (verified schedule, or edited by the broker) -> "Excludes <...>"
       total_premium      every column has an eligible premium stating one amount; the strictly lowest is
                          best "Lowest premium"; a tie marks nothing
       any other row      never marked

3. The Key difference (review D1, R3, R10, R11): `check_key_difference` (each segment's numbers equal values of
   the cells it cites; each carrier it names owns a cited cell; each term it names is a cited row; never
   "broader"/"better" for a worse cell; "lowest"/"cheapest" only with the best premium as the segment's one
   premium; at least one marked cell cited), `template_key_difference` (the fallback, never empty when a cell is
   marked) and `key_difference_stale` (the marks or a cited cell's value changed since it was written).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from backend.pipeline import compose
from backend.pipeline.coi_checks import AI_RE, PNC_RE, SCHEDULED_RE, WAIVER_RE
from backend.schemas import (
    CITED_STATES,
    EXCLUSIONS_SCHEDULE_KEY,
    ArtifactContent,
    Cell,
    CellRef,
    CellSegment,
    CitedValue,
    EvidenceState,
    KeyDifferenceBlock,
    MarkRef,
    TableBlock,
    is_exclusion_key,
)

AI_KEY = "additional_insured"
WAIVER_KEY = "waiver_of_subrogation"
PNC_KEY = "primary_noncontributory"
EXCLUSIONS_KEY = "notable_exclusions"
PREMIUM_KEY = "total_premium"
TERM_KEYS = (AI_KEY, WAIVER_KEY, PNC_KEY)
MARKED_ROWS = frozenset({*TERM_KEYS, EXCLUSIONS_KEY, PREMIUM_KEY})

BLANKET = "Blanket"
SCHEDULED = "Scheduled only"
NOT_INCLUDED = "Not included"
INCLUDED = "Included"
NOT_STATED = "Not stated"
NONE_OF_THESE = "None of these"

RANKS: dict[str, dict[str, int]] = {
    AI_KEY: {BLANKET: 2, SCHEDULED: 1, NOT_INCLUDED: 0},
    WAIVER_KEY: {BLANKET: 2, SCHEDULED: 1, NOT_INCLUDED: 0},
    PNC_KEY: {INCLUDED: 1, NOT_INCLUDED: 0},
}
TERM_PHRASES = {AI_KEY: "additional insured", WAIVER_KEY: "waiver of subrogation",
                PNC_KEY: "primary and non-contributory"}

# --------------------------------------------------------------------- classification (R1)

_OWN = {AI_KEY: AI_RE, WAIVER_KEY: WAIVER_RE, PNC_KEY: PNC_RE}
_NEGATIVE = re.compile(
    r"\bnot\s+(?:included|covered|provided|available|applicable|offered|granted|afforded|endorsed)\b"
    r"|\bexclude[sd]?\b|\bdeclined\b|\bunavailable\b|\bnone\b|\bn/a\b"
    r"|\bdoes\s+not\s+(?:apply|include)\b|\bno\s+(?:coverage|additional|waiver|primary)\b"
    r"|(?:^|[:\-–—]\s*)no\.?\s*$", re.I)
_BLANKET = re.compile(
    r"\bblanket\b|\bautomatic(?:ally)?\b"
    r"|(?:where|when|as|if)\s+required\s+(?:by|in|under)\s+(?:a\s+|any\s+)?(?:written\s+)?(?:contract|agreement)"
    r"|required\s+by\s+(?:a\s+|any\s+)?written\s+(?:contract|agreement)"
    r"|\bany\s+(?:person|organi[sz]ation|entity)\b", re.I)
_INCLUDED = re.compile(r"\b(?:included|includes|yes|applies|provided|granted|endorsed|attached|afforded)\b"
                       r"|✓|✔", re.I)


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def classify_term(key: str, wording: str | None) -> str | None:
    """The category a term's printed wording (or a broker's edited value) states, or None (Not stated)."""
    if key not in RANKS:
        return None
    text = _clean(wording)
    if not text:
        return None
    for name in RANKS[key]:  # an edited value, or a cell that already shows the category
        if text.casefold() == name.casefold():
            return name
    own = _OWN[key]
    if not own.search(text) and any(rx.search(text) for k, rx in _OWN.items() if k != key):
        return None  # wording about another term only
    negative = bool(_NEGATIVE.search(text))
    if key == PNC_KEY:
        if negative:
            return NOT_INCLUDED
        return INCLUDED if (own.search(text) or _INCLUDED.search(text) or _BLANKET.search(text)) else None
    blanket, scheduled = bool(_BLANKET.search(text)), bool(SCHEDULED_RE.search(text))
    if negative:
        return NOT_INCLUDED if not (blanket or scheduled) else None
    if blanket and scheduled:
        return None
    if blanket:
        return BLANKET
    if scheduled:
        return SCHEDULED
    return None


@dataclass(frozen=True)
class WatchItem:
    id: str
    label: str  # the cell value
    phrase: str  # in a sentence ("Excludes action over")
    pattern: re.Pattern


WATCH_LIST: tuple[WatchItem, ...] = (
    WatchItem("action_over", "Action over", "action over", re.compile(
        r"action[\s-]*over|third[\s-]*party[\s-]*over"
        r"|injury\s+to\s+(?:your\s+)?(?:employees?|workers?)\b[^.;]{0,60}?\bof\s+(?:the\s+)?(?:insureds?|contractors?|"
        r"subcontractors?|contracted\s+organi[sz]ations?)"
        r"|employees?\s+of\s+(?:contractors?|subcontractors?)", re.I)),
    WatchItem("residential", "Residential / habitational work", "residential / habitational work", re.compile(
        r"residential|habitational|condominium|\bcondos?\b|townho(?:me|use)s?|tract\s+hom|single[\s-]+family"
        r"|multi[\s-]*(?:unit|family)|apartments?", re.I)),
    WatchItem("subsidence", "Subsidence / earth movement", "subsidence / earth movement", re.compile(
        r"subsidence|earth\s+movement|soil\s+(?:movement|subsidence|settlement)|land\s*slides?|mud\s*slides?"
        r"|sink\s*holes?|expansive\s+soils?|earth\s+settlement", re.I)),
    WatchItem("designated_ongoing_operations", "Designated ongoing operations", "designated ongoing operations",
              re.compile(r"designated\s+(?:ongoing\s+)?operations?\b(?!\s+covered\s+by\s+a\s+consolidated)", re.I)),
    WatchItem("wrap_up", "Wrap-up / OCIP", "wrap-up / OCIP", re.compile(
        r"wrap[\s-]*ups?\b|\bocip\b|\bccip\b|owner[\s-]+controlled\s+insurance|contractor[\s-]+controlled\s+insurance"
        r"|consolidated\s+(?:\(wrap[\s-]*up\)\s+)?insurance\s+program|controlled\s+insurance\s+program", re.I)),
)
_WATCH_BY_LABEL = {w.label: w for w in WATCH_LIST}


def watch_matches(text: str | None) -> list[str]:
    """Labels of the watch-list exclusions `text` names, in watch-list order."""
    text = _clean(text)
    return [w.label for w in WATCH_LIST if text and w.pattern.search(text)]


def _exclusion_set(value: str | None) -> frozenset[str] | None:
    """The watch-list exclusions a notable_exclusions value states: empty for "None of these", None when the
    value states nothing comparable (Not stated, free text naming no watch-list exclusion)."""
    text = _clean(value)
    if text.casefold() in (NONE_OF_THESE.casefold(), "none"):
        return frozenset()
    found = watch_matches(text)
    return frozenset(found) if found else None


# --------------------------------------------------------------------- cells (compose.build_tables)


def term_cell(key: str, cell: Cell) -> Cell:
    """An endorsement term cell: a cited wording shows its category (chips kept); wording that states no category,
    and a field the document is silent on, show Not stated (state absent, no chip). Other states are unchanged."""
    state = EvidenceState(cell.state) if cell.state else EvidenceState.ABSENT
    if state in CITED_STATES:
        category = classify_term(key, cell.value)
        if category is not None:
            return cell.model_copy(update={"value": category})
        return Cell(value=NOT_STATED, state=EvidenceState.ABSENT, fact_ids=list(cell.fact_ids))
    if state == EvidenceState.ABSENT:
        return cell.model_copy(update={"value": NOT_STATED})
    return cell


def exclusions_cell(facts: Iterable[compose.ComposeFact], *, failed: bool = False) -> Cell:
    """The notable_exclusions cell of one document from its exclusion:<title> and exclusions_schedule facts."""
    if failed:
        return Cell(value="Extraction failed", state=EvidenceState.FAILED)
    facts = list(facts)
    titles = [f for f in facts if is_exclusion_key(f.field) and f.cited]
    schedule = next((f for f in facts if f.field == EXCLUSIONS_SCHEDULE_KEY and f.cited), None)
    complete = schedule is not None
    matched: dict[str, list[compose.ComposeFact]] = {}
    for f in titles:
        for label in watch_matches(f.value):
            matched.setdefault(label, []).append(f)
    if matched:
        value = ", ".join(w.label for w in WATCH_LIST if w.label in matched)
        used = [f for w in WATCH_LIST for f in matched.get(w.label, [])]
    elif complete and titles:
        value, used = NONE_OF_THESE, list(titles)
    else:
        return Cell(value=NOT_STATED, state=EvidenceState.ABSENT, complete=complete)
    if schedule is not None:
        used.append(schedule)
    used = list({id(f): f for f in used}.values())
    scan = any(EvidenceState(f.state) == EvidenceState.SCAN_READ for f in used)
    return Cell(value=value, state=EvidenceState.SCAN_READ if scan else EvidenceState.VERIFIED,
                passage_ids=list(dict.fromkeys(p for f in used for p in f.passage_ids)),
                fact_ids=[f.fact_id for f in used if f.fact_id is not None], complete=complete)


# --------------------------------------------------------------------- marks (R2)


@dataclass(frozen=True)
class Mark:
    kind: Literal["worse", "best"]
    reason: str


Marks = dict[tuple[str, str], Mark]


def eligible(cell: Cell | None) -> bool:
    """Verified or broker-edited (spec 3.2); never Not stated, unverified, scan-read, conflicting or failed."""
    if cell is None:
        return False
    if cell.edited:
        return bool(_clean(cell.value))
    return cell.state is not None and EvidenceState(cell.state) == EvidenceState.VERIFIED


def premium_amount(value: str | None) -> float | None:
    """The one amount a premium value states, or None when it states none or several."""
    tokens = [t for t in compose.prose_numbers(value or "") if t.kind == "number"]
    if not tokens:
        tokens = [t for t in compose._scan(value or "", checked_only=False) if t.kind == "number"]
    amounts = {t.value for t in tokens}
    return next(iter(amounts)) if len(amounts) == 1 else None


def _mark_terms(key: str, cells: Mapping[str, Cell], out: Marks) -> None:
    ranks = RANKS[key]
    found = {col: classify_term(key, c.value) for col, c in cells.items() if eligible(c)}
    found = {col: v for col, v in found.items() if v is not None}
    if len(found) < 2:
        return
    best = max(ranks[v] for v in found.values())
    best_label = next(name for name, r in ranks.items() if r == best)
    for col, v in found.items():
        if ranks[v] < best:
            out[(key, col)] = Mark("worse", f"Others: {best_label}")


def _complete(cell: Cell) -> bool:
    return cell.edited or cell.complete is True


def _mark_exclusions(cells: Mapping[str, Cell], out: Marks) -> None:
    found = {col: _exclusion_set(c.value) for col, c in cells.items() if eligible(c)}
    found = {col: s for col, s in found.items() if s is not None}
    if len(found) < 2:
        return
    for col, have in found.items():
        lacking = [w for w in WATCH_LIST if w.label in have
                   and any(other != col and _complete(cells[other]) and w.label not in s
                           for other, s in found.items())]
        if lacking:
            out[(EXCLUSIONS_KEY, col)] = Mark("worse", "Excludes " + _join([w.phrase for w in lacking]))


def _mark_premium(columns: Sequence[str], cells: Mapping[str, Cell], out: Marks) -> None:
    if len(columns) < 2:
        return
    amounts = {}
    for col in columns:
        cell = cells.get(col)
        amount = premium_amount(cell.value) if eligible(cell) else None
        if amount is None:
            return  # every column needs an eligible premium (spec 3.2)
        amounts[col] = amount
    low = min(amounts.values())
    lowest = [col for col, a in amounts.items() if a == low]
    if len(lowest) == 1:
        out[(PREMIUM_KEY, lowest[0])] = Mark("best", "Lowest premium")


def mark_cells(tables: TableBlock | Iterable[TableBlock]) -> Marks:
    """Marks of a Compare Quotes report's tables, keyed (row key, column key). Pure; nothing is stored."""
    if isinstance(tables, TableBlock):
        tables = [tables]
    out: Marks = {}
    for table in tables:
        columns = [c.key for c in table.columns]
        for row in table.rows:
            if row.key in RANKS:
                _mark_terms(row.key, row.cells, out)
            elif row.key == EXCLUSIONS_KEY:
                _mark_exclusions(row.cells, out)
            elif row.key == PREMIUM_KEY:
                _mark_premium(columns, row.cells, out)
    return out


def content_tables(content: ArtifactContent) -> list[TableBlock]:
    return [b for _, b in content.iter_blocks() if isinstance(b, TableBlock)]


def apply_edits(tables: Iterable[TableBlock], edits: Iterable[compose.ComposeEdit]) -> list[TableBlock]:
    """Copies of `tables` with the run's cell edits applied (O3: compose marks what the broker sees)."""
    out = [t.model_copy(deep=True) for t in tables]
    for e in edits:
        for t in out:
            row = next((r for r in t.rows if r.key == e.field), None)
            if row is not None and e.column_key in row.cells:
                cell = row.cells[e.column_key]
                if not cell.edited:
                    cell.original_value, cell.original_passage_ids = cell.value, list(cell.passage_ids)
                cell.value, cell.passage_ids, cell.edited = e.value, [], True
                break
    return out


def mark_refs(marks: Marks) -> list[MarkRef]:
    return [MarkRef(row=r, column=c, kind=m.kind) for (r, c), m in sorted(marks.items())]


# --------------------------------------------------------------------- Key difference (R10, R11)

_POSITIVE = re.compile(r"\b(?:broader|better|best|stronger|wider|superior|more\s+(?:favou?rable|comprehensive|"
                       r"complete|generous))\b", re.I)
_CHEAP = re.compile(r"\b(?:cheap(?:er|est)?|lowest|least\s+expensive|less\s+expensive|lower[\s-]+(?:premium|priced)"
                    r"|most\s+affordable)\b", re.I)
_TERM_WORDS: dict[str, re.Pattern] = {
    AI_KEY: AI_RE,
    WAIVER_KEY: re.compile(WAIVER_RE.pattern + r"|subrogation", re.I),
    PNC_KEY: re.compile(PNC_RE.pattern + r"|non[\s-]*contributory", re.I),
    EXCLUSIONS_KEY: re.compile(r"exclu|" + "|".join(f"(?:{w.pattern.pattern})" for w in WATCH_LIST), re.I),
    PREMIUM_KEY: re.compile(r"premium|\bprice|\bcost|" + _CHEAP.pattern, re.I),
}
MAX_KEY_DIFFERENCE_CHARS = 600


def _join(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _cells(tables: Iterable[TableBlock]) -> tuple[dict[tuple[str, str], Cell], dict[str, str]]:
    cells: dict[tuple[str, str], Cell] = {}
    labels: dict[str, str] = {}
    for t in tables:
        for c in t.columns:
            labels.setdefault(c.key, c.label)
        for row in t.rows:
            for col, cell in row.cells.items():
                cells.setdefault((row.key, col), cell)
    return cells, labels


def carriers_named(text: str, labels: Mapping[str, str]) -> list[frozenset[str]]:
    """Each document `text` names, as the set of columns the name could mean (a label two columns share is
    either). A document is named by its letter followed by "(" ("A (Harborline)") or by its label."""
    found: list[frozenset[str]] = []
    work = text
    by_label: dict[str, set[str]] = {}
    for col, label in labels.items():
        name = _clean(label)
        if len(name) >= 3:
            by_label.setdefault(name.casefold(), set()).add(col)
    for name in sorted(by_label, key=len, reverse=True):  # longest first, so "Harbor Mutual" is not "Harbor"
        rx = re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])", re.I)
        if rx.search(work):
            found.append(frozenset(by_label[name]))
            work = rx.sub(lambda m: " " * len(m.group(0)), work)
    for col in labels:
        if re.search(r"(?<![\w])" + re.escape(col) + r"\s*\(", work):
            found.append(frozenset({col}))
    return found


def check_key_difference(segments: Sequence[CellSegment], tables: Iterable[TableBlock], marks: Marks) -> list[str]:
    """Errors of a model-written Key difference (R11); an empty list means it may be shown."""
    cells, labels = _cells(tables)
    errors: list[str] = []
    text = "".join(s.text for s in segments)
    if not text.strip():
        return ["the key difference has no text"]
    if len(text) > MAX_KEY_DIFFERENCE_CHARS:
        errors.append(f"the key difference is longer than {MAX_KEY_DIFFERENCE_CHARS} characters")
    cited_mark = False
    for i, seg in enumerate(segments):
        refs = list(dict.fromkeys((c.row, c.column) for c in seg.cells))
        for ref in refs:
            if ref not in cells:
                errors.append(f"segment {i}: cell {ref[0]}/{ref[1]} is not in the tables")
        valid = [r for r in refs if r in cells]
        cited_mark = cited_mark or any(r in marks for r in valid)
        allowed = compose.allowed_numbers(cells[r].value for r in valid)
        for tok in compose.prose_numbers(seg.text):
            if (tok.kind, tok.value) not in allowed:
                errors.append(f"segment {i}: {tok.text!r} is not a value of a cell this segment cites")
        cited_cols = {c for _, c in valid}
        for cols in carriers_named(seg.text, labels):
            if not cols & cited_cols:
                errors.append(f"segment {i}: names document {'/'.join(sorted(cols))} without citing its cell")
        cited_rows = {r for r, _ in valid}
        for row, rx in _TERM_WORDS.items():
            if rx.search(seg.text) and row not in cited_rows:
                errors.append(f"segment {i}: names {row} without citing a {row} cell")
        if _POSITIVE.search(seg.text) and any(marks.get(r) and marks[r].kind == "worse" for r in valid):
            errors.append(f"segment {i}: calls a cell marked worse broader or better")
        if _CHEAP.search(seg.text):
            premiums = [r for r in valid if r[0] == PREMIUM_KEY]
            if len(premiums) != 1 or getattr(marks.get(premiums[0]), "kind", None) != "best":
                errors.append(f"segment {i}: says lowest or cheapest of a premium not marked best")
    if not cited_mark:
        errors.append("the key difference cites no marked cell")
    return errors


def _term_reason(row: str, mark: Mark, cell: Cell) -> str:
    if row in TERM_PHRASES:
        value = classify_term(row, cell.value) or _clean(cell.value)
        return f"{TERM_PHRASES[row]} is {value} ({mark.reason[0].lower()}{mark.reason[1:]})"
    return f"{mark.reason[0].lower()}{mark.reason[1:]}"


def template_key_difference(tables: Iterable[TableBlock], marks: Marks) -> list[CellSegment]:
    """The Key difference written from the marks (spec 3.3): "<A> has the lowest premium (<amount>) but <its worse
    terms>." then "<B>: <its worse terms>; <C>: ...". Empty only when nothing is marked."""
    tables = list(tables)
    cells, labels = _cells(tables)
    if not marks:
        return []
    columns = list(labels)
    rows = [row.key for t in tables for row in t.rows]
    worse: dict[str, list[tuple[str, Mark]]] = {}
    for row in rows:
        for col in columns:
            m = marks.get((row, col))
            if m is not None and m.kind == "worse":
                worse.setdefault(col, []).append((row, m))
    segs: list[CellSegment] = []

    def reasons(col: str, prefix: str) -> None:
        for j, (row, m) in enumerate(worse.pop(col, [])):
            if j:
                segs.append(CellSegment(text=", "))
            segs.append(CellSegment(text=(prefix if j == 0 else "") + _term_reason(row, m, cells[(row, col)]),
                                    cells=[CellRef(row=row, column=col)]))

    best = next(((r, c) for (r, c), m in marks.items() if m.kind == "best"), None)
    if best is not None:
        row, col = best
        segs.append(CellSegment(text=f"{col} ({labels.get(col, col)}) has the lowest premium "
                                     f"({_clean(cells[best].value)})", cells=[CellRef(row=row, column=col)]))
        if worse.get(col):
            segs.append(CellSegment(text=" but "))
            reasons(col, "")
        segs.append(CellSegment(text="."))
    others = [c for c in columns if worse.get(c)]
    for k, col in enumerate(others):
        segs.append(CellSegment(text=(" " if segs else "") if k == 0 else "; "))
        reasons(col, f"{col} ({labels.get(col, col)}): ")
    if others:
        segs.append(CellSegment(text="."))
    return [s for s in segs if s.text]


def build_key_difference(segments: Sequence[CellSegment], tables: Iterable[TableBlock], marks: Marks, *,
                         source: Literal["model", "template"]) -> KeyDifferenceBlock:
    """The stored block: its segments plus what it was written against (R3)."""
    cells, _ = _cells(tables)
    block = KeyDifferenceBlock(segments=list(segments), source=source, marks=mark_refs(marks))
    block.cited_values = [CitedValue(row=r.row, column=r.column, value=cells[(r.row, r.column)].value,
                                     edited=cells[(r.row, r.column)].edited)
                          for r in block.cell_refs() if (r.row, r.column) in cells]
    return block


def key_difference_stale(block: KeyDifferenceBlock, content: ArtifactContent,
                         marks: Marks | None = None) -> bool:
    """R3: the marks differ from those it was written against, or a cited cell's value or edited state changed."""
    if marks is None:
        marks = mark_cells(content_tables(content))
    if {(m.row, m.column, m.kind) for m in mark_refs(marks)} != {(m.row, m.column, m.kind) for m in block.marks}:
        return True
    for cv in block.cited_values:
        cell = content.find_row_cell(cv.row, cv.column)
        if cell is None or cell.value != cv.value or cell.edited != cv.edited:
            return True
    return False


def parse_segments(raw: object) -> list[CellSegment] | None:
    """The model's key_difference.segments, or None when missing or malformed."""
    if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
        return None
    out = []
    for s in raw["segments"]:
        if not isinstance(s, dict) or not isinstance(s.get("text"), str):
            return None
        refs = []
        for c in s.get("cells") or []:
            if not isinstance(c, dict) or not isinstance(c.get("row"), str) or not isinstance(c.get("column"), str):
                return None
            refs.append(CellRef(row=c["row"], column=c["column"]))
        out.append(CellSegment(text=s["text"], cells=refs))
    return out


__all__ = ["AI_KEY", "BLANKET", "EXCLUSIONS_KEY", "INCLUDED", "MARKED_ROWS", "Mark", "NONE_OF_THESE",
           "NOT_INCLUDED", "NOT_STATED", "PNC_KEY", "PREMIUM_KEY", "SCHEDULED", "TERM_KEYS", "WAIVER_KEY",
           "WATCH_LIST", "apply_edits", "build_key_difference", "carriers_named", "check_key_difference",
           "classify_term", "content_tables", "eligible", "exclusions_cell", "key_difference_stale", "mark_cells",
           "mark_refs", "parse_segments", "premium_amount", "template_key_difference", "term_cell",
           "watch_matches"]
