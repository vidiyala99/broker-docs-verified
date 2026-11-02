import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

let lastHide = 0; // tooltips after the first open instantly (emil: skip delay on subsequent hovers)

/**
 * Fixed-position tooltip so it escapes scrolling tables. Shown on hover and keyboard focus.
 * The same text must also be in the trigger's aria-label (DG10); the bubble itself is aria-hidden.
 */
export function Tip({ text, children, side = "top" }: { text: ReactNode; children: ReactNode; side?: "top" | "right" | "bottom" }) {
  const wrap = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ x: number; y: number; instant: boolean } | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const show = (delay: number) => {
    window.clearTimeout(timer.current);
    const instant = Date.now() - lastHide < 400;
    timer.current = window.setTimeout(() => {
      const el = wrap.current?.firstElementChild ?? wrap.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      if (side === "right") setPos({ x: r.right + 8, y: r.top + r.height / 2, instant });
      else if (side === "bottom") setPos({ x: r.left + r.width / 2, y: r.bottom + 8, instant });
      else setPos({ x: r.left + r.width / 2, y: r.top - 8, instant });
    }, instant ? 0 : delay);
  };
  const hide = () => {
    window.clearTimeout(timer.current);
    if (pos) lastHide = Date.now();
    setPos(null);
  };

  useEffect(() => {
    if (!pos) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPos(null);
    const onScroll = () => setPos(null);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [pos]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <span
      ref={wrap}
      className="tip-anchor"
      onMouseEnter={() => show(350)}
      onMouseLeave={hide}
      onFocus={(e) => {
        if ((e.target as HTMLElement).matches(":focus-visible")) show(0);
      }}
      onBlur={hide}
    >
      {children}
      {pos &&
        createPortal(
          <span
            className={`tooltip tooltip--${side}${pos.instant ? " is-instant" : ""}`}
            style={{ left: pos.x, top: pos.y }}
            aria-hidden="true"
          >
            {text}
          </span>,
          document.body,
        )}
    </span>
  );
}
