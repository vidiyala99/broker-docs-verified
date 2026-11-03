import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Database, FilePdf, Trash, WarningCircle } from "@phosphor-icons/react";
import { api, ApiError, uploadFile } from "../api/client";
import type { DocumentInfo, PolicyRole, Workspace } from "../api/types";
import { DropZone, formatBytes } from "../launch/Upload";
import { hueOf } from "../report/Chip";
import type { Viewport } from "../shell/layout";
import { useToast } from "../ui/Toast";
import { Stepper } from "../wizard/Stepper";
import { UploadedPicker } from "../wizard/StepDetails";
import {
  clearPcDraft,
  EMPTY_PC_DRAFT,
  guessRoles,
  isBlankPc,
  loadPcDraft,
  MAX_AMS,
  MAX_PDFS,
  PC_STEPS,
  ROLE_INFO,
  savePcDraft,
  validateRoles,
  type PcDraft,
  type PcStep,
} from "./draft";
import "../wizard/wizard.css";
import "./policy.css";

const AMS_LIMITS = "JSON or CSV, up to 512 KB and 400 fields, one export per check";

/** Slice 3 T9: the new Policy Check flow. Run check posts a policy-check run and opens the run view (step 3). */
export function PolicyWizard({ workspace, viewport }: { workspace: Workspace; viewport: Viewport }) {
  const toast = useToast();
  const [draft, setDraft] = useState<PcDraft>(loadPcDraft);
  const [stepError, setStepError] = useState<string | null>(null);
  const [roleErrors, setRoleErrors] = useState<Partial<Record<PolicyRole, string>>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState({ pdf: false, ams: false });
  const headingRef = useRef<HTMLHeadingElement>(null);
  const started = useRef(false);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  useEffect(() => savePcDraft(draft), [draft]);

  useEffect(() => {
    const onUnload = (e: BeforeUnloadEvent) => {
      if (started.current || isBlankPc(draftRef.current)) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, []);

  const update = useCallback((patch: Partial<PcDraft>) => {
    setStepError(null);
    setServerError(null);
    setDraft((d) => ({ ...d, ...patch }));
  }, []);

  const goTo = useCallback((step: PcStep) => {
    setStepError(null);
    setDraft((d) => {
      const next = { ...d, step, reached: Math.max(d.reached, step) as PcStep };
      // Entering Roles: empty roles get a guess from file names (the user's own choices stay); the user confirms below.
      if (step !== 1) return next;
      const guess = guessRoles(d.pdfs, d.ams);
      const roles = { ...d.roles };
      const used = new Set(Object.values(roles).filter((v): v is number => v != null));
      for (const info of ROLE_INFO) {
        const g = guess[info.role];
        if (roles[info.role] == null && g != null && !used.has(g) && !(d.rolesSet && !info.required)) {
          roles[info.role] = g;
          used.add(g);
        }
      }
      return { ...next, roles, rolesSet: true };
    });
    window.setTimeout(() => headingRef.current?.focus(), 0);
  }, []);

  const setDocs = (kind: "pdfs" | "ams", docs: DocumentInfo[]) => {
    setDraft((d) => {
      const ids = new Set(docs.map((x) => x.id));
      // A removed document leaves its role; a new one is fitted in on the next Roles visit.
      const roles = { ...d.roles };
      for (const info of ROLE_INFO) {
        const id = roles[info.role];
        if (id != null && (info.kind === "ams") === (kind === "ams") && !ids.has(id)) roles[info.role] = null;
      }
      return { ...d, [kind]: docs, roles };
    });
    setStepError(null);
    setServerError(null);
  };

  const next = () => {
    if (busy.pdf || busy.ams) return setStepError("Wait for the uploads to finish.");
    if (draft.pdfs.length < 2) return setStepError("Add at least two policy PDFs: the expiring policy and the renewal.");
    goTo(1);
  };

  const run = async () => {
    const errors = validateRoles(draft);
    setRoleErrors(errors);
    if (Object.keys(errors).length) {
      window.setTimeout(() => document.querySelector<HTMLSelectElement>(".role-row [aria-invalid='true']")?.focus(), 0);
      return;
    }
    setRunning(true);
    setServerError(null);
    try {
      const slots = ROLE_INFO.map((r) => r.role)
        .sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b))
        .filter((role) => draft.roles[role] != null)
        .map((role) => ({ documentId: draft.roles[role]!, role }));
      const res = await api.startPolicyCheck(slots);
      started.current = true;
      clearPcDraft();
      setDraft(EMPTY_PC_DRAFT);
      window.location.hash = `#/runs/${res.run.id}`;
    } catch (e) {
      setServerError(e instanceof ApiError ? e.message : "Couldn't start the check. Retry.");
    } finally {
      setRunning(false);
    }
  };

  const discard = () => {
    const prev = draftRef.current;
    clearPcDraft();
    setDraft(EMPTY_PC_DRAFT);
    setRoleErrors({});
    toast({ message: "Draft discarded.", action: { label: "Undo", run: () => setDraft(prev) } });
  };

  const assigned = ROLE_INFO.filter((r) => draft.roles[r.role] != null).map((r) => r.name.toLowerCase());
  const summary =
    draft.step === 1 && assigned.length
      ? `Checking ${assigned.length} ${assigned.length === 1 ? "document" : "documents"}`
      : [draft.pdfs.length ? `${draft.pdfs.length} ${draft.pdfs.length === 1 ? "PDF" : "PDFs"}` : "", draft.ams.length ? "1 AMS export" : ""]
          .filter(Boolean)
          .join(", ");

  return (
    <div className="workspace">
      <main className="pane wizard" id="main" tabIndex={-1} aria-labelledby="wizard-title">
        <header className="wizard__head">
          <h1 id="wizard-title" className="wizard__title">
            New policy check
          </h1>
          <Stepper step={draft.step} reached={draft.reached} onGo={goTo} compact={viewport === "narrow"} steps={PC_STEPS} label="Policy check steps" />
        </header>

        <div className="wizard__body">
          <section className="wizard__step" aria-labelledby="step-heading">
            <h2 id="step-heading" ref={headingRef} tabIndex={-1} className="wizard__step-title">
              <span className="sr-only">
                Step {draft.step + 1} of {PC_STEPS.length},{" "}
              </span>
              {draft.step === 0 ? "Add the documents" : "Assign a role to each document"}
            </h2>
            {draft.step === 0 ? (
              <StepDocuments draft={draft} workspace={workspace} setDocs={setDocs} onBusy={(k, b) => setBusy((s) => (s[k] === b ? s : { ...s, [k]: b }))} />
            ) : (
              <StepRoles
                draft={draft}
                errors={roleErrors}
                serverError={serverError}
                onChange={(role, id) => {
                  setRoleErrors((e) => {
                    const { [role]: _drop, ...rest } = e;
                    return rest;
                  });
                  update({ roles: { ...draft.roles, [role]: id }, rolesSet: true });
                }}
                onBack={() => goTo(0)}
              />
            )}
          </section>
        </div>

        <footer className="wizard__foot">
          {draft.step > 0 ? (
            <button type="button" className="btn btn--secondary" onClick={() => goTo(0)} disabled={running}>
              <ArrowLeft size={16} aria-hidden /> Back
            </button>
          ) : (
            <a href="#/" className="btn btn--ghost">
              Cancel
            </a>
          )}
          <div className="wizard__foot-mid">
            {stepError ? (
              <p className="inline-error" role="alert">
                {stepError}
              </p>
            ) : (
              summary && <p className="wizard__summary">{summary}</p>
            )}
            {!isBlankPc(draft) && (
              <button type="button" className="link-btn link-btn--small wizard__discard" onClick={discard} disabled={running}>
                Discard draft
              </button>
            )}
          </div>
          {draft.step === 0 ? (
            <button type="button" className="btn btn--primary" onClick={next}>
              Continue <ArrowRight size={16} aria-hidden />
            </button>
          ) : (
            <button type="button" className="btn btn--primary" onClick={() => void run()} disabled={running}>
              {running ? (
                <>
                  <span className="spinner spinner--sm spinner--on-primary" aria-hidden /> Starting...
                </>
              ) : (
                "Run check"
              )}
            </button>
          )}
        </footer>
        <p className="sr-only" role="status" aria-live="polite">
          {running ? "Starting the check." : `${PC_STEPS[draft.step]} step.`}
        </p>
      </main>
    </div>
  );
}

