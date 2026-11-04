/* Pieces shared by the three direction comps: the claims table, the check bodies, the totals. Phase A only. */
import { Fragment, useState } from "react";
import { CaretDown, CaretRight, Check as CheckGlyph, Columns, MagnifyingGlass } from "@phosphor-icons/react";
import {
  checkTitle, data, day, docOf, groups, LChip, QUICK, rowStates, StateWord, usd,
  type Check, type Group, type Row,
} from "./shared";

/* ---------- quick filters (claim level, effective values, D10) ---------- */
export function QuickFilters({ on, setOn }: { on: string[]; setOn: (k: string[]) => void }) {
  const all = groups();
  return (
    <div className="lr-filters" role="group" aria-label="Quick filters">
      {QUICK.map((q) => {
        const pressed = on.includes(q.key);
        const n = all.filter(q.test).length;
        return (
          <button key={q.key} type="button" className="lr-filter" aria-pressed={pressed} onClick={() => setOn(pressed ? on.filter((k) => k !== q.key) : [...on, q.key])}>
            <CheckGlyph size={14} weight="bold" aria-hidden className="lr-filter__tick" />
            {q.label}
            <span className="count">{n}</span>
          </button>
        );
      })}
      <span className="lr-filters__end">
        <label className="lr-find">
          <MagnifyingGlass size={16} aria-hidden />
          <span className="visually-hidden">Find a claim</span>
          <input placeholder="Claim number or claimant" />
        </label>
        <button type="button" className="btn btn--ghost btn--sm"><Columns size={16} aria-hidden /> Columns 8 of 12</button>
      </span>
    </div>
  );
}

/* ---------- the claims table ---------- */
export function ClaimLine({ r, first, g, open, onFold, showSource = true, muted = false, shortChip = false }: { r: Row; first: boolean; g?: Group; open?: boolean; onFold?: () => void; showSource?: boolean; muted?: boolean; shortChip?: boolean }) {
  const states = rowStates(r);
  const out = !r.counts;
  return (
    <tr className={`lr-row${out ? " is-out" : ""}${muted ? " is-muted" : ""}${first ? "" : " is-cont"}${r.role === "conflicting" ? " is-conflict" : ""}`}>
      <td className="lr-c-claim">
        {first ? (
          <span className="lr-claimno">
            {g && g.history.length > 0 ? (
              <button type="button" className="lr-fold" aria-expanded={open} aria-label={`${open ? "Hide" : "Show"} older valuations of ${r.claim_number}`} onClick={onFold}>
                {open ? <CaretDown size={14} /> : <CaretRight size={14} />}
              </button>
            ) : <span className="lr-fold-space" />}
            {r.claim_number}
          </span>
        ) : <span className="lr-claimno lr-claimno--cont"><span className="lr-fold-space" />{r.claim_number}</span>}
      </td>
      <td className="lr-c-who">
        <span className="lr-who" title={r.claimant}>{r.claimant}</span>
        {(showSource || states.length > 0) && (
          <span className="lr-states">
            {showSource && <LChip letter={r.letter} label={muted !== undefined && shortChip ? r.chip.replace(/^.* (p\.\d+)$/, "$1") : r.chip} scan={r.scan} />}
            {states.map((s, i) => (
              <span key={i} className={`lr-state lr-state--${s.key}`} title={s.tip}>
                <span aria-hidden>{s.icon}</span>{s.word}
              </span>
            ))}
          </span>
        )}
      </td>
      <td className="lr-c-date num">{day(r.date_of_loss)}</td>
      <td className="lr-c-status"><span className="status-word lr-status" title={`Printed "${r.status_raw}"`}>{r.status === "unknown" ? r.status_raw : r.status}</span></td>
      <td className="num">{usd(r.paid)}</td>
      <td className="num">{usd(r.reserve)}</td>
      <td className="num lr-c-inc">{usd(r.incurred)}</td>
    </tr>
  );
}

export function HistoryRows({ g }: { g: Group }) {
  const byDoc = new Map<string, Row[]>();
  g.history.forEach((r) => byDoc.set(r.fid, [...(byDoc.get(r.fid) ?? []), r]));
  return (
    <>
      {[...byDoc.entries()].map(([fid, rs]) => {
        const d = docOf(fid);
        const sum = (k: "paid" | "reserve" | "incurred") => rs.reduce((n, r) => n + (r[k] ?? 0), 0);
        return (
          <tr key={fid} className="lr-row lr-history">
            <td />
            <td colSpan={3}>
              <span className="lr-history__label">
                {rs[0].verify === "verified" && rs.length > 0 ? "Older valuation" : "Same valuation"}, {d.short}
                {d.valuation_date ? `, valued ${day(d.valuation_date)}` : ", no valuation date"}
                {rs.length > 1 ? `, ${rs.length} lines` : ""}
              </span>{" "}
              <LChip letter={rs[0].letter} label={rs[0].chip} />
            </td>
            <td className="num">{usd(sum("paid"))}</td>
            <td className="num">{usd(sum("reserve"))}</td>
            <td className="num lr-c-inc">{usd(sum("incurred"))}</td>
          </tr>
        );
      })}
    </>
  );
}

