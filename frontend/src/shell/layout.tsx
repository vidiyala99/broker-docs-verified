import { useEffect, useState, type ReactNode } from "react";
import { ChatCircleText, SidebarSimple, UploadSimple } from "@phosphor-icons/react";
import { keyboardAction } from "../ui/input";
import { tokenMs } from "../ui/motion";

export type Viewport = "wide" | "mid" | "narrow";

/** DG9: 1280+ full, 1024 to 1279 collapsed left panel and PDF as a tab, under 1024 a notice. */
export function useViewport(): Viewport {
  const get = (): Viewport => (window.innerWidth >= 1280 ? "wide" : window.innerWidth >= 1024 ? "mid" : "narrow");
  const [v, setV] = useState<Viewport>(get);
  useEffect(() => {
    const wide = window.matchMedia("(min-width: 1280px)");
    const mid = window.matchMedia("(min-width: 1024px)");
    const on = () => setV(get());
    wide.addEventListener("change", on);
    mid.addEventListener("change", on);
    return () => {
      wide.removeEventListener("change", on);
      mid.removeEventListener("change", on);
    };
  }, []);
  return v;
}

/**
 * The 300px left panel. Under 1280px it collapses to a 56px strip; expanding it lays the
 * panel over the report (Esc or the close button folds it back).
 */
export function LeftPanel({
  viewport,
  label,
  onReveal,
  children,
}: {
  viewport: Viewport;
  label: string;
  /** The strip's Uploads and Chat buttons open the overlay with that section expanded. */
  onReveal?: (section: "uploads" | "chat") => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  // Motion (app.css .left-panel--overlay): pointer opens slide in, pointer closes slide out and unmount after the
  // exit; keyboard opens and Esc are instant. `animated` is decided at the open, `leaving` holds the panel for its exit.
  const [animated, setAnimated] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const show = () => {
    setAnimated(!keyboardAction());
    setLeaving(false);
    setOpen(true);
  };
  const hide = (viaPointer: boolean) => {
    setOpen(false);
    setLeaving(viaPointer && !keyboardAction());
  };
  const reveal = (section: "uploads" | "chat") => {
    onReveal?.(section);
    show();
  };
  const collapsed = viewport !== "wide";

  useEffect(() => {
    if (!collapsed) {
      setOpen(false);
      setLeaving(false);
    }
  }, [collapsed]);

  useEffect(() => {
    if (!leaving) return;
    const t = window.setTimeout(() => setLeaving(false), tokenMs("--dur-base", 200));
    return () => window.clearTimeout(t);
  }, [leaving]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !(e.target as HTMLElement).closest("input, textarea")) hide(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!collapsed) {
    return (
      <aside className="left-panel" aria-label={label}>
        {children}
      </aside>
    );
  }
  return (
    <>
      <div className="left-strip">
        <button type="button" className="icon-btn" aria-label={`Show ${label.toLowerCase()}`} aria-expanded={open} onClick={show}>
          <SidebarSimple size={20} aria-hidden />
        </button>
        <button type="button" className="icon-btn" aria-label="Show uploads" onClick={() => reveal("uploads")}>
          <UploadSimple size={20} aria-hidden />
        </button>
        <button type="button" className="icon-btn" aria-label="Show chat" onClick={() => reveal("chat")}>
          <ChatCircleText size={20} aria-hidden />
        </button>
      </div>
      {(open || leaving) && (
        <div
          className={`panel-scrim${open ? (animated ? "" : " is-instant") : " is-leaving"}`}
          aria-hidden
          onClick={() => hide(true)}
        />
      )}
      {(open || leaving) && (
        <aside
          className={`left-panel left-panel--overlay${open ? (animated ? " is-animated" : "") : " is-leaving"}`}
          aria-label={label}
          aria-hidden={open ? undefined : true}
          inert={!open || undefined}
        >
          <button type="button" className="btn btn--ghost btn--sm left-panel__close" autoFocus onClick={(e) => hide(e.detail > 0)}>
            <SidebarSimple size={16} aria-hidden /> Hide panel
          </button>
          {children}
        </aside>
      )}
    </>
  );
}
