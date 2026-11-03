import { QueueView } from "../queue/QueueView";
import { useQueue } from "../queue/useQueue";
import { isEmpty } from "../queue/queueUtil";
import "./personal.css";
import { Glyph, WorkflowMark } from "../voice/marks";

export const PERSONAL_COMPARE_HREF = "#/?line=personal";
export const PERSONAL_CHECK_HREF = "#/policy-check/new?line=personal";

function Actions() {
  return (
    <div className="personal__actions">
      <a href={PERSONAL_COMPARE_HREF} className="btn btn--secondary">
        <WorkflowMark slug="compare-quotes" size={18} /> Compare homeowners quotes
      </a>
      <a href={PERSONAL_CHECK_HREF} className="btn btn--secondary">
        <WorkflowMark slug="policy-check" size={18} /> Check a renewal
      </a>
    </div>
  );
}

/**
 * Slice 3b T6 page, work queue lane (spec section 2): the personal-lines work queue under the header and the two entry
 * points that open the Compare Quotes and Policy Check screens with Personal preselected. The raw run list lives in
 * Activity (`#/activity`). Decisions in PERSONAL-LINES-BRIEF.md and QUEUE-BRIEF.md.
 */
export function PersonalLinesPage() {
  const data = useQueue("personal");
  const empty = data.queue != null && !data.error && isEmpty(data.queue);
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

          <QueueView
            data={data}
            line="personal"
            empty={
              <div className="queue-empty">
                <Glyph name="blank" size={32} className="muted-glyph" />
                <h2 className="queue-empty__title">No personal-lines work yet.</h2>
                <p className="hint">Start a comparison or a renewal check. Work can also be moved here from its report.</p>
                <Actions />
              </div>
            }
          />
        </div>
      </main>
    </div>
  );
}