export function TotalsRow({ cols = 8 }: { cols?: number }) {
  const t = data.totals;
  return (
    <tr className="lr-footing">
      <th scope="row" colSpan={4} className="lr-footing__label">
        <span className="lr-footing__name">Counted totals <span className="lr-unrec" title="2 printed totals do not tie yet">Unreconciled</span></span>
        <span className="lr-footing__sub">{t.claims} claims, {t.open} open, valued as of {day(t.as_of)}</span>
      </th>
      <td className="num">{usd(t.paid)}</td>
      <td className="num">{usd(t.reserve)}</td>
      <td className="num lr-c-inc">{usd(t.incurred)}</td>
    </tr>
  );
}

export function ClaimsTable({ quick, scope, sticky = true, head }: { quick: string[]; scope?: Check | null; sticky?: boolean; head?: React.ReactNode }) {
  const [open, setOpen] = useState<Set<string>>(new Set(["Driftwood Specialty Insurance Company|DS-24-0101"]));
  let gs = groups();
  for (const q of QUICK) if (quick.includes(q.key)) gs = gs.filter(q.test);
  let rows: Row[] | null = null;
  if (scope?.fid) {
    rows = data.rows.filter((r) => r.fid === scope.fid && (scope.scope_kind !== "page_subtotal" || String(r.page) === scope.scope_value) && (scope.kind !== "unverified" || r.verify === "unverified"));
  }
  return (
    <div className={`lr-tablewrap${sticky ? " is-sticky" : ""}`}>
      <table className="lr-table">
        <thead>
          <tr className="lr-head">
            <th scope="col">Claim</th>
            <th scope="col">Claimant</th>
            <th scope="col" className="num">Loss date</th>
            <th scope="col">Status</th>
            <th scope="col" className="num">Paid</th>
            <th scope="col" className="num">Reserve</th>
            <th scope="col" className="num" aria-sort="descending">Incurred <CaretDown size={12} aria-hidden /></th>
          </tr>
          {head ?? <TotalsRow />}
        </thead>
        <tbody>
          {rows
            ? rows.map((r, i) => <ClaimLine key={r.id} r={r} first={i === 0 || rows![i - 1].claim_key !== r.claim_key} />)
            : gs.length === 0
              ? (
                <tr><td colSpan={7} className="lr-empty">No claim is over $100,000 incurred. The largest is {usd(groups()[0].incurred)} ({groups()[0].lines[0].claim_number}).</td></tr>
              )
              : gs.map((g) => (
                <Fragment key={g.key}>
                  {g.lines.map((r, i) => (
                    <ClaimLine key={r.id} r={r} g={g} first={i === 0 || g.lines[i - 1].fid !== r.fid} open={open.has(g.key)}
                      onFold={() => { const n = new Set(open); n.has(g.key) ? n.delete(g.key) : n.add(g.key); setOpen(n); }} />
                  ))}
                  {open.has(g.key) && <HistoryRows g={g} />}
                </Fragment>
              ))}
        </tbody>
        {scope && scope.metrics && <ScopeFooting c={scope} />}
      </table>
    </div>
  );
}

/* The reconciliation as a footing under the rows in scope (carried from direction B into A). */
export function ScopeFooting({ c }: { c: Check }) {
  const m = (k: string) => c.metrics!.find((x) => x.metric === k);
  const cell = (k: string, f: "printed" | "computed" | "difference") => {
    const x = m(k);
    if (!x) return <td className="num" />;
    if (f !== "printed" && x.computed == null) return <td className="num lr-muted">{f === "computed" ? "Not checked" : ""}</td>;
    const v = x[f];
    return <td className={`num${f === "difference" && v ? " lr-diff" : ""}`}>{f === "difference" ? (v ? usd(v) : "None") : usd(v)}</td>;
  };
  return (
    <tfoot>
      <tr className="lr-tie">
        <th scope="row" colSpan={4}>Rows above add up to</th>
        {cell("paid", "computed")}{cell("reserve", "computed")}{cell("incurred", "computed")}
      </tr>
      <tr className="lr-tie lr-tie--printed">
        <th scope="row" colSpan={4}>Printed: {c.label} <LChip letter={c.letter!} label={c.chip!} /></th>
        {cell("paid", "printed")}{cell("reserve", "printed")}{cell("incurred", "printed")}
      </tr>
      {c.verdict === "off" && (
        <tr className="lr-tie lr-tie--diff">
          <th scope="row" colSpan={4}><StateWord c={c} /></th>
          {cell("paid", "difference")}{cell("reserve", "difference")}{cell("incurred", "difference")}
        </tr>
      )}
    </tfoot>
  );
}

