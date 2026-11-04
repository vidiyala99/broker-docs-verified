import { useCallback, useEffect, useId, useState } from "react";
import { ArrowClockwise, CaretRight, Plus } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import type { CertificateQueue, CertificateQueueGroup, CertificateQueueItem } from "../api/types";
import { Collapse } from "../ui/motion";
import { useToast } from "../ui/Toast";
import { Glyph, GroupMark, ReadingMark, Seal } from "../voice/marks";
import { startedParts } from "../queue/queueUtil";
import { certificateNewHref, formatDate } from "./certUtil";
import "../queue/queue.css";
import "./certificates.css";

const GROUPS: CertificateQueueGroup[] = ["in_progress", "needs_attention", "ready", "done_recently"];
const LABEL: Record<CertificateQueueGroup, string> = {
  in_progress: "In progress",
  needs_attention: "Needs attention",
  ready: "Ready to issue",
  done_recently: "Done recently",
};
const UNNAMED = "Unnamed client";

/**
 * The Certificates page (spec section 4): the certificate queue with the work-queue row pattern (serif client, one
 * next action) and New certificate. Certificates are listed here only, not in the main queue (C8); Activity lists
 * them like any run.
 */
export function CertificatesPage() {
  const toast = useToast();
  const [queue, setQueue] = useState<CertificateQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null);
    api.certificateQueue().then(setQueue, (e: unknown) => setError(e instanceof ApiError ? e.message : "Couldn't load the certificates."));
  }, []);
  useEffect(() => load(), [load]);
  // Reading runs move on by themselves: poll while anything is in progress.
  useEffect(() => {
    if (!queue?.in_progress.length) return;
    const t = window.setInterval(load, 3000);
    return () => window.clearInterval(t);
  }, [queue, load]);

  const retry = async (item: CertificateQueueItem) => {
    try {
      await api.retryRun(item.run_id);
      load();
    } catch (e) {
      toast({ message: e instanceof ApiError ? e.message : "Couldn't retry. Retry again in a moment.", tone: "error" });
    }
  };

  const empty = queue != null && GROUPS.every((g) => queue[g].length === 0);
  return (
    <div className="workspace">
      <main className="pane personal certs" id="main" tabIndex={-1} aria-labelledby="certs-title">
        <div className="personal__body">
          <header className="personal__head">
            <h1 id="certs-title" className="page-title">
              Certificates
            </h1>
            <p className="personal__sub">Certificates of insurance, each claim checked against the policies before you issue.</p>
            {!empty && (
              <div className="personal__actions">
                <a href={certificateNewHref()} className="btn btn--primary">
                  <Plus size={16} aria-hidden /> New certificate
                </a>
              </div>
            )}
          </header>
          <div className="queue">
            {error ? (
              <div className="state-block" role="alert">
                <p className="state-block__title">{error}</p>
                <button type="button" className="btn btn--secondary" onClick={load}>
                  Retry
                </button>
              </div>
            ) : !queue ? (
              <>
                <div className="runs-skeleton queue-skeleton" aria-hidden>
                  <span />
                  <span />
                  <span />
                </div>
                <span className="sr-only" role="status">
                  Loading the certificates
                </span>
              </>
            ) : empty ? (
              <div className="queue-empty">
                <Glyph name="blank" size={32} className="muted-glyph" />
                <h2 className="queue-empty__title">No certificates yet.</h2>
                <p className="hint">Start one for a client: their holder, the wording asked for, and the policies.</p>
                <a href={certificateNewHref()} className="btn btn--primary">
                  <Plus size={16} aria-hidden /> New certificate
                </a>
              </div>
            ) : (
              <>
                {queue.truncated && <p className="hint queue__note">Showing the first 500 items. Older certificates are in Activity.</p>}
                {GROUPS.map((g) => (
                  <Group key={g} group={g} items={queue[g]} onRetry={(i) => void retry(i)} />
                ))}
                <p className="queue__footer">
                  <a href="#/activity" className="link-btn">
                    See everything in Activity
                  </a>
                </p>
              </>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function Group({ group, items, onRetry }: { group: CertificateQueueGroup; items: CertificateQueueItem[]; onRetry: (i: CertificateQueueItem) => void }) {
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
      {items.map((item) => (
        <Row key={item.run_id} item={item} group={group} onRetry={onRetry} />
      ))}
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
            onClick={(e) => setOpen((o) => ({ value: !o.value, animate: e.detail > 0 }))}
          >
            {title}
            <CaretRight size={14} weight="bold" className="queue-group__caret" aria-hidden />
          </button>
        ) : (
          title
        )}
      </h2>
      {collapsible ? (
        <Collapse open={open.value} animate={open.animate} id={`${id}-list`}>
          {list}
        </Collapse>
      ) : (
        list
      )}
    </section>
  );
}

function Row({ item, group, onRetry }: { item: CertificateQueueItem; group: CertificateQueueGroup; onRetry: (i: CertificateQueueItem) => void }) {
  const name = item.client ?? UNNAMED;
  const href = `#/runs/${item.run_id}`;
  const started = startedParts(item.created_at);
  // One action per row, in the secondary look: every row of a group offers the same step (DESIGN.md "one loud button").
  let action = null;
  if (group === "needs_attention" && item.retryable && item.status !== "done") {
    action = (
      <button type="button" className="btn btn--secondary btn--sm" onClick={() => onRetry(item)}>
        <ArrowClockwise size={16} weight="bold" aria-hidden /> Retry<span className="sr-only"> {name}</span>
      </button>
    );
  } else if (group === "needs_attention" && item.open_claims) {
    action = (
      <a href={href} className="btn btn--secondary btn--sm">
        Resolve checks<span className="sr-only"> for {name}</span>
      </a>
    );
  } else if (group === "ready") {
    action = (
      <a href={href} className="btn btn--secondary btn--sm">
        Review and issue<span className="sr-only"> for {name}</span>
      </a>
    );
  }
  return (
    <li className="queue-item" data-run-id={item.run_id}>
      <div className={`queue-row queue-row--${group}${group === "in_progress" ? " motion-live" : ""}`}>
        {group === "in_progress" && <span className="motion-live__line" aria-hidden />}
        <span className="queue-row__mark" aria-hidden>
          {group === "in_progress" ? <ReadingMark size={22} /> : <Glyph name="certificate" size={28} />}
          {group === "done_recently" && <Seal lands={false} />}
        </span>
        <div className="queue-row__who">
          <span className="queue-row__name">
            <a href={href} className={`queue-row__client${item.client == null ? " is-unnamed" : ""}`}>
              {name}
            </a>
            {item.client_source === "extracted" && <span className="queue-row__source">name from the documents</span>}
          </span>
          <span className="queue-row__what">
            <span className="queue-row__summary">Certificate</span>
            {started && (
              <time className="queue-row__when" dateTime={item.created_at ?? undefined}>
                {started.day}
                <span className="queue-row__clock">, {started.time}</span>
              </time>
            )}
          </span>
        </div>
        <div className="queue-row__status">
          <span className={group === "done_recently" ? "queue-row__done" : group === "in_progress" ? "queue-row__progress" : "certs__status"}>
            {group === "done_recently" && item.issued_at ? `Issued ${formatDate(item.issued_at)}` : item.summary}
          </span>
          {item.reasons && item.reasons.length > 0 && group === "needs_attention" && !item.open_claims && (
            <span className="queue-row__reason">{item.reasons.join(". ")}</span>
          )}
        </div>
        <div className="queue-row__actions">{action}</div>
      </div>
    </li>
  );
}
