import { useId, useState, type ReactNode } from "react";
import { CaretDown } from "@phosphor-icons/react";

export function Accordion({
  title,
  count,
  defaultOpen = false,
  open: controlled,
  onToggle,
  extra,
  children,
}: {
  title: string;
  count?: number;
  defaultOpen?: boolean;
  open?: boolean;
  onToggle?: (open: boolean) => void;
  extra?: ReactNode;
  children: ReactNode;
}) {
  const [own, setOwn] = useState(defaultOpen);
  const open = controlled ?? own;
  const id = useId();
  return (
    <div className={`accordion${open ? " is-open" : ""}`}>
      <div className="accordion__head">
        <button
          type="button"
          className="accordion__toggle"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => {
            setOwn(!open);
            onToggle?.(!open);
          }}
        >
          <span className="accordion__title">{title}</span>
          {count != null && <span className="accordion__count">{count}</span>}
          <CaretDown size={16} aria-hidden className="accordion__caret" />
        </button>
        {extra}
      </div>
      <div id={id} className="accordion__body" hidden={!open}>
        {children}
      </div>
    </div>
  );
}
