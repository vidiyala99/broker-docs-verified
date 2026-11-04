/* Direction C, "Exceptions desk": the review opens on its exceptions. Tabs like the real product's Table view
   (Checks, Claims, Files); each open check is a case that sets the printed line, as paper, beside the rows it covers.
   The full claims table is its own tab, paged 50 rows at a time. Phase A comp only. */
import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { CaretDown, CaretLeft, CaretRight, Scan } from "@phosphor-icons/react";
import { ClaimsTable, PhoneStatus, QuickFilters } from "./parts";
import {
  checkTitle, crumbs, data, day, docOf, DoneGate, handled, Header, LChip, matches, matchTotals, rowStates, Shell, StateWord, Strip, toResolve, usd,
  type Check, type Row,
} from "./shared";

function Statement() {
  const t = data.totals;
  const cells: [string, string, string][] = [
    ["Claims", String(t.claims), `${t.open} open`],
    ["Incurred", usd(t.incurred), `from ${t.incurred_from} claims`],
    ["Paid", usd(t.paid), `from ${t.paid_from} claims`],
    ["Reserve", usd(t.reserve), `from ${t.reserve_from} claims`],
    ["Recoveries", usd(t.recovery), `from ${t.recovery_from} claims`],
    ["Deductibles", usd(t.deductible), `from ${t.deductible_from} claims`],
  ];
  return (
    <section className="lrC-statement" aria-label="Counted totals">
      <p className="lrC-statement__lead">
        Counted as of {day(t.as_of)} <span className="lr-unrec">Unreconciled</span>
      </p>
      <dl className="lrC-statement__list">
        {cells.map(([k, v, s]) => (
          <div key={k}><dt>{k}</dt><dd>{v}</dd><dd className="lrC-statement__from">{s}</dd></div>
        ))}
      </dl>
    </section>
  );
}

/* The printed line drawn as a strip of the carrier's paper, the cited figures boxed like the viewer's passage box. */
function Printed({ c, label }: { c: Check; label?: string }) {
  const d = docOf(c.fid!);
  return (
    <figure className="lrC-paper">
      <figcaption className="lrC-paper__cap">{label ?? "As printed"}, {d.carrier}{c.scope_kind === "page_subtotal" ? `, page ${c.scope_value}` : ""}</figcaption>
      <div className="lrC-paper__sheet">
        <span className="lrC-paper__label">{c.label}</span>
        {c.metrics!.map((m) => (
          <span key={m.metric} className="lrC-paper__fig"><small>{m.metric[0].toUpperCase() + m.metric.slice(1)}</small><span className="lrC-box">{usd(m.printed)}</span></span>
        ))}
      </div>
      <LChip letter={c.letter!} label={c.chip!} />
    </figure>
  );
}

function RowsSum({ rows, c }: { rows: Row[]; c: Check }) {
  const inc = c.metrics!.find((m) => m.metric === "incurred")!;
  return (
    <div className="lrC-rows">
      <p className="lrC-rows__cap">From the rows: {rows.length} lines in scope</p>
      <ul>
        {rows.map((r) => (
          <li key={r.id}>
            <span>{r.claim_number}</span>
            <span className="lrC-rows__who">{r.claimant}{rowStates(r).map((s, i) => <span key={i} className={`lr-state lr-state--${s.key}`}> {s.icon}{s.word}</span>)}</span>
            <span className="num">{usd(r.incurred)}</span>
          </li>
        ))}
      </ul>
      <p className="lrC-rows__sum"><span>Rows add up to</span><span className="num">{inc.computed == null ? "Not checked" : usd(inc.computed)}</span></p>
      {c.verdict === "off" && <p className="lrC-rows__sum lrC-rows__diff"><span>Printed minus rows</span><span className="num">{usd(inc.difference)}</span></p>}
    </div>
  );
}

