/* Direction B, "Workpaper": the review read the way an auditor ticks and ties a carrier's loss run. A summary of
   files on top (the totals and the check index in one table), then one schedule per file with every printed total
   footed where the carrier printed it. Phase A comp only. */
import { Fragment, StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { CaretDown } from "@phosphor-icons/react";
import { ClaimLine, PhoneStatus } from "./parts";
import {
  crumbs, data, day, DoneGate, Header, hue, LChip, Shell, StateWord, Strip, usd,
  type Check, type Doc, type Row,
} from "./shared";

const totalsOf = (fid: string) => data.checks.filter((c) => c.kind === "total" && c.fid === fid);
const openOf = (fid: string) => data.checks.filter((c) => c.fid === fid && ["off", "needs_review", "unverified"].includes(c.verdict));
const conflictIn = (fid: string) => data.checks.find((c) => c.kind === "conflict" && c.options!.some((o) => o.fid === fid));
const rowsOf = (fid: string) => data.rows.filter((r) => r.fid === fid);

function inScope(c: Check, r: Row) {
  if (c.scope_kind === "policy_year") return String(r.policy_year) === c.scope_value;
  if (c.scope_kind === "policy_number") return r.policy_number === c.scope_value;
  if (c.scope_kind === "page_subtotal") return String(r.page) === c.scope_value;
  return true;
}

function fileResult(d: Doc) {
  const ts = totalsOf(d.fid);
  const open = openOf(d.fid);
  const cf = conflictIn(d.fid);
  const parts: Check[] = [...open.filter((c) => c.kind === "total"), ...(cf ? [cf] : []), ...open.filter((c) => c.kind === "unverified")];
  const ties = ts.filter((c) => c.verdict === "matches").length;
  return { ts, parts, ties };
}

function Summary({ go }: { go: (fid: string) => void }) {
  const t = data.totals;
  return (
    <table className="lr-table lrB-summary">
      <caption className="visually-hidden">Files in this review</caption>
      <thead>
        <tr className="lr-head">
          <th scope="col">File</th><th scope="col">Valued</th><th scope="col" className="num">Lines</th>
          <th scope="col">Printed totals</th><th scope="col" className="num">Counted incurred</th>
        </tr>
      </thead>
      <tbody>
        {data.docs.map((d) => {
          const { ts, parts, ties } = fileResult(d);
          const lines = rowsOf(d.fid);
          const counted = lines.filter((r) => r.counts).reduce((n, r) => n + (r.incurred ?? 0), 0);
          return (
            <tr key={d.fid} className="lr-row">
              <td>
                <a href={`#file-${d.fid}`} className="lrB-file" onClick={(e) => { e.preventDefault(); go(d.fid); }}>
                  <span className={`chip lr-chip hue-${hue(d.letter)}`}><span className="lr-chip__letter">{d.letter}</span>{d.short}</span>
                </a>
              </td>
              <td className="num">{d.valuation_date ? day(d.valuation_date) : <span className="lr-muted">No date</span>}</td>
              <td className="num">{lines.length}</td>
              <td className="lrB-result">
                {parts.map((c, i) => <StateWord key={i} c={c} />)}
                {ts.length > 0 && <span className="lrB-ties">{ties} of {ts.length} {ts.length === 1 ? "line ties" : "lines tie"}</span>}
                {ts.length === 0 && <span className="lr-muted">No totals printed</span>}
              </td>
              <td className="num">{counted ? usd(counted) : <span className="lr-muted">None counted</span>}</td>
            </tr>
          );
        })}
      </tbody>
      <tfoot>
        <tr className="lr-footing lrB-grand">
          <th scope="row" colSpan={3}>
            <span className="lr-footing__name">Counted totals <span className="lr-unrec">Unreconciled</span></span>
            <span className="lr-footing__sub">{t.claims} claims, {t.open} open. Paid {usd(t.paid)}, reserve {usd(t.reserve)}, recoveries {usd(t.recovery)} ({t.recovery_from} claims), deductibles {usd(t.deductible)} ({t.deductible_from} claims)</span>
          </th>
          <td />
          <td className="num lr-c-inc">{usd(t.incurred)}</td>
        </tr>
      </tfoot>
    </table>
  );
}

function Footing({ c }: { c: Check }) {
  const m = (k: string) => c.metrics!.find((x) => x.metric === k);
  const v = (k: string, f: "printed" | "computed" | "difference") => {
    const x = m(k);
    if (!x) return "";
    if (f !== "printed" && x.computed == null) return f === "computed" ? "Not checked" : "";
    return usd(x[f]);
  };
  const match = c.verdict === "matches";
  return (
    <>
      <tr className={`lrB-foot lrB-foot--${c.verdict}`}>
        <th scope="row" colSpan={4}>
          <span className="lrB-foot__label">{c.label}{c.scope_kind === "page_subtotal" ? `, page ${c.scope_value}` : ""}</span>
          <LChip letter={c.letter!} label={c.chip!} />
          {match && <StateWord c={c} />}
        </th>
        <td className="num">{v("paid", "printed")}</td><td className="num">{v("reserve", "printed")}</td><td className="num lr-c-inc">{v("incurred", "printed")}</td>
      </tr>
      {!match && (
        <tr className={`lrB-foot lrB-foot--sub lrB-foot--${c.verdict}`}>
          <th scope="row" colSpan={4}>
            <StateWord c={c} />
            <span className="lrB-foot__why">{c.verdict === "off" ? `The ${c.lines_in_scope} rows above add up to less.` : c.reason}</span>
          </th>
          <td className="num">{v("paid", "computed")}</td><td className="num">{v("reserve", "computed")}</td><td className="num">{v("incurred", "computed")}</td>
        </tr>
      )}
      {c.verdict === "off" && (
        <tr className="lrB-foot lrB-foot--sub lrB-foot--diff">
          <th scope="row" colSpan={4}>Difference</th>
          <td className="num lr-diff">{v("paid", "difference")}</td><td className="num lr-diff">{v("reserve", "difference")}</td><td className="num lr-diff">{v("incurred", "difference")}</td>
        </tr>
      )}
      {!match && (
        <tr className="lrB-acts"><td colSpan={7}>
          {c.verdict === "off" && <button type="button" className="btn btn--secondary btn--sm">Add a missed claim</button>}
          <button type="button" className="btn btn--ghost btn--sm">{c.verdict === "off" ? "Fix a row..." : "Show the rows on page " + c.scope_value}</button>
          <button type="button" className="btn btn--ghost btn--sm">{c.verdict === "off" ? "Accept the gap..." : "Accept with a note..."}</button>
        </td></tr>
      )}
    </>
  );
}

function Schedule({ d, open, toggle }: { d: Doc; open: boolean; toggle: () => void }) {
  const rows = rowsOf(d.fid);
  const ts = totalsOf(d.fid);
  const { parts, ties } = fileResult(d);
  const after = new Map<string, Check[]>();
  for (const c of ts) {
    const last = [...rows].reverse().find((r) => inScope(c, r)) ?? rows[rows.length - 1];
    after.set(last.id, [...(after.get(last.id) ?? []), c]);
  }
  const cf = conflictIn(d.fid);
  return (
    <section className="lrB-sched" id={`file-${d.fid}`} aria-labelledby={`h-${d.fid}`}>
      <header className="lrB-sched__head">
        <button type="button" className="ledger__fold" aria-expanded={open} onClick={toggle}>
          <CaretDown size={16} aria-hidden className={open ? "is-open" : ""} />
          <h2 id={`h-${d.fid}`} className="lrB-sched__title">{d.carrier}</h2>
        </button>
        <span className="lrB-sched__meta">
          <LChip letter={d.letter} label={d.short} /> {d.filename}, {d.format.toUpperCase()}, {d.valuation_date ? `valued ${day(d.valuation_date)}` : "no valuation date"}, {rows.length} lines
        </span>
        <span className="lrB-sched__state">
          {parts.length ? parts.map((c, i) => <StateWord key={i} c={c} />) : ts.length ? <span className="lrB-ties">{ties} of {ts.length} tie</span> : <span className="lr-muted">No totals printed</span>}
        </span>
      </header>
      {open && (
        <table className="lr-table lrB-table">
          <thead>
            <tr className="lr-head">
              <th scope="col">Claim</th><th scope="col">Claimant</th><th scope="col" className="num">Loss date</th><th scope="col">Status</th>
              <th scope="col" className="num">Paid</th><th scope="col" className="num">Reserve</th><th scope="col" className="num">Incurred</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <Fragment key={r.id}>
                <ClaimLine r={r} first={i === 0 || rows[i - 1].claim_key !== r.claim_key} muted={r.role === "superseded"} shortChip />
                {r.role === "superseded" && rows[i + 1]?.claim_key !== r.claim_key && (
                  <tr className="lr-row lr-history"><td /><td colSpan={6}><span className="lr-history__label">Replaced by a later valuation in another file; still counted in this file's printed totals.</span></td></tr>
                )}
                {cf && r.role === "conflicting" && (
                  <tr className="lrB-acts"><td colSpan={7}>
                    <span className="lrB-acts__lead">{cf.options!.map((o) => `${o.doc} says incurred ${usd(o.incurred)}`).join("; ")}.</span>
                    {cf.options!.map((o) => <button key={o.fid} type="button" className="btn btn--secondary btn--sm">Use {o.doc}</button>)}
                  </td></tr>
                )}
                {after.get(r.id)?.map((c, j) => <Footing key={j} c={c} />)}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function B() {
  const need = (d: Doc) => (fileResult(d).parts.length ? 0 : 1);
  const order = [...data.docs].sort((a, b) => need(a) - need(b));
  const [open, setOpen] = useState<Set<string>>(new Set(order.filter((d) => need(d) === 0).map((d) => d.fid)));
  const go = (fid: string) => { setOpen(new Set([...open, fid])); setTimeout(() => document.getElementById(`file-${fid}`)?.scrollIntoView({ block: "start" }), 0); };
  return (
    <Shell crumbs={crumbs}>
      <div className="workspace">
        <Strip />
        <main className="pane lr lrB" id="main">
          <Header action={<DoneGate />} />
          <div className="lrB__body">
            <PhoneStatus />
            <section className="lrB__summary" aria-labelledby="sum-title">
              <div className="lrB__summaryhead">
                <h2 id="sum-title" className="lr-h2">Files and totals</h2>
                <div className="lrB-view" role="radiogroup" aria-label="Show">
                  <button type="button" role="radio" aria-checked="true" className="lr-filter" >By file</button>
                  <button type="button" role="radio" aria-checked="false" className="lr-filter">All claims</button>
                </div>
              </div>
              <Summary go={go} />
              {data.notices.map((n) => <p key={n} className="lr-notice">{n}</p>)}
            </section>
            {order.map((d) => (
              <Schedule key={d.fid} d={d} open={open.has(d.fid)} toggle={() => { const n = new Set(open); n.has(d.fid) ? n.delete(d.fid) : n.add(d.fid); setOpen(n); }} />
            ))}
          </div>
        </main>
      </div>
    </Shell>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><B /></StrictMode>);
