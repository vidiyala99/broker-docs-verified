import type { Run, Task } from "../api/types";

/** Slice 3b T7 helpers: wording, dates, overdue, search, filter and sort (all client-side, R4). TASKS-BRIEF.md. */

export const MAX_TITLE = 200;

export type Filter = "open" | "done" | "all";
export type SortKey = "due" | "created" | "title";
export type SortDir = "asc" | "desc";
export interface Sort {
  key: SortKey;
  dir: SortDir;
}

export const DEFAULT_SORT: Sort = { key: "due", dir: "asc" };
/** First click on a header: due soonest, created newest, title A to Z (brief, owner call). */
export const FIRST_DIR: Record<SortKey, SortDir> = { due: "asc", created: "desc", title: "asc" };

const WORKFLOW_WORD: Record<string, string> = { "Policy Check": "Policy check", "Proposal Builder": "Proposal", "Compare Quotes": "Compare Quotes" };

/** The app's run wording ("Policy check, run 7", as in the run lists) from the API's "Policy Check · Run 7". */
export function friendlyRunLabel(runLabel: string | null, runId: number | null): string {
  if (!runLabel) return runId != null ? `Run ${runId}` : "";
  const m = /^(.*) · Run (\d+)$/.exec(runLabel);
  if (!m) return runLabel;
  return `${WORKFLOW_WORD[m[1]!] ?? m[1]}, run ${m[2]}`;
}

export function runOptionLabel(run: Run): string {
  return `${WORKFLOW_WORD[run.workflow_name] ?? run.workflow_name}, run ${run.id} (${run.line === "personal" ? "Personal" : "Commercial"})`;
}

/** "Follow up: <title>", cut to the 200-character limit (spec 2.3). */
export function followUpTitle(discrepancyTitle: string): string {
  return `Follow up: ${discrepancyTitle}`.slice(0, MAX_TITLE);
}

/** Local calendar date as YYYY-MM-DD. */
export function todayIso(now = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/** Overdue = past its due date and still open (decided by the UI, API.md). */
export function isOverdue(task: Task, today = todayIso()): boolean {
  return task.status === "open" && task.due_date != null && task.due_date < today;
}

function dateFromIso(d: string): Date {
  const [y, m, day] = d.split("-").map(Number);
  return new Date(y!, (m ?? 1) - 1, day ?? 1);
}

/** "Nov 5", with the year when it is not this year ("Nov 5, 2027"). */
export function formatDay(d: Date): string {
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

export const formatDue = (due: string) => formatDay(dateFromIso(due));
export const formatCreated = (iso: string) => formatDay(new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`));

export function matchesSearch(task: Task, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return [task.title, task.run_label ?? "", friendlyRunLabel(task.run_label, task.run_id)].some((s) => s.toLowerCase().includes(needle));
}

export function matchesFilter(task: Task, f: Filter): boolean {
  return f === "all" || task.status === f;
}

const collator = new Intl.Collator("en", { sensitivity: "base", numeric: true });

/** Sort a copy. Tasks without a due date stay last in both due-date directions. Ties: newest created, then id. */
export function sortTasks(tasks: Task[], sort: Sort): Task[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  const tie = (a: Task, b: Task) => b.created_at.localeCompare(a.created_at) || b.id - a.id;
  return [...tasks].sort((a, b) => {
    if (sort.key === "due") {
      if (a.due_date == null || b.due_date == null) {
        if (a.due_date == null && b.due_date == null) return tie(a, b);
        return a.due_date == null ? 1 : -1;
      }
      return sign * a.due_date.localeCompare(b.due_date) || tie(a, b);
    }
    if (sort.key === "created") return sign * (a.created_at.localeCompare(b.created_at) || a.id - b.id);
    return sign * collator.compare(a.title, b.title) || tie(a, b);
  });
}

/** `#/tasks?task=<id>` (O7). */
export function taskIdFromHash(hash: string): number | null {
  const q = hash.indexOf("?");
  if (q === -1) return null;
  const v = Number(new URLSearchParams(hash.slice(q + 1)).get("task"));
  return Number.isInteger(v) && v > 0 ? v : null;
}

/** `#/runs/<id>?discrepancy=<item_id>` (O7). */
export function discrepancyFromHash(hash: string): string | null {
  const q = hash.indexOf("?");
  if (q === -1) return null;
  return new URLSearchParams(hash.slice(q + 1)).get("discrepancy") || null;
}

export const taskHref = (id: number) => `#/tasks?task=${id}`;
export const discrepancyHref = (runId: number, itemId: string) => `#/runs/${runId}?discrepancy=${encodeURIComponent(itemId)}`;
