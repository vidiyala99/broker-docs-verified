import type { Run } from "../api/types";
import { hueOf } from "../report/Chip";

const STATUS_TEXT: Record<Run["status"], string> = {
  queued: "Queued",
  running: "Running",
  composing: "Writing report",
  done: "Done",
  partial: "Partial",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function statusText(s: Run["status"]): string {
  return STATUS_TEXT[s];
}

export function runTitle(run: Run): string {
  return `Run ${run.id}`;
}

export function formatWhen(iso: string): string {
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** DG1: Recent runs sit where the artifact goes on the launch screen. */
export function RecentRuns({ runs, compact, currentId }: { runs: Run[]; compact?: boolean; currentId?: number }) {
  return (
    <ul className={`runs${compact ? " runs--compact" : ""}`}>
      {runs.map((r) => (
        <li key={r.id}>
          <a href={`#/runs/${r.id}`} className="run-link" aria-current={currentId === r.id ? "page" : undefined}>
            <span className="run-link__top">
              <span className="run-link__title">{runTitle(r)}</span>
              <span className={`status-word status-word--${r.status}`}>{statusText(r.status)}</span>
            </span>
            {!compact && (
              <span className="run-link__docs">
                {r.documents.map((d) => (
                  <span key={d.run_document_id} className="run-link__doc">
                    <span className={`doc-letter hue-${hueOf(d.letter)}`} aria-hidden>
                      {d.letter}
                    </span>
                    <span className="sr-only">Document {d.letter}: </span>
                    {d.filename}
                  </span>
                ))}
              </span>
            )}
            <span className="run-link__meta">
              {r.documents.length} {r.documents.length === 1 ? "quote" : "quotes"}, {formatWhen(r.created_at)}
            </span>
          </a>
        </li>
      ))}
    </ul>
  );
}
