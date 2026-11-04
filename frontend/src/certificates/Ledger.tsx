import { useEffect, useId, useMemo, useRef, useState } from "react";
import { CaretDown, ClockCounterClockwise, FilePlus } from "@phosphor-icons/react";
import { ApiError } from "../api/client";
import type { CertificateView, ClaimItem, ClaimStatus } from "../api/types";
import { Segments } from "../report/Report";
import type { OpenSource } from "../report/Chip";
import { Collapse } from "../ui/motion";
import { pointerAction } from "../ui/input";
import { handled, needsYou, removeLabel, STATE_ORDER, STATE_WORD, stateOf, type ClaimState } from "./certUtil";
import { StateGlyph } from "./StateGlyph";

export type Decide = (claim: ClaimItem, status: ClaimStatus, note?: string | null) => Promise<void>;

/**
 * The check ledger (direction A, owner-approved 2026-11-03): one row per claim, ordered by need. To resolve, Handled
 * by you (with the reason and a way back), Supported by the documents (folded). Read-only on an issued version, where
 * it is the record of the issue.
 */
export function Ledger({
  view,
  readOnly,
  onDecide,
  onAddDocument,
  onPoint,
  onChip,
  rowNotes,
}: {
  view: CertificateView;
  readOnly: boolean;
  onDecide: Decide;
  onAddDocument: () => void;
  onPoint: (claim: ClaimItem | null, via: "pointer" | "keyboard") => void;
  onChip: OpenSource;
  /** A message per claim key after a refresh (D15 item_changed). */
  rowNotes: Record<string, string>;
}) {
  const [showSupported, setShowSupported] = useState<{ open: boolean; animate: boolean }>({ open: false, animate: false });
  const supId = useId();
  // A row that changed group after a pointer decision drops into its new place (list enter, ui/motion.css); first
  // load, polling and keyboard decisions stay still (DESIGN.md Motion).
  const lastStates = useRef<Map<string, string> | null>(null);
  const moved = new Set<string>();
  if (lastStates.current) for (const c of view.claims) if (lastStates.current.get(c.key) !== stateOf(c)) moved.add(c.key);
  useEffect(() => {
    lastStates.current = new Map(view.claims.map((c) => [c.key, stateOf(c)]));
  });
  const groups = useMemo(() => {
    const list = [...view.claims].sort((a, b) => STATE_ORDER.indexOf(stateOf(a)) - STATE_ORDER.indexOf(stateOf(b)));
    return {
      todo: list.filter((c) => needsYou(stateOf(c))),
      handled: list.filter((c) => handled(stateOf(c))),
      supported: list.filter((c) => stateOf(c) === "supported"),
    };
  }, [view.claims]);
  const row = (c: ClaimItem) => (
    <ClaimRow
      key={c.key}
      claim={c}
      view={view}
      readOnly={readOnly}
      onDecide={onDecide}
      onAddDocument={onAddDocument}
      onPoint={onPoint}
      onChip={onChip}
      note={rowNotes[c.key]}
      entering={moved.has(c.key)}
    />
  );
  return (
    <aside className="ledger" aria-labelledby="ledger-title">
      <h2 id="ledger-title" className="ledger__title">
        {readOnly ? "Record" : "Checks"}
      </h2>
      <p className="ledger__sum">
        {readOnly
          ? `${view.claims.length} claims, checked before issue. Reasons stay on the record and are not printed.`
          : groups.todo.length
            ? `${view.claims.length} claims on this certificate. ${groups.todo.length} ${groups.todo.length === 1 ? "needs" : "need"} you.`
            : `${view.claims.length} claims on this certificate. Every check is handled.`}
      </p>
      {groups.todo.length > 0 && (
        <section aria-labelledby="ledger-todo">
          <h3 id="ledger-todo" className="ledger__group">
            To resolve <span className="count">{groups.todo.length}</span>
          </h3>
          <ol className="ledger__list">{groups.todo.map(row)}</ol>
        </section>
      )}
      {groups.handled.length > 0 && (
        <section aria-labelledby="ledger-handled">
          <h3 id="ledger-handled" className="ledger__group">
            {readOnly ? "Decided before issue" : "Handled by you"} <span className="count">{groups.handled.length}</span>
          </h3>
          <ol className="ledger__list">{groups.handled.map(row)}</ol>
        </section>
      )}
      {groups.supported.length > 0 && (
        <section aria-labelledby={`${supId}-h`}>
          <h3 id={`${supId}-h`} className="ledger__group">
            <button
              type="button"
              className="ledger__fold"
              aria-expanded={showSupported.open}
              aria-controls={`${supId}-list`}
              onClick={(e) => setShowSupported((s) => ({ open: !s.open, animate: e.detail > 0 }))}
            >
              <CaretDown size={14} weight="bold" aria-hidden className={showSupported.open ? "is-open" : ""} />
              Supported by the documents <span className="count">{groups.supported.length}</span>
            </button>
          </h3>
          <Collapse open={showSupported.open} animate={showSupported.animate} id={`${supId}-list`}>
            <ol className="ledger__list">{groups.supported.map(row)}</ol>
          </Collapse>
        </section>
      )}
    </aside>
  );
}

function noteLabel(c: ClaimItem, state: ClaimState): string {
  if (state === "signoff") return c.kind === "description" ? "Confirm the description as typed" : "Why is the changed value right?";
  if (c.condition) return "Why does the condition hold for this holder?";
  if (c.kind === "insured") return "Why is it the same insured?";
  return "Reason for the override";
}

