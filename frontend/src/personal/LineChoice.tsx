import { useId } from "react";
import { Check } from "@phosphor-icons/react";
import type { Line } from "../api/types";
import "./personal.css";

export const LINE_LABEL: Record<Line, string> = { commercial: "Commercial", personal: "Personal" };
const LINES: Line[] = ["commercial", "personal"];

/** `?line=personal` in the hash query presets an entry screen (PERSONAL-LINES-BRIEF.md); no preset means commercial. */
export function lineFromHash(hash: string): Line | null {
  const q = hash.indexOf("?");
  if (q === -1) return null;
  const v = new URLSearchParams(hash.slice(q + 1)).get("line");
  return v === "personal" || v === "commercial" ? v : null;
}

/**
 * Slice 3b T6: Commercial / Personal on the start screens (Compare upload, Policy Check step 1). Native radios in a
 * fieldset (one tab stop, arrow keys), drawn as a two-button segment with the Policy Check filter-toggle look (tint,
 * primary border, check glyph). The run header no longer uses it: a run shows its book tab and "Move to ..." instead
 * (line move B, run/BookTab.tsx).
 */
export function LineChoice({ value, onChange }: { value: Line; onChange: (line: Line) => void }) {
  const name = useId();
  return (
    <fieldset className="line-choice">
      <legend className="line-choice__legend">Line of business</legend>
      <div className="line-choice__seg">
        {LINES.map((l) => (
          <label key={l} className="line-choice__opt">
            <input type="radio" name={name} value={l} checked={value === l} onChange={() => onChange(l)} />
            {/* Always drawn, hidden when unchecked: both halves keep one width, so a flip never shifts the segment. */}
            <Check size={14} weight="bold" className={`line-choice__check${value === l ? "" : " is-off"}`} aria-hidden />
            <span>{LINE_LABEL[l]}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
