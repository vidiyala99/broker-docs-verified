import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "@phosphor-icons/react";

export interface ToastInput {
  message: string;
  action?: { label: string; run: () => void };
  /** ms; default 6000 (DG12 "Edited. Undo"). */
  hold?: number;
  tone?: "neutral" | "error";
}

interface ToastItem extends ToastInput {
  id: number;
  leaving: boolean;
}

const Ctx = createContext<(t: ToastInput) => void>(() => {});

export function useToast() {
  return useContext(Ctx);
}

/** One toast at a time, bottom center. Timers pause while the tab is hidden or the toast is hovered/focused. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [item, setItem] = useState<ToastItem | null>(null);
  const seq = useRef(0);

  const show = useCallback((t: ToastInput) => {
    seq.current += 1;
    setItem({ ...t, id: seq.current, leaving: false });
  }, []);

  const dismiss = useCallback((id: number) => {
    setItem((cur) => (cur && cur.id === id ? { ...cur, leaving: true } : cur));
    window.setTimeout(() => setItem((cur) => (cur && cur.id === id ? null : cur)), 150);
  }, []);

  return (
    <Ctx.Provider value={show}>
      {children}
      <div className="toast-region" role="status" aria-live="polite" aria-atomic="true">
        {item && <Toast key={item.id} item={item} onDone={() => dismiss(item.id)} />}
      </div>
    </Ctx.Provider>
  );
}

function Toast({ item, onDone }: { item: ToastItem; onDone: () => void }) {
  const hold = item.hold ?? 6000;
  const remaining = useRef(hold);
  const started = useRef(Date.now());
  const timer = useRef<number | undefined>(undefined);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    const onVis = () => setPaused(document.hidden);
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  useEffect(() => {
    if (paused) {
      window.clearTimeout(timer.current);
      remaining.current -= Date.now() - started.current;
      return;
    }
    started.current = Date.now();
    timer.current = window.setTimeout(onDone, Math.max(500, remaining.current));
    return () => window.clearTimeout(timer.current);
  }, [paused, onDone]);

  // Keyboard path to the action: the toast sits outside the tab order, so Ctrl/Cmd+Z runs it
  // while it is showing (not while typing in a field, where Ctrl+Z means text undo).
  const action = item.action;
  useEffect(() => {
    if (!action) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el?.closest("input, textarea, select, [contenteditable]")) return;
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z") {
        e.preventDefault();
        action.run();
        onDone();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [action, onDone]);

  return (
    <div
      className={`toast${item.leaving ? " is-leaving" : ""}${item.tone === "error" ? " toast--error" : ""}`}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <span className="toast__text">{item.message}</span>
      {item.action && (
        <button
          type="button"
          className="toast__action"
          aria-keyshortcuts="Control+Z Meta+Z"
          onClick={() => {
            item.action?.run();
            onDone();
          }}
        >
          {item.action.label}
        </button>
      )}
      <button type="button" className="icon-btn icon-btn--on-dark" aria-label="Dismiss" onClick={onDone}>
        <X size={16} aria-hidden />
      </button>
    </div>
  );
}