/** Column order of the run: expiring, quote, renewal, AMS, so letters A to D follow the comparison row. */
const ORDER: PolicyRole[] = ["expiring", "quote", "renewal", "ams"];

interface Pending {
  key: string;
  name: string;
  progress: number;
  error?: string;
}

/** Upload rows for one kind of document (PDFs through /api/documents, AMS through /api/documents/ams). */
function useUploadList(kind: "pdfs" | "ams", docs: DocumentInfo[], max: number, setDocs: (docs: DocumentInfo[]) => void) {
  const [pending, setPending] = useState<Pending[]>([]);
  const seq = useRef(0);
  const docsRef = useRef(docs);
  docsRef.current = docs;
  const add = (files: File[]) => {
    let room = max - docsRef.current.length - pending.filter((p) => !p.error).length;
    const fresh: Pending[] = [];
    for (const f of files) {
      seq.current += 1;
      const key = `${kind}${seq.current}`;
      if (room <= 0) {
        fresh.push({
          key,
          name: f.name,
          progress: 0,
          error: kind === "ams" ? "A check uses one AMS export. Remove the current one to add this file." : `A check uses at most ${max} policy PDFs. Remove one to add this file.`,
        });
        continue;
      }
      room -= 1;
      fresh.push({ key, name: f.name, progress: 0 });
      const patch = (p: Partial<Pending>) => setPending((rs) => rs.map((r) => (r.key === key ? { ...r, ...p } : r)));
      uploadFile(f, (fr) => patch({ progress: fr }), kind === "ams" ? "/api/documents/ams" : "/api/documents")
        .then((res) => {
          const doc = res.documents[0];
          if (!doc) return patch({ error: res.rejected[0]?.message ?? "This file was not accepted." });
          setPending((rs) => rs.filter((r) => r.key !== key));
          docsRef.current = [...docsRef.current.filter((d) => d.id !== doc.id), doc];
          setDocs(docsRef.current);
        })
        .catch((e: unknown) => patch({ error: e instanceof ApiError ? e.message : "Upload failed. Retry." }));
    }
    setPending((rs) => [...rs, ...fresh]);
  };
  const dismiss = (key: string) => setPending((rs) => rs.filter((r) => r.key !== key));
  return { pending, add, dismiss, busy: pending.some((p) => !p.error) };
}

