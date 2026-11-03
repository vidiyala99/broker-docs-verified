// Reusable motion primitives (work queue lane; the app-wide motion lane builds on these). CSS in ui/motion.css.
// Rules (DESIGN.md Motion, emil-design-eng, animate): transform and opacity only, plus the sanctioned grid-row collapse
// for disclosure height; ease-out on enter/exit; at most 200ms; keyboard-triggered opens are instant; reduced motion
// keeps opacity and drops movement.
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { keyboardAction } from "./input";
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
 * `keepMounted` keeps children that hold state (upload rows, a chat draft) and hides them with `hidden` once the exit
 * has settled instead of unmounting. Reduced motion (motion.css) keeps the opacity fade and drops the height change.
 */
export function Collapse({
  open,
  animate,
  id,
  keepMounted = false,
  className,
  children,
}: {
  open: boolean;
  animate: boolean;
  id?: string;
  keepMounted?: boolean;
  className?: string;
  children: ReactNode;
}) {
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
  if (!mounted && !open && !keepMounted) return null;
  return (
    <div
      id={id}
      className={`collapse${animate ? " collapse--animate" : ""}${className ? ` ${className}` : ""}`}
      data-open={open ? "true" : "false"}
      data-settled={settled ? "true" : undefined}
      hidden={(!mounted && !open) || undefined}
      inert={!open || undefined}
    >
      <div className="collapse__inner">{children}</div>
    </div>
  );
}

/** The live value of a motion token from tokens.css (WAAPI cannot read `var()`), e.g. `token("--ease-out")`. */
export function token(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** Milliseconds of a `--dur-*` token. */
export function tokenMs(name: string, fallback: number): number {
  const v = parseFloat(token(name, `${fallback}ms`));
  return Number.isFinite(v) ? v : fallback;
}

export function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Filtered lists (motion audit, list filters): when `key` changes because of a pointer action (a filter chip, a
 * checkbox click), the list re-settles with a short opacity lift (0.35 to 1, --dur-fast, --ease-out) instead of
 * teleporting. The rows are not remounted, so no state or focus is lost. Keyboard changes (typing in search, Space on
 * a filter) stay instant. Opacity only, so reduced motion keeps it.
 */
export function useFadeOnChange(ref: RefObject<HTMLElement | null>, key: unknown): void {
  const first = useRef(true);
  const anim = useRef<Animation | null>(null);
  useLayoutEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const el = ref.current;
    if (!el || keyboardAction() || typeof el.animate !== "function") return;
    anim.current?.cancel();
    anim.current = el.animate([{ opacity: 0.35 }, { opacity: 1 }], {
      duration: tokenMs("--dur-fast", 150),
      easing: token("--ease-out", "ease-out"),
    });
    // The key is the trigger; the ref is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}

/**
 * Wizard steps (Stepper, Next / Back): the new step body fades in (--dur-fast, --ease-out) from 8px on the side it
 * comes from, so forward reads as forward. Pointer changes only; keyboard step changes are instant; reduced motion
 * keeps the fade and drops the slide. WAAPI with no fill, so nothing (no transform, no stacking context) stays behind.
 */
export function useStepTransition(ref: RefObject<HTMLElement | null>, step: number): void {
  const prev = useRef(step);
  useLayoutEffect(() => {
    const from = prev.current;
    prev.current = step;
    if (from === step) return;
    const el = ref.current;
    if (!el || keyboardAction() || typeof el.animate !== "function") return;
    const dx = reducedMotion() ? 0 : step > from ? 8 : -8;
    el.animate(
      [
        { opacity: 0, transform: `translateX(${dx}px)` },
        { opacity: 1, transform: "translateX(0)" },
      ],
      { duration: tokenMs("--dur-fast", 150), easing: token("--ease-out", "ease-out") },
    );
  }, [ref, step]);
}

/** Lands just before the dialog's CSS exit (--dur-fast, 150ms) ends, so the closed dialog never flashes back. */
export const DIALOG_EXIT_MS = 140;

/**
 * Close for a native `<dialog>` opened with showModal(). A pointer-initiated close plays the exit (the dialog gets
 * `is-closing`: opacity + scale 0.98 and a backdrop fade, CSS) and calls `close()` when it ends, so `showModal`
 * semantics, the native focus return and the `close` event are unchanged. Keyboard closes (Esc is native; Enter on a
 * button) are instant.
 */
export function useDialogClose(ref: RefObject<HTMLDialogElement | null>): { closing: boolean; close: () => void } {
  const [closing, setClosing] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const close = useCallback(() => {
    const d = ref.current;
    if (!d || !d.open || timer.current !== undefined) return;
    if (keyboardAction()) {
      d.close();
      return;
    }
    setClosing(true);
    timer.current = window.setTimeout(() => {
      timer.current = undefined;
      if (d.open) d.close();
      setClosing(false);
    }, DIALOG_EXIT_MS);
  }, [ref]);
  return { closing, close };
}

/** A count that pops briefly when its value changes after the first render (state indication, not decoration). */
export function Count({ value, animate, className = "accordion__count" }: { value: number; animate: boolean; className?: string }) {
  return (
    <span key={value} className={`${className}${animate ? " count-bump" : ""}`}>
      {value}
    </span>
  );
}
