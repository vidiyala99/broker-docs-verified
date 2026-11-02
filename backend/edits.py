"""Cell edits, D9 staleness, and prose regeneration that keeps the user's edits (T8).

Staleness rule (D9): prose cites passages, not cells, so "a section whose prose
cites an edited cell" means a section with a paragraph, list or issue segment
that cites one of the edited cell's original passages. A passage is a
(document, page) target shared by every fact on that page, so the rule can
flag a section that talks about another field on the same page. That errs on
the safe side: the tag says "may be out of date", never "is wrong". A cell
that had no chip (Not specified) cannot make prose stale.

Every edit, undo and regeneration snapshots a new artifact version (models).

    edit cell ──► ArtifactContent.edit_cell (edited badge, chips kept as original)
              ──► mark sections citing the original passages stale
              ──► snapshot_version
    regenerate ─► compose_report with the edits as ComposeEdit (the prose sees them)
              ──► merge_edits(latest, new): re-apply every edited cell to the new tables;
                  an edit made while the call ran is unknown to the prose, so its
                  sections are marked stale again
              ──► snapshot_version (stale flags of known edits cleared, notices recomputed)
    retry run ──► RunManager recompose (no edits in the prompt) ──► merge_edits_after_retry
                  (edits kept, their sections stale)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from backend import db, models
from backend.context import AppContext
from backend.pipeline import compose, runner
from backend.schemas import (
    ArtifactContent,
    Cell,
    IssueBlock,
    ListBlock,
    ParagraphBlock,
    Row,
    Section,
    Segment,
    TableBlock,
)

EditKey = tuple[str, str, str, str | None]  # block id, row key, column key, value


class EditError(Exception):
    """A request the edit layer refuses. `code` maps to an API error."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# --------------------------------------------------------------------- queries


def iter_cells(content: ArtifactContent) -> Iterator[tuple[Section, TableBlock, Row, str, Cell]]:
    for section, block in content.iter_blocks():
        if isinstance(block, TableBlock):
            for row in block.rows:
                for col_key, cell in row.cells.items():
                    yield section, block, row, col_key, cell


def edited_cells(content: ArtifactContent) -> list[tuple[TableBlock, Row, str, Cell]]:
    return [(b, r, k, c) for _, b, r, k, c in iter_cells(content) if c.edited]


def prose_passage_ids(section: Section) -> set[int]:
    """Passages cited by the section's prose (tables excluded)."""
    found: set[int] = set()

    def segs(segments: list[Segment]) -> None:
        for s in segments:
            found.update(s.passage_ids)

    for block in section.blocks:
        if isinstance(block, ParagraphBlock):
            segs(block.segments)
        elif isinstance(block, ListBlock):
            for item in block.items:
                segs(item)
        elif isinstance(block, IssueBlock):
            segs(block.impact)
    return found


def all_passage_ids(content: ArtifactContent) -> set[int]:
    """Current chips plus the original-source chips of edited cells."""
    ids = set(content.passage_ids())
    for *_, cell in iter_cells(content):
        ids.update(cell.original_passage_ids)
    return ids


# --------------------------------------------------------------------- pure transforms


def mark_stale(content: ArtifactContent, passage_ids: set[int]) -> list[str]:
    """Flag (in place) every section whose prose cites one of `passage_ids`. Returns their ids."""
    hit: list[str] = []
    if not passage_ids:
        return hit
    for section in content.sections:
        if prose_passage_ids(section) & passage_ids:
            section.stale = True
            hit.append(section.id)
    return hit


def apply_cell_edit(content: ArtifactContent, block_id: str, row_key: str, column_key: str,
                    value: str | None) -> tuple[ArtifactContent, list[str]]:
    """New content with the edit applied and dependent sections stale. Raises KeyError for a bad cell."""
    new = content.edit_cell(block_id, row_key, column_key, value)
    cell = new.find_cell(block_id, row_key, column_key)
    return new, mark_stale(new, set(cell.original_passage_ids))


def edit_keys(content: ArtifactContent) -> set[EditKey]:
    return {(b.id, r.key, k, c.value) for b, r, k, c in edited_cells(content)}


def merge_edits(previous: ArtifactContent, new: ArtifactContent, *,
                known: set[EditKey] | None = None) -> ArtifactContent:
    """Re-apply every edited cell of `previous` onto freshly composed `new` content.

    The new cell's extracted value and chips become the original source. Edits
    not in `known` (the prose was written without them) mark their sections stale.
    """
    out = new.model_copy(deep=True)
    unknown: set[int] = set()
    for block, row, col_key, cell in edited_cells(previous):
        try:
            target = out.find_cell(block.id, row.key, col_key)
        except KeyError:
            continue  # the table no longer has this cell (section plan changed)
        target.original_value = target.value
        target.original_passage_ids = list(target.passage_ids)
        target.value = cell.value
        target.passage_ids = []
        target.edited = True
        if known is None or (block.id, row.key, col_key, cell.value) not in known:
            unknown.update(target.original_passage_ids)
    mark_stale(out, unknown)
    return out


