import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowCounterClockwise,
  CaretDown,
  CaretRight,
  CaretUp,
  Check,
  Columns,
  Eraser,
  MagnifyingGlass,
  PencilSimple,
  PlusCircle,
  Scales,
  Scan,
  Signature,
  Warning,
  WarningDiamond,
} from "@phosphor-icons/react";
import { api } from "../api/client";
import type { ClaimRow, ClaimsPage, LossChip as LossChipData, LossQuickFilter, LossReview } from "../api/types";
import { RowMenu, type MenuItem } from "../tasks/RowMenu";
import { LossChip } from "./Checks";
import { day, METRIC_LABEL, QUICK_FILTERS, ROW_STATE_WORD, rowStates, statusWord, usd, type CheckCard, type RowStateKey } from "./lossUtil";

/** Every row is one fixed height so 200,000 rows scroll as a window over server pages (D2). */
export const ROW_H = 56;
const PAGE = 100;
const OVERSCAN = 8;

export type SortKey = "claim_number" | "claimant" | "date_of_loss" | "status" | "paid" | "reserve" | "claim_incurred";
export interface TableQuery {
  sort: SortKey;
  dir: "asc" | "desc";
  quick: LossQuickFilter[];
  q: string;
  /** A check's `filter` (rows behind it), or a file (By file uses its own view). */
  scope: Record<string, unknown> | null;
}

type Optional = "policy_number" | "policy_year" | "line" | "carrier" | "recovery" | "deductible";
const OPTIONAL: { key: Optional; label: string; num?: boolean }[] = [
  { key: "policy_number", label: "Policy" },
  { key: "policy_year", label: "Policy year", num: true },
  { key: "line", label: "Line" },
  { key: "carrier", label: "Carrier" },
  { key: "recovery", label: "Recovery", num: true },
  { key: "deductible", label: "Deductible", num: true },
];

const STATE_GLYPH: Record<RowStateKey, ReactNode> = {
  unverified: <Warning size={14} weight="bold" />,
  conflicting: <Scales size={14} weight="bold" />,
  confirmed: <Signature size={14} weight="bold" />,
  edited: <PencilSimple size={14} weight="bold" />,
  added: <PlusCircle size={14} weight="bold" />,
  left_out: <Eraser size={14} weight="bold" />,
  warning: <WarningDiamond size={14} weight="bold" />,
  changed: <ArrowCounterClockwise size={14} weight="bold" />,
  repeated: <WarningDiamond size={14} weight="bold" />,
};

function stateTip(r: ClaimRow, s: RowStateKey): string {
  switch (s) {
    case "unverified":
      return r.verify_reason === "scan" || r.chip?.kind === "page"
        ? `Not verified: ${r.verify_reason ?? "read from a scan"}. Kept out of totals until you confirm, fix or leave it out.`
        : `Not verified: ${r.verify_reason ?? "the amounts were not found on the printed line"}. Kept out of totals.`;
    case "conflicting":
      return "Another file gives this claim different amounts on the same valuation date. Out of totals until you pick one.";
    case "warning":
      return `Incurred ${usd(r.effective.incurred)} is not paid + reserve (${usd((r.effective.paid ?? 0) + (r.effective.reserve ?? 0))}). Counted as printed; the carrier may include expense.`;
    case "repeated":
      return "This line is identical to another line of the same claim. Both count, as printed.";
    case "edited":
      return `Edited by you. Read from the file: paid ${usd(r.extracted.paid)}, reserve ${usd(r.extracted.reserve)}, incurred ${usd(r.extracted.incurred)}.`;
    case "changed":
      return "The file was read again and this row's values changed, so your decision is no longer applied.";
    default:
      return `${ROW_STATE_WORD[s]}${r.note ? `: ${r.note}` : ""}`;
  }
}

/** The amount cell: an edited value shows the value read from the file struck beside it. */
function Money({ r, k }: { r: ClaimRow; k: "paid" | "reserve" | "incurred" | "recovery" | "deductible" }) {
  const v = r.effective[k];
  const was = r.extracted[k];
  const edited = r.decision?.kind === "edited" && was !== v;
  return (
    <>
      {edited && <s className="lr-was" aria-label={`read as ${usd(was)}`}>{usd(was)}</s>}
      {usd(v)}
    </>
  );
}

