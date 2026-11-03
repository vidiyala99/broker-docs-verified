import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { DotsThree } from "@phosphor-icons/react";

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
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]")[open.focus]?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!menu.current?.contains(t) && !trigger.current?.contains(t)) setOpen(null);
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
        onClick={(e) => setOpen((o) => (o ? null : { focus: 0, animate: e.detail > 0 }))}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            setOpen({ focus: e.key === "ArrowUp" ? items.length - 1 : 0, animate: false });
          }
        }}
      >
        <DotsThree size={20} weight="bold" aria-hidden />
      </button>
      {open && (
        <div
          ref={menu}
          id={id}
          role="menu"
          aria-label={label}
          className={`row-menu__list${open.animate ? " row-menu__list--animate" : ""}`}
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
              onClick={() => {
                setOpen(null);
                item.run();
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
