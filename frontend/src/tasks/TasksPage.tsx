import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  CaretDown,
  CaretUp,
  CaretUpDown,
  Check,
  CheckCircle,
  LinkBreak,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  Trash,
  Warning,
} from "@phosphor-icons/react";
import { Glyph } from "../voice/marks";
import { api, ApiError } from "../api/client";
import type { Task } from "../api/types";
import { useToast, type ToastHandle } from "../ui/Toast";
import { useFadeOnChange } from "../ui/motion";
import { RowMenu } from "./RowMenu";
import { TaskDialog, type TaskDialogMode } from "./TaskDialog";
import {
  DEFAULT_SORT,
  FIRST_DIR,
  discrepancyHref,
  formatCreated,
  formatDue,
  friendlyRunLabel,
  isOverdue,
  matchesFilter,
  matchesSearch,
  sortTasks,
  todayIso,
  type Filter,
  type Sort,
  type SortKey,
} from "./taskUtil";
import "./tasks.css";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "done", label: "Done" },
  { value: "all", label: "All" },
];

const SORT_LABEL: Record<SortKey, string> = { title: "Title", due: "Due date", created: "Created" };

const PHONE_SORTS: { value: string; label: string }[] = [
  { value: "due:asc", label: "Due date, soonest first" },
  { value: "due:desc", label: "Due date, latest first" },
  { value: "created:desc", label: "Created, newest first" },
  { value: "created:asc", label: "Created, oldest first" },
  { value: "title:asc", label: "Title, A to Z" },
  { value: "title:desc", label: "Title, Z to A" },
];

export const EMPTY_TEXT = "No tasks yet. Create one, or add one from a Policy Check discrepancy.";

/**
 * Slice 3b T7 (spec 2.2, R3 to R5, D2, D3, D6, D8, O3, O7): the broker's follow-ups. Client-side search, Open / Done /
 * All filter and sortable columns over GET /api/tasks; done checkbox (optimistic), new / edit dialog, delete with Undo
 * (the DELETE waits for the toast to close), deep link `#/tasks?task=<id>`. Decisions in TASKS-BRIEF.md.
 */
