import { Scales, ShieldCheck } from "@phosphor-icons/react";
import type { Run } from "../api/types";
import { RecentRuns } from "../launch/RecentRuns";
import "./personal.css";

/** The Personal lines list reads the newest 100 runs (D5); there is no paging. */
export const PERSONAL_LIMIT = 100;
export const PERSONAL_COMPARE_HREF = "#/?line=personal";
export const PERSONAL_CHECK_HREF = "#/policy-check/new?line=personal";

function Actions() {
  return (
    <div className="personal__actions">
      <a href={PERSONAL_COMPARE_HREF} className="btn btn--secondary">
        <Scales size={18} aria-hidden /> Compare homeowners quotes
      </a>
      <a href={PERSONAL_CHECK_HREF} className="btn btn--secondary">
        <ShieldCheck size={18} aria-hidden /> Check a renewal
      </a>
    </div>
  );
}

/**
 * Slice 3b T6 (spec 2.1): runs tagged `personal` (R2) in the shared Workflows run list, plus two entry points that
 * open the Compare Quotes and Policy Check screens with Personal preselected. Decisions in PERSONAL-LINES-BRIEF.md.
 */
export function PersonalLinesPage({ runs, runsError, onRetry }: { runs: Run[] | null; runsError: string | null; onRetry: () => void }) {
  const empty = runs != null && runs.length === 0 && !runsError;
  return (
    <div className="workspace">
      <main className="pane personal" id="main" tabIndex={-1} aria-labelledby="personal-title">
        <div className="personal__body">
          <header className="personal__head">
            <h1 id="personal-title" className="page-title">
              Personal lines
            </h1>
            <p className="personal__sub">Homeowners, auto and other household policies.</p>
            {/* One place per state for the two actions: the header, or the empty state (no duplicate CTAs). */}
            {!empty && <Actions />}
          </header>

          <section className="personal__list" aria-labelledby="personal-runs">
            {runsError ? (
              <>
                <h2 id="personal-runs" className="pane__subtitle">
                  Recent runs
                </h2>
                <div className="notice notice--error" role="alert">
                  <span className="notice__text">{runsError}</span>
                  <button type="button" className="btn btn--secondary btn--sm" onClick={onRetry}>
                    Retry
                  </button>
                </div>
              </>
            ) : runs == null ? (
              <>
                <h2 id="personal-runs" className="pane__subtitle">
                  Recent runs
                </h2>
                <div className="runs-skeleton" aria-hidden>
                  <span />
                  <span />
                </div>
                <span className="sr-only" role="status">
                  Loading runs
                </span>
              </>
            ) : empty ? (
              <div className="personal__empty">
                <h2 id="personal-runs" className="state-block__title">
                  No personal-lines runs yet.
                </h2>
                <p className="hint">Start one here. A run can also be moved to Personal lines from its own page.</p>
                <Actions />
              </div>
            ) : (
              <>
                <h2 id="personal-runs" className="pane__subtitle">
                  Recent runs
                </h2>
                <RecentRuns runs={runs} />
                {runs.length >= PERSONAL_LIMIT && <p className="hint">Showing the newest {PERSONAL_LIMIT} runs.</p>}
              </>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