export interface RowActions {
  openChip: (chip: LossChipData, scan: boolean) => void;
  edit: (row: ClaimRow, mode: "confirm" | "fix" | "leave_out") => void;
  undo: (row: ClaimRow) => void;
  removeAdded: (row: ClaimRow) => void;
}

function rowMenu(r: ClaimRow, act: RowActions): MenuItem[] {
  const icon = (n: ReactNode) => <span aria-hidden>{n}</span>;
  if (r.source === "added") return [{ label: "Remove the added claim", icon: icon(<Eraser size={16} />), run: () => act.removeAdded(r) }];
  if (r.decision) {
    const word = r.decision.kind === "edited" ? "Undo the edit" : r.decision.kind === "confirmed" ? "Undo confirm" : "Put back in totals";
    return [{ label: word, icon: icon(<ArrowCounterClockwise size={16} />), run: () => act.undo(r) }];
  }
  const items: MenuItem[] = [];
  if (r.verify_state === "unverified") items.push({ label: "Confirm...", icon: icon(<Signature size={16} />), run: () => act.edit(r, "confirm") });
  items.push({ label: "Fix amounts...", icon: icon(<PencilSimple size={16} />), run: () => act.edit(r, "fix") });
  if (r.role !== "superseded") items.push({ label: "Leave out of totals...", icon: icon(<Eraser size={16} />), run: () => act.edit(r, "leave_out") });
  return items;
}

export function ClaimLine({ r, first, act, cols, history, open, onFold, pending }: {
  r: ClaimRow;
  first: boolean;
  act: RowActions;
  cols: Optional[];
  history?: number;
  open?: boolean;
  onFold?: () => void;
  pending?: string | null;
}) {
  const states = rowStates(r);
  const scan = r.verify_state === "unverified" && r.chip?.kind === "page" && (r.verify_reason ?? "").includes("scan");
  return (
    <tr className={`lr-row${r.counted ? "" : " is-out"}${first ? "" : " is-cont"}${r.role === "conflicting" ? " is-conflict" : ""}${pending ? " is-pending" : ""}`} style={{ height: ROW_H }}>
      <td className="lr-c-claim">
        <span className={`lr-claimno${first ? "" : " lr-claimno--cont"}`}>
          {first && history ? (
            <button type="button" className="lr-fold" aria-expanded={open} aria-label={`${open ? "Hide" : "Show"} ${history} older ${history === 1 ? "valuation" : "valuations"} of ${r.claim_number}`} onClick={onFold}>
              {open ? <CaretDown size={14} aria-hidden /> : <CaretRight size={14} aria-hidden />}
            </button>
          ) : (
            <span className="lr-fold-space" />
          )}
          {r.claim_number}
        </span>
      </td>
      <td className="lr-c-who">
        <span className="lr-who" title={r.claimant ?? ""}>{r.claimant || <span className="lr-muted">No claimant</span>}</span>
        <span className="lr-states">
          {r.chip && <LossChip chip={r.chip} onOpen={(c) => act.openChip(c, scan)} scan={scan} />}
          {pending && <span className="lr-state lr-state--pending">{pending}</span>}
          {!pending &&
            states.map((s) => (
              <span key={s} className={`lr-state lr-state--${s}`} title={stateTip(r, s)}>
                <span aria-hidden>{s === "unverified" && scan ? <Scan size={14} weight="bold" /> : STATE_GLYPH[s]}</span>
                {ROW_STATE_WORD[s]}
                <span className="sr-only">. {stateTip(r, s)}</span>
              </span>
            ))}
        </span>
      </td>
      <td className="num lr-c-date">{day(r.date_of_loss)}</td>
      <td className="lr-c-status">
        <span className="status-word lr-status" title={r.status_raw ? `Printed "${r.status_raw}"` : undefined}>{statusWord(r)}</span>
      </td>
      {cols.map((c) => (
        <td key={c} className={OPTIONAL.find((o) => o.key === c)?.num ? "num" : undefined}>
          {c === "recovery" || c === "deductible" ? <Money r={r} k={c} /> : String(r[c] ?? "")}
        </td>
      ))}
      <td className="num"><Money r={r} k="paid" /></td>
      <td className="num"><Money r={r} k="reserve" /></td>
      <td className="num lr-c-inc"><Money r={r} k="incurred" /></td>
      <td className="lr-c-act">
        <RowMenu label={`Actions for claim ${r.claim_number}`} items={rowMenu(r, act)} />
      </td>
    </tr>
  );
}

