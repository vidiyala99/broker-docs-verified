import { useCallback, useEffect, useId, useMemo, useRef, useState, type MouseEvent } from "react";
import { Check, CheckCircle, ClockCounterClockwise, Eye, ListChecks, NotePencil } from "@phosphor-icons/react";
import { Glyph } from "../voice/marks";
import { api, ApiError } from "../api/client";
import {
  POLICY_ROLES,
  SEVERITIES,
  type Artifact,
  type Chip as ChipData,
  type DiscrepancyItem,
  type DiscrepancyList,
  type Run,
  type Severity,
} from "../api/types";
import type { OpenSource } from "../report/Chip";
import { SlotView } from "./Slot";
import { ReviewDialog } from "./ReviewDialog";
import { TaskDialog } from "../tasks/TaskDialog";
import { discrepancyFromHash, followUpTitle, taskHref } from "../tasks/taskUtil";
import { useToast } from "../ui/Toast";
import { useFadeOnChange } from "../ui/motion";
import "./policy.css";

const SEV_LABEL: Record<Severity, string> = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
const SEV_TICKS: Record<Severity, number> = { critical: 4, high: 3, medium: 2, low: 1 };
export const MAX_NOTE = 2000;

/** Severity: the plain uppercase status word plus a four-tick meter, so tier reads without colour. */
export function SeverityMark({ severity }: { severity: Severity }) {
  const n = SEV_TICKS[severity];
  return (
    <span className={`sev sev--${severity}`}>
      <span className="sev__meter" aria-hidden>
        {[1, 2, 3, 4].map((i) => (
          <span key={i} className={i <= n ? "is-on" : undefined} />
        ))}
      </span>
      <span className="sev__word">{SEV_LABEL[severity]}</span>
    </span>
  );
}

/** One item's status and note, saved through PATCH /discrepancies/{id}. */
export function useResolve(runId: number, onSaved: (item: DiscrepancyItem) => void) {
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  const save = useCallback(
    async (item: DiscrepancyItem, body: { status?: "open" | "resolved"; note?: string | null }) => {
      setSaving(item.id);
      setError(null);
      try {
        onSaved(await api.patchDiscrepancy(runId, item.id, body));
        return true;
      } catch (e) {
        setError({ id: item.id, message: e instanceof ApiError ? e.message : "Couldn't save." });
        return false;
      } finally {
        setSaving(null);
      }
    },
    [runId, onSaved],
  );
  return { save, saving, error };
}

