import type { ClaimRow, LossCheck, LossChip, LossQuickFilter, LossReview, Run } from "../api/types";
import type { PdfTarget } from "../pdf/PdfViewer";
import { API_BASE, isDemo } from "../config";
import { demoUrl } from "../api/client";

/**
 * A loss-run chip in the source viewer. PDF chips open the document's page; sheet chips open the rendered sheet page
 * (a one-page PDF from `sheet-pages/{n}`, R5) under its own cache key, with the row's box (D13).
 */
/** A rendered sheet page is landscape 792pt of 8pt type: 1.5x reads like the printed sheet at 12px. */
export const SHEET_ZOOM = 1.5;

export function chipTarget(chip: LossChip, scan = false, name = "", text = chip.label): PdfTarget {
  const sheet = chip.kind === "sheet" && chip.url != null;
  const box = {
    passage_id: 0,
    label: chip.label,
    letter: chip.letter,
    document_id: chip.document_id,
    filename: chip.filename,
    page_no: sheet ? 1 : (chip.page_no ?? 1),
    verified: !scan && chip.rects.length > 0,
    rects: chip.rects as [number, number, number, number][],
    page_width: chip.page_width ?? 612,
    page_height: chip.page_height ?? 792,
  };
  // Named for people (the chip's readable file name and short text, "Ashgrove 2025 CSV, Row 6"); a sheet page opens at
  // a readable zoom with its row scrolled into view, and its caption names the row, not "page 1" of the render.
  const who = name || chip.filename;
  return {
    documentId: sheet ? -(chip.document_id * 100_000 + (chip.page_no ?? 1)) : chip.document_id,
    filename: sheet ? `${who}, ${text}` : who,
    letter: chip.letter,
    page: box.page_no,
    chip: box,
    url: sheet ? (isDemo() ? demoUrl(chip.url!) : `${chip.url!.startsWith("http") ? "" : API_BASE}${chip.url}`) : undefined,
    zoom: sheet ? SHEET_ZOOM : undefined,
    caption: sheet ? `Source: ${who}, ${text}` : `Source: ${who}, page ${box.page_no}`,
  };
}

/** `workflow_name` of slice 5a loss reviews (backend seed, slug `loss-run`). */
export const LOSS_WORKFLOW = "Loss Review";
export const isLossRun = (run: Pick<Run, "workflow_name"> | null | undefined) => run?.workflow_name === LOSS_WORKFLOW;
export const lossNewHref = (client?: string | null) => `#/analytics/new${client ? `?client=${encodeURIComponent(client)}` : ""}`;

/** API.md: 1 to 8 files per review; PDFs and spreadsheets (.xlsx, .csv). */
export const MAX_LOSS_FILES = 8;
export const SPREADSHEET = /\.(xlsx|csv)$/i;

/** Money is integer cents everywhere (API.md); shown with cents, as loss runs print them. */
export function usd(cents: number | null | undefined): string {
  if (cents == null) return "";
  const v = Math.abs(cents) / 100;
  return `${cents < 0 ? "-" : ""}$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** ISO date to the US form the carriers print (MM/DD/YYYY). */
export function day(iso: string | null | undefined): string {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${m}/${d}/${y}`;
}

/** Amount typed by the broker: "12,400.00", "$12,400", "(1,200.00)" to cents; null when not a number. */
export function parseCents(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  const neg = /^\(.*\)$/.test(t) || t.startsWith("-");
  const digits = t.replace(/[()$,\s-]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(digits)) return null;
  const cents = Math.round(Number(digits) * 100);
  return neg ? -cents : cents;
}

export const QUICK_FILTERS: { key: LossQuickFilter; label: string }[] = [
  { key: "over_100k", label: "Over $100K" },
  { key: "open_only", label: "Open only" },
  { key: "last_3_months", label: "Last 3 months" },
  { key: "open_reserve", label: "Open reserve" },
];

export const METRIC_LABEL: Record<string, string> = {
  paid: "Paid", reserve: "Reserve", incurred: "Incurred", recovery: "Recovery", deductible: "Deductible", claim_count: "Claims",
};

/**
 * Owner decision (2026-11-04 12:40): one check card per printed line. The API sends one check per printed total
 * (`lr-total:<doc>:p2:y145:paid`, `...:reserve`, `...:incurred`); a card groups the metrics of one printed line, and
 * its one Accept resolves all of them. Conflicts and unverified-rows checks stay one card each.
 */
export interface CheckCard {
  key: string;
  kind: LossCheck["kind"];
  verdict: LossCheck["verdict"];
  status: LossCheck["status"];
  checks: LossCheck[];
  /** The worst metric of the line drives the card (off before needs_review before matches). */
  lead: LossCheck;
}

const VERDICT_RANK: Record<string, number> = { off: 0, needs_review: 1, conflicting: 2, unverified: 3, handled: 4, matches: 5 };

