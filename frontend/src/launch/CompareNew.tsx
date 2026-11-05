import { useId, useState } from "react";
import { Trash, WarningCircle } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import type { Line, Workspace } from "../api/types";
import { LineChoice } from "../personal/LineChoice";
import { ClientField } from "../queue/ClientField";
import { hueOf } from "../report/Chip";
import { BookTab } from "../run/BookTab";
import { WorkflowMark } from "../voice/marks";
import { acceptOf, compareUploads, DropZone, FORMAT_WORD, FormatGlyph, formatBytes, formatOfName, useUploads, type FileRow } from "./Upload";
import "../wizard/wizard.css";
import "./compare.css";

/** Spec 4.1: optional, stored on the run, used only by the compose prompt's delimited brief channel. */
export const CONTEXT_MAX = 1000;
/** R4: the upload page asks for two quotes; the API and the slot still take one. */
const MIN_QUOTES = 2;

/**
 * `#/compare/new` (slice 6, spec 4.1 with R4): the new comparison. Quotes in PDF, Word or Excel; each file shows its
 * format, its column letter once it is ready, and the server's own refusal when it is not accepted. Compare stays
 * focusable with `aria-disabled` and says why until two quotes are ready. The Line choice is held by App so the crumbs
 * and the title's book tab follow it (line switch pattern). Decisions in COMPARE-MATCH-BRIEF.md.
 */
