/* Phase A comps only (LOSS-RUNS-BRIEF.md). Static pages that reuse the app's tokens, CSS, rail and marks so each
   direction is judged inside the real chrome. Data: data.json, built from the lr_main fixture truth by build_data.py. */
import "@fontsource-variable/inter";
import "@fontsource/source-serif-4/latin-600.css";
import "@fontsource/source-serif-4/latin-ext-600.css";
import "../../src/styles/tokens.css";
import "../../src/styles/app.css";
import "../../src/voice/voice.css";
import "../../src/queue/queue.css";
import "../../src/certificates/certificates.css";
import "./comps.css";
import type { ReactNode } from "react";
import {
  CheckCircle,
  CircleDashed,
  Eraser,
  NotEquals,
  PencilSimple,
  PlusCircle,
  Scales,
  Scan,
  Signature,
  SidebarSimple,
  Stamp,
  UploadSimple,
  Warning,
  WarningDiamond,
} from "@phosphor-icons/react";
import { RailGlyph } from "../../src/voice/railMarks";
import { Glyph, type GlyphName } from "../../src/voice/marks";
import { TopBar } from "../../src/shell/Shell";
import raw from "./data.json";

export type Money = "paid" | "reserve" | "incurred" | "recovery" | "deductible";
export interface Row {
  id: string; fid: string; letter: string; carrier: string; chip: string; scan: boolean;
  claim_number: string; claim_key: string; claimant: string; date_of_loss: string; status_raw: string; status: string;
  policy_number: string; policy_year: number | null; line: string; page: number | null; sheet: string | null; row: number | null;
  paid: number | null; reserve: number | null; incurred: number | null; recovery: number | null; deductible: number | null;
  verify: string; verify_reason: string | null; warnings: string[]; role: string; counts: boolean;
  claim_incurred: number; claim_open: boolean; decision?: { kind: string; note: string };
}
export interface Metric { metric: string; printed: number; computed: number | null; difference: number | null }
export interface Check {
  kind: "total" | "conflict" | "unverified"; verdict: string; fid?: string; letter?: string; doc?: string; label?: string;
  scope_kind?: string; scope_value?: string | null; chip?: string; quote?: string; reason?: string | null;
  lines_in_scope?: number; metrics?: Metric[]; claim_number?: string; claimant?: string; rows?: number; note?: string | null;
  row_ids?: string[];
  options?: { letter: string; fid: string; chip: string; paid: number; reserve: number; incurred: number; status_raw: string; doc: string; valuation_date: string }[];
}
export interface Doc { fid: string; letter: string; short: string; filename: string; format: string; carrier: string; valuation_date: string | null; pages: number | null; sheets: string[] }
export const data = raw as unknown as {
  client: string; synthetic_label: string; docs: Doc[]; rows: Row[]; checks: Check[]; notices: string[]; run: number; version: number;
  totals: Record<string, number> & { as_of: string };
  long: Record<string, number>;
};
export const docOf = (fid: string) => data.docs.find((d) => d.fid === fid)!;

