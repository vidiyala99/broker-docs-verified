import { useLayoutEffect, useRef, useState } from "react";
import { keyboardAction } from "./input";
import { reducedMotion, token, tokenMs } from "./motion";

/** Lists longer than this fold; the first FOLD_KEEP rows stay, plus every row that carries news. */
export const FOLD_AT = 6;
export const FOLD_KEEP = 5;

/**
 * Folding for the panel's document lists: past FOLD_AT rows, quiet rows after the first
 * FOLD_KEEP collapse into a "+N more" row. Rows with news (failed, active, rejected) never fold.
 * Folding a single row saves nothing, so a fold of one shows everything instead.
 */
export function useFold<T>(items: T[], hasNews: (item: T) => boolean) {
  const [expanded, setExpanded] = useState(false);
  const foldable = items.length > FOLD_AT;
  const folded = foldable ? items.map((it, i) => !(i < FOLD_KEEP || hasNews(it))) : items.map(() => false);
  const hidden = folded.filter(Boolean).length;
  const folds = foldable && hidden > 1;
  // "+N more" sits where the first folded row would be; "Show fewer" closes the full list.
  const at = folds ? (expanded ? items.length : folded.indexOf(true)) : -1;
  const entries: ({ more: false; item: T } | { more: true })[] = [];
  items.forEach((item, i) => {
    if (i === at) entries.push({ more: true });
    if (!folds || expanded || !folded[i]) entries.push({ more: false, item });
  });
  if (at === items.length) entries.push({ more: true });
  return {
    /** Rows in order, with the "+N more" / "Show fewer" slot as `{ more: true }`. */
    entries,
    hidden: folds ? hidden : 0,
    expanded,
    toggle: () => setExpanded((e) => !e),
  };
}

export function MoreRow({
  hidden,
  expanded,
  onToggle,
  noun,
  controls,
}: {
  hidden: number;
  expanded: boolean;
  onToggle: () => void;
  noun: string;
  controls: string;
}) {
  // A pointer "+N more" reveals the folded rows with a short enter (opacity + 4px, --dur-base): they are not one
  // contiguous block (rows with news never fold), so each revealed row enters on its own instead of a single height
  // fold. Keyboard expands and "Show fewer" are instant. The rows are the callers' own <li>s; nothing remounts.
  const before = useRef<Set<Element> | null>(null);
  useLayoutEffect(() => {
    const seen = before.current;
    before.current = null;
    const list = seen && expanded ? document.getElementById(controls) : null;
    if (!seen || !list) return;
    const dy = reducedMotion() ? 0 : -4;
    for (const li of Array.from(list.children)) {
      if (seen.has(li) || typeof li.animate !== "function") continue;
      li.animate(
        [
          { opacity: 0, transform: `translateY(${dy}px)` },
          { opacity: 1, transform: "translateY(0)" },
        ],
        { duration: tokenMs("--dur-base", 200), easing: token("--ease-out", "ease-out") },
      );
    }
  }, [expanded, controls]);
  if (!hidden) return null;
  return (
    <li className="more-row">
      <button
        type="button"
        className="more-row__btn"
        aria-expanded={expanded}
        aria-controls={controls}
        aria-label={expanded ? `Show fewer ${noun}` : `Show ${hidden} more ${noun}`}
        onClick={(e) => {
          const list = document.getElementById(controls);
          if (!expanded && e.detail > 0 && !keyboardAction() && list) before.current = new Set(Array.from(list.children));
          onToggle();
        }}
      >
        {expanded ? "Show fewer" : `+${hidden} more`}
      </button>
    </li>
  );
}
