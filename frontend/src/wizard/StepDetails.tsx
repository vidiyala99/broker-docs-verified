import { useEffect, useId, useMemo, useRef, useState } from "react";
import { CaretDown, FilePdf, Trash, WarningCircle } from "@phosphor-icons/react";
import { api, ApiError, uploadFile } from "../api/client";
import type { DocumentInfo, TemplateLimits, Workspace } from "../api/types";
import { DropZone, formatBytes } from "../launch/Upload";
import { hueOf } from "../report/Chip";
import { formatWhen } from "../launch/RecentRuns";
import type { Draft, Errors } from "./draft";

interface Pending {
  key: string;
  name: string;
  progress: number;
  error?: string;
}

const DEFAULT_LIMITS = { max_name_chars: 200, max_context_chars: 4000 };

export function StepDetails({
  draft,
  update,
  workspace,
  limits,
  errors,
  onBusy,
  focusKey,
}: {
  draft: Draft;
  update: (p: Partial<Draft>) => void;
  workspace: Workspace;
  limits: TemplateLimits | null;
  errors: Errors;
  onBusy: (busy: boolean) => void;
  focusKey: { path: string; n: number } | null;
}) {
  const L = limits ?? DEFAULT_LIMITS;
  const maxDocs = workspace.limits.max_documents_per_run;
  const [pending, setPending] = useState<Pending[]>([]);
  const seq = useRef(0);
  const docsRef = useRef(draft.docs);
  docsRef.current = draft.docs;

  useEffect(() => onBusy(pending.some((p) => !p.error)), [pending, onBusy]);

  useEffect(() => {
    if (!focusKey) return;
    const el = document.getElementById(focusKey.path === "document_ids" ? "f-document_ids" : `f-${focusKey.path}`);
    el?.focus();
  }, [focusKey]);

  // Uploads finish in any order; the column order must follow the order the files were chosen.
  // Settled results wait in `queue` and are added only once every earlier upload has settled.
  const queue = useRef<{ key: string; doc: DocumentInfo | null; settled: boolean }[]>([]);
  const flush = () => {
    const ready: DocumentInfo[] = [];
    const done = new Set<string>();
    while (queue.current[0]?.settled) {
      const item = queue.current.shift()!;
      if (item.doc) {
        ready.push(item.doc);
        done.add(item.key);
      }
    }
    if (!ready.length) return;
    setPending((rs) => rs.filter((r) => !done.has(r.key)));
    const keys = new Set(ready.map((d) => d.id));
    docsRef.current = [...docsRef.current.filter((d) => !keys.has(d.id)), ...ready];
    update({ docs: docsRef.current });
  };

  // Reuses the slice 1 upload path: one request per file so each row has its own progress bar.
  const addFiles = (files: File[]) => {
    let room = maxDocs - docsRef.current.length - pending.filter((p) => !p.error).length;
    const next: Pending[] = [];
    for (const f of files) {
      seq.current += 1;
      const key = `u${seq.current}`;
      if (room <= 0) {
        next.push({ key, name: f.name, progress: 0, error: `A proposal uses at most ${maxDocs} quotes. Remove one to add this file.` });
        continue;
      }
      room -= 1;
      next.push({ key, name: f.name, progress: 0 });
      queue.current.push({ key, doc: null, settled: false });
      const settle = (doc: DocumentInfo | null) => {
        const item = queue.current.find((q) => q.key === key);
        if (item) Object.assign(item, { doc, settled: true });
        flush();
      };
      const patch = (p: Partial<Pending>) => setPending((rs) => rs.map((r) => (r.key === key ? { ...r, ...p } : r)));
      uploadFile(f, (fr) => patch({ progress: fr }))
        .then((res) => {
          const doc = res.documents[0];
          if (!doc) patch({ error: res.rejected[0]?.message ?? "This file was not accepted." });
          settle(doc ?? null);
        })
        .catch((e: unknown) => {
          patch({ error: e instanceof ApiError ? e.message : "Upload failed. Retry." });
          settle(null);
        });
    }
    setPending((rs) => [...rs, ...next]);
  };

  const removeDoc = (id: number) => update({ docs: draft.docs.filter((d) => d.id !== id) });
  const docError = errors.document_ids;

  return (
    <div className="details">
      <section className="details__group" aria-labelledby="quotes-h">
        <h3 id="quotes-h" className="group-title">
          Quote PDFs
        </h3>
        <p className="hint">Each quote becomes a column in the proposal tables, in this order.</p>
        <div id="f-document_ids" tabIndex={-1} className="details__drop" aria-describedby={docError ? "e-document_ids" : undefined}>
          <DropZone limits={workspace.limits} onFiles={addFiles} title="Drop quote PDFs for this proposal" disabled={draft.docs.length >= maxDocs} />
        </div>
        {docError && (
          <p id="e-document_ids" className="field__error" role="alert">
            <WarningCircle size={16} aria-hidden /> {docError}
          </p>
        )}
        {(draft.docs.length > 0 || pending.length > 0) && (
          <ul className="file-rows" aria-label="Quotes in this proposal">
            {draft.docs.map((d, i) => {
              const letter = String.fromCharCode(65 + i);
              return (
                <li key={d.id} className="file-row file-row--ready">
                  <span className={`doc-letter hue-${hueOf(letter)}`} aria-hidden>
                    {letter}
                  </span>
                  <div className="file-row__main">
                    <span className="file-row__name" title={d.filename}>
                      <span className="sr-only">Quote {letter}: </span>
                      {d.filename}
                    </span>
                    <span className="file-row__meta">
                      {d.warning ? (
                        <span className="file-row__warn">
                          <WarningCircle size={14} aria-hidden /> {d.warning}
                        </span>
                      ) : (
                        <>
                          {d.page_count} {d.page_count === 1 ? "page" : "pages"}, {formatBytes(d.size_bytes)}
                        </>
                      )}
                    </span>
                  </div>
                  <button type="button" className="icon-btn" aria-label={`Remove ${d.filename} from this proposal`} onClick={() => removeDoc(d.id)}>
                    <Trash size={16} aria-hidden />
                  </button>
                </li>
              );
            })}
            {pending.map((p) => (
              <li key={p.key} className={`file-row file-row--${p.error ? "rejected" : "uploading"}`}>
                <FilePdf size={20} aria-hidden className="file-row__icon" />
                <div className="file-row__main">
                  <span className="file-row__name" title={p.name}>
                    {p.name}
                  </span>
                  {p.error ? (
                    <span className="file-row__error" role="alert">
                      {p.error}
                    </span>
                  ) : (
                    <span className="file-row__meta">
                      <span className="progress" role="progressbar" aria-label={`Uploading ${p.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p.progress * 100)}>
                        <span className="progress__bar" style={{ transform: `scaleX(${p.progress})` }} />
                      </span>
                    </span>
                  )}
                </div>
                {p.error && (
                  <button type="button" className="icon-btn" aria-label={`Dismiss ${p.name}`} onClick={() => setPending((rs) => rs.filter((r) => r.key !== p.key))}>
                    <Trash size={16} aria-hidden />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
        <UploadedPicker selected={draft.docs} max={maxDocs} onChange={(docs) => update({ docs })} />
      </section>

      <section className="details__group" aria-labelledby="who-h">
        <h3 id="who-h" className="group-title">
          Who it is for
        </h3>
        <div className="details__grid">
          <TextField id="client_name" label="Client name" hint="Shown on the cover page and in the title." value={draft.client_name} max={L.max_name_chars} error={errors.client_name} onChange={(v) => update({ client_name: v })} autoComplete="organization" />
          <TextField id="broker_name" label="Broker name" value={draft.broker_name} max={L.max_name_chars} error={errors.broker_name} onChange={(v) => update({ broker_name: v })} autoComplete="name" />
          <TextField id="brokerage_name" label="Brokerage name" value={draft.brokerage_name} max={L.max_name_chars} error={errors.brokerage_name} onChange={(v) => update({ brokerage_name: v })} autoComplete="organization" />
        </div>
      </section>

      <section className="details__group" aria-labelledby="writing-h">
        <h3 id="writing-h" className="group-title">
          Writing
        </h3>
        <ContextField value={draft.additional_context} max={L.max_context_chars} error={errors.additional_context} onChange={(v) => update({ additional_context: v })} />
        <div className="check">
          <input
            id="f-no_ai_prose"
            type="checkbox"
            checked={draft.no_ai_prose}
            onChange={(e) => update({ no_ai_prose: e.target.checked })}
            aria-describedby="h-no_ai_prose"
          />
          <div>
            <label htmlFor="f-no_ai_prose" className="check__label">
              Plain text only, no AI writing
            </label>
            <p id="h-no_ai_prose" className="hint">
              Each section gets plain sentences built from the verified values. Tables and citations stay the same.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}

function TextField({
  id,
  label,
  hint,
  value,
  max,
  error,
  onChange,
  autoComplete,
}: {
  id: string;
  label: string;
  hint?: string;
  value: string;
  max: number;
  error?: string;
  onChange: (v: string) => void;
  autoComplete?: string;
}) {
  const describedBy = [hint ? `h-${id}` : null, error ? `e-${id}` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="field">
      <label htmlFor={`f-${id}`} className="field__label">
        {label} <span className="field__optional">optional</span>
      </label>
      <input
        id={`f-${id}`}
        className="input"
        value={value}
        maxLength={max}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        autoComplete={autoComplete}
      />
      {hint && (
        <p id={`h-${id}`} className="field__hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={`e-${id}`} className="field__error">
          <WarningCircle size={16} aria-hidden /> {error}
        </p>
      )}
    </div>
  );
}

function ContextField({ value, max, error, onChange }: { value: string; max: number; error?: string; onChange: (v: string) => void }) {
  const near = value.length > max * 0.9;
  return (
    <div className="field">
      <label htmlFor="f-additional_context" className="field__label">
        Additional context <span className="field__optional">optional</span>
      </label>
      <textarea
        id="f-additional_context"
        className="input textarea"
        rows={4}
        value={value}
        maxLength={max}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={`h-additional_context c-additional_context${error ? " e-additional_context" : ""}`}
      />
      <div className="field__row">
        <p id="h-additional_context" className="field__hint">
          What the AI should know about this client, for example renewal goals. It shapes the wording only; values always come from the quotes.
        </p>
        <span id="c-additional_context" className={`counter${near ? " is-near" : ""}`} aria-live={near ? "polite" : "off"}>
          {value.length.toLocaleString()} / {max.toLocaleString()}
        </span>
      </div>
      {error && (
        <p id="e-additional_context" className="field__error">
          <WarningCircle size={16} aria-hidden /> {error}
        </p>
      )}
    </div>
  );
}

/** "Choose from uploaded files": quotes already on this machine (GET /api/documents). */
function UploadedPicker({ selected, max, onChange }: { selected: DocumentInfo[]; max: number; onChange: (docs: DocumentInfo[]) => void }) {
  const [open, setOpen] = useState(false);
  const [docs, setDocs] = useState<DocumentInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bodyId = useId();

  const load = () => {
    setError(null);
    api.documents().then(setDocs, (e: unknown) => setError(e instanceof ApiError ? e.message : "Couldn't load your files."));
  };

  useEffect(() => {
    if (open && docs == null) load();
  }, [open, docs]);

  // Same file uploaded more than once (same sha256): show only the newest copy.
  const shown = useMemo(() => {
    const newest = new Map<string, DocumentInfo>();
    for (const d of docs ?? []) {
      const cur = newest.get(d.sha256);
      if (!cur || d.created_at > cur.created_at || (d.created_at === cur.created_at && d.id > cur.id)) newest.set(d.sha256, d);
    }
    return (docs ?? []).filter((d) => newest.get(d.sha256) === d);
  }, [docs]);
  const hashes = new Set(selected.map((d) => d.sha256));
  const full = selected.length >= max;

  return (
    <div className={`picker${open ? " is-open" : ""}`}>
      <button type="button" className="picker__toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((o) => !o)}>
        Choose from uploaded files
        <CaretDown size={16} aria-hidden className="picker__caret" />
      </button>
      <div id={bodyId} hidden={!open} className="picker__body">
        {error ? (
          <p className="inline-error" role="alert">
            {error}{" "}
            <button type="button" className="link-btn" onClick={load}>
              Retry
            </button>
          </p>
        ) : docs == null ? (
          <div className="skeleton-lines" aria-hidden>
            <span />
            <span />
          </div>
        ) : shown.length === 0 ? (
          <p className="hint">No files uploaded yet. Drop quote PDFs above.</p>
        ) : (
          <fieldset>
            <legend className="sr-only">Uploaded files</legend>
            {full && <p className="hint">This proposal has {max} quotes, the most a run can use.</p>}
            <ul className="picker__list">
              {shown.map((d) => {
                const on = hashes.has(d.sha256);
                return (
                  <li key={d.id}>
                    <label className="picker__row">
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={!on && full}
                        onChange={() => onChange(on ? selected.filter((x) => x.sha256 !== d.sha256) : [...selected, d])}
                      />
                      <span className="picker__name">{d.filename}</span>
                      <span className="picker__meta">
                        {d.page_count ?? "?"} pages, {formatWhen(d.created_at)}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </fieldset>
        )}
      </div>
    </div>
  );
}
