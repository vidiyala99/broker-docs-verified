import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowClockwise,
  ArrowCounterClockwise,
  ArrowSquareOut,
  ArrowsLeftRight,
  Certificate,
  ChartLine,
  CaretRight,
  CheckCircle,
  FilePlus,
  Prohibit,
  Warning,
} from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import type { Line, Queue, QueueGroup, QueueItem, QueueState } from "../api/types";
import { useToast } from "../ui/Toast";
import { Collapse, Count, usePresence } from "../ui/motion";
import { RowMenu, type MenuItem } from "../tasks/RowMenu";
import { certificateNewHref } from "../certificates/certUtil";
import { lossNewHref } from "../lossruns/lossUtil";
import { discrepancyHref } from "../tasks/taskUtil";
import { draftFromComparison, restoreDraft } from "./buildProposal";
import type { QueueData } from "./useQueue";
import {
  activityHref,
  clientOf,
  doneText,
  GROUP_LABEL,
  GROUPS,
  groupOf,
  isShared,
  linkContexts,
  metaParts,
  moveLabel,
  movedText,
  openLabel,
  openTotal,
  otherLine,
  progressText,
  SEVERITIES,
  sharedClients,
  startedParts,
} from "./queueUtil";
import "./queue.css";
import { GroupMark, ReadingMark, Seal, WorkflowMark } from "../voice/marks";
import { keyboardAction, pointerAction } from "../ui/input";

const runHref = (id: number) => `#/runs/${id}`;

/** Rows whose seal already landed this session (it lands once). */
const SEALED = new Set<number>();

const SEV_TICKS: Record<(typeof SEVERITIES)[number], number> = { critical: 4, high: 3, medium: 2, low: 1 };

/** Remove a run from every group; `into` puts it at the top of one group (optimistic moves). */
function moveItem(q: Queue, runId: number, into?: { group: QueueGroup; patch: Partial<QueueItem> }): Queue {
  let found: QueueItem | null = null;
  const next = { ...q };
  for (const g of GROUPS) {
    const hit = q[g].find((i) => i.run_id === runId);
    if (hit) found = hit;
    next[g] = q[g].filter((i) => i.run_id !== runId);
  }
  if (found && into) next[into.group] = [{ ...found, ...into.patch }, ...next[into.group]];
  return next;
}

/** A Ready comparison whose proposal did not finish and can be retried (owner feedback 2026-11-03). */
const canRetryProposal = (item: QueueItem) => item.proposal_run_id != null && item.proposal_retryable === true;

/** Telling rows apart (queue navigation, 2026-11-03): the client names more than one row shows, and each name
 * link's screen-reader context. Computed once per queue answer. */
interface Nav {
  shared: Set<string>;
  contexts: Map<number, string>;
}

interface Actions {
  leave: (item: QueueItem, state: Exclude<QueueState, "open">) => void;
  reopen: (item: QueueItem) => void;
  move: (item: QueueItem) => void;
  retry: (item: QueueItem) => Promise<void>;
  retryProposal: (item: QueueItem) => Promise<void>;
  review: (item: QueueItem) => Promise<void>;
  buildProposal: (item: QueueItem) => Promise<void>;
}

/**
 * The broker work queue (spec section 2, QUEUE-BRIEF.md): In progress strip, Needs review, Ready, Needs attention and
 * a collapsed Done recently, rows named by client with one next action. Used by Personal lines and the Compare screen.
 */
