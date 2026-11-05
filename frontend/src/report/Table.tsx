import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowsClockwise, ArrowsSplit, PencilSimple, Warning, WarningDiamond } from "@phosphor-icons/react";
import type { Block, Cell, Chip as ChipData, Column, DocumentKind, EvidenceState, SourceFormat } from "../api/types";
import { Chip, ChipList, hueOf, type OpenSource } from "./Chip";
import { Tip } from "../ui/Tip";
import "../launch/compare.css";

type TableBlock = Extract<Block, { type: "table" }>;

export interface CellRef {
  block_id: string;
  row_key: string;
  column_key: string;
}

export interface TableActions {
  chips: Record<string, ChipData>;
  onOpen: OpenSource;
  onRetryRun: (() => void) | null;
  /** Resolves on save; rejects with a message to keep the input open (DG12). */
  onSave: (ref: CellRef, value: string | null) => Promise<void>;
  editable: boolean;
  filter: EvidenceState | "edited" | null;
  /** Slice 6, Compare Quotes runs: each document's uploaded format (document_id -> format) for the column labels. */
  compare?: { formats: Record<number, SourceFormat | undefined> } | null;
}

/** Spec 5: the muted line under a Compare column's carrier, from the document's format and kind. */
export function columnKindLabel(format: SourceFormat | undefined, kind: DocumentKind | null | undefined): string {
  if (format === "xlsx") return "Spreadsheet";
  if (format === "docx") {
    return kind === "quote" ? "Word quote" : kind === "proposal" ? "Word proposal" : kind === "summary" ? "Word summary" : "Word document";
  }
  return kind === "quote" ? "PDF quote" : kind === "proposal" ? "PDF proposal" : kind === "summary" ? "PDF summary" : "PDF";
}

/** The id of a table cell's element, so a Key difference "edited by you" chip can take the reader to it. */
export function cellDomId(row: string, column: string): string {
  return `cell-${row}-${column}`.replace(/[^A-Za-z0-9_-]/g, "_");
}

function kindOf(col: Column, actions: TableActions): string | null {
  if (!actions.compare) return null;
  return columnKindLabel(col.document_id != null ? actions.compare.formats[col.document_id] : undefined, col.document_kind);
}

/** Spec 5: the marks in words, so they never rest on colour or outline alone. */
function markText(cell: Cell | undefined): string {
  if (cell?.mark === "worse") return `, worse than other quotes${cell.mark_reason ? `: ${cell.mark_reason}` : ""}`;
  if (cell?.mark === "best") return ", lowest premium";
  return "";
}

/** The value with its mark (slice 6): worse = outlined + diamond glyph whose tooltip is the reason; best = success
 *  colour + the LOWEST status word. The cell's accessible name carries both in words (markText). */
function MarkedValue({ cell }: { cell: Cell }) {
  const value = cell.value ?? "Not specified";
  if (cell.mark === "worse") {
    return (
      <>
        <span className="mark-worse" data-mark="worse">
          {value}
        </span>
        <Tip text={cell.mark_reason ? `Worse than the other quotes. ${cell.mark_reason}` : "Worse than the other quotes"}>
          <span className="mark-glyph" tabIndex={-1}>
            <WarningDiamond size={14} weight="fill" aria-hidden />
          </span>
        </Tip>
      </>
    );
  }
  if (cell.mark === "best") {
    return (
      <>
        <span className="mark-best" data-mark="best">
          {value}
        </span>
        <span className="mark-best__word" aria-hidden>
          Lowest
        </span>
      </>
    );
  }
  return <>{value}</>;
}

