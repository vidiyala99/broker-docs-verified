import { Fragment, useEffect, useState } from "react";
import { CaretDown } from "@phosphor-icons/react";
import { api } from "../api/client";
import type { ClaimRow, LossCheck, LossChip as LossChipData, LossDocumentSummary, LossReview } from "../api/types";
import { hueOf } from "../report/Chip";
import { keyboardAction } from "../ui/input";
import { Collapse } from "../ui/motion";
import { CardWord, LossChip } from "./Checks";
import { ClaimLine, type RowActions } from "./ClaimsTable";
import { checkCards, day, docName, groupOf, METRIC_LABEL, scopeLabel, usd, type CheckCard } from "./lossUtil";

const STEP = 200;

function inScope(c: LossCheck, r: ClaimRow): boolean {
  switch (c.scope_kind) {
    case "policy_year":
      return String(r.policy_year) === c.scope_value;
    case "policy_number":
      return (r.policy_number ?? "").replace(/\s+/g, " ").trim() === (c.scope_value ?? "").replace(/\s+/g, " ").trim();
    case "page_subtotal":
      return String(r.chip?.page_no) === c.scope_value;
    case "carrier":
      return (r.carrier ?? "") === (c.scope_value ?? "");
    default:
      return true;
  }
}

/** A printed total footed where the carrier printed it: printed figures, and for an open line its sum and difference. */
function Footing({ card, openChip, act }: { card: CheckCard; openChip: (c: LossChipData) => void; act: FileActions }) {
  const m = (k: string) => card.checks.find((c) => c.metric === k);
  const match = groupOf(card) === "matches";
  const v = (k: string, f: "printed" | "computed" | "difference") => {
    const x = m(k);
    if (!x) return "";
    if (f !== "printed" && (card.verdict === "needs_review" || x.computed == null)) return f === "computed" ? "Not checked" : "";
    return x[f] ? usd(x[f]) : f === "difference" ? "" : usd(x[f]);
  };
  const chip = card.lead.chips[0];
  return (
    <>
      <tr className={`lrB-foot lrB-foot--${match ? "matches" : card.verdict}`}>
        <th scope="row" colSpan={4}>
          <span className="lrB-foot__label">Printed: {scopeLabel(card.lead)}</span>
          {chip && <LossChip chip={chip} onOpen={openChip} />}
          {(match || groupOf(card) === "handled") && <CardWord card={card} />}
        </th>
        <td className="num">{v("paid", "printed")}</td>
        <td className="num">{v("reserve", "printed")}</td>
        <td className="num lr-c-inc">{v("incurred", "printed")}</td>
        <td />
      </tr>
      {!match && groupOf(card) === "to_resolve" && (
        <>
          <tr className={`lrB-foot lrB-foot--sub lrB-foot--${card.verdict}`}>
            <th scope="row" colSpan={4}>
              <CardWord card={card} /> <span className="lrB-foot__why">{card.verdict === "off" ? "Rows above add up to" : card.lead.message}</span>
            </th>
            <td className="num">{v("paid", "computed")}</td>
            <td className="num">{v("reserve", "computed")}</td>
            <td className="num">{v("incurred", "computed")}</td>
            <td />
          </tr>
          {card.verdict === "off" && (
            <tr className="lrB-foot lrB-foot--sub lrB-foot--diff">
              <th scope="row" colSpan={4}>Printed minus rows</th>
              <td className="num lr-diff">{v("paid", "difference")}</td>
              <td className="num lr-diff">{v("reserve", "difference")}</td>
              <td className="num lr-diff">{v("incurred", "difference")}</td>
              <td />
            </tr>
          )}
          <tr className="lrB-acts">
            <td colSpan={8}>
              {card.verdict === "off" && (
                <button type="button" className="btn btn--secondary btn--sm" onClick={() => act.addClaim(card.lead.document_id)}>Add a missed claim</button>
              )}
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => act.toChecks(card)}>
                {card.verdict === "off" ? "Accept the gap..." : "Accept with a note..."}
              </button>
            </td>
          </tr>
        </>
      )}
    </>
  );
}

export interface FileActions {
  addClaim: (documentId: number | null) => void;
  /** Accept lives on the check card (one place for the note editor): go to it. */
  toChecks: (card: CheckCard) => void;
}