function ClaimRow({
  claim: c,
  view,
  readOnly,
  onDecide,
  onAddDocument,
  onPoint,
  onChip,
  note: rowNote,
  entering,
}: {
  entering: boolean;
  claim: ClaimItem;
  view: CertificateView;
  readOnly: boolean;
  onDecide: Decide;
  onAddDocument: () => void;
  onPoint: (claim: ClaimItem | null, via: "pointer" | "keyboard") => void;
  onChip: OpenSource;
  note?: string;
}) {
  const state = stateOf(c);
  const [enter] = useState(() => entering && pointerAction());
  const [mode, setMode] = useState<{ open: boolean; animate: boolean }>({ open: false, animate: false });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (mode.open) area.current?.focus();
  }, [mode.open]);
  // A decision that saved closes the editor; a refreshed row (item_changed) keeps the typed reason.
  useEffect(() => {
    if (!needsYou(state)) setMode({ open: false, animate: false });
  }, [state]);

  const run = async (status: ClaimStatus, n?: string | null) => {
    setBusy(true);
    setError(null);
    try {
      await onDecide(c, status, n);
      if (status === "resolved") setText("");
    } catch (e) {
      if (e instanceof ApiError && e.code === "note_required") setError("Type the reason first. It is saved on the issued record.");
      else if (!(e instanceof ApiError && e.code === "item_changed")) setError(e instanceof ApiError ? e.message : "Couldn't save. Retry.");
    } finally {
      setBusy(false);
    }
  };
  const signoff = state === "signoff";
  return (
    <li
      className={`ledger__item ledger__item--${state}${enter ? " motion-enter" : ""}`}
      data-claim={c.key}
      onMouseEnter={() => onPoint(c, "pointer")}
      onMouseLeave={() => onPoint(null, "pointer")}
      onFocus={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) onPoint(c, "keyboard");
      }}
    >
      <div className="claim__head">
        <StateGlyph state={state} />
        <span className={`status-word cword cword--${state}`}>{STATE_WORD[state]}</span>
        {c.changed_since_resolved && (
          <span className="stale-tag" title="The evidence changed after you decided. Check it again.">
            <ClockCounterClockwise size={14} aria-hidden /> Changed since you decided
          </span>
        )}
      </div>
      <h4 className="claim__title">{c.title}</h4>
      <p className="claim__reason">
        <Segments segments={c.prose} chips={view.chips} onOpen={onChip} />
      </p>
      {c.condition && (
        <p className="claim__condition">
          <span className="claim__k">Condition</span> <q>{c.condition}</q>
        </p>
      )}
      {c.kind === "insured" && needsYou(state) && (
        <p className="hint claim__hint">To fix it, remove the policy whose insured differs, or override with a reason.</p>
      )}
      {rowNote && (
        <p className="claim__refreshed" role="status">
          {rowNote}
        </p>
      )}
      {(state === "overridden" || state === "confirmed") && c.note && (
        <p className="claim__note">
          <span className="claim__k">{state === "confirmed" ? "Note" : "Reason"}</span> {c.note}
        </p>
      )}
      {!readOnly && needsYou(state) && (
        <>
          <div className="cact__row" hidden={mode.open}>
            {!signoff && c.kind !== "edited" && (
              <button type="button" className="btn btn--secondary btn--sm" onClick={onAddDocument} disabled={busy}>
                <FilePlus size={16} aria-hidden /> Add document
              </button>
            )}
            <span className="cact__quiet">
              <button
                ref={opener}
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={busy}
                aria-expanded={mode.open}
                aria-controls={`${id}-note`}
                onClick={(e) => setMode({ open: true, animate: e.detail > 0 })}
              >
                {signoff ? "Confirm..." : "Override..."}
              </button>
              {c.removable && (
                <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void run("removed")}>
                  {removeLabel(c)}
                </button>
              )}
            </span>
          </div>
          <Collapse open={mode.open} animate={mode.animate} id={`${id}-note`}>
            <div className="cact cact--note">
              <label htmlFor={`${id}-area`} className="field__label">
                {noteLabel(c, state)}
              </label>
              <p className="hint" id={`${id}-hint`}>
                Saved on the issued record. Not printed.
              </p>
              <textarea
                id={`${id}-area`}
                ref={area}
                className="input textarea"
                rows={3}
                maxLength={1000}
                aria-describedby={`${id}-hint${error ? ` ${id}-err` : ""}`}
                aria-invalid={error ? true : undefined}
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  setError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    e.stopPropagation();
                    setMode({ open: false, animate: false });
                    window.setTimeout(() => opener.current?.focus(), 0);
                  }
                }}
              />
              {error && (
                <p id={`${id}-err`} className="inline-error" role="alert">
                  {error}
                </p>
              )}
              <div className="cact__row cact__row--end">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={busy}
                  onClick={() => {
                    setMode({ open: false, animate: false });
                    window.setTimeout(() => opener.current?.focus(), 0);
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  disabled={busy}
                  onClick={() => {
                    if (!text.trim()) return setError("Type the reason first. It is saved on the issued record.");
                    void run("resolved", text.trim());
                  }}
                >
                  {busy ? "Saving..." : signoff ? "Confirm" : "Override"}
                </button>
              </div>
            </div>
          </Collapse>
        </>
      )}
      {!readOnly && handled(state) && (
        <div className="cact__row">
          <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void run("open", null)}>
            {state === "removed" ? "Put back on the certificate" : state === "confirmed" ? "Undo confirm" : "Undo override"}
          </button>
        </div>
      )}
      {error && !mode.open && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}
