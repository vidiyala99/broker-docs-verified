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

/**
 * "Policy Check · Renewal check", "Compare Quotes · 3 carriers" (one middle dot per line, taste-core 9.F). When the
 * client shows on more than one row (`shared`), a comparison's carriers replace the line so the rows can be told
 * apart: "Harborline, Ridgecrest and 2 more".
 */
export function secondaryLine(item: QueueItem, shared = false): string {
  const { lead, detail } = metaParts(item, shared);
  return lead && detail ? `${lead} · ${detail}` : (lead ?? detail);
}

// Words that name the kind of company, not which one ("Ridgecrest Mutual Casualty Company" is Ridgecrest).
const CARRIER_TAIL = new Set([
  "insurance", "assurance", "company", "co", "co.", "companies", "corp", "corp.", "corporation", "inc", "inc.",
  "llc", "ltd", "group", "mutual", "casualty", "indemnity", "specialty", "surplus", "lines", "underwriters",
  "of", "america", "national", "general", "fire", "&", "and",
]);

/** A carrier's short name for a one-line list: its legal and line-of-business words dropped, at most two words. */
export function shortCarrier(name: string): string {
  const words = name.replace(/,/g, " ").split(/\s+/).filter(Boolean);
  if (words[0]?.toLowerCase() === "the" && words.length > 1) words.shift();
  while (words.length > 1 && CARRIER_TAIL.has((words[words.length - 1] ?? "").toLowerCase())) words.pop();
  return words.slice(0, 2).join(" ") || name;
}

/**
 * "Harborline", "Harborline and Ridgecrest", "Harborline, Ridgecrest and 2 more". `carriers` has one entry per
 * document (null: no verified carrier), so "more" counts every other quote, named or not. "" when none is named.
 */
export function carrierList(carriers: (string | null)[]): string {
  const short = carriers.map((c) => (c ? shortCarrier(c) : null));
  const shown = [...new Set(short.filter((c): c is string => c != null))].slice(0, 2);
  if (shown.length === 0) return "";
  const more = short.filter((c) => c == null || !shown.includes(c)).length;
  if (more === 0) return shown.join(" and ");
  return `${shown.join(", ")} and ${more} more`;
}

const normName = (name: string) => name.replace(/\s+/g, " ").trim().toLowerCase();

/** Client names (normalised) that more than one row of the queue shows; "Unnamed client" rows count too. */
export function sharedClients(q: Queue): Set<string> {
  const seen = new Map<string, number>();
  for (const g of GROUPS) for (const i of q[g]) seen.set(normName(clientOf(i)), (seen.get(normName(clientOf(i))) ?? 0) + 1);
  return new Set([...seen].filter(([, n]) => n > 1).map(([k]) => k));
}

export const isShared = (shared: Set<string>, item: QueueItem) => shared.has(normName(clientOf(item)));

function toDate(iso: string): Date {
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
}

/** When the work was started, as day and time in the broker's locale: "Nov 3" and "9:22 AM" (the year only when it
 * is not this year). The phone shows the day alone. */
export function startedParts(iso: string | null, now = new Date()): { day: string; time: string } | null {
  if (!iso) return null;
  const d = toDate(iso);
  const year = d.getFullYear() !== now.getFullYear() ? ({ year: "numeric" } as const) : {};
  return {
    day: d.toLocaleDateString(undefined, { month: "short", day: "numeric", ...year }),
    time: d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }),
  };
}

/** "Nov 3, 9:22 AM". */
export function formatStarted(iso: string | null, now = new Date()): string {
  const p = startedParts(iso, now);
  return p ? `${p.day}, ${p.time}` : "";
}

/**
 * The meta line in two parts: `lead` is the workflow (the phone drops it when there is a detail: the row's mark
 * already says which workflow), `detail` what this piece of work is. Same text as `secondaryLine`.
 */
export function metaParts(item: QueueItem, shared = false): { lead: string | null; detail: string } {
  // "Building proposal" is the status column's text while the comparison's proposal runs; do not say it twice.
  if (item.summary === "Building proposal") return { lead: item.workflow_name, detail: "" };
  // Only comparisons name carriers, so the names say "comparison" themselves (the row's mark does too) and the
  // workflow word gives them its room: beside the status column there is space for one or the other.
  const carriers = shared ? carrierList(item.carriers ?? []) : "";
  if (carriers) return { lead: null, detail: carriers };
  return { lead: item.workflow_name, detail: item.summary };
}

/** What the row's report is, in the broker's words: the menu item and the name link both use it. */
export function reportNoun(item: QueueItem): string {
  return item.workflow_slug === "policy-check" ? "policy check" : item.workflow_slug === "compare-quotes" ? "comparison" : "report";
}

export const openLabel = (item: QueueItem) => `Open ${reportNoun(item)}`;

/**
 * The client-name link's text after the visible name (screen readers only): rows can share a client, so the link
 * says which piece of work it opens. Same name, date and carriers on two rows: the run number settles it.
 */
export function linkContexts(q: Queue): Map<number, string> {
  const shared = sharedClients(q);
  const items = GROUPS.flatMap((g) => q[g]);
  const base = new Map<number, string>();
  for (const i of items) {
    const started = formatStarted(i.created_at);
    const list = isShared(shared, i) ? carrierList(i.carriers ?? []) : "";
    const carriers = list ? `, ${list}` : "";
    base.set(i.run_id, `, ${reportNoun(i)}${started ? ` started ${started}` : ""}${carriers}`);
  }
  const key = (i: QueueItem) => `${normName(clientOf(i))}|${base.get(i.run_id)}`;
  const counts = new Map<string, number>();
  for (const i of items) counts.set(key(i), (counts.get(key(i)) ?? 0) + 1);
  return new Map(items.map((i) => [i.run_id, (counts.get(key(i)) ?? 0) > 1 ? `${base.get(i.run_id)}, run ${i.run_id}` : base.get(i.run_id)!]));
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
  return toDate(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
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