export function ReportTable({ block, actions }: { block: TableBlock; actions: TableActions }) {
  const titleId = useId();
  return (
    <figure className="table-block" aria-labelledby={block.title ? titleId : undefined}>
      {block.title && (
        <figcaption id={titleId} className="table-block__title">
          {block.title}
        </figcaption>
      )}
      <div className="table-scroll" tabIndex={-1}>
        <table className="report-table">
          <thead>
            <tr>
              <th scope="col" className="report-table__corner">
                <span className="sr-only">Field</span>
              </th>
              {block.columns.map((col) => (
                <th key={col.key} scope="col" className={`col-head hue-${hueOf(col.key)}`}>
                  <span className="col-head__name">
                    <span className={`doc-letter hue-${hueOf(col.key)}`} aria-hidden>
                      {col.key}
                    </span>
                    <span className="sr-only">Document {col.key}: </span>
                    {col.label}
                  </span>
                  {kindOf(col, actions) && <span className="col-head__kind">{kindOf(col, actions)}</span>}
                  {col.warning && (
                    <ColumnWarning text={col.warning} onRetry={col.warning.startsWith("Extraction failed") ? actions.onRetryRun : null} />
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row) => (
              <tr key={row.key}>
                <th scope="row" className="row-head">
                  {row.label}
                </th>
                {block.columns.map((col) => {
                  const cell = row.cells[col.key];
                  return (
                    <EditableCell
                      key={col.key}
                      cell={cell}
                      cellRef={{ block_id: block.id, row_key: row.key, column_key: col.key }}
                      domId={actions.compare ? cellDomId(row.key, col.key) : undefined}
                      label={`${row.label}, ${col.key} ${col.label}`}
                      actions={actions}
                    />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

function ColumnWarning({ text, onRetry }: { text: string; onRetry: (() => void) | null }) {
  const failed = Boolean(onRetry) || text.startsWith("Extraction failed");
  return (
    <span className="col-warning" role={failed ? "alert" : undefined}>
      {failed ? <ArrowsClockwise size={14} aria-hidden /> : <Warning size={14} aria-hidden />}
      <span className="col-warning__text">{text}</span>
      {onRetry && (
        <button type="button" className="link-btn" onClick={onRetry}>
          Retry
        </button>
      )}
    </span>
  );
}

function stateOf(cell: Cell | undefined): EvidenceState | "edited" {
  if (!cell) return "absent";
  if (cell.edited) return "edited";
  return cell.state ?? "absent";
}

function EditableCell({
  cell,
  cellRef,
  domId,
  label,
  actions,
}: {
  cell: Cell | undefined;
  cellRef: CellRef;
  domId?: string;
  label: string;
  actions: TableActions;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [phase, setPhase] = useState<"idle" | "saving" | "error">("idle");
  const input = useRef<HTMLTextAreaElement>(null);
  const td = useRef<HTMLTableCellElement>(null);
  const errId = useId();
  const state = stateOf(cell);
  const canEdit = actions.editable && state !== "failed";

  const refocus = useRef(false);
  useEffect(() => {
    if (editing) {
      input.current?.focus();
      input.current?.select();
      if (input.current) input.current.scrollTop = 0;
    } else if (refocus.current) {
      refocus.current = false;
      td.current?.focus();
    }
  }, [editing]);

  // Grow the editor to fit its text so long values (territory, policy form) stay readable.
  useLayoutEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft, editing]);

  const begin = () => {
    if (!canEdit) return;
    const v = cell?.value ?? "";
    const placeholderValues = ["Not specified", "Couldn't verify", "Conflicting values", "Extraction failed"];
    setDraft(cell?.edited || !placeholderValues.includes(v) ? v : "");
    setPhase("idle");
    setEditing(true);
  };
  const cancel = () => {
    refocus.current = true;
    setEditing(false);
    setPhase("idle");
  };
  const save = async () => {
    setPhase("saving");
    const value = draft.trim() === "" ? null : draft.trim();
    try {
      await actions.onSave(cellRef, value);
      refocus.current = true;
      setEditing(false);
      setPhase("idle");
    } catch {
      setPhase("error");
      input.current?.focus();
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTableCellElement>) => {
    if (!editing && e.key === "Enter" && e.target === td.current) {
      e.preventDefault();
      begin();
    }
  };

  const matches = actions.filter != null && (actions.filter === state || (actions.filter === "edited" && cell?.edited));

  return (
    <td
      ref={td}
      id={domId}
      className={`cell cell--${state}${editing ? " is-editing" : ""}${matches ? " is-match" : ""}`}
      data-state={state}
      tabIndex={canEdit && !editing ? 0 : undefined}
      aria-label={editing ? undefined : `${label}: ${describe(cell, state)}${markText(cell)}${canEdit ? ". Press Enter to edit" : ""}`}
      onDoubleClick={(e) => !editing && !(e.target as HTMLElement).closest("button") && begin()}
      onKeyDown={onKey}
    >
      {editing ? (
        <div className="cell-edit">
          <textarea
            ref={input}
            rows={1}
            className="cell-edit__input"
            value={draft}
            aria-label={`Edit ${label}`}
            aria-describedby={phase === "error" ? errId : undefined}
            aria-invalid={phase === "error" || undefined}
            disabled={phase === "saving"}
            maxLength={2000}
            onChange={(e) => setDraft(e.target.value.replace(/\s*[\r\n]+\s*/g, " "))}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void save();
              } else if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                cancel();
              }
            }}
          />
          {phase === "saving" && <span className="cell-edit__status">Saving...</span>}
          {phase === "error" && (
            <span className="cell-edit__error" id={errId}>
              Couldn't save.{" "}
              <button type="button" className="link-btn link-btn--danger" onClick={() => void save()}>
                Retry
              </button>
            </span>
          )}
        </div>
      ) : (
        <CellBody cell={cell} state={state} actions={actions} />
      )}
    </td>
  );
}

function describe(cell: Cell | undefined, state: EvidenceState | "edited"): string {
  const v = cell?.value ?? "Not specified";
  switch (state) {
    case "scan_read":
      return `${v}, read from scan, unverified`;
    case "not_found":
      return "Couldn't verify";
    case "conflicting":
      return `Conflicting values: ${(cell?.conflicts ?? []).map((c) => `${c.value} on page ${c.page_no}`).join(", ")}`;
    case "failed":
      return "Extraction failed";
    case "edited":
      return `${v}, edited by you${cell?.original_value ? `, original ${cell.original_value}` : ""}`;
    default:
      return v;
  }
}

function CellBody({ cell, state, actions }: { cell: Cell | undefined; state: EvidenceState | "edited"; actions: TableActions }) {
  const [showOriginal, setShowOriginal] = useState(false);
  const [showNotice, setShowNotice] = useState(false);
  if (!cell || state === "absent") return <span className="cell__muted">Not specified</span>;

  if (state === "failed") {
    return (
      <span className="evidence evidence--failed">
        <ArrowsClockwise size={14} aria-hidden />
        <span>Extraction failed</span>
        {actions.onRetryRun && (
          <button type="button" className="link-btn" onClick={actions.onRetryRun}>
            Retry
          </button>
        )}
      </span>
    );
  }

  if (state === "not_found") {
    return (
      <Tip text="A value was read, but it was not found on the page it cited, so it is not shown.">
        <span className="evidence evidence--unverified" tabIndex={-1}>
          <Warning size={14} aria-hidden />
          <span>Couldn't verify</span>
        </span>
      </Tip>
    );
  }

  if (state === "conflicting") {
    const conflicts = cell.conflicts ?? [];
    return (
      <div className="evidence-conflict">
        <span className="evidence evidence--conflict">
          <ArrowsSplit size={14} aria-hidden />
          <span>Conflicting values</span>
        </span>
        <ul className="conflict-list">
          {conflicts.map((c, i) => {
            const chip = c.passage_id != null ? actions.chips[String(c.passage_id)] : undefined;
            return (
              <li key={i}>
                <span className="conflict-list__value">{c.value ?? "No value"}</span>
                {chip && <Chip chip={chip} onOpen={actions.onOpen} />}
              </li>
            );
          })}
        </ul>
        {cell.notice && (
          <p className="cell-notice">
            {/No value is shown as verified[^.]*\./.exec(cell.notice.message)?.[0] ?? cell.notice.message}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="cell__content">
      <span className="cell__value">
        <MarkedValue cell={cell} />
        {state === "edited" ? (
          <Tip text={cell.original_value ? `Edited by you. Original: ${cell.original_value}` : "Edited by you"}>
            <PencilSimple size={14} className="edited-glyph" aria-hidden />
          </Tip>
        ) : (
          <ChipList ids={cell.passage_ids} chips={actions.chips} onOpen={actions.onOpen} />
        )}
      </span>
      {state === "scan_read" && <span className="sr-only">Read from scan, unverified</span>}
      {state === "edited" && cell.original_passage_ids.length > 0 && (
        <>
          <button
            type="button"
            className="link-btn link-btn--small"
            aria-expanded={showOriginal}
            onClick={() => setShowOriginal((v) => !v)}
          >
            Original source
          </button>
          {showOriginal && (
            <span className="original-source">
              <span>{cell.original_value ?? "No value"}</span>
              <ChipList ids={cell.original_passage_ids} chips={actions.chips} onOpen={actions.onOpen} />
            </span>
          )}
        </>
      )}
      {cell.notice && (
        <>
          <button
            type="button"
            className="cell-notice-toggle"
            aria-expanded={showNotice}
            onClick={() => setShowNotice((v) => !v)}
          >
            <Warning size={14} aria-hidden />
            {cell.notice.kind === "conflicting_values" ? "Conflicting values" : "Note"}
          </button>
          {showNotice && <p className="cell-notice">{cell.notice.message}</p>}
        </>
      )}
    </div>
  );
}
