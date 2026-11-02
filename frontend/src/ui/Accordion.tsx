import { useId, useState, type ReactNode } from "react";
import { CaretDown } from "@phosphor-icons/react";

/**
 * Disclosure section. `summary` is shown inside the toggle only while closed, so the button's
 * name carries what is folded away ("Upload 3, 3 documents, all read"). The body fades in on a
 * pointer open; keyboard opens are instant (DESIGN.md: no keyboard-triggered animation).
 */
export function Accordion({
  title,
  count,
  summary,
  defaultOpen = false,
  open: controlled,
  onToggle,
  extra,
  className,
  children,
}: {
  title: string;
  count?: number;
  summary?: ReactNode;
  defaultOpen?: boolean;
  open?: boolean;
  onToggle?: (open: boolean) => void;
  extra?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const [own, setOwn] = useState(defaultOpen);
  const [viaPointer, setViaPointer] = useState(false);
  const open = controlled ?? own;
  const id = useId();
  return (
    <div className={`accordion${open ? " is-open" : ""}${className ? ` ${className}` : ""}`}>
      <div className="accordion__head">
        <button
          type="button"
          className="accordion__toggle"
          aria-expanded={open}
          aria-controls={id}
          onClick={(e) => {
            // detail is 0 for Enter/Space activation.
            setViaPointer(e.detail > 0);
            setOwn(!open);
            onToggle?.(!open);
          }}
        >
          <span className="accordion__title">{title}</span>
          {/* Folded with a summary, the summary carries the count ("3 documents, ..."). */}
          {count != null && (open || summary == null) && <span className="accordion__count">{count}</span>}
          {summary != null && !open && <span className="accordion__summary">{summary}</span>}
          <CaretDown size={16} aria-hidden className="accordion__caret" />
        </button>
        {extra}
      </div>
      <div id={id} className={`accordion__body${viaPointer ? " fade-in" : ""}`} hidden={!open}>
        {children}
      </div>
    </div>
  );
}