function HistoryLine({ h, act, cols }: { h: ClaimRow; act: RowActions; cols: Optional[] }) {
  return (
    <tr className="lr-row lr-history" style={{ height: ROW_H }}>
      <td />
      <td colSpan={3 + cols.length}>
        <span className="lr-history__label">
          Older valuation, {h.filename?.replace(/\.(pdf|xlsx|csv)$/i, "")}
          {h.role_reason ? `. ${h.role_reason}` : ""}
        </span>{" "}
        {h.chip && <LossChip chip={h.chip} onOpen={(c) => act.openChip(c, false)} />}
      </td>
      <td className="num">{usd(h.effective.paid)}</td>
      <td className="num">{usd(h.effective.reserve)}</td>
      <td className="num lr-c-inc">{usd(h.effective.incurred)}</td>
      <td />
    </tr>
  );
}

/** The quick filters (claim level, effective values, D10) with the server's counts (`filter_counts`). */
export function QuickFilters({ review, query, setQuery }: { review: LossReview; query: TableQuery; setQuery: (q: TableQuery) => void }) {
  const counts = review.filter_counts ?? null;
  const [q, setQ] = useState(query.q);
  useEffect(() => {
    const t = window.setTimeout(() => q !== query.q && setQuery({ ...query, q }), 250);
    return () => window.clearTimeout(t);
  }, [q, query, setQuery]);
  return (
    <div className="lr-filters" role="group" aria-label="Quick filters">
      {QUICK_FILTERS.map((f) => {
        const on = query.quick.includes(f.key);
        return (
          <button
            key={f.key}
            type="button"
            className="lr-filter"
            aria-pressed={on}
            onClick={() => setQuery({ ...query, quick: on ? query.quick.filter((k) => k !== f.key) : [...query.quick, f.key] })}
          >
            <Check size={14} weight="bold" aria-hidden className="lr-filter__tick" />
            {f.label}
            {counts && <span className="count">{counts[f.key].toLocaleString("en-US")}</span>}
          </button>
        );
      })}
      <label className="lr-find">
        <MagnifyingGlass size={16} aria-hidden />
        <span className="sr-only">Find a claim</span>
        <input type="search" placeholder="Claim number or claimant" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} />
      </label>
    </div>
  );
}

function ColumnPicker({ cols, setCols }: { cols: Optional[]; setCols: (c: Optional[]) => void }) {
  return (
    <details className="lr-cols">
      <summary className="btn btn--ghost btn--sm">
        <Columns size={16} aria-hidden /> Columns {7 + cols.length} of {7 + OPTIONAL.length}
      </summary>
      <fieldset className="lr-cols__menu">
        <legend className="sr-only">More columns</legend>
        {OPTIONAL.map((o) => (
          <label key={o.key} className="ccheck">
            <input type="checkbox" checked={cols.includes(o.key)} onChange={(e) => setCols(e.target.checked ? [...cols, o.key] : cols.filter((c) => c !== o.key))} />
            {o.label}
          </label>
        ))}
      </fieldset>
    </details>
  );
}

const SORTABLE: { key: SortKey; label: string; num?: boolean; cls?: string }[] = [
  { key: "claim_number", label: "Claim" },
  { key: "claimant", label: "Claimant" },
  { key: "date_of_loss", label: "Loss date", num: true, cls: "lr-c-date" },
  { key: "status", label: "Status" },
];