function Schedule({ runId, review, doc, cards, open, toggle, version, act, rowAct, pending }: {
  runId: number;
  review: LossReview;
  doc: LossDocumentSummary;
  cards: CheckCard[];
  open: boolean;
  toggle: () => void;
  version: number;
  act: FileActions;
  rowAct: RowActions;
  pending: Map<number, string>;
}) {
  const [rows, setRows] = useState<ClaimRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(STEP);
  useEffect(() => {
    if (!open) return;
    let live = true;
    api.lossClaims(runId, { document_id: doc.document_id, include_superseded: true, sort: "row", dir: "asc", limit: Math.min(limit, 200), offset: 0 }).then(async (first) => {
      let all = first.rows;
      for (let off = 200; off < Math.min(limit, first.total); off += 200) {
        const more = await api.lossClaims(runId, { document_id: doc.document_id, include_superseded: true, sort: "row", dir: "asc", limit: 200, offset: off });
        all = all.concat(more.rows);
      }
      if (live) {
        setRows(all);
        setTotal(first.total);
      }
    }, () => live && setRows([]));
    return () => {
      live = false;
    };
  }, [open, runId, doc.document_id, version, limit]);
  const totals = cards.filter((c) => c.kind === "total");
  const after = new Map<number, CheckCard[]>();
  if (rows && rows.length) {
    for (const card of totals) {
      const last = [...rows].reverse().find((r) => r.source === "extracted" && inScope(card.lead, r)) ?? rows[rows.length - 1]!;
      after.set(last.id, [...(after.get(last.id) ?? []), card]);
    }
  }
  const open_ = cards.filter((c) => groupOf(c) === "to_resolve");
  const ties = totals.filter((c) => groupOf(c) === "matches").length;
  return (
    <section className="lrB-sched" aria-labelledby={`lrf-${doc.document_id}`} data-file={doc.document_id}>
      <header className="lrB-sched__head">
        <button type="button" className="ledger__fold" aria-expanded={open} onClick={toggle}>
          <CaretDown size={16} aria-hidden className={open ? "is-open" : ""} />
          <h3 id={`lrf-${doc.document_id}`} className="lrB-sched__title">{doc.carrier ?? doc.filename}</h3>
        </button>
        <span className="lrB-sched__meta">
          <span className={`chip lr-chip hue-${hueOf(doc.letter)} lr-chip--static`}><span className="lr-chip__letter">{doc.letter}</span> {docName(review, doc.document_id)}</span>
          {doc.valuation_date ? `valued ${day(doc.valuation_date)}` : "no valuation date"}, {doc.rows.toLocaleString("en-US")} lines
        </span>
        <span className="lrB-sched__state">
          {open_.map((c) => <CardWord key={c.key} card={c} />)}
          {totals.length ? <span className="lrB-ties">{ties} of {totals.length} printed {totals.length === 1 ? "line ties" : "lines tie"}</span> : <span className="lr-muted">No totals printed</span>}
        </span>
      </header>
      <Collapse open={open} animate={!keyboardAction()}>
        {open && (
          <div className="lrB-tablewrap">
            <table className="lr-table lrB-table">
              <thead>
                <tr className="lr-head">
                  <th scope="col">Claim</th>
                  <th scope="col">Claimant</th>
                  <th scope="col" className="num lr-c-date">Loss date</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="num">{METRIC_LABEL.paid}</th>
                  <th scope="col" className="num">{METRIC_LABEL.reserve}</th>
                  <th scope="col" className="num">{METRIC_LABEL.incurred}</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {rows == null && <tr className="lr-row lr-skel"><td colSpan={8}><span /></td></tr>}
                {rows?.map((r, i) => (
                  <Fragment key={r.id}>
                    <ClaimLine r={r} first={i === 0 || rows[i - 1]!.claim_key !== r.claim_key} act={rowAct} cols={[]} pending={pending.get(r.id) ?? null} />
                    {after.get(r.id)?.map((c) => <Footing key={c.key} card={c} openChip={(ch) => rowAct.openChip(ch, false)} act={act} />)}
                  </Fragment>
                ))}
              </tbody>
            </table>
            {rows && total > rows.length && (
              <p className="lr-pager">
                {rows.length.toLocaleString("en-US")} of {total.toLocaleString("en-US")} lines shown; printed totals below them appear as their rows load.{" "}
                <button type="button" className="link-btn" onClick={() => setLimit(limit + STEP * 5)}>Show {Math.min(STEP * 5, total - rows.length).toLocaleString("en-US")} more</button>
              </p>
            )}
          </div>
        )}
      </Collapse>
    </section>
  );
}

/** By file (owner, 2026-11-04: B's footing as a view of the same page): one schedule per file, need first. */
export function ByFile({ runId, review, version, act, rowAct, pending }: {
  runId: number;
  review: LossReview;
  version: number;
  act: FileActions;
  rowAct: RowActions;
  pending: Map<number, string>;
}) {
  const docs = review.totals?.documents ?? [];
  const cards = checkCards(review.checks);
  const cardsOf = (id: number) => cards.filter((c) => c.lead.document_id === id || c.lead.options.some((o) => o.document_id === id));
  const needs = (id: number) => cardsOf(id).some((c) => groupOf(c) === "to_resolve");
  const order = [...docs].sort((a, b) => Number(needs(b.document_id)) - Number(needs(a.document_id)));
  const [open, setOpen] = useState<Set<number>>(() => new Set(order.filter((d) => needs(d.document_id)).map((d) => d.document_id)));
  return (
    <div className="lrB">
      {order.map((d) => (
        <Schedule
          key={d.document_id}
          runId={runId}
          review={review}
          doc={d}
          cards={cardsOf(d.document_id)}
          open={open.has(d.document_id)}
          toggle={() => setOpen((o) => {
            const n = new Set(o);
            if (n.has(d.document_id)) n.delete(d.document_id);
            else n.add(d.document_id);
            return n;
          })}
          version={version}
          act={act}
          rowAct={rowAct}
          pending={pending}
        />
      ))}
    </div>
  );
}
