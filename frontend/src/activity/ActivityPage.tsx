import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Check } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import type { Line, Run } from "../api/types";
import { RecentRuns } from "../launch/RecentRuns";
import { useFadeOnChange } from "../ui/motion";
import "../personal/personal.css";
import "../queue/queue.css";

/** GET /api/runs has no paging: Activity shows the newest 100 (D11, TODOS.md list paging). */
export const ACTIVITY_LIMIT = 100;

type Filter = "all" | Line;
const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "commercial", label: "Commercial" },
  { value: "personal", label: "Personal" },
];

/**
 * Work queue W2: every run, newest first (the existing RecentRuns list, unchanged), with a line filter. Linked from the
 * bottom of the queue and the run view's "Activity" tab; `?line=` presets the filter and follows it (QUEUE-BRIEF.md).
 */
export function ActivityPage({ preset }: { preset: Line | null }) {
  const [filter, setFilter] = useState<Filter>(preset ?? "all");
  useEffect(() => setFilter(preset ?? "all"), [preset]);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const name = useId();
  // Lists loaded so far: from the second on (a filter change), a pointer change re-settles the new list with a short
  // opacity lift; the first load and keyboard changes are instant (ui/motion.tsx).
  const [loads, setLoads] = useState(0);
  const listRef = useRef<HTMLElement>(null);
  useFadeOnChange(listRef, loads > 1 ? loads : 0);

  const load = useCallback(() => {
    const n = ++seq.current;
    setError(null);
    setRuns(null);
    api.runs(ACTIVITY_LIMIT, filter === "all" ? undefined : filter).then(
      (r) => {
        if (n !== seq.current) return;
        setRuns(r);
        setLoads((l) => l + 1);
      },
      (e: unknown) => n === seq.current && setError(e instanceof ApiError ? e.message : "Couldn't load recent work."),
    );
  }, [filter]);

  useEffect(() => load(), [load]);

  const choose = (f: Filter) => {
    setFilter(f);
    // Keep the URL in step without a hashchange (no reload of the route).
    window.history.replaceState(null, "", `#/activity${f === "all" ? "" : `?line=${f}`}`);
  };

  const noun = filter === "personal" ? "personal-lines " : filter === "commercial" ? "commercial " : "";

  return (
    <div className="workspace">
      <main className="pane personal" id="main" tabIndex={-1} aria-labelledby="activity-title">
        <div className="personal__body">
          <header className="personal__head">
            <h1 id="activity-title" className="page-title">
              Activity
            </h1>
            <p className="personal__sub">Every comparison, check, proposal and certificate, newest first.</p>
            <fieldset className="line-choice activity__filter">
              <legend className="line-choice__legend">Show</legend>
              <div className="line-choice__seg">
                {FILTERS.map((f) => (
                  <label key={f.value} className="line-choice__opt">
                    <input type="radio" name={name} value={f.value} checked={filter === f.value} onChange={() => choose(f.value)} />
                    {/* Always drawn (line switch): the glyph cross-fades and the option keeps one width. */}
                    <Check size={14} weight="bold" className={`line-choice__check${filter === f.value ? "" : " is-off"}`} aria-hidden />
                    <span>{f.label}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          </header>

          <section ref={listRef} className="personal__list" aria-labelledby="activity-runs">
            <h2 id="activity-runs" className="sr-only">
              Runs
            </h2>
            {error ? (
              <div className="notice notice--error" role="alert">
                <span className="notice__text">{error}</span>
                <button type="button" className="btn btn--secondary btn--sm" onClick={load}>
                  Retry
                </button>
              </div>
            ) : runs == null ? (
              <>
                <div className="runs-skeleton" aria-hidden>
                  <span />
                  <span />
                  <span />
                </div>
                <span className="sr-only" role="status">
                  Loading runs
                </span>
              </>
            ) : runs.length === 0 ? (
              <p className="hint">No {noun}runs yet.</p>
            ) : (
              <>
                <RecentRuns runs={runs} />
                {runs.length >= ACTIVITY_LIMIT && <p className="hint">Showing the newest {ACTIVITY_LIMIT} runs.</p>}
              </>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
