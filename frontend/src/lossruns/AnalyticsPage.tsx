import { useCallback, useEffect, useId, useState } from "react";
import { ArrowClockwise, CaretRight, Plus } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import type { LossReviewListItem, QueueGroup } from "../api/types";
import { Collapse } from "../ui/motion";
import { keyboardAction } from "../ui/input";
import { useToast } from "../ui/Toast";
import { Glyph, GroupMark, ReadingMark } from "../voice/marks";
import { startedParts } from "../queue/queueUtil";
import { day, lossNewHref, usd } from "./lossUtil";
import "../queue/queue.css";
import "./lossruns.css";

const GROUPS: QueueGroup[] = ["in_progress", "needs_review", "needs_attention", "ready", "done_recently"];
const LABEL: Record<QueueGroup, string> = {
  in_progress: "In progress",
  needs_review: "Needs review",
  needs_attention: "Needs attention",
  ready: "Ready",
  done_recently: "Done recently",
};

/** The same rules as the work queue (spec section 2): reading, then open checks, then ready, done when filed. */
function groupOf(r: LossReviewListItem): QueueGroup {
  if (r.queue_state === "done" || r.queue_state === "dismissed") return "done_recently";
  if (r.active) return "in_progress";
  if (r.status === "failed" || r.status === "cancelled") return "needs_attention";
  if ((r.open_checks ?? 0) > 0) return "needs_review";
  return "ready";
}

/**
 * Analytics (`#/analytics`, slice 5a): loss reviews by client, with the work-queue row pattern (serif client link,
 * files and latest valuation date, open checks, one next action) and New loss review. Phone: the list, status only.
 */
