import { useId } from "react";
import { ArrowsClockwise, CheckCircle, Circle, StopCircle, WarningCircle } from "@phosphor-icons/react";
import type { PolicyRole, Run, RunDocument } from "../api/types";
import { ROLE_LABEL } from "../policy/draft";
import { hueOf } from "../report/Chip";
import { MoreRow, useFold } from "../ui/Fold";
import { statusText } from "../launch/RecentRuns";
import { ReadingMark } from "../voice/marks";

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
  pageCounts,
}: {
  run: Run;
  onCancel: () => void;
  onRetry: () => void;
  busy: "cancel" | "retry" | null;
  /** document_id -> page count, from GET /api/documents (the run payload has no page counts). */
  pageCounts?: Map<number, number | null>;
}) {
  const listId = useId();
  const fold = useFold(run.documents, (d) => docNews(d) != null && d.status !== "done");  const settled = run.documents.filter((d) => d.status === "done" || d.status === "failed").length;
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
      <ul className="doc-rows" id={listId} aria-label="Documents in this run">
        {fold.entries.map((e) => {
          if (e.more) return <MoreRow key="more" hidden={fold.hidden} expanded={fold.expanded} onToggle={fold.toggle} noun="documents" controls={listId} />;
          const d = e.item;
          const pages = pageCounts?.get(d.document_id);
          const news = docNews(d);
          return (
            <li key={d.run_document_id} className={`doc-row doc-row--${d.status}`}>
              <span className={`doc-letter hue-${hueOf(d.letter)}`} aria-hidden>
                {d.letter}
              </span>
              <span className="doc-row__name" title={d.filename}>
                <span className="sr-only">Document {d.letter}: </span>
                {d.slot && d.slot !== "quotes" && <span className="doc-row__role">{ROLE_LABEL[d.slot as PolicyRole] ?? d.slot}</span>}
                {d.filename}
              </span>
              {pages != null && (
                <span className="doc-row__pages">
                  {pages} {pages === 1 ? "page" : "pages"}
                </span>
              )}
              <DocGlyph status={d.status} />
              {/* The glyph is the visual status; the word is for screen readers unless the row has news. */}
              {news == null && <span className="sr-only">, {DOC_TEXT[d.status]}</span>}
              {news != null &&
                (d.status === "failed" ? (
                  <span className="doc-row__news doc-row__news--error" role="alert">
                    {news}
                  </span>
                ) : (
                  <span className="doc-row__news">{news}</span>
                ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The second line of a row, only when it says something the glyph cannot. */
function docNews(d: RunDocument): string | null {
  if (d.status === "failed") return d.error ?? "Failed";
  if (d.status === "done" && d.reused_from_run_id != null && d.read_at)
    // Slice 4 (spec section 5): a document whose earlier read was reused says when it was read.
    return `Read on ${new Date(d.read_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}, no new read`;
  if (d.status === "done") return d.option_count > 1 ? `Compared option 1 of ${d.option_count}` : null;
  if (d.status === "queued") return null;
  return d.progress || DOC_TEXT[d.status];
}

function DocGlyph({ status }: { status: RunDocument["status"] }) {
  if (status === "done") return <CheckCircle size={16} weight="fill" className="ok-glyph doc-row__glyph" aria-hidden />;
  if (status === "failed") return <WarningCircle size={16} weight="fill" className="danger-glyph doc-row__glyph" aria-hidden />;
  if (status === "queued") return <Circle size={16} className="muted-glyph doc-row__glyph" aria-hidden />;
  // Reading: the page with its reading line (DESIGN.md Voice), not a generic spinner.
  return <ReadingMark size={16} className="doc-row__glyph" />;
}