export function TasksPage({ targetId }: { targetId: number | null }) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("open");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT);
  // Ticked (or created) while the view would hide them: kept in place until the filter or search changes.
  const [kept, setKept] = useState<number[]>([]);
  // Deleted, waiting for the undo toast to close (D6).
  const [hidden, setHidden] = useState<number[]>([]);
  const [dialog, setDialog] = useState<{ mode: TaskDialogMode; animate: boolean } | null>(null);
  const [missingTarget, setMissingTarget] = useState(false);
  const [announce, setAnnounce] = useState("");
  const toast = useToast();
  const opener = useRef<HTMLElement | null>(null);
  const focusAfter = useRef<number | null>(null);
  const saving = useRef(new Set<number>());
  const pending = useRef(new Map<number, { commit: () => Promise<void>; handle: ToastHandle }>());
  const searchId = useId();
  const sortId = useId();
  // A pointer filter or sort change re-settles the table with a short opacity lift; typing in search is instant.
  const tableRef = useRef<HTMLTableElement>(null);
  useFadeOnChange(tableRef, `${filter}|${search}|${sort.key}:${sort.dir}`);

  const load = useCallback(async () => {
    setError(null);
    try {
      setTasks(await api.tasks());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't load the tasks.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Navigation away commits every pending delete and closes its now-stale Undo toast (D6).
  useEffect(() => {
    const map = pending.current;
    return () => {
      for (const { commit, handle } of [...map.values()]) {
        void commit();
        handle.close();
      }
    };
  }, []);

  const resetView = (f: Filter, q: string) => {
    setKept([]);
    setFilter(f);
    setSearch(q);
  };

  // Deep link (O7): show the task (filter to All if it is filtered out), then scroll to it and focus its title.
  const handled = useRef<number | null>(null);
  useEffect(() => {
    if (!tasks || targetId == null || handled.current === targetId) return;
    handled.current = targetId;
    const t = tasks.find((x) => x.id === targetId);
    setMissingTarget(!t);
    if (!t) return;
    if (!matchesFilter(t, filter) || !matchesSearch(t, search)) resetView("all", "");
    focusAfter.current = t.id;
    // Only when the target or the first load changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks, targetId]);

  useEffect(() => {
    const id = focusAfter.current;
    if (id == null) return;
    const el = document.querySelector<HTMLElement>(`[data-task="${id}"] .task-title`);
    if (!el) return;
    focusAfter.current = null;
    el.scrollIntoView({ block: "center" });
    el.focus({ preventScroll: true });
  });

  const visible = useMemo(() => {
    if (!tasks) return [];
    return sortTasks(
      tasks.filter((t) => !hidden.includes(t.id) && ((matchesFilter(t, filter) && matchesSearch(t, search)) || kept.includes(t.id))),
      sort,
    );
  }, [tasks, hidden, filter, search, kept, sort]);

  const live = tasks?.filter((t) => !hidden.includes(t.id)) ?? [];
  const openCount = live.filter((t) => t.status === "open").length;
  const doneCount = live.length - openCount;
  const today = todayIso();

  const openDialog = (mode: TaskDialogMode, from: HTMLElement | null, animate: boolean) => {
    opener.current = from;
    setDialog({ mode, animate });
  };

  const onSaved = (task: Task, created: boolean) => {
    setTasks((ts) => (ts ? (created ? [task, ...ts] : ts.map((t) => (t.id === task.id ? task : t))) : [task]));
    // A new or edited task stays visible even when the filter or search would hide it.
    setKept((k) => (k.includes(task.id) ? k : [...k, task.id]));
    focusAfter.current = task.id;
    opener.current = null;
    toast({ message: created ? "Task created." : "Task saved." });
  };

  const toggle = async (task: Task) => {
    if (saving.current.has(task.id)) return;
    saving.current.add(task.id);
    const next = task.status === "open" ? "done" : "open";
    setTasks((ts) => ts?.map((t) => (t.id === task.id ? { ...t, status: next } : t)) ?? ts);
    setKept((k) => (k.includes(task.id) ? k : [...k, task.id]));
    setAnnounce(next === "done" ? `Done: ${task.title}.` : `Reopened: ${task.title}.`);
    try {
      const saved = await api.patchTask(task.id, { status: next });
      setTasks((ts) => ts?.map((t) => (t.id === saved.id ? saved : t)) ?? ts);
    } catch (e) {
      setTasks((ts) => ts?.map((t) => (t.id === task.id ? { ...t, status: task.status } : t)) ?? ts);
      toast({ message: e instanceof ApiError ? `Couldn't save: ${e.message}` : "Couldn't save the task. Try again.", tone: "error" });
    } finally {
      saving.current.delete(task.id);
    }
  };

  const remove = (task: Task) => {
    const id = task.id;
    setHidden((h) => [...h, id]);
    const restore = () => setHidden((h) => h.filter((x) => x !== id));
    const commit = async () => {
      if (!pending.current.has(id)) return;
      pending.current.delete(id);
      try {
        await api.deleteTask(id);
        setTasks((ts) => ts?.filter((t) => t.id !== id) ?? ts);
        setHidden((h) => h.filter((x) => x !== id));
      } catch {
        restore();
        toast({ message: "Couldn't delete the task. It is back in the list.", tone: "error" });
      }
    };
    const handle = toast({
      message: "Task deleted.",
      action: {
        label: "Undo",
        run: () => {
          pending.current.delete(id);
          restore();
          focusAfter.current = id;
        },
      },
      onClose: (reason) => {
        if (reason !== "action") void commit();
      },
    });
    pending.current.set(id, { commit, handle });
    setAnnounce(`Deleted: ${task.title}.`);
    // Focus would fall to the page: put it on the table's next sensible control.
    window.setTimeout(() => document.querySelector<HTMLElement>(".tasks-table tbody .task-title, .tasks__new")?.focus(), 0);
  };

  const headerSort = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: FIRST_DIR[key] }));

  const sortHeader = (key: SortKey, className: string) => {
    const on = sort.key === key;
    const Icon = on ? (sort.dir === "asc" ? CaretUp : CaretDown) : CaretUpDown;
    return (
      <th role="columnheader" scope="col" className={className} aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}>
        <button type="button" className={`sort-btn${on ? " is-on" : ""}`} onClick={() => headerSort(key)}>
          {SORT_LABEL[key]}
          <Icon size={14} weight={on ? "bold" : "regular"} aria-hidden />
        </button>
      </th>
    );
  };

  const newButton = (
    <button type="button" className="btn btn--primary tasks__new" onClick={(e) => openDialog({ kind: "new" }, e.currentTarget, e.detail > 0)}>
      <Plus size={18} aria-hidden /> New task
    </button>
  );

  const empty = tasks != null && live.length === 0;

  return (
    <div className="workspace">
      <main className="pane tasks" id="main" tabIndex={-1} aria-labelledby="tasks-title">
        <div className="tasks__body">
          <header className="tasks__head">
            <div className="tasks__title-row">
              <h1 id="tasks-title" className="page-title">
                Tasks
              </h1>
              {!empty && tasks != null && newButton}
            </div>
            {tasks != null && !empty && (
              <p className="tasks__count" role="status">
                {openCount} open, {doneCount} done
              </p>
            )}
          </header>

          {error ? (
            <div className="notice notice--error" role="alert">
              <span className="notice__text">{error}</span>
              <button type="button" className="btn btn--secondary btn--sm" onClick={() => void load()}>
                Retry
              </button>
            </div>
          ) : tasks == null ? (
            <div className="tasks-skeleton" aria-busy="true">
              <span className="sr-only" role="status">
                Loading tasks
              </span>
              {[0, 1, 2].map((i) => (
                <span key={i} aria-hidden />
              ))}
            </div>
          ) : empty ? (
            <div className="tasks__empty">
              <Glyph name="tasks" size={32} className="muted-glyph" />
              <h2 className="state-block__title">{EMPTY_TEXT}</h2>
              {newButton}
            </div>
          ) : (
            <section className="tasks__list" aria-labelledby="tasks-title">
              <div className="tasks__toolbar">
                <div className="tasks__search">
                  <label htmlFor={searchId} className="sr-only">
                    Search tasks
                  </label>
                  <MagnifyingGlass size={18} className="tasks__search-icon" aria-hidden />
                  <input
                    id={searchId}
                    type="search"
                    className="input"
                    placeholder="Search by title or run"
                    value={search}
                    autoComplete="off"
                    onChange={(e) => {
                      setKept([]);
                      setSearch(e.target.value);
                    }}
                  />
                </div>
                <FilterChoice value={filter} onChange={(f) => resetView(f, search)} />
                <div className="tasks__sort-phone">
                  <label htmlFor={sortId} className="field__label">
                    Sort by
                  </label>
                  <select
                    id={sortId}
                    className="input select"
                    value={`${sort.key}:${sort.dir}`}
                    onChange={(e) => {
                      const [key, dir] = e.target.value.split(":") as [SortKey, Sort["dir"]];
                      setSort({ key, dir });
                    }}
                  >
                    {PHONE_SORTS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {missingTarget && (
                <p className="notice notice--info" role="note">
                  <span className="notice__text">That task no longer exists.</span>
                </p>
              )}

              {visible.length === 0 ? (
                <div className="tasks__empty tasks__empty--match">
                  <h2 className="state-block__title">No tasks match this search and filter.</h2>
                  <button type="button" className="btn btn--secondary" onClick={() => resetView("all", "")}>
                    Show all tasks
                  </button>
                </div>
              ) : (
                <table ref={tableRef} className="tasks-table" role="table" aria-label="Tasks">
                  <thead role="rowgroup">
                    <tr role="row">
                      <th role="columnheader" scope="col" className="tasks-table__check">
                        <span className="sr-only">Done</span>
                      </th>
                      {sortHeader("title", "tasks-table__title")}
                      {sortHeader("due", "tasks-table__due")}
                      <th role="columnheader" scope="col" className="tasks-table__link">
                        Linked to
                      </th>
                      {sortHeader("created", "tasks-table__created")}
                      <th role="columnheader" scope="col" className="tasks-table__menu">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody role="rowgroup">
                    {visible.map((t) => (
                      <TaskRow
                        key={t.id}
                        task={t}
                        today={today}
                        target={t.id === targetId}
                        onToggle={() => void toggle(t)}
                        onEdit={(from, animate) => openDialog({ kind: "edit", task: t }, from, animate)}
                        onDelete={() => remove(t)}
                      />
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          )}
        </div>
        <p className="sr-only" role="status" aria-live="polite">
          {announce}
        </p>
      </main>
      {dialog && (
        <TaskDialog
          mode={dialog.mode}
          animate={dialog.animate}
          onSaved={onSaved}
          onClose={() => {
            setDialog(null);
            const back = opener.current;
            if (back && document.contains(back)) window.setTimeout(() => back.focus(), 0);
          }}
        />
      )}
    </div>
  );
}

function FilterChoice({ value, onChange }: { value: Filter; onChange: (f: Filter) => void }) {
  const name = useId();
  return (
    <fieldset className="line-choice line-choice--compact tasks__filter" aria-labelledby={`${name}-label`}>
      <span id={`${name}-label`} className="line-choice__inline-label">
        Show
      </span>
      <div className="line-choice__seg">
        {FILTERS.map((f) => (
          <label key={f.value} className="line-choice__opt">
            <input type="radio" name={name} value={f.value} checked={value === f.value} onChange={() => onChange(f.value)} />
            {value === f.value && <Check size={14} weight="bold" className="line-choice__check" aria-hidden />}
            <span>{f.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function TaskRow({
  task,
  today,
  target,
  onToggle,
  onEdit,
  onDelete,
}: {
  task: Task;
  today: string;
  target: boolean;
  onToggle: () => void;
  onEdit: (from: HTMLElement | null, animate: boolean) => void;
  onDelete: () => void;
}) {
  const titleRef = useRef<HTMLButtonElement>(null);
  const overdue = isOverdue(task, today);
  const done = task.status === "done";
  return (
    <tr role="row" data-task={task.id} className={`task-row${done ? " is-done" : ""}${target ? " is-target" : ""}`}>
      <td role="cell" className="tasks-table__check">
        <label className="task-check">
          <input type="checkbox" checked={done} onChange={onToggle} aria-label={`Done, ${task.title}`} />
        </label>
      </td>
      <td role="cell" className="tasks-table__title">
        <button ref={titleRef} type="button" className="task-title" aria-label={`Edit task: ${task.title}`} onClick={(e) => onEdit(e.currentTarget, e.detail > 0)}>
          {task.title}
        </button>
      </td>
      <td role="cell" className="tasks-table__due">
        {task.due_date ? (
          <span className="task-due">
            <span>
              <span className="task-cell__label">Due </span>
              <time dateTime={task.due_date}>{formatDue(task.due_date)}</time>
            </span>
            {overdue && (
              <span className="task-overdue">
                <Warning size={14} weight="bold" aria-hidden /> Overdue
              </span>
            )}
          </span>
        ) : (
          <span className="sr-only">No due date</span>
        )}
      </td>
      <td role="cell" className="tasks-table__link">
        <LinkedItem task={task} />
      </td>
      <td role="cell" className="tasks-table__created">
        <span className="task-cell__label">Created </span>
        <time dateTime={task.created_at}>{formatCreated(task.created_at)}</time>
      </td>
      <td role="cell" className="tasks-table__menu">
        <RowMenu
          label={`Actions for ${task.title}`}
          items={[
            { label: "Edit", icon: <PencilSimple size={16} aria-hidden />, run: () => onEdit(titleRef.current, false) },
            { label: "Delete", icon: <Trash size={16} aria-hidden />, danger: true, run: onDelete },
          ]}
        />
      </td>
    </tr>
  );
}

/** The linked run and discrepancy, with the item's live state (R5), dropped items (D3, D8) and removed runs (D2). */
export function LinkedItem({ task }: { task: Task }) {
  if (task.run_id == null) return <span className="sr-only">Not linked</span>;
  if (task.run_missing) {
    return (
      <span className="task-link task-link--gone">
        <LinkBreak size={16} aria-hidden /> Linked run removed
      </span>
    );
  }
  const d = task.discrepancy;
  const runHref = `#/runs/${task.run_id}`;
  return (
    <span className="task-link">
      <a className="task-link__run" href={runHref}>
        {friendlyRunLabel(task.run_label, task.run_id)}
      </a>
      {d &&
        (d.in_report ? (
          <a className="task-chip" href={discrepancyHref(task.run_id, d.item_id)} title={`${d.title} · ${d.status === "resolved" ? "Resolved" : "Open"}`}>
            <span className="task-chip__title">{d.title}</span>
            <span className="task-chip__state">
              {" · "}
              {d.status === "resolved" ? (
                <>
                  <CheckCircle size={14} weight="fill" aria-hidden /> Resolved
                </>
              ) : (
                "Open"
              )}
            </span>
          </a>
        ) : (
          <a className="task-chip task-chip--dropped" href={runHref} title={`${d.title} · No longer in the report`}>
            <span className="task-chip__title">{d.title}</span>
            <span className="task-chip__state"> · No longer in the report</span>
          </a>
        ))}
    </span>
  );
}
