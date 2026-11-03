import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import type { Run, Task, TaskInput } from "../api/types";
import { POLICY_WORKFLOW } from "../config";
import { useDialogClose } from "../ui/motion";
import { MAX_TITLE, runOptionLabel } from "./taskUtil";
import "./tasks.css";

export type TaskDialogMode =
  | { kind: "new"; prefill?: { title: string; runId: number; itemId: string } }
  | { kind: "edit"; task: Task };

type Field = "title" | "due_date" | "run_id" | "discrepancy_item_id";
type Errors = Partial<Record<Field | "general", string>>;

interface ItemOption {
  id: string;
  title: string;
}

/**
 * Slice 3b T7: one dialog for new and edit (spec 2.2). Native <dialog> with showModal(): focus stays inside, Esc
 * closes, the page behind is inert. Title, optional due date, optional run, then optional discrepancy of that run
 * (Policy Check runs only). `400 invalid_task` messages show under the named field. TASKS-BRIEF.md.
 */
export function TaskDialog({
  mode,
  animate,
  onClose,
  onSaved,
}: {
  mode: TaskDialogMode;
  animate: boolean;
  onClose: () => void;
  onSaved: (task: Task, created: boolean) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const base = useId();
  const original = mode.kind === "edit" ? mode.task : null;
  const prefill = mode.kind === "new" ? mode.prefill : undefined;

  const [title, setTitle] = useState(original?.title ?? prefill?.title ?? "");
  const [due, setDue] = useState(original?.due_date ?? "");
  const [runId, setRunId] = useState<string>(String(original?.run_id ?? prefill?.runId ?? ""));
  const [itemId, setItemId] = useState<string>(original?.discrepancy_item_id ?? prefill?.itemId ?? "");
  const [errors, setErrors] = useState<Errors>({});
  const [saving, setSaving] = useState(false);

  const [runs, setRuns] = useState<Run[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [items, setItems] = useState<{ runId: string; list: ItemOption[] | null; note: string | null } | null>(null);

  const fieldRefs = {
    title: useRef<HTMLInputElement>(null),
    due_date: useRef<HTMLInputElement>(null),
    run_id: useRef<HTMLSelectElement>(null),
    discrepancy_item_id: useRef<HTMLSelectElement>(null),
  };

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    fieldRefs.title.current?.focus();
    // Once, on open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let live = true;
    api.runs(100).then(
      (r) => live && setRuns(r),
      (e: unknown) => live && setRunsError(e instanceof ApiError ? e.message : "Couldn't load the runs."),
    );
    return () => {
      live = false;
    };
  }, []);

  const selectedRun = runs?.find((r) => String(r.id) === runId) ?? null;
  // A Policy Check run can link one of its discrepancies. Before the run list loads, a task that already links an
  // item is known to be a Policy Check one.
  const isPolicy = selectedRun ? selectedRun.workflow_name === POLICY_WORKFLOW : Boolean(runId && itemId);

  useEffect(() => {
    if (!runId || !isPolicy) {
      setItems(null);
      return;
    }
    let live = true;
    setItems({ runId, list: null, note: null });
    api.discrepancies(Number(runId)).then(
      (d) => live && setItems({ runId, list: d.items.map((i) => ({ id: i.id, title: i.title })), note: d.items.length ? null : "This run has no discrepancies." }),
      (e: unknown) =>
        live &&
        setItems({
          runId,
          list: [],
          note: e instanceof ApiError && e.code === "no_report" ? "This run has no report yet, so no discrepancy can be linked." : "Couldn't load this run's discrepancies.",
        }),
    );
    return () => {
      live = false;
    };
  }, [runId, isPolicy]);

  // The linked item may have left the report (D3): keep it choosable under its saved title so an edit keeps the link.
  const itemOptions = useMemo(() => {
    const list = items?.runId === runId ? (items.list ?? []) : [];
    const savedId = original?.discrepancy_item_id;
    if (original && savedId && String(original.run_id) === runId && !list.some((i) => i.id === savedId)) {
      const saved = original.discrepancy?.title ?? savedId;
      return [...list, { id: savedId, title: original.discrepancy?.in_report === false ? `${saved} (no longer in the report)` : saved }];
    }
    return list;
  }, [items, runId, original]);

  // The task's run is always an option, even when it is not among the newest 100 or no longer exists (D2, D5).
  const runOptions = useMemo(() => {
    const list = (runs ?? []).map((r) => ({ value: String(r.id), label: runOptionLabel(r) }));
    const keep = original?.run_id ?? prefill?.runId;
    if (keep != null && !list.some((o) => o.value === String(keep))) {
      list.unshift({ value: String(keep), label: original?.run_missing ? `Run ${keep} (removed)` : `Run ${keep}` });
    }
    return list;
  }, [runs, original, prefill]);

  // Pointer closes (Cancel, Create / Save clicked) play the exit; Esc and keyboard closes are instant (ui/motion.tsx).
  const { closing, close } = useDialogClose(ref);

  const body = (): TaskInput | null => {
    const t = title.trim();
    const run = runId ? Number(runId) : null;
    const item = run != null && itemId ? itemId : null;
    if (!original) {
      return { title: t, ...(due ? { due_date: due } : {}), ...(run != null ? { run_id: run } : {}), ...(item ? { discrepancy_item_id: item } : {}) };
    }
    const patch: TaskInput = {};
    if (t !== original.title) patch.title = t;
    if ((due || null) !== original.due_date) patch.due_date = due || null;
    const runChanged = run !== original.run_id;
    const itemChanged = item !== original.discrepancy_item_id;
    if (runChanged) {
      patch.run_id = run;
      if (item) patch.discrepancy_item_id = item;
    } else if (itemChanged) {
      // The pair is checked as a whole; sending the run with the item keeps the request self-describing.
      patch.run_id = run;
      patch.discrepancy_item_id = item;
    }
    return Object.keys(patch).length ? patch : null;
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (saving) return;
    if (!title.trim()) {
      setErrors({ title: "Enter a title." });
      fieldRefs.title.current?.focus();
      return;
    }
    const payload = body();
    if (!payload) {
      close();
      return;
    }
    setSaving(true);
    setErrors({});
    try {
      const task = original ? await api.patchTask(original.id, payload) : await api.createTask(payload);
      onSaved(task, !original);
      close();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Couldn't save the task. Try again.";
      const field = err instanceof ApiError && err.code === "invalid_task" ? (err.extra.field as string | undefined) : undefined;
      if (field && field in fieldRefs) {
        setErrors({ [field]: message });
        window.setTimeout(() => fieldRefs[field as Field].current?.focus(), 0);
      } else {
        setErrors({ general: message });
      }
    } finally {
      setSaving(false);
    }
  };

  const errId = (f: Field) => `${base}-${f}-err`;
  const invalid = (f: Field) => (errors[f] ? { "aria-invalid": true as const, "aria-describedby": errId(f) } : {});
  const fieldError = (f: Field) =>
    errors[f] ? (
      <p id={errId(f)} className="inline-error task-form__error" role="alert">
        {errors[f]}
      </p>
    ) : null;

  const heading = original ? "Edit task" : "New task";
  const itemsLoading = isPolicy && runId && (items?.runId !== runId || items.list == null);

  return (
    <dialog
      ref={ref}
      className={`task-dialog dialog-motion${animate ? " task-dialog--animate dialog-motion--enter" : ""}${closing ? " is-closing" : ""}`}
      aria-labelledby={`${base}-h`}
      onClose={onClose}
    >
      <form className="task-form" onSubmit={(e) => void submit(e)} noValidate>
        <h2 id={`${base}-h`} className="task-form__title">
          {heading}
        </h2>

        <div className="field">
          <div className="field__row">
            <label htmlFor={`${base}-title`} className="field__label">
              Title
            </label>
            {title.length > MAX_TITLE * 0.8 && (
              <span className={`counter${title.length > MAX_TITLE * 0.95 ? " is-near" : ""}`} aria-live="polite">
                {title.length} / {MAX_TITLE}
              </span>
            )}
          </div>
          <input
            id={`${base}-title`}
            ref={fieldRefs.title}
            className="input"
            value={title}
            maxLength={MAX_TITLE}
            required
            autoComplete="off"
            onChange={(e) => setTitle(e.target.value)}
            {...invalid("title")}
          />
          {fieldError("title")}
        </div>

        <div className="field">
          <label htmlFor={`${base}-due`} className="field__label">
            Due date <span className="field__optional">Optional</span>
          </label>
          <input id={`${base}-due`} ref={fieldRefs.due_date} type="date" className="input task-form__date" value={due} onChange={(e) => setDue(e.target.value)} {...invalid("due_date")} />
          {fieldError("due_date")}
        </div>

        <div className="field">
          <label htmlFor={`${base}-run`} className="field__label">
            Run <span className="field__optional">Optional</span>
          </label>
          <select
            id={`${base}-run`}
            ref={fieldRefs.run_id}
            className="input select"
            value={runId}
            onChange={(e) => {
              setRunId(e.target.value);
              setItemId("");
            }}
            {...invalid("run_id")}
          >
            <option value="">None</option>
            {runs == null && !runsError && <option disabled>Loading runs...</option>}
            {runOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {runsError && <p className="hint">{runsError}</p>}
          {fieldError("run_id")}
        </div>

        {isPolicy && runId && (
          <div className="field">
            <label htmlFor={`${base}-item`} className="field__label">
              Discrepancy <span className="field__optional">Optional</span>
            </label>
            <select
              id={`${base}-item`}
              ref={fieldRefs.discrepancy_item_id}
              className="input select"
              value={itemId}
              onChange={(e) => setItemId(e.target.value)}
              {...invalid("discrepancy_item_id")}
            >
              <option value="">None, link the run only</option>
              {itemsLoading && <option disabled>Loading discrepancies...</option>}
              {itemOptions.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.title}
                </option>
              ))}
            </select>
            {items?.runId === runId && items.note && <p className="hint">{items.note}</p>}
            {fieldError("discrepancy_item_id")}
          </div>
        )}

        {errors.general && (
          <p className="inline-error" role="alert">
            {errors.general}
          </p>
        )}

        <div className="task-form__foot">
          <button type="button" className="btn btn--secondary" onClick={close} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn btn--primary" disabled={saving}>
            {saving ? "Saving..." : original ? "Save changes" : "Create task"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