export function checkCards(checks: LossCheck[]): CheckCard[] {
  const byKey = new Map<string, LossCheck[]>();
  for (const c of checks) {
    // One printed line = one document, one scope, one printed page or sheet page (the metric's own word position
    // differs per amount, so the id alone does not group a scanned line's totals).
    const key = c.kind === "total"
      ? `total:${c.document_id}:${c.scope_kind}:${c.scope_value ?? ""}:${c.chips[0]?.kind ?? ""}${c.chips[0]?.page_no ?? ""}:${c.title.split(":")[0]}`
      : c.id;
    byKey.set(key, [...(byKey.get(key) ?? []), c]);
  }
  return [...byKey.entries()].map(([key, cs]) => {
    const lead = [...cs].sort((a, b) => (VERDICT_RANK[a.verdict] ?? 9) - (VERDICT_RANK[b.verdict] ?? 9))[0]!;
    const status = cs.some((c) => c.status === "open") ? "open" : cs.some((c) => c.status === "resolved") ? "resolved" : "match";
    return { key, kind: lead.kind, verdict: lead.verdict, status, checks: cs, lead };
  });
}

export type CardGroup = "to_resolve" | "handled" | "matches";
export const groupOf = (card: CheckCard): CardGroup =>
  card.checks.some((c) => c.needs_action) ? "to_resolve" : card.status === "resolved" ? "handled" : "matches";

/** The printed line's own label ("Total, all policies", "Page subtotal, page 2"), else the API's scope ("All claims"). */
export function scopeLabel(c: LossCheck): string {
  const scope = c.title.split(":")[0] || "All claims";
  if (!c.label) return scope;
  return c.scope_kind === "page_subtotal" && !/page \d/i.test(c.label) ? `${c.label}, page ${c.scope_value}` : c.label;
}

type DocLike = { document_id: number; carrier: string | null; valuation_date: string | null; filename: string };

function baseName(d: DocLike): string {
  const who = d.carrier?.trim().split(/\s+/)[0] ?? d.filename.replace(/\.(pdf|xlsx|csv)$/i, "");
  return `${who} ${d.valuation_date ? d.valuation_date.slice(0, 4) : "undated"}`;
}

const KIND: Record<string, string> = { pdf: "PDF", xlsx: "workbook", csv: "CSV" };

/**
 * A file named for people (coordinator note, round 3): the carrier's first word and the valuation year, "Driftwood
 * 2025"; when two files of the review would read the same, the file type follows ("Ashgrove 2025 workbook", "Ashgrove
 * 2025 CSV"), then the letter. The file name stays in the chip's tooltip and accessible name.
 */
export function docName(review: LossReview | null, documentId: number | null | undefined): string {
  const docs = review?.totals?.documents ?? [];
  const d = docs.find((x) => x.document_id === documentId);
  if (!d) return "";
  const base = baseName(d);
  const same = docs.filter((x) => baseName(x) === base);
  if (same.length < 2) return base;
  const ext = (f: DocLike) => /\.(pdf|xlsx|csv)$/i.exec(f.filename)?.[1]?.toLowerCase() ?? "";
  const withKind = `${base} ${KIND[ext(d)] ?? ext(d)}`.trim();
  return same.filter((x) => ext(x) === ext(d)).length > 1 ? `${withKind} (${d.letter})` : withKind;
}

export function docLetter(review: LossReview | null, documentId: number | null | undefined): string {
  return review?.totals?.documents.find((x) => x.document_id === documentId)?.letter ?? "";
}

/** Row state words (spec section 6: always shown). Silhouette and word in the component; this is the logic. */
export type RowStateKey = "unverified" | "conflicting" | "confirmed" | "edited" | "added" | "left_out" | "warning" | "changed" | "repeated";

export function rowStates(r: ClaimRow): RowStateKey[] {
  const out: RowStateKey[] = [];
  if (r.changed_since_resolved) out.push("changed");
  if (r.source === "added") out.push("added");
  if (r.decision?.kind === "edited") out.push("edited");
  if (r.decision?.kind === "confirmed") out.push("confirmed");
  if (r.decision?.kind === "left_out" || r.role === "left_out") out.push("left_out");
  if (r.verify_state === "unverified" && !r.decision && r.source === "extracted") out.push("unverified");
  if (r.role === "conflicting") out.push("conflicting");
  if (r.flags.includes("incurred_not_paid_plus_reserve")) out.push("warning");
  if (r.flags.includes("repeated_line")) out.push("repeated");
  return out;
}

export const ROW_STATE_WORD: Record<RowStateKey, string> = {
  unverified: "Not verified",
  conflicting: "Conflicting",
  confirmed: "Confirmed by you",
  edited: "Edited by you",
  added: "Added by you",
  left_out: "Left out of totals",
  warning: "Incurred differs",
  changed: "Changed since you decided",
  repeated: "Printed twice",
};

/** Plain uppercase status word for the claim status (DESIGN.md: status words in tables). */
export function statusWord(r: Pick<ClaimRow, "status" | "status_raw">): string {
  return r.status === "unknown" ? (r.status_raw ?? "Unknown") : r.status;
}