function Case({ c }: { c: Check }) {
  const rows = c.fid ? data.rows.filter((r) => r.fid === c.fid && (c.scope_kind !== "page_subtotal" || String(r.page) === c.scope_value)) : [];
  return (
    <li className={`lrC-case lr-item--${c.verdict}`}>
      <div className="lrC-case__head">
        <StateWord c={c} />
        <h3 className="lrC-case__title">{checkTitle(c)}</h3>
      </div>
      {c.kind === "total" && (
        <>
          <div className="lrC-case__pair">
            <Printed c={c} />
            <RowsSum rows={rows} c={c} />
          </div>
          <p className="claim__reason">{c.verdict === "off" ? "Paid is off by $4,150.00 and reserve by $8,250.00 too: one claim of that size may be missing from the rows." : c.reason}</p>
          <div className="lrC-acts">
            {c.verdict === "off" && <button type="button" className="btn btn--secondary btn--sm">Add a missed claim</button>}
            <button type="button" className="btn btn--ghost btn--sm">Fix a row...</button>
            <button type="button" className="btn btn--ghost btn--sm">{c.verdict === "off" ? "Accept the gap..." : "Accept with a note..."}</button>
          </div>
        </>
      )}
      {c.kind === "conflict" && (
        <>
          <p className="claim__reason">{c.claimant}. Both files are valued {day(c.options![0].valuation_date)}. Pick the one that counts; the other stays as history.</p>
          <div className="lrC-case__pair">
            {c.options!.map((o) => (
              <figure key={o.fid} className="lrC-paper">
                <figcaption className="lrC-paper__cap">As printed, {o.doc}</figcaption>
                <div className="lrC-paper__sheet">
                  <span className="lrC-paper__label">{c.claim_number}, {o.status_raw}</span>
                  <span className="lrC-paper__fig"><small>Paid</small>{usd(o.paid)}</span>
                  <span className="lrC-paper__fig"><small>Reserve</small><span className={o.reserve !== c.options![0].reserve || o.reserve !== c.options![1].reserve ? "lrC-box" : ""}>{usd(o.reserve)}</span></span>
                  <span className="lrC-paper__fig"><small>Incurred</small><span className="lrC-box">{usd(o.incurred)}</span></span>
                </div>
                <span className="lrC-paper__foot"><LChip letter={o.letter} label={o.chip} /><button type="button" className="btn btn--secondary btn--sm">Use {o.doc}</button></span>
              </figure>
            ))}
          </div>
        </>
      )}
      {c.kind === "unverified" && (
        <>
          <p className="claim__reason"><Scan size={14} aria-hidden /> Read from a scan, so no amount can be matched to printed words. Each row stays out of totals until you confirm, fix or leave it out.</p>
          <ul className="lrC-unv">
            {data.rows.filter((r) => c.row_ids!.includes(r.id)).map((r) => (
              <li key={r.id}>
                <span className="lrC-unv__no">{r.claim_number}</span>
                <span className="lrC-unv__who">{r.claimant}</span>
                <span className="num">{usd(r.incurred)}</span>
                <LChip letter={r.letter} label={r.chip} scan />
                <span className="lrC-unv__acts">
                  <button type="button" className="btn btn--ghost btn--sm">Confirm...</button>
                  <button type="button" className="btn btn--ghost btn--sm">Fix</button>
                  <button type="button" className="btn btn--ghost btn--sm">Leave out...</button>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </li>
  );
}

function C() {
  const tab = new URLSearchParams(location.search).get("tab") ?? "checks";
  const [quick, setQuick] = useState<string[]>([]);
  const [more, setMore] = useState(false);
  const tabs: [string, string, number][] = [["checks", "Checks", toResolve.length], ["claims", "Claims", 65], ["files", "Files", data.docs.length]];
  return (
    <Shell crumbs={crumbs}>
      <div className="workspace">
        <Strip />
        <main className="pane lr lrC" id="main">
          <Header action={<DoneGate />} />
          <div className="lrC__body">
            <PhoneStatus />
            <Statement />
            <nav className="lrC-tabs" aria-label="Review views">
              {tabs.map(([k, l, n]) => (
                <a key={k} href={`?tab=${k}`} className={`lrC-tab${tab === k ? " is-on" : ""}`} aria-current={tab === k ? "page" : undefined}>
                  {l} <span className="count">{k === "checks" ? `${n} to resolve` : n}</span>
                </a>
              ))}
            </nav>
            {tab === "checks" && (
              <div className="lrC-checks">
                <ol className="lrC-cases">{toResolve.map((c, i) => <Case key={i} c={c} />)}</ol>
                <h2 className="ledger__group">Handled by you <span className="count">{handled.length}</span></h2>
                <ul className="lrC-done">
                  {handled.map((c, i) => (
                    <li key={i}><StateWord c={c} /> <span>{checkTitle(c).replace("not verified", "confirmed")}: {c.note}</span> <button type="button" className="btn btn--ghost btn--sm">Undo</button></li>
                  ))}
                </ul>
                <h2 className="ledger__group">
                  <button type="button" className="ledger__fold" aria-expanded={more} onClick={() => setMore(!more)}>
                    <CaretDown size={16} className={more ? "is-open" : ""} aria-hidden /> Matches <span className="count">{matches.length} printed lines, {matchTotals} totals</span>
                  </button>
                </h2>
                {more && <ul className="lrC-done">{matches.map((c, i) => <li key={i}><StateWord c={c} /> {checkTitle(c)} <LChip letter={c.letter!} label={c.chip!} /></li>)}</ul>}
              </div>
            )}
            {tab === "claims" && (
              <div className="lrC-claims">
                <QuickFilters on={quick} setOn={setQuick} />
                <ClaimsTable quick={quick} sticky={false} />
                <div className="lrC-pager">
                  <span>Rows per page 50</span>
                  <span>1 to 50 of 65 claims</span>
                  <button type="button" className="icon-btn" aria-label="Previous page" aria-disabled="true"><CaretLeft size={16} /></button>
                  <button type="button" className="icon-btn" aria-label="Next page"><CaretRight size={16} /></button>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </Shell>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><C /></StrictMode>);