function StepDocuments({
  draft,
  workspace,
  setDocs,
  onBusy,
}: {
  draft: PcDraft;
  workspace: Workspace;
  setDocs: (kind: "pdfs" | "ams", docs: DocumentInfo[]) => void;
  onBusy: (kind: "pdf" | "ams", busy: boolean) => void;
}) {
  const pdf = useUploadList("pdfs", draft.pdfs, MAX_PDFS, (d) => setDocs("pdfs", d));
  const ams = useUploadList("ams", draft.ams, MAX_AMS, (d) => setDocs("ams", d));
  useEffect(() => onBusy("pdf", pdf.busy), [pdf.busy, onBusy]);
  useEffect(() => onBusy("ams", ams.busy), [ams.busy, onBusy]);
  const l = workspace.limits;
  return (
    <div className="details">
      <section className="details__group" aria-labelledby="pdfs-h">
        <h3 id="pdfs-h" className="group-title">
          Policy PDFs
        </h3>
        <p className="hint">The expiring policy and the renewal are required. Add the quote too if you have one.</p>
        <div className="details__drop">
          <DropZone
            limits={l}
            onFiles={pdf.add}
            title="Drop the expiring policy, the renewal and the quote"
            limitsLine={`PDF, up to ${l.max_pages} pages and ${l.max_megabytes} MB each, ${MAX_PDFS} files per check`}
            disabled={draft.pdfs.length >= MAX_PDFS}
          />
        </div>
        <DocRows docs={draft.pdfs} pending={pdf.pending} onRemove={(id) => setDocs("pdfs", draft.pdfs.filter((d) => d.id !== id))} onDismiss={pdf.dismiss} label="Policy PDFs in this check" />
        <UploadedPicker
          selected={draft.pdfs}
          max={MAX_PDFS}
          onChange={(docs) => setDocs("pdfs", docs)}
          filter={(d) => d.kind !== "ams"}
          emptyText="No PDFs uploaded yet. Drop policy PDFs above."
          fullText={`This check has ${MAX_PDFS} PDFs, the most it can use.`}
        />
      </section>

      <section className="details__group" aria-labelledby="ams-h">
        <h3 id="ams-h" className="group-title">
          AMS export <span className="field__optional">optional</span>
        </h3>
        <p className="hint">The policy record from your agency system. Each field is compared by name, and its citation opens the exported line.</p>
        <div className="details__drop">
          <DropZone
            limits={l}
            onFiles={ams.add}
            title="Drop the AMS export"
            limitsLine={AMS_LIMITS}
            accept=".json,.csv,application/json,text/csv"
            buttonLabel="Choose AMS file"
            multiple={false}
            disabled={draft.ams.length >= MAX_AMS}
          />
        </div>
        <DocRows docs={draft.ams} pending={ams.pending} onRemove={(id) => setDocs("ams", draft.ams.filter((d) => d.id !== id))} onDismiss={ams.dismiss} label="AMS export in this check" ams />
        <UploadedPicker
          selected={draft.ams}
          max={MAX_AMS}
          onChange={(docs) => setDocs("ams", docs)}
          filter={(d) => d.kind === "ams"}
          label="Choose an uploaded AMS export"
          emptyText="No AMS exports uploaded yet."
          fullText="This check has its AMS export."
        />
      </section>
    </div>
  );
}

function amsMeta(d: DocumentInfo): string {
  if (!d.ams) return "AMS export";
  const n = d.ams.unmapped.length;
  return `${d.ams.field_count} fields${n ? `, ${n} not compared` : ""}`;
}

