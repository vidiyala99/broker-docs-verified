import { useEffect, useId, useRef, useState } from "react";
import { UserCircle } from "@phosphor-icons/react";
import "../queue/queue.css";

/**
 * Work queue W4: the run header's Client control, next to Line. A quiet button ("Add client" when empty) that becomes
 * an input in place. Enter or leaving the field saves, Esc cancels; an empty value clears the name. Saving is the
 * caller's (optimistic PATCH with rollback, like the Line control).
 */
export function RunClient({ value, onSave }: { value: string | null; onSave: (name: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(value ?? "");
  const input = useRef<HTMLInputElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const handled = useRef(false);
  const labelId = useId();
  const hintId = useId();

  useEffect(() => {
    handled.current = false;
    if (editing) {
      input.current?.focus();
      input.current?.select();
    }
  }, [editing]);

  const finish = (save: boolean) => {
    setEditing(false);
    const next = text.replace(/\s+/g, " ").trim();
    if (save && next !== (value ?? "")) onSave(next);
    window.setTimeout(() => button.current?.focus(), 0);
  };

  return (
    <div className="run-client">
      <span id={labelId} className="run-client__label">
        Client
      </span>
      {editing ? (
        <>
          <input
            ref={input}
            className="run-client__input"
            aria-labelledby={labelId}
            aria-describedby={hintId}
            value={text}
            maxLength={120}
            autoComplete="organization"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handled.current = true;
                finish(true);
              } else if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                handled.current = true;
                finish(false);
              }
            }}
            onBlur={() => {
              if (handled.current) {
                handled.current = false;
                return;
              }
              finish(true);
            }}
          />
          <span id={hintId} className="sr-only">
            Enter saves, Escape cancels. Leave it empty to clear the name.
          </span>
        </>
      ) : (
        <button
          ref={button}
          type="button"
          className={`run-client__value${value ? "" : " is-empty"}`}
          aria-label={value ? `Client: ${value}. Edit` : "Add client"}
          title={value ?? undefined}
          onClick={() => {
            setText(value ?? "");
            setEditing(true);
          }}
        >
          <UserCircle size={16} aria-hidden />
          <span>{value ?? "Add client"}</span>
        </button>
      )}
    </div>
  );
}