function SortHead({ col, query, setQuery }: { col: { key: SortKey; label: string; num?: boolean; cls?: string }; query: TableQuery; setQuery: (q: TableQuery) => void }) {
  const on = query.sort === col.key;
  return (
    <th scope="col" className={`${col.num ? "num " : ""}${col.cls ?? ""}`} aria-sort={on ? (query.dir === "asc" ? "ascending" : "descending") : undefined}>
      <button type="button" className="lr-sort" onClick={() => setQuery({ ...query, sort: col.key, dir: on && query.dir === "desc" ? "asc" : "desc" })}>
        {col.label}
        {on && (query.dir === "asc" ? <CaretUp size={12} aria-hidden /> : <CaretDown size={12} aria-hidden />)}
      </button>
    </th>
  );
}

/** Counted totals: the register's own footing row, each figure under its column (direction A). */
function TotalsRow({ review, cols, recounting }: { review: LossReview; cols: Optional[]; recounting: boolean }) {
  const t = review.totals;
  if (!t) return null;
  const pick = (k: "paid" | "reserve" | "incurred" | "recovery" | "deductible") => (
    <span className={recounting ? "lr-recounting" : undefined}>{usd(t[k].cents)}</span>
  );
  return (
    <tr className="lr-footing">
      <th scope="row" colSpan={4} className="lr-footing__label">
        <span className="lr-footing__name">
          Counted totals{" "}
          {recounting ? (
            <span className="lr-recount" role="status">Recounting...</span>
          ) : (
            t.unreconciled && <span className="lr-unrec" title="A printed total does not tie yet">Unreconciled</span>
          )}
        </span>
        <span className="lr-footing__sub">
          {t.claims.toLocaleString("en-US")} claims, {t.open_claims.toLocaleString("en-US")} open{t.as_of ? `, valued as of ${day(t.as_of)}` : ""}
        </span>
      </th>
      {cols.map((c) => (
        <td key={c} className="num">{c === "recovery" || c === "deductible" ? pick(c) : ""}</td>
      ))}
      <td className="num">{pick("paid")}</td>
      <td className="num">{pick("reserve")}</td>
      <td className="num lr-c-inc">{pick("incurred")}</td>
      <td />
    </tr>
  );
}

/** A check's reconciliation as a footing under the rows it covers (owner: B's footing in A). */
function ScopeFooting({ card, cols, openChip }: { card: CheckCard; cols: Optional[]; openChip: (c: LossChipData, scan: boolean) => void }) {
  const m = (k: string) => card.checks.find((c) => c.metric === k);
  const review = card.verdict === "needs_review";
  const cell = (k: string, f: "printed" | "computed" | "difference") => {
    const x = m(k);
    if (!x) return <td className="num" />;
    if (f !== "printed" && (review || x.computed == null)) return <td className="num lr-muted">{f === "computed" ? "Not checked" : ""}</td>;
    const v = x[f];
    return <td className={`num${f === "difference" && v ? " lr-diff" : ""}`}>{f === "difference" && !v ? "" : usd(v)}</td>;
  };
  const span = 4 + cols.length;
  const chip = card.lead.chips[0];
  return (
    <tfoot>
      <tr className="lr-tie">
        <th scope="row" colSpan={span}>Rows above add up to</th>
        {cell("paid", "computed")}
        {cell("reserve", "computed")}
        {cell("incurred", "computed")}
        <td />
      </tr>
      <tr className="lr-tie lr-tie--printed">
        <th scope="row" colSpan={span}>
          Printed {chip && <LossChip chip={chip} onOpen={(c) => openChip(c, false)} />}
        </th>
        {cell("paid", "printed")}
        {cell("reserve", "printed")}
        {cell("incurred", "printed")}
        <td />
      </tr>
      {card.verdict === "off" && (
        <tr className="lr-tie lr-tie--diff">
          <th scope="row" colSpan={span}>Printed minus rows</th>
          {cell("paid", "difference")}
          {cell("reserve", "difference")}
          {cell("incurred", "difference")}
          <td />
        </tr>
      )}
    </tfoot>
  );
}

type Item = { kind: "row"; i: number } | { kind: "hist"; row: ClaimRow; h: ClaimRow };

