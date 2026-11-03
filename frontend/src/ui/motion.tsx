// Reusable motion primitives (work queue lane; the app-wide motion lane builds on these). CSS in ui/motion.css.
// Rules (DESIGN.md Motion, emil-design-eng, animate): transform and opacity only, plus the sanctioned grid-row collapse
// for disclosure height; ease-out on enter/exit; at most 200ms; keyboard-triggered opens are instant; reduced motion
// keeps opacity and drops movement.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import "./motion.css";

/** Exit duration in ms; matches `--dur-base` (200ms) minus a frame so the node is gone when the transition ends. */
export const EXIT_MS = 190;

export interface Presence<T> {
  item: T;
  /** Rendered only for its exit animation; not interactive. */
  leaving: boolean;
  /** Mounted after the list was first shown (plays the enter animation once). */
  entering: boolean;
}

/**
 * Keeps removed list items mounted for EXIT_MS so they can animate out, at their old position. `animate` false (first
 * load, reduced motion is handled in CSS) mounts and removes without motion.
 */
export function usePresence<T>(items: T[], keyOf: (t: T) => string | number, animate: boolean): Presence<T>[] {
  const prev = useRef<T[]>(items);
  const [leaving, setLeaving] = useState<{ item: T; index: number }[]>([]);
  const known = useRef<Set<string | number>>(new Set(items.map(keyOf)));
  const enteringKeys = useRef<Set<string | number>>(new Set());

  // Entering: keys this list has not shown before (decided during render, kept for the node's lifetime).
  for (const it of items) {
    const k = keyOf(it);
    if (!known.current.has(k)) {
      known.current.add(k);
      if (animate) enteringKeys.current.add(k);
    }
  }

  useLayoutEffect(() => {
    const now = new Set(items.map(keyOf));
    const gone = prev.current
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => !now.has(keyOf(item)));
    prev.current = items;
    for (const g of gone) known.current.delete(keyOf(g.item));
    if (!animate || gone.length === 0) return;
    setLeaving((l) => [...l.filter((x) => !now.has(keyOf(x.item))), ...gone]);
    // Not cleared on the next change: each batch of leavers is removed on its own clock.
    window.setTimeout(() => {
      const goneKeys = new Set(gone.map((g) => keyOf(g.item)));
      setLeaving((l) => l.filter((x) => !goneKeys.has(keyOf(x.item))));
    }, EXIT_MS);
    // keyOf is stable by contract (a pure field read)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, animate]);

  const out: Presence<T>[] = items.map((item) => ({ item, leaving: false, entering: enteringKeys.current.has(keyOf(item)) }));
  const present = new Set(items.map(keyOf));
  for (const l of leaving) {
    if (present.has(keyOf(l.item))) continue;
    out.splice(Math.min(l.index, out.length), 0, { item: l.item, leaving: true, entering: false });
  }
  return out;
}

/**
 * Disclosure body that animates its height (grid rows 0fr to 1fr) and opacity on pointer opens; keyboard opens and
 * closes are instant (`animate` false). Closed content is unmounted after the exit, so it is out of the tab order.
 */
export function Collapse({ open, animate, id, children }: { open: boolean; animate: boolean; id?: string; children: ReactNode }) {
  const [mounted, setMounted] = useState(open);
  const [settled, setSettled] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      if (!animate) {
        setSettled(true);
        return;
      }
      const t = window.setTimeout(() => setSettled(true), EXIT_MS);
      return () => window.clearTimeout(t);
    }
    setSettled(false);
    if (!animate) {
      setMounted(false);
      return;
    }
    const t = window.setTimeout(() => setMounted(false), EXIT_MS);
    return () => window.clearTimeout(t);
  }, [open, animate]);
  if (!mounted && !open) return null;
  return (
    <div
      id={id}
      className={`collapse${animate ? " collapse--animate" : ""}`}
      data-open={open ? "true" : "false"}
      data-settled={settled ? "true" : undefined}
      inert={!open || undefined}
    >
      <div className="collapse__inner">{children}</div>
    </div>
  );
}

/** A count that pops briefly when its value changes after the first render (state indication, not decoration). */
export function Count({ value, animate, className = "accordion__count" }: { value: number; animate: boolean; className?: string }) {
  return (
    <span key={value} className={`${className}${animate ? " count-bump" : ""}`}>
      {value}
    </span>
  );
}
