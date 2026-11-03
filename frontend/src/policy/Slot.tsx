import { ArrowsClockwise, Warning } from "@phosphor-icons/react";
import type { Cell, Chip as ChipData, DiscrepancyItem, PolicyRole, RunDocument } from "../api/types";
import { Chip, hueOf, type OpenSource } from "../report/Chip";
import { ROLE_LABEL } from "./draft";

/**
 * The chips behind a slot. Each chip is the run's passage chip, narrowed to the cited fact: its own
 * highlight rects (a passage's rects cover every fact on that page) and, for an AMS export, the field
 * name read from the verified quote, so the chip reads "AMS · Cov A" and opens that line.
 */
export function slotChips(item: DiscrepancyItem, role: PolicyRole, chips: Record<string, ChipData>): ChipData[] {
  const cell = item.slots[role];
  if (!cell) return [];
  const sources = item.sources?.[role] ?? [];
  return cell.passage_ids
    .map((pid) => {
      const base = chips[String(pid)];
      if (!base) return null;
      const src = sources.find((s) => s.passage_id === pid);
      if (!src) return base;
      return {
        ...base,
        rects: src.rects.length ? src.rects : base.rects,
        label: src.field ? `AMS · ${src.field}` : base.label,
      };
    })
    .filter((c): c is ChipData => c != null);
}

/** Slot state words (API.md "Policy Check discrepancies", slot table). */
export function slotState(cell: Cell | undefined): "na-doc" | "na-stated" | "not-read" | "review" | "value" {
  if (!cell) return "na-doc";
  if (cell.state === "failed") return "not-read";
  if (cell.state === "conflicting" || cell.state === "not_found") return "review";
  if (cell.state === "verified" || cell.state === "scan_read") return cell.value != null ? "value" : "na-stated";
  return "na-stated";
}

export function describeSlot(item: DiscrepancyItem, role: PolicyRole): string {
  const cell = item.slots[role];
  switch (slotState(cell)) {
    case "na-doc":
      return "N/A, no document for this role";
    case "na-stated":
      return "N/A, not stated in this document";
    case "not-read":
      return "Not read: the document could not be read";
    case "review":
      return cell?.state === "conflicting"
        ? `Needs review: ${(cell.conflicts ?? []).map((c) => `${c.value} on page ${c.page_no}`).join(" or ")}`
        : "Needs review: couldn't verify";
    default:
      return `${cell!.value}${cell!.state === "scan_read" ? ", read from scan, unverified" : ""}`;
  }
}

export function roleLetter(docs: RunDocument[], role: PolicyRole): string | null {
  return docs.find((d) => d.slot === role)?.letter ?? null;
}

export function SlotView({
  item,
  role,
  docs,
  chips,
  onOpen,
  selectedPassage,
}: {
  item: DiscrepancyItem;
  role: PolicyRole;
  docs: RunDocument[];
  chips: Record<string, ChipData>;
  onOpen: OpenSource;
  /** Review dialog: the chip whose source is shown beside the item. */
  selectedPassage?: string | null;
}) {
  const cell = item.slots[role];
  const state = slotState(cell);
  const letter = roleLetter(docs, role);
  // D11: the failed document's own reason (RunDocument.error), so "Not read" never looks like "not stated".
  const reason = state === "not-read" ? docs.find((d) => d.slot === role)?.error ?? null : null;
  const list = slotChips(item, role, chips);
  const chipNodes = (ids: ChipData[]) =>
    ids.length > 0 && (
      <span className="chips">
        {ids.map((c) => (
          <span key={`${c.passage_id}-${c.label}`} className={selectedPassage === `${role}:${c.passage_id}` ? "chip-wrap is-selected" : "chip-wrap"}>
            <Chip chip={c} onOpen={onOpen} forceScan={cell?.state === "scan_read"} />
          </span>
        ))}
      </span>
    );
  return (
    <div className={`slot slot--${state}${role === "renewal" ? " slot--checked" : ""}`} data-role={role}>
      <span className="slot__role">
        {letter && (
          <span className={`doc-letter doc-letter--sm hue-${hueOf(letter)}`} aria-hidden>
            {letter}
          </span>
        )}
        {ROLE_LABEL[role]}
        {letter && <span className="sr-only"> (document {letter})</span>}
        <span className="sr-only">: </span>
      </span>
      <div className="slot__body">
        {state === "value" && (
          <>
            <span className="slot__value">{cell!.value}</span>
            {chipNodes(list)}
            {cell!.state === "scan_read" && <span className="slot__note">Read from scan, unverified</span>}
          </>
        )}
        {state === "na-doc" && (
          <>
            <span className="slot__na">N/A</span>
            <span className="slot__note">No document</span>
          </>
        )}
        {state === "na-stated" && (
          <>
            <span className="slot__na">N/A</span>
            <span className="slot__note">Not stated</span>
          </>
        )}
        {state === "not-read" && (
          <>
            <span className="evidence evidence--failed">
              <ArrowsClockwise size={14} aria-hidden />
              <span>Not read</span>
            </span>
            {reason && <span className="slot__note">{reason}</span>}
          </>
        )}
        {state === "review" && (
          <>
            <span className="evidence evidence--unverified slot__review">
              <Warning size={14} aria-hidden />
              <span>Needs review</span>
            </span>
            {cell!.state === "conflicting" && cell!.conflicts?.length ? (
              <ul className="slot__conflicts" aria-label="Values this document states">
                {cell!.conflicts.map((c, i) => {
                  const chip = list.find((x) => x.passage_id === c.passage_id) ?? null;
                  return (
                    <li key={i}>
                      <span className="slot__conflict-value">{c.value}</span>
                      {chip && chipNodes([chip])}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <span className="slot__note">Couldn't verify</span>
            )}
          </>
        )}
      </div>
    </div>
  );
}
