// Work queue "Build proposal" (spec 1.3, D6, M5): a proposal draft from a comparison, replacing any saved draft.
import { api } from "../api/client";
import type { DocumentInfo, QueueItem } from "../api/types";
import { DRAFT_RESTORED_EVENT, EMPTY_DRAFT, isBlank, loadDraft, saveDraft, type Draft } from "../wizard/draft";

export interface BuiltDraft {
  /** The unfinished draft that was replaced, if there was one worth restoring. */
  replaced: Draft | null;
}

/**
 * Writes a new proposal draft with the comparison's documents (column order), its client and `source_run_id` + `line`,
 * then the caller opens `#/proposals/new`. A previous unfinished draft is returned so the caller can offer Undo.
 */
export async function draftFromComparison(item: QueueItem): Promise<BuiltDraft> {
  const [run, docs] = await Promise.all([api.run(item.run_id), api.documents()]);
  const byId = new Map<number, DocumentInfo>(docs.map((d) => [d.id, d]));
  const picked = [...run.documents]
    .sort((a, b) => a.position - b.position)
    .map((d) => byId.get(d.document_id))
    .filter((d): d is DocumentInfo => d != null && d.kind !== "ams");
  const previous = loadDraft();
  // Building again from the same comparison only refreshes it: nothing to restore.
  const replaced = !isBlank(previous) && previous.source_run_id !== item.run_id ? previous : null;
  saveDraft({
    ...EMPTY_DRAFT,
    docs: picked,
    client_name: item.client ?? "",
    source_run_id: item.run_id,
    source_client: item.client,
    line: run.line,
  });
  return { replaced };
}

/** Undo: put the replaced draft back; an open wizard reloads it. */
export function restoreDraft(draft: Draft) {
  saveDraft(draft);
  window.dispatchEvent(new Event(DRAFT_RESTORED_EVENT));
}
