import { createContext, useContext, type MouseEvent } from "react";
import { FileDoc, FilePdf, FileXls, Scan } from "@phosphor-icons/react";
import type { Chip as ChipData, SourceFormat } from "../api/types";
import { Tip } from "../ui/Tip";

/** Slice 6: each document's uploaded format (document_id -> format), so a chip on a Word or Excel quote says its page
 *  was drawn from that file. Provided by the run view; empty elsewhere (every chip then reads as a PDF page). */
export const SourceFormats = createContext<Record<number, SourceFormat | undefined>>({});

const RENDERED_FROM: Partial<Record<SourceFormat, string>> = {
  docx: "Rendered from the Word file",
  xlsx: "Rendered from the Excel file",
};

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
export function Chip({ chip, onOpen, forceScan }: { chip: ChipData; onOpen: OpenSource; forceScan?: boolean }) {  const scan = forceScan || !chip.verified;
  const format = useContext(SourceFormats)[chip.document_id];
  const rendered = format ? RENDERED_FROM[format] : undefined;
  const tip = `${chip.filename}, page ${chip.page_no}${rendered ? `. ${rendered}` : ""}${scan ? ". Read from scan, unverified" : ""}`;
  const onClick = (e: MouseEvent) => onOpen(chip, e.detail > 0 ? "pointer" : "keyboard");
  const Icon = format === "docx" ? FileDoc : format === "xlsx" ? FileXls : FilePdf;
  return (
    <Tip
      text={
        <span className="tip-source">
          <Icon size={16} aria-hidden />
          <span>
            {chip.filename}
            <br />
            Page: {chip.page_no}
            {rendered && (
              <>
                <br />
                {rendered}
              </>
            )}
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
