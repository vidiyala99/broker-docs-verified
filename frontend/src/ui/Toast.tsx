import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "@phosphor-icons/react";

/**
 * Why a toast closed (slice 3b O3). "action" = its action ran (Undo); "replaced" = a newer toast took its place;
 * "unmount" = the toast region went away; "closed" = the caller closed it through its handle.
 */
export type ToastCloseReason = "timeout" | "dismiss" | "action" | "replaced" | "unmount" | "closed";

export interface ToastInput {
  message: string;
  action?: { label: string; run: () => void };
  /** ms; default 6000 (DG12 "Edited. Undo"). */
  hold?: number;
  tone?: "neutral" | "error";
  /** Called exactly once, whatever closes the toast. Task delete sends its DELETE here unless the reason is "action" (D6). */
  onClose?: (reason: ToastCloseReason) => void;
}

export interface ToastHandle {
  id: number;
  /** Close this toast now (no-op if it already closed). */
  close: () => void;
}

interface ToastItem extends ToastInput {
  id: number;
  leaving: boolean;
}

const Ctx = createContext<(t: ToastInput) => ToastHandle>(() => ({ id: 0, close: () => {} }));

export function useToast() {
  return useContext(Ctx);
}

/** One toast at a time, bottom center. Timers pause while the tab is hidden or the toast is hovered/focused. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [item, setItem] = useState<ToastItem | null>(null);
  const seq = useRef(0);
  // The live toast and the ids whose onClose already ran, so onClose fires once per toast.
  const current = useRef<ToastItem | null>(null);
  const closed = useRef(new Set<number>());

  const fire = useCallback((t: ToastItem | null, reason: ToastCloseReason) => {
    if (!t || closed.current.has(t.id)) return;
    closed.current.add(t.id);
    t.onClose?.(reason);
  }, []);

  const dismiss = useCallback(
    (id: number, reason: ToastCloseReason) => {
      if (current.current?.id === id) {
        fire(current.current, reason);
        current.current = null;
      }
      setItem((cur) => (cur && cur.id === id ? { ...cur, leaving: true } : cur));
      window.setTimeout(() => setItem((cur) => (cur && cur.id === id ? null : cur)), 150);
    },
    [fire],
  );

  const show = useCallback(
    (t: ToastInput): ToastHandle => {
      fire(current.current, "replaced");
      seq.current += 1;
      const next: ToastItem = { ...t, id: seq.current, leaving: false };
      current.current = next;
      setItem(next);
      const id = next.id;
      return { id, close: () => dismiss(id, "closed") };
    },
    [fire, dismiss],
  );

  useEffect(() => () => fire(current.current, "unmount"), [fire]);

  return (
    <Ctx.Provider value={show}>
      {children}
      <div className="toast-region" role="status" aria-live="polite" aria-atomic="true">
        {item && <Toast key={item.id} item={item} onDone={(reason) => dismiss(item.id, reason)} />}
      </div>
    </Ctx.Provider>
  );
}

function Toast({ item, onDone }: { item: ToastItem; onDone: (reason: ToastCloseReason) => void }) {
  const hold = item.hold ?? 6000;
  const remaining = useRef(hold);
  const started = useRef(Date.now());
  const timer = useRef<number | undefined>(undefined);
  const [paused, setPaused] = useState(false);
  // The latest onDone without restarting the timer on every render.
  const done = useRef(onDone);
  done.current = onDone;

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
    timer.current = window.setTimeout(() => done.current("timeout"), Math.max(500, remaining.current));
    return () => window.clearTimeout(timer.current);
  }, [paused]);

  // Keyboard path to the action: the toast sits outside the tab order, so Ctrl/Cmd+Z runs it
  // while it is showing (not while typing in a field, where Ctrl+Z means text undo).
  const action = item.action;
  useEffect(() => {
    if (!action || item.leaving) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el?.closest("input, textarea, select, [contenteditable]")) return;
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z") {
        e.preventDefault();
        action.run();
        done.current("action");
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [action, item.leaving]);

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
            onDone("action");
          }}
        >
          {item.action.label}
        </button>
      )}
      <button type="button" className="icon-btn icon-btn--on-dark" aria-label="Dismiss" onClick={() => onDone("dismiss")}>
        <X size={16} aria-hidden />
      </button>
    </div>
  );
}
