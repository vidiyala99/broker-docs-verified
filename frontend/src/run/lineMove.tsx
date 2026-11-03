// PROTOTYPE (line-move lane, Phase A). Throwaway: three directions for "moving a file between Commercial and Personal
// reads static", switched by `localStorage.lineMove` = "a" | "b" | "c" (anything else = shipped behaviour). Not for merge
// as is; the chosen direction gets rebuilt properly in Phase B.
import { useLayoutEffect, useRef } from "react";
import type { Line } from "../api/types";
import { keyboardAction, pointerAction } from "../ui/input";
import { reducedMotion } from "../ui/motion";
import "./lineMove.css";

export type LineMoveVariant = "0" | "a" | "b" | "c";

export function lineMoveVariant(): LineMoveVariant {
  const v = typeof localStorage !== "undefined" ? localStorage.getItem("lineMove") : null;
  return v === "a" || v === "b" || v === "c" ? v : "0";
}

export const BOOK_NAME: Record<Line, string> = { commercial: "Commercial", personal: "Personal lines" };
export const otherLine = (l: Line): Line => (l === "personal" ? "commercial" : "personal");

const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round" } as const;

/** The book's mark: the house (the rail's Personal lines outline) or an office block for Commercial. Decorative. */
export function BookMark({ line, size = 18 }: { line: Line; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden className="bookmark">
      {line === "personal" ? (
        <path {...S} d="M3.75 11 12 4l8.25 7M6 9.25v11h12v-11M10 20.25v-5.5h4v5.5" />
      ) : (
        <>
          <rect x="5.25" y="3.75" width="13.5" height="16.5" rx="1.5" {...S} fill="var(--color-surface)" />
          <path {...S} d="M8.75 7.75h1.5M13.75 7.75h1.5M8.75 11.25h1.5M13.75 11.25h1.5M10.5 20.25v-4.5h3v4.5M3.75 20.25h16.5" />
        </>
      )}
    </svg>
  );
}

/**
 * Re-file motion: when `key` changes because of a pointer action, the element drops into place
 * (translateY 6px to 0 + opacity, 200ms ease-out). Keyboard: instant. Reduced motion: opacity only. First render: still.
 */
export function useRefile(ref: React.RefObject<HTMLElement | null>, key: unknown, dy = 6) {
  const prev = useRef(key);
  useLayoutEffect(() => {
    if (prev.current === key) return;
    prev.current = key;
    const el = ref.current;
    if (!el || keyboardAction() || !pointerAction()) return;
    const move = reducedMotion() ? 0 : dy;
    el.animate(
      [
        { opacity: 0, transform: `translateY(${move}px)` },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { duration: 200, easing: "cubic-bezier(0.23, 1, 0.32, 1)" },
    );
  }, [key, ref, dy]);
}

/** Direction A: the book named in the header metadata ("Personal lines" before "Run 7, version 1"). */
export function BookMeta({ line }: { line: Line }) {
  const ref = useRef<HTMLSpanElement>(null);
  useRefile(ref, line, 4);
  return (
    <span ref={ref} className="toolbar__book">
      <BookMark line={line} size={14} />
      {BOOK_NAME[line]}
    </span>
  );
}

/** Direction B/C header control: the book the file sits in, as a paper tab, plus the one action that moves it. */
export function BookTab({ line, onMove, busy }: { line: Line; onMove: (to: Line) => void; busy?: boolean }) {
  const tab = useRef<HTMLSpanElement>(null);
  useRefile(tab, line);
  const to = otherLine(line);
  return (
    <div className="booktab" role="group" aria-label="Book">
      <span ref={tab} className={`booktab__tab booktab__tab--${line}`}>
        <BookMark line={line} />
        <span>{BOOK_NAME[line]}</span>
      </span>
      <button type="button" className="booktab__move" disabled={busy} onClick={() => onMove(to)}>
        Move to {BOOK_NAME[to]}
      </button>
    </div>
  );
}

/** Direction C: the rail's active tint travels from the old book to the new one (240ms ease-in-out, pointer only). */
export function useRailTravel(section: string) {
  const prev = useRef<{ section: string; top: number } | null>(null);
  useLayoutEffect(() => {
    const now = document.querySelector<HTMLElement>(".rail__item.is-active");
    const before = prev.current;
    prev.current = now ? { section, top: now.getBoundingClientRect().top } : null;
    if (lineMoveVariant() !== "c" || !now || !before || before.section === section) return;
    if (keyboardAction() || !pointerAction()) return;
    const r = now.getBoundingClientRect();
    if (reducedMotion()) {
      now.animate([{ opacity: 0.4 }, { opacity: 1 }], { duration: 200, easing: "ease-out" });
      return;
    }
    const ghost = document.createElement("span");
    ghost.className = "rail-ghost";
    Object.assign(ghost.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
    document.body.appendChild(ghost);
    now.classList.add("is-arriving");
    const a = ghost.animate([{ transform: `translateY(${before.top - r.top}px)` }, { transform: "translateY(0)" }], {
      duration: 240,
      easing: "cubic-bezier(0.77, 0, 0.175, 1)",
    });
    a.onfinish = a.oncancel = () => {
      ghost.remove();
      now.classList.remove("is-arriving");
    };
  }, [section]);
}
