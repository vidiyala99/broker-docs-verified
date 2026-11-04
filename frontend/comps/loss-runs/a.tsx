/* Direction A, "Register and ledger": the claims register with its counted totals as the table's own footing,
   beside the check ledger (Certificates' Proof and ledger pattern). A check opens its scope in the register with
   the printed line as a footing under the rows. Phase A comp only. */
import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { CaretDown } from "@phosphor-icons/react";
import { ClaimsTable, CheckBody, PhoneStatus, QuickFilters } from "./parts";
import { crumbs, data, DoneGate, Header, handled, matches, matchTotals, Shell, Strip, toResolve, usd, checkTitle, type Check } from "./shared";

function A() {
  const params = new URLSearchParams(location.search);
  const [quick, setQuick] = useState<string[]>(params.get("q")?.split(",").filter(Boolean) ?? []);
  const [scope, setScope] = useState<Check | null>(params.get("scope") === "off" ? toResolve[0] : params.get("scope") === "review" ? toResolve[1] : null);
  const [showMatches, setShowMatches] = useState(false);
  const t = data.totals;
  return (
    <Shell crumbs={crumbs}>
      <div className="workspace">
        <Strip />
        <main className="pane lr lrA" id="main">
          <Header action={<DoneGate />} />
          <div className="lrA__body">
            <PhoneStatus />
            <section className="lrA__register" aria-label="Claims">
              <div className="lrA__band">
                <h2 className="lr-h2">Claims</h2>
                <p className="lrA__also">
                  Recoveries <b>{usd(t.recovery)}</b> from {t.recovery_from} claims. Deductibles <b>{usd(t.deductible)}</b> from {t.deductible_from} claims.
                  Superseded valuations fold under their claim.
                </p>
              </div>
              {scope ? (
                <div className="lr-scopebar">
                  <span>Showing the rows behind <b>{checkTitle(scope)}</b></span>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => setScope(null)}>Show all claims</button>
                </div>
              ) : (
                <QuickFilters on={quick} setOn={setQuick} />
              )}
              <ClaimsTable quick={quick} scope={scope} />
              <p className="lr-pager">65 claims on 77 lines. Rows load 50 at a time as you scroll; sort and filters run on the server (D2, D10).</p>
            </section>
            <aside className="ledger lr-ledger" aria-labelledby="checks-title">
              <h2 id="checks-title" className="ledger__title">Checks</h2>
              <p className="ledger__sum">{toResolve.length + handled.length + matches.length} checks on {data.docs.length} files. {toResolve.length} need you.</p>
              <h3 className="ledger__group">To resolve <span className="count">{toResolve.length}</span></h3>
              <ul className="ledger__list">
                {toResolve.map((c, i) => (
                  <li key={i} className={`ledger__item lr-item lr-item--${c.verdict}${scope === c ? " is-scoped" : ""}`}>
                    <CheckBody c={c} onShow={() => setScope(c)} />
                  </li>
                ))}
              </ul>
              <h3 className="ledger__group">Handled by you <span className="count">{handled.length}</span></h3>
              <ul className="ledger__list">
                {handled.map((c, i) => <li key={i} className="ledger__item lr-item"><CheckBody c={c} /></li>)}
              </ul>
              <h3 className="ledger__group">
                <button type="button" className="ledger__fold" aria-expanded={showMatches} onClick={() => setShowMatches(!showMatches)}>
                  <CaretDown size={16} className={showMatches ? "is-open" : ""} aria-hidden /> Matches <span className="count">{matches.length} printed lines, {matchTotals} totals</span>
                </button>
              </h3>
              {showMatches && (
                <ul className="ledger__list">
                  {matches.map((c, i) => <li key={i} className="ledger__item lr-item"><CheckBody c={c} actions={false} /></li>)}
                </ul>
              )}
              {data.notices.map((n) => <p key={n} className="lr-notice">{n}</p>)}
            </aside>
          </div>
        </main>
      </div>
    </Shell>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><A /></StrictMode>);
