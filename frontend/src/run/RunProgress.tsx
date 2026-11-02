import { ArrowsClockwise, CheckCircle, Circle, StopCircle, WarningCircle } from "@phosphor-icons/react";
import type { Run, RunDocument } from "../api/types";
import { hueOf } from "../report/Chip";
import { statusText } from "../launch/RecentRuns";

const DOC_TEXT: Record<RunDocument["status"], string> = {
  queued: "Queued",
  reading: "Reading pages",
  extracting: "Extracting",
  verifying: "Verifying",
  done: "Done",
  failed: "Failed",
};

/** DG4: per-document rows in the Upload accordion, Cancel on the run header. */
export function RunProgress({
  run,
  onCancel,
  onRetry,
  busy,
}: {
  run: Run;
  onCancel: () => void;
  onRetry: () => void;
  busy: "cancel" | "retry" | null;
}) {
  const settled = run.documents.filter((d) => d.status === "done" || d.status === "failed").length;
  const summary = run.active
    ? run.status === "composing"
      ? "All documents read. Writing report..."
      : `${settled} of ${run.documents.length} documents read`
    : statusText(run.status);
  return (
    <div className="run-progress">
      <div className="run-head">
        <div className="run-head__text">
          <span className="run-head__title">Run {run.id}</span>
          <span className={`status-word status-word--${run.status}`}>{statusText(run.status)}</span>
        </div>
        {run.active ? (
          <button type="button" className="btn btn--secondary btn--sm" onClick={onCancel} disabled={busy != null}>
            <StopCircle size={16} aria-hidden /> {busy === "cancel" ? "Cancelling..." : "Cancel"}
          </button>
        ) : (
          ["failed", "partial", "cancelled"].includes(run.status) && (
            <button type="button" className="btn btn--secondary btn--sm" onClick={onRetry} disabled={busy != null}>
              <ArrowsClockwise size={16} aria-hidden /> {busy === "retry" ? "Retrying..." : "Retry"}
            </button>
          )
        )}
      </div>
      <p className="sr-only" role="status" aria-atomic="true">
        Run {run.id}: {summary}
      </p>
      {run.error && (
        <p className="inline-error" role="alert">
          {run.error}
        </p>
      )}
      <ul className="doc-rows" aria-label="Documents in this run">
        {run.documents.map((d) => (
          <li key={d.run_document_id} className={`doc-row doc-row--${d.status}`}>
            <span className={`doc-letter hue-${hueOf(d.letter)}`} aria-hidden>
              {d.letter}
            </span>
            <div className="doc-row__main">
              <span className="doc-row__name" title={d.filename}>
                <span className="sr-only">Document {d.letter}: </span>
                {d.filename}
              </span>
              {d.status === "failed" ? (
                <span className="doc-row__error" role="alert">
                  {d.error ?? "Failed"}
                </span>
              ) : (
                <span className="doc-row__status">
                  {d.status === "done" && d.option_count > 1
                    ? `Done. Compared option 1 of ${d.option_count}`
                    : d.progress && d.status !== "done" && d.status !== "queued"
                      ? d.progress
                      : DOC_TEXT[d.status]}
                </span>
              )}
            </div>
            <DocGlyph status={d.status} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function DocGlyph({ status }: { status: RunDocument["status"] }) {
  if (status === "done") return <CheckCircle size={18} className="ok-glyph" aria-hidden />;
  if (status === "failed") return <WarningCircle size={18} className="danger-glyph" aria-hidden />;
  if (status === "queued") return <Circle size={18} className="muted-glyph" aria-hidden />;
  return <span className="spinner" aria-hidden />;
}