/* ---------- formatting ---------- */
export const usd = (c: number | null | undefined) =>
  c == null ? "" : `${c < 0 ? "-" : ""}$${(Math.abs(c) / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const day = (iso: string | null) => (iso ? `${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(0, 4)}` : "No date");
export const metricName: Record<string, string> = { paid: "Paid", reserve: "Reserve", incurred: "Incurred", recovery: "Recovery" };
export const hue = (letter: string) => ((letter.charCodeAt(0) - 65) % 5) + 1;

/* ---------- checks: the buckets the API sends (open first, handled, matches) ---------- */
export const toResolve = data.checks.filter((c) => ["off", "needs_review", "conflicting", "unverified"].includes(c.verdict));
export const handled = data.checks.filter((c) => c.verdict === "handled");
export const matches = data.checks.filter((c) => c.verdict === "matches");
export const matchTotals = matches.reduce((n, c) => n + (c.metrics?.length ?? 0), 0);
const ORDER = { off: 0, needs_review: 1, conflicting: 2, unverified: 3 } as Record<string, number>;
toResolve.sort((a, b) => ORDER[a.verdict] - ORDER[b.verdict]);

export function checkTitle(c: Check): string {
  if (c.kind === "conflict") return `Claim ${c.claim_number} has two values for the same valuation date`;
  if (c.kind === "unverified") return `${c.rows} ${c.rows === 1 ? "row" : "rows"} in ${c.doc} not verified`;
  return `${c.doc}: ${c.label}${c.scope_kind === "page_subtotal" ? `, page ${c.scope_value}` : ""}`;
}
export function offBy(c: Check) {
  const inc = c.metrics?.find((m) => m.metric === "incurred") ?? c.metrics?.[0];
  return inc?.difference ?? 0;
}

/* Status words carry the meaning; the glyph is the silhouette, the colour is third (DESIGN.md Evidence states). */
export function StateWord({ c }: { c: Check }) {
  const map: Record<string, [ReactNode, string, string]> = {
    off: [<NotEquals size={16} weight="bold" />, `Off by ${usd(Math.abs(offBy(c)))}`, "off"],
    needs_review: [<CircleDashed size={16} weight="bold" />, "Couldn't check", "review"],
    conflicting: [<Scales size={16} weight="bold" />, "Conflicting", "conflict"],
    unverified: [<Warning size={16} weight="bold" />, "Not verified", "unverified"],
    handled: [<Signature size={16} weight="bold" />, "Confirmed by you", "handled"],
    matches: [<CheckCircle size={16} weight="bold" />, "Matches", "match"],
  };
  const [g, w, k] = map[c.verdict];
  return (
    <span className={`lr-word lr-word--${k}`}>
      <span aria-hidden className="lr-word__g">{g}</span>
      <span className="status-word">{w}</span>
    </span>
  );
}

/* A loss-run chip: letter + label ("Driftwood 2025 p.2", "Sheet 2024, row 8"); dashed + scan glyph when scan-read. */
export function LChip({ letter, label, scan = false }: { letter: string; label: string; scan?: boolean }) {
  return (
    <button type="button" className={`chip lr-chip hue-${hue(letter)}${scan ? " chip--scan" : ""}`} aria-label={`Source ${letter}, ${label}${scan ? ". Read from scan, unverified" : ""}`}>
      {scan && <Scan size={12} weight="bold" aria-hidden />}
      <span className="lr-chip__letter">{letter}</span>
      {label}
    </button>
  );
}

/* Row state: every non-plain row says what it is (spec section 6: always shown). */
export function rowStates(r: Row): { key: string; icon: ReactNode; word: string; tip: string }[] {
  const out = [];
  if (r.decision?.kind === "confirmed") out.push({ key: "confirmed", icon: <Signature size={14} />, word: "Confirmed", tip: `Confirmed by you: ${r.decision.note}` });
  else if (r.verify === "unverified") out.push({ key: "unverified", icon: r.scan ? <Scan size={14} /> : <Warning size={14} />, word: "Not verified", tip: r.verify_reason === "scan" ? "Read from scan, unverified. Kept out of totals until you confirm it." : `Couldn't verify: ${r.verify_reason}. Kept out of totals.` });
  if (r.role === "conflicting") out.push({ key: "conflicting", icon: <Scales size={14} />, word: "Conflicting", tip: "Two files give this claim different amounts on the same valuation date. Out of totals until you pick one." });
  if (r.warnings.includes("incurred_not_paid_plus_reserve")) out.push({ key: "warning", icon: <WarningDiamond size={14} />, word: "Incurred differs", tip: `Incurred ${usd(r.incurred)} is not paid + reserve (${usd((r.paid ?? 0) + (r.reserve ?? 0))}). Counted as printed; the carrier may include expense.` });
  if (r.warnings.includes("repeated_line")) out.push({ key: "warning", icon: <WarningDiamond size={14} />, word: "Printed twice", tip: "This line is identical to another line of the same claim. Both count, as printed." });
  return out;
}
export const SpecimenIcons = { PencilSimple, PlusCircle, Eraser, Stamp };