export function QueueView({ data, line, empty }: { data: QueueData; line: Line; empty: ReactNode }) {
  const { queue, error, reload, update } = data;
  const toast = useToast();
  const [announce, setAnnounce] = useState("");
  // Rows animate only after the queue was first shown (DESIGN.md: no entrance staggers on load), and never for a
  // keyboard action (motion audit B4: keyboard changes are instant).
  const shown = useRef(false);
  const animate = shown.current && !keyboardAction();
  useEffect(() => {
    if (queue) shown.current = true;
  }, [queue]);

  // A row that leaves In progress (polling, D5) is announced once, in one polite region.
  const prevActive = useRef<Map<number, string> | null>(null);
  useEffect(() => {
    if (!queue) return;
    const before = prevActive.current;
    if (before) {
      const moved: string[] = [];
      for (const [id, name] of before) {
        if (queue.in_progress.some((i) => i.run_id === id)) continue;
        const g = groupOf(queue, id);
        if (g) moved.push(`${name} moved to ${GROUP_LABEL[g]}.`);
      }
      if (moved.length) setAnnounce(moved.join(" "));
    }
    prevActive.current = new Map(queue.in_progress.map((i) => [i.run_id, clientOf(i)]));
  }, [queue]);

  const nav = useMemo<Nav>(
    () => (queue ? { shared: sharedClients(queue), contexts: linkContexts(queue) } : { shared: new Set(), contexts: new Map() }),
    [queue],
  );

  const failToast = useCallback(
    (e: unknown, fallback: string) => toast({ message: e instanceof ApiError ? e.message : fallback, tone: "error" }),
    [toast],
  );

  const reopen = useCallback(
    (item: QueueItem) => {
      api.patchRun(item.run_id, { queue_state: "open" }).then(
        () => {
          reload();
          toast({ message: "Back in the queue.", hold: 4000 });
        },
        (e: unknown) => failToast(e, "Couldn't move it back to the queue. Retry."),
      );
    },
    [reload, toast, failToast],
  );

  const actions: Actions = {
    // D9: no confirm; the row moves to Done recently at once and the toast offers Undo.
    leave: (item, state) => {
      update((q) => moveItem(q, item.run_id, { group: "done_recently", patch: { queue_state: state, queue_changed_at: new Date().toISOString() } }));
      api.patchRun(item.run_id, { queue_state: state }).then(
        () => {
          reload();
          toast({
            message: state === "done" ? "Marked done." : "Dismissed.",
            action: { label: "Undo", run: () => reopen(item) },
          });
        },
        (e: unknown) => {
          reload();
          failToast(e, state === "done" ? "Couldn't mark it done. Retry." : "Couldn't dismiss it. Retry.");
        },
      );
    },
    reopen,
    move: (item) => {
      const to = otherLine(item.line);
      update((q) => moveItem(q, item.run_id));
      api.patchRun(item.run_id, { line: to }).then(
        () => {
          reload();
          toast({
            message: movedText(to),
            action: {
              label: "Undo",
              run: () =>
                api.patchRun(item.run_id, { line: item.line }).then(
                  () => reload(),
                  (e: unknown) => failToast(e, "Couldn't move it back. Retry."),
                ),
            },
          });
        },
        (e: unknown) => {
          reload();
          failToast(e, "Couldn't change the line. Retry.");
        },
      );
    },
    retry: async (item) => {
      try {
        await api.retryRun(item.run_id);
        update((q) => moveItem(q, item.run_id, { group: "in_progress", patch: { status: "queued", retryable: false } }));
        setAnnounce(`Retrying ${clientOf(item)}.`);
      } catch (e) {
        failToast(e, "Couldn't retry. Retry again in a moment.");
      }
      reload();
    },
    // Reruns the failed proposal itself (template, details and outline kept); D3 then shows "Building proposal".
    retryProposal: async (item) => {
      if (item.proposal_run_id == null) return;
      try {
        await api.retryRun(item.proposal_run_id);
        update((q) =>
          moveItem(q, item.run_id, { group: "in_progress", patch: { summary: "Building proposal", reasons: null, proposal_retryable: false } }),
        );
        setAnnounce(`Retrying the proposal for ${clientOf(item)}.`);
      } catch (e) {
        failToast(e, "Couldn't retry the proposal. Retry again in a moment.");
      }
      reload();
    },
    // Review opens the run at its first open discrepancy (`?discrepancy=`, Tasks lane deep link).
    review: async (item) => {
      let href = runHref(item.run_id);
      try {
        const list = await api.discrepancies(item.run_id);
        const first = list.items.find((i) => i.status === "open");
        if (first) href = discrepancyHref(item.run_id, first.id);
      } catch {
        /* the run itself still opens */
      }
      window.location.hash = href;
    },
    buildProposal: async (item) => {
      try {
        const { replaced } = await draftFromComparison(item);
        window.location.hash = "#/proposals/new";
        if (replaced) {
          toast({ message: "Replaced your unfinished proposal draft.", action: { label: "Undo", run: () => restoreDraft(replaced) } });
        }
      } catch (e) {
        failToast(e, "Couldn't open the proposal. Retry.");
      }
    },
  };

  let body: ReactNode;
  if (error) {
    body = (
      <div className="notice notice--error" role="alert">
        <span className="notice__text">{error}</span>
        <button type="button" className="btn btn--secondary btn--sm" onClick={reload}>
          Retry
        </button>
      </div>
    );
  } else if (!queue) {
    body = (
      <>
        <div className="runs-skeleton queue-skeleton" aria-hidden>
          <span />
          <span />
          <span />
        </div>
        <span className="sr-only" role="status">
          Loading the work queue
        </span>
      </>
    );
  } else if (GROUPS.every((g) => queue[g].length === 0)) {
    body = empty;
  } else {
    body = (
      <>
        {queue.truncated && (
          <p className="hint queue__note">Showing the first 500 items. Older finished work is in Activity.</p>
        )}
        {GROUPS.map((g) => (
          <Group key={g} group={g} items={queue[g]} actions={actions} animate={animate} nav={nav} />
        ))}
        <p className="queue__footer">
          <a href={activityHref(line)} className="link-btn">
            See everything in Activity
          </a>
        </p>
      </>
    );
  }

  return (
    <div className="queue" data-line={line}>
      {body}
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announce}
      </p>
    </div>
  );
}