def merge_edits_after_retry(previous: ArtifactContent, new: ArtifactContent) -> ArtifactContent:
    """RunManager hook: a retried run recomposes without the edits, so they all count as unknown."""
    return merge_edits(previous, new, known=None)


def compose_edits(content: ArtifactContent) -> list[compose.ComposeEdit]:
    return [compose.ComposeEdit(column_key=k, field=r.key, value=c.value, original_value=c.original_value)
            for _, r, k, c in edited_cells(content)]


# --------------------------------------------------------------------- database operations


@dataclass(frozen=True)
class SavedVersion:
    artifact_id: int
    version: int
    previous_version: int
    stale_sections: list[str]


def save_cell_edit(ctx: AppContext, artifact_id: int, block_id: str, row_key: str, column_key: str,
                   value: str | None, *, base_version: int | None = None, note: str = "edit") -> SavedVersion:
    """Apply one edit to the latest version (D9: chat-proposed edits too) and snapshot it."""
    with db.session_scope(ctx.factory) as s:
        art = s.get(models.Artifact, artifact_id)
        if art is None:
            raise EditError(404, "not_found", f"No artifact {artifact_id}.")
        if ctx.manager.is_active(art.run_id):
            raise EditError(409, "run_active", "The report is being rebuilt. Edit it when the run finishes.")
        if base_version is not None and base_version != art.current_version:
            raise EditError(409, "version_conflict",
                            f"The report changed (now version {art.current_version}). Reload and edit again.")
        previous = art.current_version
        try:
            content, stale = apply_cell_edit(art.latest_content(), block_id, row_key, column_key, value)
        except KeyError:
            raise EditError(404, "cell_not_found", "That table cell does not exist in this report.") from None
        version = models.snapshot_version(s, art, content, note=f"{note} {block_id}/{row_key}/{column_key}"[:200])
        return SavedVersion(art.id, version.version, previous, stale)


def restore(ctx: AppContext, artifact_id: int, version: int, *, base_version: int | None = None) -> SavedVersion:
    """Undo (DG12): copy `version` forward as a new version."""
    with db.session_scope(ctx.factory) as s:
        art = s.get(models.Artifact, artifact_id)
        if art is None:
            raise EditError(404, "not_found", f"No artifact {artifact_id}.")
        if ctx.manager.is_active(art.run_id):
            raise EditError(409, "run_active", "The report is being rebuilt. Try again when the run finishes.")
        if base_version is not None and base_version != art.current_version:
            raise EditError(409, "version_conflict",
                            f"The report changed (now version {art.current_version}). Reload first.")
        previous = art.current_version
        try:
            row = models.restore_version(s, art, version)
        except LookupError:
            raise EditError(404, "version_not_found", f"This report has no version {version}.") from None
        content = ArtifactContent.model_validate(row.content)
        return SavedVersion(art.id, row.version, previous, [x.id for x in content.sections if x.stale])


@dataclass(frozen=True)
class Regenerated:
    artifact_id: int
    version: int
    previous_version: int
    fallback: bool
    reason: str | None  # "budget_exhausted" or another provider code when the prose call failed


async def regenerate_prose(ctx: AppContext, artifact_id: int, *, base_version: int | None = None) -> Regenerated:
    """Regenerate prose / Retry prose (DG5): recompose from the verified facts plus the user's edits."""
    lock = ctx.lock(("artifact", artifact_id))
    if lock.locked():
        raise EditError(409, "regenerate_in_progress", "Prose is already being regenerated.")
    async with lock:
        with ctx.factory() as s:
            art = s.get(models.Artifact, artifact_id)
            if art is None:
                raise EditError(404, "not_found", f"No artifact {artifact_id}.")
            run_id = art.run_id
            if ctx.manager.is_active(run_id):
                raise EditError(409, "run_active", "The run is still going. Regenerate when it finishes.")
            if base_version is not None and base_version != art.current_version:
                raise EditError(409, "version_conflict",
                                f"The report changed (now version {art.current_version}). Reload first.")
            latest = art.latest_content()
            known = edit_keys(latest)
            inp = runner.compose_input_for_run(art.run, compose_edits(latest))
        budget = ctx.run_budget(run_id)
        result = await compose.compose_report(ctx.provider, budget, inp, label=f"regenerate:{run_id}")
        with db.session_scope(ctx.factory) as s:
            art = s.get(models.Artifact, artifact_id)
            previous = art.current_version
            content = merge_edits(art.latest_content(), result.content, known=known)
            note = "regenerate prose, template fallback" if result.fallback else "regenerate prose"
            version = models.snapshot_version(s, art, content, note=note)
        reason = None
        if result.fallback:
            codes = [e.split(": ", 1)[1] for e in result.errors if e.startswith("provider: ")]
            reason = codes[0] if codes else "validation_failed"
        ctx.suggestions.pop(artifact_id, None)
        return Regenerated(artifact_id, version.version, previous, result.fallback, reason)