/* ---------- table model: claims sorted by claim incurred, superseded valuations folded under their claim ---------- */
export interface Group { key: string; lines: Row[]; history: Row[]; incurred: number; open: boolean; reserve: number; dol: string }
export function groups(rows = data.rows): Group[] {
  const m = new Map<string, Group>();
  for (const r of rows) {
    const k = `${r.carrier}|${r.claim_key}`;
    if (!m.has(k)) m.set(k, { key: k, lines: [], history: [], incurred: 0, open: false, reserve: 0, dol: r.date_of_loss });
    const g = m.get(k)!;
    if (r.role === "superseded") g.history.push(r);
    else g.lines.push(r);
  }
  for (const g of m.values()) {
    g.incurred = Math.max(...g.lines.map((r) => r.claim_incurred));
    g.open = g.lines.some((r) => r.claim_open);
    g.reserve = g.lines.reduce((n, r) => n + (r.reserve ?? 0), 0);
  }
  return [...m.values()].filter((g) => g.lines.length).sort((a, b) => b.incurred - a.incurred);
}
export const QUICK = [
  { key: "over_100k", label: "Over $100K", test: (g: Group) => g.incurred > 10_000_000 },
  { key: "open_only", label: "Open only", test: (g: Group) => g.open },
  { key: "last_3_months", label: "Last 3 months", test: (g: Group) => g.dol >= "2025-06-30" },
  { key: "open_reserve", label: "Open reserve", test: (g: Group) => g.reserve > 0 },
];

/* ---------- shell: the real rail marks and top bar, Analytics live ---------- */
const RAIL: GlyphName[][] = [
  ["compare", "proposal", "personal", "policy-check", "certificate", "tasks", "integrations"],
  ["assistant", "search", "analytics", "insights"],
  ["vault"],
];
export function Shell({ children, crumbs }: { children: ReactNode; crumbs: string[] }) {
  return (
    <div className="app">
      <nav className="rail" aria-label="Main">
        <a href="#/" className="rail__home" aria-label="BrokerDocs home"><span className="glyph" aria-hidden /></a>
        {RAIL.map((g, i) => (
          <ul key={i} className="rail__group">
            {g.map((n) => (
              <li key={n}>
                <span className={`rail__item${n === "analytics" ? " is-active" : ""}${["assistant", "search", "insights", "vault"].includes(n) ? " is-disabled" : ""}`} aria-label={n}>
                  <RailGlyph name={n} />
                </span>
              </li>
            ))}
          </ul>
        ))}
      </nav>
      <div className="app__main">
        <TopBar crumbs={crumbs.map((label) => ({ label, href: "#" }))} />
        {children}
      </div>
    </div>
  );
}
export function Strip() {
  return (
    <div className="left-strip">
      <button type="button" className="icon-btn" aria-label="Show loss review panel"><SidebarSimple size={20} aria-hidden /></button>
      <button type="button" className="icon-btn" aria-label="Show files"><UploadSimple size={20} aria-hidden /></button>
    </div>
  );
}
export function Header({ action }: { action?: ReactNode }) {
  const carriers = new Set(data.docs.map((d) => d.carrier)).size;
  return (
    <div className="toolbar lr-toolbar">
      <h1 className="toolbar__title toolbar__title--named">
        <Glyph name="analytics" size={30} tabs />
        <span className="toolbar__heading">
          <span className="toolbar__name">{data.client} · Loss review</span>
          <span className="toolbar__meta">
            <span className="toolbar__version">
              {data.docs.length} files from {carriers} carriers, valued as of {day(data.totals.as_of)}. Run {data.run}, version {data.version}
            </span>
          </span>
        </span>
      </h1>
      <span className="lr-synthetic">Synthetic sample data</span>
      {action}
    </div>
  );
}
export function DoneGate() {
  return (
    <div className="issue lr-gate">
      <button type="button" className="btn btn--primary" aria-disabled="true">Mark review done</button>
      <span className="issue__count">{toResolve.length} checks to resolve</span>
    </div>
  );
}
export const crumbs = ["Commercial", "Analytics", data.client];
