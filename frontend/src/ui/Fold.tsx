import { useState } from "react";

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
  if (!hidden) return null;
  return (
    <li className="more-row">
      <button
        type="button"
        className="more-row__btn"
        aria-expanded={expanded}
        aria-controls={controls}
        aria-label={expanded ? `Show fewer ${noun}` : `Show ${hidden} more ${noun}`}
        onClick={onToggle}
      >
        {expanded ? "Show fewer" : `+${hidden} more`}
      </button>
    </li>
  );
}