/**
 * The claims register (direction A): a window over server pages of 100 rows, fixed row height, sticky header with the
 * counted totals under the column names, quick filters with counts, server sort, folds for older valuations, every
 * row state, and a row menu for the broker's decisions. `version` changes after every action, so visible pages reload.
 */
export function ClaimsTable({
  runId,
  review,
  query,
  setQuery,
  version,
  act,
  scope,
  recounting,
  pending,
  narrow,
}: {
  runId: number;
  review: LossReview;
  query: TableQuery;
  setQuery: (q: TableQuery) => void;
  version: number;
  act: RowActions;
  scope: CheckCard | null;
  recounting: boolean;
  pending: Map<number, string>;
  narrow: boolean;
}) {
  const [cols, setCols] = useState<Optional[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [pages, setPages] = useState<Map<number, ClaimRow[]>>(new Map());
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(600);
  const [loadError, setLoadError] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const inflight = useRef(new Set<string>());
  const params = useMemo(
    () => ({
      sort: query.sort,
      dir: query.dir,
      q: query.q || null,
      ...Object.fromEntries(query.quick.map((k) => [k, true])),
      ...Object.fromEntries(Object.entries(query.scope ?? {}).map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : (v as string | number | boolean)])),
    }),
    [query],
  );
  const key = JSON.stringify(params);

  const fetchPage = useCallback(
    async (p: number, reset = false) => {
      const tag = `${key}|${version}|${p}`;
      if (inflight.current.has(tag)) return;
      inflight.current.add(tag);
      try {
        const page: ClaimsPage = await api.lossClaims(runId, { ...params, offset: p * PAGE, limit: PAGE });
        setTotal(page.total);
        setPages((prev) => {
          const next = reset ? new Map<number, ClaimRow[]>() : new Map(prev);
          next.set(p, page.rows);
          return next;
        });
        setLoadError(null);
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : "Couldn't load the claims. Retry.");
      } finally {
        inflight.current.delete(tag);
      }
    },
    [runId, key, version], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // New query: back to the top, first page. After an action (`version`), reload the pages in view in place.
  const lastKey = useRef(key);
  useEffect(() => {
    if (lastKey.current !== key) {
      lastKey.current = key;
      setTotal(null);
      setPages(new Map());
      if (wrap.current) wrap.current.scrollTop = 0;
      setScrollTop(0);
      void fetchPage(0, true);
      return;
    }
    const first = Math.floor(scrollTop / ROW_H / PAGE);
    const shown = [...new Set([first, Math.floor((scrollTop + height) / ROW_H / PAGE)])];
    setPages(new Map());
    shown.forEach((p, i) => void fetchPage(p, i === 0));
  }, [key, version]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => e && setHeight(e.contentRect.height));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const rowAt = (i: number): ClaimRow | undefined => pages.get(Math.floor(i / PAGE))?.[i % PAGE];
  const items: Item[] = useMemo(() => {
    const out: Item[] = [];
    for (let i = 0; i < (total ?? 0); i++) {
      out.push({ kind: "row", i });
      const r = rowAt(i);
      if (r && open.has(r.id)) for (const h of r.history ?? []) out.push({ kind: "hist", row: r, h });
    }
    return out;
  }, [total, pages, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const headH = 92; // two header rows: names and counted totals
  const start = Math.max(0, Math.floor((scrollTop - headH) / ROW_H) - OVERSCAN);
  const end = Math.min(items.length, Math.ceil((scrollTop + height) / ROW_H) + OVERSCAN);
  useEffect(() => {
    const need = new Set<number>();
    for (let k = start; k < end; k++) {
      const it = items[k];
      if (it?.kind === "row" && !rowAt(it.i)) need.add(Math.floor(it.i / PAGE));
    }
    need.forEach((p) => void fetchPage(p));
  }, [start, end, items]); // eslint-disable-line react-hooks/exhaustive-deps

  const colCount = 8 + cols.length;
  const loaded = total != null;
  const empty = loaded && total === 0;
  const quickNames = QUICK_FILTERS.filter((f) => query.quick.includes(f.key)).map((f) => f.label);

  return (
    <>
      <div className="lr-tabletools">
        <QuickFilters review={review} query={query} setQuery={setQuery} />
        {!narrow && <ColumnPicker cols={cols} setCols={setCols} />}
      </div>
      <div className="lr-tablewrap" ref={wrap} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)} tabIndex={0} aria-label="Claims table">
        <table className="lr-table" aria-rowcount={loaded ? (total ?? 0) + 2 : undefined}>
          <thead>
            <tr className="lr-head">
              {SORTABLE.map((c) => <SortHead key={c.key} col={c} query={query} setQuery={setQuery} />)}
              {cols.map((c) => {
                const o = OPTIONAL.find((x) => x.key === c)!;
                return <th key={c} scope="col" className={o.num ? "num" : undefined}>{o.label}</th>;
              })}
              <SortHead col={{ key: "paid", label: METRIC_LABEL.paid!, num: true }} query={query} setQuery={setQuery} />
              <SortHead col={{ key: "reserve", label: METRIC_LABEL.reserve!, num: true }} query={query} setQuery={setQuery} />
              <SortHead col={{ key: "claim_incurred", label: METRIC_LABEL.incurred!, num: true }} query={query} setQuery={setQuery} />
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
            <TotalsRow review={review} cols={cols} recounting={recounting} />
          </thead>
          <tbody>
            {start > 0 && <tr aria-hidden style={{ height: start * ROW_H }}><td colSpan={colCount} /></tr>}
            {items.slice(start, end).map((it, k) => {
              if (it.kind === "hist") return <HistoryLine key={`h${it.h.id}`} h={it.h} act={act} cols={cols} />;
              const r = rowAt(it.i);
              if (!r) {
                return (
                  <tr key={`s${it.i}`} className="lr-row lr-skel" style={{ height: ROW_H }} aria-hidden>
                    <td colSpan={colCount}><span /></td>
                  </tr>
                );
              }
              const prev = rowAt(it.i - 1);
              const first = !prev || prev.group_key !== r.group_key || prev.document_id !== r.document_id;
              return (
                <Fragment key={r.id}>
                  <ClaimLine
                    r={r}
                    first={first}
                    act={act}
                    cols={cols}
                    history={first ? (r.history?.length ?? 0) : 0}
                    open={open.has(r.id)}
                    pending={pending.get(r.id) ?? null}
                    onFold={() => setOpen((o) => {
                      const n = new Set(o);
                      if (n.has(r.id)) n.delete(r.id);
                      else n.add(r.id);
                      return n;
                    })}
                  />
                </Fragment>
              );
              void k;
            })}
            {end < items.length && <tr aria-hidden style={{ height: (items.length - end) * ROW_H }}><td colSpan={colCount} /></tr>}
            {!loaded && !loadError && (
              <tr className="lr-row lr-skel" style={{ height: ROW_H }}><td colSpan={colCount}><span /><span className="sr-only" role="status">Loading claims</span></td></tr>
            )}
            {empty && (
              <tr>
                <td colSpan={colCount} className="lr-empty">
                  {query.q
                    ? `No claim number or claimant matches "${query.q}".`
                    : quickNames.length
                      ? `No claim matches ${quickNames.join(" and ")}.${query.quick.includes("over_100k") ? " Over $100K counts a claim's incurred across all its lines." : ""}`
                      : "No claims in this view."}
                </td>
              </tr>
            )}
            {loadError && (
              <tr>
                <td colSpan={colCount} className="lr-empty" role="alert">
                  {loadError}{" "}
                  <button type="button" className="link-btn" onClick={() => void fetchPage(0, true)}>Retry</button>
                </td>
              </tr>
            )}
          </tbody>
          {scope && scope.kind === "total" && <ScopeFooting card={scope} cols={cols} openChip={act.openChip} />}
        </table>
      </div>
      <p className="lr-pager">
        {loaded
          ? `${(total ?? 0).toLocaleString("en-US")} ${total === 1 ? "line" : "lines"}${review.filter_counts && !query.quick.length && !query.scope && !query.q ? ` on ${review.filter_counts.all.toLocaleString("en-US")} claims` : ""}. Rows load as you scroll; older valuations fold under their claim.`
          : "Loading claims..."}
      </p>
    </>
  );
}