export function Discrepancies({
  run,
  artifact,
  chips,
  onOpen,
  editable,
}: {
  run: Run;
  artifact: Artifact;
  chips: Record<string, ChipData>;
  onOpen: OpenSource;
  editable: boolean;
}) {
  const [data, setData] = useState<DiscrepancyList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [severities, setSeverities] = useState<Severity[]>([]);
  const [hideResolved, setHideResolved] = useState(false);
  // Resolved while "Hide resolved" is on: kept in place until the filters change, so focus never jumps.
  const [kept, setKept] = useState<string[]>([]);
  const [review, setReview] = useState<{ ids: string[]; index: number; animate: boolean } | null>(null);
  const [announce, setAnnounce] = useState("");
  const opener = useRef<HTMLElement | null>(null);
  // Slice 3b T7: "Create task" opens the task dialog prefilled (spec 2.3); `?discrepancy=<id>` targets a card (O7).
  const [taskFor, setTaskFor] = useState<{ item: DiscrepancyItem; animate: boolean; opener: HTMLElement | null } | null>(null);
  const [target, setTarget] = useState<string | null>(() => discrepancyFromHash(window.location.hash));
  const focusTask = useRef<string | null>(null);
  const toast = useToast();

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api.discrepancies(run.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't load the discrepancies.");
    }
  }, [run.id]);

  useEffect(() => {
    void load();
  }, [load, artifact.version]);

  const onSaved = useCallback((item: DiscrepancyItem) => {
    setData((d) => (d ? { ...d, items: d.items.map((x) => (x.id === item.id ? item : x)) } : d));
    if (item.status === "resolved") setKept((k) => (k.includes(item.id) ? k : [...k, item.id]));
    setAnnounce(item.status === "resolved" ? `Resolved: ${item.title}.` : `Reopened: ${item.title}.`);
  }, []);
  const resolve = useResolve(run.id, onSaved);

  useEffect(() => {
    const on = () => setTarget(discrepancyFromHash(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const items = data?.items ?? [];
  const resolvedCount = items.filter((i) => i.status === "resolved").length;
  const visible = useMemo(
    () =>
      items.filter(
        (i) =>
          (severities.length === 0 || severities.includes(i.severity)) &&
          !(hideResolved && i.status === "resolved" && !kept.includes(i.id)),
      ),
    [items, severities, hideResolved, kept],
  );
  // A pointer filter change (severity, Hide resolved) re-settles the list with a short opacity lift; keyboard
  // toggles are instant. The cards are not remounted.
  const listRef = useRef<HTMLOListElement>(null);
  useFadeOnChange(listRef, `${severities.join(",")}|${hideResolved}`);

  // Deep link (O7): once the list is loaded, clear filters that hide the card, then scroll it into view, outline and
  // focus it. Runs again when the hash names another card.
  const scrolled = useRef<string | null>(null);
  useEffect(() => {
    if (!data || !target || scrolled.current === target) return;
    if (!data.items.some((i) => i.id === target)) return;
    if (!visible.some((i) => i.id === target)) {
      setSeverities([]);
      setHideResolved(false);
      setKept([]);
      return;
    }
    const card = document.querySelector<HTMLElement>(`.disc[data-item="${CSS.escape(target)}"]`);
    if (!card) return;
    scrolled.current = target;
    // Centred when it fits; a card taller than most of the screen (phone) starts at the top so its heading shows.
    card.scrollIntoView({ block: card.offsetHeight > window.innerHeight * 0.6 ? "start" : "center" });
    card.focus({ preventScroll: true });
  }, [data, target, visible]);

  useEffect(() => {
    const id = focusTask.current;
    if (!id) return;
    focusTask.current = null;
    document.querySelector<HTMLElement>(`.disc[data-item="${CSS.escape(id)}"] .disc__task`)?.focus();
  });

  // A card chip opens its source side by side; the list then reflows to half width, so bring the
  // card back into view once the split has settled (--dur-base).
  const openFromCard: OpenSource = useCallback(
    (chip, via) => {
      const card = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>(".disc");
      onOpen(chip, via);
      if (card) window.setTimeout(() => card.scrollIntoView({ block: "nearest" }), 220);
    },
    [onOpen],
  );

  const toggleSeverity = (s: Severity) => {
    setKept([]);
    setSeverities((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  };

  const openReview = (id: string | null, e: MouseEvent) => {
    opener.current = e.currentTarget as HTMLElement;
    const ids = visible.map((i) => i.id);
    if (!ids.length) return;
    setReview({ ids, index: Math.max(0, id ? ids.indexOf(id) : 0), animate: e.detail > 0 });
  };

  const noneText = artifact.content.sections
    .flatMap((s) => s.blocks)
    .find((b) => b.type === "paragraph" && b.id === "discrepancies-none");

  if (error) {
    return (
      <div className="state-block" role="alert">
        <p>{error}</p>
        <button type="button" className="btn btn--secondary" onClick={() => void load()}>
          Retry
        </button>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="disc-list" aria-busy="true">
        <span className="sr-only" role="status">
          Loading discrepancies
        </span>
        {[0, 1, 2].map((i) => (
          <div key={i} className="disc disc--skeleton" aria-hidden>
            <div className="skeleton-lines">
              <span />
              <span />
            </div>
            <div className="disc__slots">
              {POLICY_ROLES.map((r) => (
                <div key={r} className="slot slot--skeleton" />
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (data.total === 0) {
    return (
      <div className="state-block disc-empty">
        <Glyph name="done" size={32} className="ok-glyph" />
        <p className="state-block__title">No discrepancies found.</p>
        <p className="hint">
          {noneText && noneText.type === "paragraph" ? noneText.segments.map((s) => s.text).join("") : "Every value the renewal states agrees with the other documents."}
        </p>
      </div>
    );
  }

  return (
    <div className="disc-view">
      <div className="disc-head">
        <p className="disc-head__count" role="status" aria-live="polite" aria-atomic="true">
          {data.total} {data.total === 1 ? "discrepancy" : "discrepancies"}, {resolvedCount} resolved
        </p>
        <button type="button" className="btn btn--secondary btn--sm" onClick={(e) => openReview(null, e)} disabled={!visible.length}>
          <Eye size={16} aria-hidden /> Review one at a time
        </button>
      </div>
      <div className="disc-filters" role="group" aria-label="Filter discrepancies">
        <div className="sev-filters">
          <button
            type="button"
            className="sev-filter"
            aria-pressed={severities.length === 0}
            onClick={() => {
              setKept([]);
              setSeverities([]);
            }}
          >
            {severities.length === 0 && <Check size={14} weight="bold" className="sev-filter__check" aria-hidden />}
            All <span className="sev-filter__count">{data.total}</span>
          </button>
          {SEVERITIES.map((s) => {
            const on = severities.includes(s);
            return (
              <button
                key={s}
                type="button"
                className={`sev-filter sev-filter--${s}`}
                aria-pressed={on}
                disabled={data.counts[s] === 0}
                onClick={() => toggleSeverity(s)}
              >
                {on && <Check size={14} weight="bold" className="sev-filter__check" aria-hidden />}
                {SEV_LABEL[s]} <span className="sev-filter__count">{data.counts[s]}</span>
              </button>
            );
          })}
        </div>
        <label className="hide-resolved">
          <input
            type="checkbox"
            checked={hideResolved}
            onChange={(e) => {
              setKept([]);
              setHideResolved(e.target.checked);
            }}
          />
          Hide resolved <span className="sev-filter__count">{resolvedCount}</span>
        </label>
      </div>

      {visible.length === 0 ? (
        <div className="state-block">
          <p className="state-block__title">No discrepancies match these filters.</p>
          <button
            type="button"
            className="btn btn--secondary"
            onClick={() => {
              setSeverities([]);
              setHideResolved(false);
              setKept([]);
            }}
          >
            Show all
          </button>
        </div>
      ) : (
        <ol ref={listRef} className="disc-list" aria-label="Discrepancies">
          {visible.map((item) => (
            <li key={item.id}>
              <DiscrepancyCard
                item={item}
                run={run}
                chips={chips}
                onOpen={openFromCard}
                editable={editable}
                resolve={resolve}
                onReview={(e) => openReview(item.id, e)}
                target={item.id === target}
                onCreateTask={(e) => setTaskFor({ item, animate: e.detail > 0, opener: e.currentTarget as HTMLElement })}
              />
            </li>
          ))}
        </ol>
      )}

      {review && (
        <ReviewDialog
          run={run}
          items={review.ids.map((id) => items.find((i) => i.id === id)).filter((i): i is DiscrepancyItem => Boolean(i))}
          index={review.index}
          animate={review.animate}
          onIndex={(index) => setReview((r) => (r ? { ...r, index } : r))}
          onClose={() => {
            setReview(null);
            window.setTimeout(() => opener.current?.focus(), 0);
          }}
          chips={chips}
          editable={editable}
          resolve={resolve}
        />
      )}
      {taskFor && (
        <TaskDialog
          mode={{ kind: "new", prefill: { title: followUpTitle(taskFor.item.title), runId: run.id, itemId: taskFor.item.id } }}
          animate={taskFor.animate}
          onSaved={(task) => {
            // The card shows "Task: Open" at once; the next list load agrees (API `task`, O4).
            const linked = task.run_id === run.id ? task.discrepancy_item_id : null;
            if (linked) {
              setData((d) => (d ? { ...d, items: d.items.map((x) => (x.id === linked && !x.task ? { ...x, task: { id: task.id, status: task.status } } : x)) } : d));
              focusTask.current = linked;
            }
            toast({ message: "Task created." });
          }}
          onClose={() => {
            const back = taskFor.opener;
            setTaskFor(null);
            if (back && document.contains(back)) window.setTimeout(() => back.focus(), 0);
          }}
        />
      )}
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>
    </div>
  );
}

export function DiscrepancyCard({
  item,
  run,
  chips,
  onOpen,
  editable,
  resolve,
  onReview,
  target = false,
  onCreateTask,
}: {
  item: DiscrepancyItem;
  run: Run;
  chips: Record<string, ChipData>;
  onOpen: OpenSource;
  editable: boolean;
  resolve: ReturnType<typeof useResolve>;
  onReview: (e: MouseEvent) => void;
  /** `#/runs/<id>?discrepancy=<item_id>` names this card (O7). */
  target?: boolean;
  onCreateTask?: (e: MouseEvent) => void;
}) {
  const titleId = useId();
  return (
    <article
      className={`disc${item.status === "resolved" ? " is-resolved" : ""}${target ? " is-target" : ""}`}
      aria-labelledby={titleId}
      data-item={item.id}
      tabIndex={target ? -1 : undefined}
    >
      <ItemHead item={item} titleId={titleId} />
      <p className="disc__prose">{item.prose.map((s) => s.text).join("")}</p>
      <div className="disc__slots">
        {POLICY_ROLES.map((role) => (
          <SlotView key={role} item={item} role={role} docs={run.documents} chips={chips} onOpen={onOpen} />
        ))}
      </div>
      <ResolveControls item={item} editable={editable} resolve={resolve}>
        {/* Slice 3b T7 (spec 2.3): a task for this item, or the action that makes one. */}
        {item.task ? (
          <a className="disc__task" href={taskHref(item.task.id)}>
            <ListChecks size={16} aria-hidden /> Task: {item.task.status === "done" ? "Done" : "Open"}
          </a>
        ) : (
          editable &&
          onCreateTask && (
            <button type="button" className="btn btn--ghost btn--sm" onClick={onCreateTask}>
              <ListChecks size={16} aria-hidden /> Create task
            </button>
          )
        )}
        <button type="button" className="btn btn--ghost btn--sm" onClick={onReview}>
          <Eye size={16} aria-hidden /> Review
        </button>
      </ResolveControls>
    </article>
  );
}

export function ItemHead({ item, titleId, as = "h3" }: { item: DiscrepancyItem; titleId: string; as?: "h2" | "h3" }) {
  const Tag = as;
  return (
    <div className="disc__head">
      <SeverityMark severity={item.severity} />
      <Tag id={titleId} className="disc__title">
        {item.title}
      </Tag>
      <span className="disc__status">
        {item.changed_since_resolved && (
          <span className="stale-tag" title="The values changed after you resolved this. Check them again.">
            <ClockCounterClockwise size={14} aria-hidden /> Changed since resolved
          </span>
        )}
        {item.status === "resolved" ? (
          <span className="disc__resolved">
            <CheckCircle size={16} weight="fill" aria-hidden /> Resolved
          </span>
        ) : (
          <span className="status-word">Open</span>
        )}
      </span>
      {item.changed_since_resolved && <p className="disc__changed">The values changed after you resolved this. Check them again.</p>}
    </div>
  );
}

export function ResolveControls({
  item,
  editable,
  resolve,
  children,
}: {
  item: DiscrepancyItem;
  editable: boolean;
  resolve: ReturnType<typeof useResolve>;
  children?: React.ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState(item.note ?? "");
  const noteId = useId();
  const area = useRef<HTMLTextAreaElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const busy = resolve.saving === item.id;
  const err = resolve.error?.id === item.id ? resolve.error.message : null;

  useEffect(() => {
    if (!editing) setNote(item.note ?? "");
  }, [item.note, editing]);
  useEffect(() => {
    if (editing) area.current?.focus();
  }, [editing]);

  const saveNote = async () => {
    const ok = await resolve.save(item, { note: note.trim() ? note.trim() : null });
    if (ok) {
      setEditing(false);
      window.setTimeout(() => toggleRef.current?.focus(), 0);
    }
  };

  return (
    <div className="disc__foot">
      {item.note && !editing && (
        <p className="disc__note">
          <NotePencil size={16} aria-hidden />
          <span>
            <span className="sr-only">Note: </span>
            {item.note}
          </span>
        </p>
      )}
      {editing && (
        <div className="note-edit">
          <label htmlFor={noteId} className="field__label">
            Note
          </label>
          <textarea
            id={noteId}
            ref={area}
            className="input textarea"
            rows={3}
            maxLength={MAX_NOTE}
            value={note}
            disabled={busy}
            aria-describedby={`${noteId}-c`}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                setEditing(false);
                window.setTimeout(() => toggleRef.current?.focus(), 0);
              }
            }}
          />
          <div className="note-edit__row">
            <span id={`${noteId}-c`} className={`counter${note.length > MAX_NOTE * 0.9 ? " is-near" : ""}`}>
              {note.length.toLocaleString()} / {MAX_NOTE.toLocaleString()}
            </span>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="btn btn--primary btn--sm" onClick={() => void saveNote()} disabled={busy}>
              {busy ? "Saving..." : "Save note"}
            </button>
          </div>
        </div>
      )}
      <div className="disc__actions">
        {editable && (
          <>
            <button
              type="button"
              className="btn btn--sm btn--secondary"
              disabled={busy}
              onClick={() => void resolve.save(item, { status: item.status === "resolved" ? "open" : "resolved" })}
            >
              {busy && !editing ? (
                "Saving..."
              ) : item.status === "resolved" ? (
                "Reopen"
              ) : (
                <>
                  <Check size={16} aria-hidden /> Mark resolved
                </>
              )}
            </button>
            {!editing && (
              <button type="button" ref={toggleRef} className="btn btn--ghost btn--sm" onClick={() => setEditing(true)} disabled={busy}>
                <NotePencil size={16} aria-hidden /> {item.note ? "Edit note" : "Add note"}
              </button>
            )}
          </>
        )}
        {children}
        {err && (
          <p className="inline-error disc__error" role="alert">
            {err === "Couldn't save." ? "Couldn't save. Try again." : `Couldn't save: ${err}`}
          </p>
        )}
      </div>
    </div>
  );
}
