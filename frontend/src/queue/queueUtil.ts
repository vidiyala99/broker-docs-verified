// Work queue copy and small helpers (QUEUE-BRIEF.md). Groups and order come from the server (API.md "Work queue").
import type { Line, Queue, QueueGroup, QueueItem } from "../api/types";

/** Display order (spec section 2): the slim In progress strip first, Done recently last and collapsed. */
export const GROUPS: QueueGroup[] = ["in_progress", "needs_review", "ready", "needs_attention", "done_recently"];

export const GROUP_LABEL: Record<QueueGroup, string> = {
  in_progress: "In progress",
  needs_review: "Needs review",
  ready: "Ready",
  needs_attention: "Needs attention",
  done_recently: "Done recently",
};

export const UNNAMED = "Unnamed client";

export const clientOf = (item: QueueItem) => item.client ?? UNNAMED;

export function isEmpty(q: Queue): boolean {
  return GROUPS.every((g) => q[g].length === 0);
}

export function groupOf(q: Queue, runId: number): QueueGroup | null {
  return GROUPS.find((g) => q[g].some((i) => i.run_id === runId)) ?? null;
}

/** "Policy Check · Renewal check", "Compare Quotes · 3 carriers" (one middle dot per line, taste-core 9.F). */
export function secondaryLine(item: QueueItem): string {
  // "Building proposal" is the status column's text while the comparison's proposal runs; do not say it twice.
  if (item.summary === "Building proposal") return item.workflow_name;
  return `${item.workflow_name} · ${item.summary}`;
}

export const SEVERITIES = ["critical", "high", "medium", "low"] as const;

export function openTotal(item: QueueItem): number {
  return item.open_counts ? SEVERITIES.reduce((n, s) => n + item.open_counts![s], 0) : 0;
}

const IN_PROGRESS_TEXT: Record<string, string> = {
  queued: "Queued",
  running: "Reading documents",
  composing: "Writing report",
};

/** In progress rows: the comparison's proposal may be the active run ("Building proposal" is the summary then). */
export function progressText(item: QueueItem): string {
  return IN_PROGRESS_TEXT[item.status] ?? "Building proposal";
}

export function formatDay(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Why a Done recently row is done. */
export function doneText(item: QueueItem): string {
  const when = formatDay(laterOf(item.finished_at, item.queue_changed_at));
  const what =
    item.queue_state === "done"
      ? "Marked done"
      : item.queue_state === "dismissed"
        ? "Dismissed"
        : item.workflow_slug === "compare-quotes" && item.proposal_run_id != null
          ? "Proposal built"
          : item.workflow_slug === "policy-check"
            ? "Every discrepancy resolved"
            : "Finished";
  return when ? `${what} ${when}` : what;
}

function laterOf(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return a > b ? a : b;
}

export const otherLine = (line: Line): Line => (line === "personal" ? "commercial" : "personal");
export const moveLabel = (to: Line) => (to === "personal" ? "Move to Personal lines" : "Move to Commercial");
export const movedText = (to: Line) => (to === "personal" ? "Moved to Personal lines." : "Moved to Commercial.");
export const activityHref = (line?: Line) => `#/activity${line ? `?line=${line}` : ""}`;
