import type { MouseEvent } from "react";
import { FilePdf, Scan } from "@phosphor-icons/react";
import type { Chip as ChipData } from "../api/types";
import { Tip } from "../ui/Tip";

/** DG11: five hues cycle by document position; the letter always travels with the hue. */
export function hueOf(letter: string): number {
  const i = Math.max(0, letter.toUpperCase().charCodeAt(0) - 65);
  return (i % 5) + 1;
}

export interface OpenSource {
  (chip: ChipData, via: "pointer" | "keyboard"): void;
}

/**
 * A citation chip is a button (DG10): Enter or click opens the PDF at the page.
 * Verified: solid tint. Scan-read: dashed border plus scan glyph (DG3).
 */
export function Chip({ chip, onOpen, forceScan }: { chip: ChipData; onOpen: OpenSource; forceScan?: boolean }) {
  const scan = forceScan || !chip.verified;
  const tip = `${chip.filename}, page ${chip.page_no}${scan ? ". Read from scan, unverified" : ""}`;
  const onClick = (e: MouseEvent) => onOpen(chip, e.detail > 0 ? "pointer" : "keyboard");
  return (
    <Tip
      text={
        <span className="tip-source">
          <FilePdf size={16} aria-hidden />
          <span>
            {chip.filename}
            <br />
            Page: {chip.page_no}
            {scan && (
              <>
                <br />
                Read from scan, unverified
              </>
            )}
          </span>
        </span>
      }
    >
      <button
        type="button"
        className={`chip hue-${hueOf(chip.letter)}${scan ? " chip--scan" : ""}`}
        aria-label={`Source ${chip.label}: ${tip}`}
        data-chip={chip.label}
        onClick={onClick}
      >
        {scan && <Scan size={12} weight="bold" aria-hidden />}
        {chip.label}
      </button>
    </Tip>
  );
}

export function ChipList({
  ids,
  chips,
  onOpen,
}: {
  ids: number[];
  chips: Record<string, ChipData>;
  onOpen: OpenSource;
}) {
  const list = ids.map((id) => chips[String(id)]).filter((c): c is ChipData => Boolean(c));
  if (!list.length) return null;
  return (
    <span className="chips">
      {list.map((c) => (
        <Chip key={c.passage_id} chip={c} onOpen={onOpen} />
      ))}
    </span>
  );
}