/* ---------- check bodies ---------- */
export function Recon({ c }: { c: Check }) {
  return (
    <table className="lr-recon">
      <thead>
        <tr><th scope="col"><span className="visually-hidden">Total</span></th><th scope="col" className="num">Printed</th><th scope="col" className="num">Rows add up to</th>{c.verdict === "off" && <th scope="col" className="num">Off by</th>}</tr>
      </thead>
      <tbody>
        {c.metrics!.map((m) => (
          <tr key={m.metric}>
            <th scope="row">{m.metric[0].toUpperCase() + m.metric.slice(1)}</th>
            <td className="num">{usd(m.printed)}</td>
            <td className={`num${m.computed == null ? " lr-muted" : ""}`}>{m.computed == null ? "Not checked" : usd(m.computed)}</td>
            {c.verdict === "off" && <td className={`num${m.difference ? " lr-diff" : ""}`}>{usd(m.difference)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function CheckBody({ c, onShow, actions = true }: { c: Check; onShow?: () => void; actions?: boolean }) {
  return (
    <>
      <div className="claim__head"><StateWord c={c} /></div>
      <p className="claim__title">{checkTitle(c)}</p>
      {c.kind === "total" && (
        <>
          <Recon c={c} />
          <p className="claim__reason">
            {c.verdict === "off"
              ? `The printed total is higher than the ${c.lines_in_scope} rows it covers: a claim may be missing from the rows. `
              : c.verdict === "needs_review"
                ? `${c.reason} `
                : ""}
            <LChip letter={c.letter!} label={c.chip!} />
          </p>
          {actions && c.verdict !== "matches" && (
            <div className="cact__row lr-acts">
              <button type="button" className="btn btn--secondary btn--sm" onClick={onShow}>
                {c.verdict === "off" ? `Show the ${c.lines_in_scope} rows` : `Show the rows on page ${c.scope_value}`}
              </button>
              <span className="cact__quiet">
                {c.verdict === "off" && <button type="button" className="btn btn--ghost btn--sm">Add a missed claim</button>}
                <button type="button" className="btn btn--ghost btn--sm">{c.verdict === "off" ? "Accept the gap..." : "Accept with a note..."}</button>
              </span>
            </div>
          )}
        </>
      )}
      {c.kind === "conflict" && (
        <>
          <p className="claim__reason">{c.claimant}. Both files are valued {day(c.options![0].valuation_date)}; out of totals until you pick one.</p>
          <ul className="lr-options">
            {c.options!.map((o) => (
              <li key={o.fid} className="lr-option">
                <LChip letter={o.letter} label={`${o.doc}, ${o.chip.toLowerCase()}`} />
                <dl>
                  <div><dt>Paid</dt><dd>{usd(o.paid)}</dd></div>
                  <div className={o.reserve !== c.options![0].reserve || o.reserve !== c.options![1].reserve ? "is-diff" : ""}><dt>Reserve</dt><dd>{usd(o.reserve)}</dd></div>
                  <div className={c.options![0].incurred !== c.options![1].incurred ? "is-diff" : ""}><dt>Incurred</dt><dd>{usd(o.incurred)}</dd></div>
                </dl>
                {actions && <button type="button" className="btn btn--secondary btn--sm">Use {o.doc}</button>}
              </li>
            ))}
          </ul>
        </>
      )}
      {c.kind === "unverified" && c.verdict === "unverified" && (
        <>
          <p className="claim__reason">Read from a scan, so the amounts cannot be matched to printed words. They stay out of totals until you confirm, fix or leave out each row.</p>
          {actions && (
            <div className="cact__row lr-acts">
              <button type="button" className="btn btn--secondary btn--sm" onClick={onShow}>Show the {c.rows} rows</button>
            </div>
          )}
        </>
      )}
      {c.verdict === "handled" && (
        <>
          <p className="claim__note"><span className="claim__k">Your note</span>{c.note}</p>
          {actions && <div className="cact__row lr-acts"><span className="cact__quiet"><button type="button" className="btn btn--ghost btn--sm">Undo confirm</button></span></div>}
        </>
      )}
    </>
  );
}

export { data };

/* Phone: status only (PRODUCT.md). The totals as a short list; the review work waits for a wider screen. */
export function PhoneStatus() {
  const t = data.totals;
  return (
    <section className="lr-phone" aria-label="Totals">
      <h2 className="lr-h2">Totals</h2>
      <dl>
        <div><dt>Claims</dt><dd>{t.claims}<small>{t.open} open</small></dd></div>
        <div><dt>Incurred</dt><dd>{usd(t.incurred)}<small>from {t.incurred_from} claims</small></dd></div>
        <div><dt>Paid</dt><dd>{usd(t.paid)}</dd></div>
        <div><dt>Reserve</dt><dd>{usd(t.reserve)}</dd></div>
        <div><dt>Recoveries</dt><dd>{usd(t.recovery)}<small>from {t.recovery_from} claims</small></dd></div>
        <div><dt>Deductibles</dt><dd>{usd(t.deductible)}<small>from {t.deductible_from} claims</small></dd></div>
      </dl>
      <p className="hint">Unreconciled: 2 printed totals do not tie yet. The claims table and the fixes open on a wider screen.</p>
    </section>
  );
}