function DocRows({
  docs,
  pending,
  onRemove,
  onDismiss,
  label,
  ams,
}: {
  docs: DocumentInfo[];
  pending: Pending[];
  onRemove: (id: number) => void;
  onDismiss: (key: string) => void;
  label: string;
  ams?: boolean;
}) {
  if (!docs.length && !pending.length) return null;
  return (
    <ul className="file-rows" aria-label={label}>
      {docs.map((d) => (
        <li key={d.id} className="file-row file-row--ready">
          {ams ? <Database size={20} aria-hidden className="file-row__icon" /> : <FilePdf size={20} aria-hidden className="file-row__icon" />}
          <div className="file-row__main">
            <span className="file-row__name" title={d.filename}>
              {d.filename}
            </span>
            <span className="file-row__meta">
              {d.warning ? (
                <span className="file-row__warn">
                  <WarningCircle size={14} aria-hidden /> {d.warning}
                </span>
              ) : ams || d.kind === "ams" ? (
                amsMeta(d)
              ) : (
                <>
                  {d.page_count} {d.page_count === 1 ? "page" : "pages"}, {formatBytes(d.size_bytes)}
                </>
              )}
            </span>
          </div>
          <button type="button" className="icon-btn" aria-label={`Remove ${d.filename} from this check`} onClick={() => onRemove(d.id)}>
            <Trash size={16} aria-hidden />
          </button>
        </li>
      ))}
      {pending.map((p) => (
        <li key={p.key} className={`file-row file-row--${p.error ? "rejected" : "uploading"}`}>
          {ams ? <Database size={20} aria-hidden className="file-row__icon" /> : <FilePdf size={20} aria-hidden className="file-row__icon" />}
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
            <button type="button" className="icon-btn" aria-label={`Dismiss ${p.name}`} onClick={() => onDismiss(p.key)}>
              <Trash size={16} aria-hidden />
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function StepRoles({
  draft,
  errors,
  serverError,
  onChange,
  onBack,
}: {
  draft: PcDraft;
  errors: Partial<Record<PolicyRole, string>>;
  serverError: string | null;
  onChange: (role: PolicyRole, id: number | null) => void;
  onBack: () => void;
}) {
  const baseId = useId();
  const owner = (id: number): PolicyRole | null => ROLE_INFO.find((r) => draft.roles[r.role] === id)?.role ?? null;
  return (
    <div className="roles">
      <p className="hint">
        The renewal is the document being checked; each other document is compared to it. Columns in the report follow this order:
        expiring, quote, renewal, AMS.
      </p>
      {serverError && (
        <div className="notice notice--error" role="alert">
          <WarningCircle size={16} aria-hidden />
          <span className="notice__text">{serverError}</span>
        </div>
      )}
      <ul className="role-rows">
        {ROLE_INFO.map((info) => {
          const id = `${baseId}-${info.role}`;
          const options = info.kind === "ams" ? draft.ams : draft.pdfs;
          const err = errors[info.role];
          const value = draft.roles[info.role];
          const letter = value != null ? String.fromCharCode(65 + ORDER.filter((r) => draft.roles[r] != null).indexOf(info.role)) : null;
          return (
            <li key={info.role} className={`role-row${info.role === "renewal" ? " role-row--checked" : ""}`}>
              <div className="role-row__text">
                <label htmlFor={id} className="role-row__name">
                  {info.name}
                </label>
                <span className={`role-row__need${info.required ? " is-required" : ""}`}>{info.required ? "Required" : "Optional"}</span>
                <p id={`${id}-h`} className="field__hint">
                  {info.about}
                </p>
              </div>
              <div className="role-row__control">
                <span className={`doc-letter${letter ? ` hue-${hueOf(letter)}` : " doc-letter--none"}`} aria-hidden>
                  {letter ?? ""}
                </span>
                <select
                  id={id}
                  className="input select"
                  value={value ?? ""}
                  aria-invalid={err ? true : undefined}
                  aria-describedby={`${id}-h${err ? ` ${id}-e` : ""}`}
                  onChange={(e) => onChange(info.role, e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">{info.required ? "Choose a document" : "Not used"}</option>
                  {options.map((d) => {
                    const by = owner(d.id);
                    const taken = by != null && by !== info.role;
                    return (
                      <option key={d.id} value={d.id} disabled={taken}>
                        {d.filename}
                        {taken ? ` (used as ${ROLE_INFO.find((r) => r.role === by)!.name.toLowerCase()})` : ""}
                      </option>
                    );
                  })}
                </select>
              </div>
              {err && (
                <p id={`${id}-e`} className="field__error role-row__error">
                  <WarningCircle size={16} aria-hidden /> {err}
                </p>
              )}
              {info.kind === "ams" && options.length === 0 && (
                <p className="field__hint role-row__error">
                  No AMS export added.{" "}
                  <button type="button" className="link-btn" onClick={onBack}>
                    Add one
                  </button>
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
