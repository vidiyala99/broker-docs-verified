import { useEffect, useState, type ReactNode } from "react";
import { ChatCircleText, SidebarSimple, UploadSimple } from "@phosphor-icons/react";

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
export function LeftPanel({ viewport, label, children }: { viewport: Viewport; label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const collapsed = viewport !== "wide";

  useEffect(() => {
    if (!collapsed) setOpen(false);
  }, [collapsed]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !(e.target as HTMLElement).closest("input, textarea")) setOpen(false);
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
        <button type="button" className="icon-btn" aria-label={`Show ${label.toLowerCase()}`} aria-expanded={open} onClick={() => setOpen(true)}>
          <SidebarSimple size={20} aria-hidden />
        </button>
        <button type="button" className="icon-btn" aria-label="Show uploads" onClick={() => setOpen(true)}>
          <UploadSimple size={20} aria-hidden />
        </button>
        <button type="button" className="icon-btn" aria-label="Show chat" onClick={() => setOpen(true)}>
          <ChatCircleText size={20} aria-hidden />
        </button>
      </div>
      {open && (
        <aside className="left-panel left-panel--overlay" aria-label={label}>
          <button type="button" className="btn btn--ghost btn--sm left-panel__close" onClick={() => setOpen(false)}>
            <SidebarSimple size={16} aria-hidden /> Hide panel
          </button>
          {children}
        </aside>
      )}
    </>
  );
}