export function CompareNew({
  workspace,
  entryLine,
  line,
  onLineChange,
}: {
  workspace: Workspace;
  entryLine: Line;
  line: Line;
  onLineChange: (line: Line) => void;
}) {
  const uploads = useUploads(workspace.limits, compareUploads);
  const [client, setClient] = useState("");
  const [context, setContext] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [nudge, setNudge] = useState(false);
  const gateId = useId();
  const contextId = useId();
  const countId = useId();
  const ready = uploads.rows.filter((r) => r.status === "ready" && r.doc);
  const busy = uploads.rows.some((r) => r.status === "uploading");
  const gate =
    ready.length >= MIN_QUOTES
      ? busy
        ? "Wait for the uploads to finish."
        : null
      : ready.length === 1
        ? "Add one more quote to compare."
        : "Add two quotes to compare.";
  const blocked = gate != null || starting;

  const start = async () => {
    if (blocked) {
      setNudge(true);
      return;
    }
    setStarting(true);
    setStartError(null);
    try {
      const res = await api.startRun(ready.map((r) => r.doc!.id), line, client, context);
      uploads.clear();
      window.location.hash = `#/runs/${res.run.id}`;
    } catch (e) {
      setStartError(e instanceof ApiError ? e.message : "Couldn't start the comparison. Retry.");
    } finally {
      setStarting(false);
    }
  };

  const limits = workspace.limits;
  return (
    <div className="workspace">
      <main className="pane wizard compare-new" id="main" tabIndex={-1} aria-labelledby="compare-new-title">
        <header className="wizard__head compare-new__head">
          <div className="wizard__titlebar">
            <h1 id="compare-new-title" className="wizard__title">
              <WorkflowMark slug="compare-quotes" size={34} />
              New comparison
            </h1>
            <BookTab line={line} />
          </div>
          <div className="compare-new__go">
            <div className="compare-new__go-row">
              <a href={entryLine === "personal" ? "#/personal-lines" : "#/"} className="btn btn--ghost">
                Cancel
              </a>
              <button
                type="button"
                className="btn btn--primary"
                aria-disabled={blocked || undefined}
                aria-describedby={gate ? gateId : undefined}
                onClick={() => void start()}
              >
                {starting ? (
                  <>
                    <span className="spinner spinner--sm spinner--on-primary" aria-hidden /> Starting...
                  </>
                ) : ready.length >= MIN_QUOTES ? (
                  `Compare ${ready.length} quotes`
                ) : (
                  "Compare quotes"
                )}
              </button>
            </div>
            {/* Always one line, so the head never shifts as files arrive: the reason while blocked, else the columns. */}
            <p id={gateId} className={`compare-new__gate${gate && nudge ? " is-nudged" : ""}`} role={gate && nudge ? "alert" : undefined}>
              {gate ?? `Columns A to ${String.fromCharCode(64 + ready.length)}, in the order added.`}
            </p>
          </div>
        </header>

        <div className="wizard__body">
          <div className="compare-new__grid">
            <section className="compare-new__files" aria-labelledby="quotes-h">
              <h2 id="quotes-h" className="wizard__step-title">
                Quotes
              </h2>
              <p className="compare-new__lede">
                Every carrier's quote for one client. Columns follow the order you add them.
              </p>
              <DropZone
                limits={limits}
                onFiles={(files) => {
                  setNudge(false);
                  uploads.add(files);
                }}
                large={uploads.rows.length === 0}
                title="Upload quotes"
                accept={acceptOf(limits)}
                limitsLine="PDF, Word and Excel quotes, proposals and summaries"
                note={`Up to ${limits.max_pages} pages and ${limits.max_megabytes} MB each, ${limits.max_documents_per_run} files per comparison`}
              />
              <QuoteRows rows={uploads.rows} onRemove={uploads.remove} />
              {!workspace.model_configured && (
                <p className="hint">No model key is set on the server, so documents can't be read until one is configured.</p>
              )}
            </section>

            <section className="compare-new__details" aria-labelledby="details-h">
              <h2 id="details-h" className="wizard__step-title">
                Details
              </h2>
              <ClientField value={client} onChange={setClient} />
              <LineChoice value={line} onChange={onLineChange} />
              <div className="context-field">
                <label htmlFor={contextId} className="client-field__label">
                  Additional context <span className="client-field__optional">optional</span>
                </label>
                <textarea
                  id={contextId}
                  className="context-field__input"
                  rows={4}
                  maxLength={CONTEXT_MAX}
                  value={context}
                  aria-describedby={countId}
                  placeholder="Leave blank. BrokerDocs already knows what a quote comparison needs."
                  onChange={(e) => setContext(e.target.value)}
                />
                <p id={countId} className="context-field__count">
                  {context.length.toLocaleString("en-US")} of {CONTEXT_MAX.toLocaleString("en-US")} characters
                </p>
              </div>
              {startError && (
                <p className="inline-error" role="alert">
                  {startError}
                </p>
              )}
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}

/** One row per file in column order: letter (once ready), format glyph, name, "Word, 2 pages", Remove; a refused file
 *  keeps its row with the server's message under it and no letter (it will not be a column). */
function QuoteRows({ rows, onRemove }: { rows: FileRow[]; onRemove: (key: string) => void }) {
  if (!rows.length) return null;
  const letters = new Map<string, string>();
  rows.filter((r) => r.status === "ready").forEach((r, i) => letters.set(r.key, String.fromCharCode(65 + i)));
  return (
    <ul className="quote-rows" aria-label="Quotes in this comparison">
      {rows.map((r) => {
        const letter = letters.get(r.key) ?? null;
        const format = r.doc?.source_format ?? formatOfName(r.name);
        const word = format ? FORMAT_WORD[format] : null;
        const pages = r.status === "ready" ? r.doc?.page_count : null;
        const meta = r.status === "ready" ? [word, pages != null ? `${pages} ${pages === 1 ? "page" : "pages"}` : null].filter(Boolean).join(", ") : null;
        return (
          <li key={r.key} className={`quote-row quote-row--${r.status}`}>
            <span className="quote-row__slot" aria-hidden>
              {letter ? <span className={`doc-letter hue-${hueOf(letter)}`}>{letter}</span> : null}
            </span>
            <FormatGlyph format={format} className="quote-row__glyph" />
            <span className="quote-row__name" title={r.name}>
              {letter && <span className="sr-only">Column {letter}: </span>}
              {r.name}
            </span>
            {meta && (
              <span className="quote-row__meta">
                {meta}
                <span className="sr-only">, {formatBytes(r.doc!.size_bytes)}</span>
              </span>
            )}
            {r.status === "uploading" && (
              <span className="quote-row__meta">
                Uploading
                <span className="progress quote-row__progress" role="progressbar" aria-label={`Uploading ${r.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(r.progress * 100)}>
                  <span className="progress__bar" style={{ transform: `scaleX(${r.progress})` }} />
                </span>
              </span>
            )}
            {r.status === "rejected" && (
              <span className="quote-row__meta quote-row__refused">
                <WarningCircle size={16} weight="fill" aria-hidden className="danger-glyph" /> Not added
              </span>
            )}
            <button type="button" className="icon-btn quote-row__remove" aria-label={`Remove ${r.name}`} onClick={() => onRemove(r.key)} disabled={r.status === "uploading"}>
              <Trash size={16} aria-hidden />
            </button>
            {r.status === "ready" && r.doc?.warning && <span className="quote-row__news">{r.doc.warning}</span>}
            {r.status === "rejected" && (
              <span className="quote-row__news quote-row__error" role="alert">
                {r.message}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