export function AnalyticsPage() {
  const toast = useToast();
  const [reviews, setReviews] = useState<LossReviewListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null);
    api.lossReviews().then(setReviews, (e: unknown) => setError(e instanceof ApiError ? e.message : "Couldn't load the loss reviews."));
  }, []);
  useEffect(() => load(), [load]);
  useEffect(() => {
    if (!reviews?.some((r) => r.active)) return;
    const t = window.setInterval(load, 3000);
    return () => window.clearInterval(t);
  }, [reviews, load]);

  const retry = async (r: LossReviewListItem) => {
    try {
      await api.retryRun(r.run_id);
      load();
    } catch (e) {
      toast({ message: e instanceof ApiError ? e.message : "Couldn't retry. Retry again in a moment.", tone: "error" });
    }
  };

  const empty = reviews != null && reviews.length === 0;
  return (
    <div className="workspace">
      <main className="pane personal certs" id="main" tabIndex={-1} aria-labelledby="lr-page-title">
        <div className="personal__body">
          <header className="personal__head">
            <h1 id="lr-page-title" className="page-title">Loss reviews</h1>
            <p className="personal__sub">Each client's loss runs in one table, every claim cited and every printed total checked.</p>
            {!empty && (
              <div className="personal__actions">
                <a href={lossNewHref()} className="btn btn--primary"><Plus size={16} aria-hidden /> New loss review</a>
              </div>
            )}
          </header>
          <div className="queue">
            {error ? (
              <div className="state-block" role="alert">
                <p className="state-block__title">{error}</p>
                <button type="button" className="btn btn--secondary" onClick={load}>Retry</button>
              </div>
            ) : !reviews ? (
              <>
                <div className="runs-skeleton queue-skeleton" aria-hidden><span /><span /><span /></div>
                <span className="sr-only" role="status">Loading the loss reviews</span>
              </>
            ) : empty ? (
              <div className="queue-empty">
                <Glyph name="blank" size={32} className="muted-glyph" />
                <h2 className="queue-empty__title">No loss reviews yet.</h2>
                <p className="hint">Start one for a client with their carriers' loss runs, PDF or spreadsheet.</p>
                <a href={lossNewHref()} className="btn btn--primary"><Plus size={16} aria-hidden /> New loss review</a>
              </div>
            ) : (
              GROUPS.map((g) => <Group key={g} group={g} items={reviews.filter((r) => groupOf(r) === g)} onRetry={(r) => void retry(r)} />)
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function Group({ group, items, onRetry }: { group: QueueGroup; items: LossReviewListItem[]; onRetry: (r: LossReviewListItem) => void }) {
  const id = useId();
  const collapsible = group === "done_recently";
  const [open, setOpen] = useState({ value: !collapsible, animate: false });
  if (!items.length) return null;
  const title = (
    <>
      <GroupMark group={group} />
      <span className="queue-group__label">{LABEL[group]}</span>
      <span className="queue-group__count">{items.length}</span>
    </>
  );
  const list = (
    <ul className={`queue-list${group === "in_progress" ? " queue-list--slim" : ""}`}>
      {items.map((r) => <Row key={r.run_id} item={r} group={group} onRetry={onRetry} />)}
    </ul>
  );
  return (
    <section className={`queue-group queue-group--${group}`} aria-labelledby={`${id}-h`} data-group={group}>
      <h2 id={`${id}-h`} className="queue-group__title">
        {collapsible ? (
          <button
            type="button"
            className="queue-group__toggle"
            aria-expanded={open.value}
            aria-controls={`${id}-list`}
            onClick={(e) => setOpen((o) => ({ value: !o.value, animate: e.detail > 0 && !keyboardAction() }))}
          >
            {title}
            <CaretRight size={14} weight="bold" className="queue-group__caret" aria-hidden />
          </button>
        ) : (
          title
        )}
      </h2>
      {collapsible ? (
        <Collapse open={open.value} animate={open.animate} id={`${id}-list`}>{list}</Collapse>
      ) : (
        list
      )}
    </section>
  );
}

function Row({ item, group, onRetry }: { item: LossReviewListItem; group: QueueGroup; onRetry: (r: LossReviewListItem) => void }) {
  const href = `#/runs/${item.run_id}`;
  const name = item.client ?? "Unnamed client";
  const started = startedParts(item.created_at);
  const summary = [
    `${item.files} ${item.files === 1 ? "file" : "files"}`,
    item.latest_valuation_date ? `valued as of ${day(item.latest_valuation_date)}` : null,
  ].filter(Boolean).join(", ");
  let status = "";
  if (group === "in_progress") status = "Reading the files";
  else if (group === "needs_attention") status = item.status === "cancelled" ? "Stopped before it finished" : "Didn't finish";
  else if (item.claims != null) status = `${item.claims.toLocaleString("en-US")} claims, ${usd(item.incurred)} incurred`;
  const action =
    group === "needs_attention" ? (
      <button type="button" className="btn btn--secondary btn--sm" onClick={() => onRetry(item)}>
        <ArrowClockwise size={16} weight="bold" aria-hidden /> Retry
      </button>
    ) : group === "needs_review" || group === "ready" ? (
      <a href={href} className="btn btn--secondary btn--sm">Review losses</a>
    ) : null;
  return (
    <li className="queue-item" data-run-id={item.run_id}>
      <div className={`queue-row queue-row--${group}`}>
        <span className="queue-row__mark" aria-hidden>
          {group === "in_progress" ? <ReadingMark size={22} /> : <Glyph name="analytics" size={28} tabs className="vmark--workflow" />}
        </span>
        <div className="queue-row__who">
          <span className="queue-row__name">
            <a href={href} className={`queue-row__client${item.client == null ? " is-unnamed" : ""}`}>{name}</a>
            {item.client_source === "extracted" && <span className="queue-row__source">name from the documents</span>}
          </span>
          <span className="queue-row__what">
            <span className="queue-row__summary">{summary}</span>
            {started && (
              <time className="queue-row__when" dateTime={item.created_at ?? undefined}>
                {started.day}
                <span className="queue-row__clock">, {started.time}</span>
              </time>
            )}
          </span>
        </div>
        <div className="queue-row__status">
          {(item.open_checks ?? 0) > 0 && group === "needs_review" && (
            <span className="lr-qcount">{item.open_checks} {item.open_checks === 1 ? "check" : "checks"} to resolve</span>
          )}
          <span className={group === "in_progress" ? "queue-row__progress" : group === "done_recently" ? "queue-row__done" : "certs__status"}>{status}</span>
        </div>
        <div className="queue-row__actions">{action}</div>
      </div>
    </li>
  );
}
