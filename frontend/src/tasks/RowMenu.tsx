import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { DotsThree } from "@phosphor-icons/react";
import { keyboardAction } from "../ui/input";
import { tokenMs } from "../ui/motion";

export interface MenuItem {
  label: string;
  icon: ReactNode;
  danger?: boolean;
  run: () => void;
}

/**
 * Row overflow menu (spec 2.2: Delete lives here). Menu button pattern: Enter / Space / ArrowDown open on the first
 * item, ArrowUp on the last; arrows, Home and End move; Esc or Tab closes; Esc returns focus to the trigger; a click
 * outside closes. Pointer opens scale in from the trigger's corner (150ms); keyboard opens are instant (DESIGN.md).
 */
export function RowMenu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState<{ focus: number; animate: boolean } | null>(null);
  // Pointer closes leave the list mounted for its 120ms exit (opacity + scale 0.97 toward the trigger); it is inert and
  // hidden from assistive tech meanwhile. Keyboard closes (Esc, Tab, Enter on an item) are instant.
  const [leaving, setLeaving] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();

  const close = (viaPointer: boolean) => {
    setOpen(null);
    setLeaving(viaPointer && !keyboardAction());
  };

  useEffect(() => {
    if (!leaving) return;
    const t = window.setTimeout(() => setLeaving(false), tokenMs("--dur-press", 120));
    return () => window.clearTimeout(t);
  }, [leaving]);

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]")[open.focus]?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!menu.current?.contains(t) && !trigger.current?.contains(t)) close(true);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const move = (i: number) => {
    const n = items.length;
    menu.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]")[(i + n) % n]?.focus();
  };
  const current = () => Array.from(menu.current?.querySelectorAll("[role=menuitem]") ?? []).indexOf(document.activeElement as Element);

  return (
    <div className="row-menu">
      <button
        ref={trigger}
        type="button"
        className="icon-btn"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open != null}
        aria-controls={open ? id : undefined}
        onClick={(e) => {
          if (open) close(e.detail > 0);
          else {
            setLeaving(false);
            setOpen({ focus: 0, animate: e.detail > 0 });
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            setLeaving(false);
            setOpen({ focus: e.key === "ArrowUp" ? items.length - 1 : 0, animate: false });
          }
        }}
      >
        <DotsThree size={20} weight="bold" aria-hidden />
      </button>
      {(open || leaving) && (
        <div
          ref={menu}
          id={open ? id : undefined}
          role="menu"
          aria-label={label}
          aria-hidden={open ? undefined : true}
          inert={!open || undefined}
          className={`row-menu__list${open ? (open.animate ? " row-menu__list--animate" : "") : " row-menu__list--leaving"}`}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              move(current() + 1);
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              move(current() - 1);
            } else if (e.key === "Home") {
              e.preventDefault();
              move(0);
            } else if (e.key === "End") {
              e.preventDefault();
              move(items.length - 1);
            } else if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              setOpen(null);
              trigger.current?.focus();
            } else if (e.key === "Tab") {
              setOpen(null);
            }
          }}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              tabIndex={-1}
              className={`row-menu__item${item.danger ? " row-menu__item--danger" : ""}`}
              onClick={(e) => {
                close(e.detail > 0);
                item.run();
                // The leaving list is inert, so the clicked item loses focus. If the action did not move focus itself
                // (a dialog, a navigation, a row that left), return it to the trigger instead of the page body.
                window.setTimeout(() => {
                  const a = document.activeElement;
                  if ((a === document.body || a === null || menu.current?.contains(a)) && trigger.current?.isConnected) trigger.current.focus();
                }, 0);
              }}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