function Group({
  group,
  items,
  actions,
  animate,
  nav,
}: {
  group: QueueGroup;
  items: QueueItem[];
  actions: Actions;
  animate: boolean;
  nav: Nav;
}) {
  const id = useId();
  const collapsible = group === "done_recently";
  const [open, setOpen] = useState<{ value: boolean; animate: boolean }>({ value: !collapsible, animate: false });
  const rows = usePresence(items, (i) => i.run_id, animate);
  if (rows.length === 0) return null;
  const title = (
    <>
      <GroupMark group={group} />
      <span className="queue-group__label">{GROUP_LABEL[group]}</span>
      <Count value={items.length} animate={animate} className="queue-group__count" />
    </>
  );
  const list = (
    <ul className={`queue-list${group === "in_progress" ? " queue-list--slim" : ""}`}>
      {rows.map(({ item, leaving, entering }) => (
        <QueueRow
          key={`${item.run_id}${leaving ? "-leaving" : ""}`}
          item={item}
          group={group}
          actions={actions}
          leaving={leaving}
          entering={entering}
          nav={nav}
        />
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
            // Pointer opens animate; keyboard opens are instant (DESIGN.md motion).
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

function QueueRow({
  item,
  group,
  actions,
  leaving,
  entering,
  nav,
}: {
  item: QueueItem;
  group: QueueGroup;
  actions: Actions;
  leaving: boolean;
  entering: boolean;
  nav: Nav;
}) {
  const [busy, setBusy] = useState(false);
  // The seal lands once per filed row, and only after a pointer action (the archive may open later: it lands then).
  const [sealLands] = useState(() => {
    const lands = group === "done_recently" && entering && pointerAction() && !SEALED.has(item.run_id);
    if (lands) SEALED.add(item.run_id);
    return lands;
  });
  const name = clientOf(item);
  const started = startedParts(item.created_at);
  const meta = metaParts(item, isShared(nav.shared, item));
  const forName = <span className="sr-only"> {name}</span>;
  const run = (fn: () => Promise<void>) => async () => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };
  // D3 amended (owner 2026-11-04): a comparison whose proposal did not finish sits in Needs attention.
  const retryProposal = group === "needs_attention" && canRetryProposal(item);

  // Rows without a next step (In progress, Done recently, a Needs attention row that cannot be retried) have no
  // primary action: the client name is the link to the report, so a quiet "Open" beside it was the same stop twice.
  let primary: ReactNode = null;
  const lossReview = item.workflow_slug === "loss-run";
  if (lossReview && (group === "needs_review" || group === "ready")) {
    // Slice 5a: a loss review's one next step is the review page itself ("Review losses", API `action`).
    primary = (
      <a href={runHref(item.run_id)} className="btn btn--primary btn--sm">
        {item.action ?? "Review losses"}
        {forName}
      </a>
    );
  } else if (group === "needs_review") {
    primary = (
      <a
        href={runHref(item.run_id)}
        className="btn btn--primary btn--sm"
        aria-busy={busy || undefined}
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
          e.preventDefault();
          void run(() => actions.review(item))();
        }}
      >
        Review{forName}
      </a>
    );
  } else if (retryProposal) {
    primary = (
      <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={run(() => actions.retryProposal(item))}>
        <ArrowClockwise size={16} weight="bold" aria-hidden />
        {busy ? "Retrying..." : "Retry proposal"}
        {forName}
      </button>
    );
  } else if (group === "ready") {
    primary = (
      <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={run(() => actions.buildProposal(item))}>
        <FilePlus size={16} weight="bold" aria-hidden />
        {busy ? "Opening..." : "Build proposal"}
        {forName}
      </button>
    );
  } else if (group === "needs_attention" && item.retryable) {
    primary = (
      <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={run(() => actions.retry(item))}>
        <ArrowClockwise size={16} weight="bold" aria-hidden />
        {busy ? "Retrying..." : "Retry"}
        {forName}
      </button>
    );
  }

  const open = (id: number) => () => (window.location.hash = runHref(id));
  const menu: MenuItem[] = [];
  if (group === "done_recently") {
    menu.push({ label: openLabel(item), icon: <ArrowSquareOut size={16} aria-hidden />, run: open(item.run_id) });
    if (item.queue_state !== "open") {
      menu.push({ label: "Move back to queue", icon: <ArrowCounterClockwise size={16} aria-hidden />, run: () => actions.reopen(item) });
    }
  } else {
    // M1: an active run cannot be marked done or dismissed.
    if (group !== "in_progress") {
      menu.push({ label: "Mark done", icon: <CheckCircle size={16} aria-hidden />, run: () => actions.leave(item, "done") });
      menu.push({ label: "Dismiss", icon: <Prohibit size={16} aria-hidden />, run: () => actions.leave(item, "dismissed") });
    }
    if (retryProposal && item.proposal_run_id != null) {
      menu.push({ label: "Open failed proposal", icon: <ArrowSquareOut size={16} aria-hidden />, run: open(item.proposal_run_id) });
      menu.push({ label: "Start new proposal", icon: <FilePlus size={16} aria-hidden />, run: () => void actions.buildProposal(item) });
    }
    menu.push({ label: openLabel(item), icon: <ArrowSquareOut size={16} aria-hidden />, run: open(item.run_id) });
  }
  menu.push({ label: moveLabel(otherLine(item.line)), icon: <ArrowsLeftRight size={16} aria-hidden />, run: () => actions.move(item) });
  // Slice 4 hook: a certificate for this client, prefilled (spec section 4 "Hooks").
  menu.push({ label: "Issue certificate", icon: <Certificate size={16} aria-hidden />, run: () => (window.location.hash = certificateNewHref(item.client)) });
  // Slice 5a hook: a new loss review for this client, prefilled.
  menu.push({ label: "Review losses", icon: <ChartLine size={16} aria-hidden />, run: () => (window.location.hash = lossNewHref(item.client)) });

  return (
    <li
      className={`queue-item${leaving ? " motion-exit" : entering ? " motion-enter" : ""}`}
      data-run-id={leaving ? undefined : item.run_id}
      aria-hidden={leaving || undefined}
      inert={leaving || undefined}
    >
      <div className={`queue-row queue-row--${group}${group === "in_progress" ? " motion-live" : ""}`}>
        {group === "in_progress" && <span className="motion-live__line" aria-hidden />}
        <span className="queue-row__mark" aria-hidden>
          {group === "in_progress" ? <ReadingMark size={22} /> : <WorkflowMark slug={item.workflow_slug} />}
          {/* Filed rows carry the seal; it lands once when a pointer action filed the row (DESIGN.md Voice). */}
          {group === "done_recently" && <Seal lands={sealLands} />}
        </span>
        <div className="queue-row__who">
          {/* The name is the way into the report (owner, 2026-11-03): no extra button, icon or line. */}
          <span className="queue-row__name">
            <a href={runHref(item.run_id)} className={`queue-row__client${item.client == null ? " is-unnamed" : ""}`}>
              {name}
              <span className="sr-only">{nav.contexts.get(item.run_id) ?? ""}</span>
            </a>
            {/* Beside the name it qualifies, so the meta line below stays one line (queue navigation). */}
            {item.client_source === "extracted" && <span className="queue-row__source">name from the documents</span>}
          </span>
          <span className="queue-row__what">
            <span className="queue-row__summary">
              {meta.lead && (
                <span className={meta.detail ? "queue-row__lead" : undefined}>
                  {meta.lead}
                  {meta.detail ? " · " : ""}
                </span>
              )}
              {meta.detail}
            </span>
            {started && (
              <time className="queue-row__when" dateTime={item.created_at ?? undefined}>
                {started.day}
                <span className="queue-row__clock">, {started.time}</span>
              </time>
            )}
          </span>
        </div>
        <div className="queue-row__status">
          <Status item={item} group={group} />
        </div>
        <div className="queue-row__actions">
          {primary}
          <RowMenu label={`Actions for ${name}`} items={menu} />
        </div>
      </div>
    </li>
  );
}

function Status({ item, group }: { item: QueueItem; group: QueueGroup }) {
  if (group === "in_progress") return <span className="queue-row__progress">{progressText(item)}</span>;
  if (group === "done_recently") return <span className="queue-row__done">{doneText(item)}</span>;
  const total = openTotal(item);
  const top = item.open_counts ? SEVERITIES.find((s) => item.open_counts![s] > 0) : undefined;
  return (
    <>
      {item.reasons && item.reasons.length > 0 && (
        <span className="queue-row__reason">
          <Warning size={16} weight="bold" aria-hidden />
          <span>{item.reasons.join(". ")}</span>
        </span>
      )}
      {total > 0 && item.open_counts && top && (
        <span className="queue-row__counts">
          {/* The Policy Check severity meter, set to the worst open item: tier reads without colour. */}
          <span className={`sev sev--${top} queue-row__meter`} aria-hidden>
            <span className="sev__meter">
              {[1, 2, 3, 4].map((i) => (
                <span key={i} className={i <= SEV_TICKS[top] ? "is-on" : undefined} />
              ))}
            </span>
          </span>
          <span>
            <strong>{total} open:</strong>{" "}
            {SEVERITIES.filter((s) => item.open_counts![s] > 0).map((s, i, arr) => (
              <span key={s}>
                <span className={`queue-sev queue-sev--${s}`}>
                  <b>{item.open_counts![s]}</b> {s}
                </span>
                {i < arr.length - 1 ? ", " : ""}
              </span>
            ))}
          </span>
        </span>
      )}
      {group === "ready" && !item.reasons?.length && <span className="queue-row__ready">Ready for a proposal</span>}
    </>
  );
}
