// Line move, direction B (owner-approved 2026-11-03, LINE-MOVE-BRIEF.md). The run header shows the book the file is
// filed in (a paper tab: the book's mark and name) and one action that moves it ("Move to Commercial"). This replaced
// the Commercial / Personal radios in the run header; the start screens keep their full-size Line choice.
import { useLayoutEffect, useRef, type RefObject } from "react";
import type { Line } from "../api/types";
import { keyboardAction, pointerAction } from "../ui/input";
import { reducedMotion, token, tokenMs } from "../ui/motion";
import { Glyph } from "../voice/marks";
import "./booktab.css";

/** The book's name, as the crumbs, the tab, the Move button and the toast say it. */
export const BOOK_NAME: Record<Line, string> = { commercial: "Commercial", personal: "Personal lines" };
export const otherBook = (l: Line): Line => (l === "personal" ? "commercial" : "personal");

/**
 * Re-file motion: when `key` changes because of a pointer action, the element drops into place (translateY 6px to 0
 * plus opacity, --dur-base, --ease-out). Keyboard: instant. Reduced motion: the fade only. First render: still.
 */
function useRefile(ref: RefObject<HTMLElement | null>, key: unknown) {
  const prev = useRef(key);
  useLayoutEffect(() => {
    if (prev.current === key) return;
    prev.current = key;
    const el = ref.current;
    if (!el || keyboardAction() || !pointerAction()) return;
    const dy = reducedMotion() ? 0 : 6;
    el.animate(
      [
        { opacity: 0, transform: `translateY(${dy}px)` },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { duration: tokenMs("--dur-base", 200), easing: token("--ease-out", "cubic-bezier(0.23, 1, 0.32, 1)") },
    );
  }, [key, ref]);
}

/**
 * The run header passes `onMove` (tab + "Move to ..." button). Without it (line switch, LINE-SWITCH-BRIEF.md) the tab
 * stands alone: the Policy Check wizard shows the book its new check will be filed in, following the Line choice.
 */
export function BookTab({ line, onMove }: { line: Line; onMove?: (to: Line) => void }) {
  const tab = useRef<HTMLSpanElement>(null);
  useRefile(tab, line);
  const to = otherBook(line);
  return (
    <div className="booktab" role="group" aria-label="Book">
      <span ref={tab} className="booktab__tab">
        <Glyph name={line} size={18} />
        <span>{BOOK_NAME[line]}</span>
      </span>
      {onMove && (
        <button type="button" className="booktab__move" onClick={() => onMove(to)}>
          <span>Move to {BOOK_NAME[to]}</span>
        </button>
      )}
    </div>
  );
}
