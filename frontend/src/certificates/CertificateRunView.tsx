import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { DownloadSimple, FilePlus, Info, Plus, X } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import { useRunEvents } from "../api/useRunEvents";
import type { CertificateField, CertificateView, Chip, ClaimItem, ClaimStatus, DocumentInfo, Run, RunDocument, Workspace } from "../api/types";
import { Accordion } from "../ui/Accordion";
import { useToast } from "../ui/Toast";
import { reducedMotion, useDialogClose } from "../ui/motion";
import { LeftPanel, type Viewport } from "../shell/layout";
import { RunProgress } from "../run/RunProgress";
import { hueOf } from "../report/Chip";
import type { PdfTarget } from "../pdf/PdfViewer";
import { Glyph, ReadingMark } from "../voice/marks";
import { CertificatePaper } from "./CertificatePaper";
import { Ledger } from "./Ledger";
import { PolicyPicker } from "./PolicyPicker";
import {
  BLOCKED_TEXT,
  certificateNewHref,
  formatDate,
  isDraftEditable,
  isIssued,
  MAX_POLICY_PDFS,
  regionOf,
  STATE_ORDER,
  stateOf,
  WORDING_LABEL,
  WORDINGS,
  type ClaimState,
} from "./certUtil";
import "./certificates.css";

const PdfViewer = lazy(() => import("../pdf/PdfViewer").then((m) => ({ default: m.PdfViewer })));

const ITEM_CHANGED = "This check changed while you were deciding. Its new evidence is below; your reason is still typed.";

/**
 * A certificate run (spec section 4, direction A "Proof and ledger", owner-approved 2026-11-03): the certificate as an
 * intact sheet beside the check ledger, Issue as the one primary action with the count of checks left. An issued
 * version is read-only: the ledger becomes the record, Download PDF and New version replace Issue.
 */
export function CertificateRunView({
  runId,
  workspace,
  viewport,
  onRunChanged,
}: {
  runId: number;
  workspace: Workspace;
  viewport: Viewport;
  onRunChanged?: (run: Run) => void;
}) {
  const toast = useToast();
  const [view, setView] = useState<CertificateView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [rowNotes, setRowNotes] = useState<Record<string, string>>({});
  const [pdf, setPdf] = useState<PdfTarget | null>(null);
  const [pointed, setPointed] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [issuing, setIssuing] = useState(false);
  const [busy, setBusy] = useState<"cancel" | "retry" | "version" | null>(null);
  const [policiesOpen, setPoliciesOpen] = useState<boolean | null>(null);
  const [pageCounts, setPageCounts] = useState<Map<number, number | null>>();
  const [announce, setAnnounce] = useState("");
  const viewRef = useRef(view);
  viewRef.current = view;
  const opener = useRef<HTMLElement | null>(null);
  const runChanged = useRef(onRunChanged);
  runChanged.current = onRunChanged;

  const load = useCallback(async () => {
    try {
      const v = await api.certificate(runId);
      setView(v);
      setLoadError(null);
      runChanged.current?.({ ...v.run, client: v.client, client_source: v.client_source });
      return v;
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : "Couldn't load this certificate.");
      return null;
    }
  }, [runId]);

  useEffect(() => {
    setView(null);
    setPdf(null);
    void load();
  }, [load]);

  const docCount = view?.run.documents.length ?? 0;
  useEffect(() => {
    let live = true;
    api.documents().then((docs) => live && setPageCounts(new Map(docs.map((d) => [d.id, d.page_count]))), () => undefined);
    return () => {
      live = false;
    };
  }, [runId, docCount]);

  const updateRun = (fn: (r: Run) => Run) => setView((v) => (v ? { ...v, run: fn(v.run) } : v));
  useRunEvents(view?.run.id ?? null, Boolean(view?.run.active), {
    onStatus: (status, error, retryable) =>
      updateRun((r) => ({ ...r, status, error, retryable, active: !["done", "partial", "failed", "cancelled"].includes(status) })),
    onDocument: (doc: RunDocument) =>
      updateRun((r) => ({ ...r, documents: r.documents.map((d) => (d.run_document_id === doc.run_document_id ? { ...d, ...doc } : d)) })),
    onArtifact: () => undefined,
    onEnd: () => {
      void load().then((v) => v && !v.run.active && setAnnounce("Every document is read. The checks are ready."));
    },
  });

  // ------------------------------------------------------------------ sources
  const openSource = useCallback((chip: Chip) => {
    opener.current = document.activeElement as HTMLElement | null;
    setPdf({ documentId: chip.document_id, filename: chip.filename, letter: chip.letter, page: chip.page_no, chip });
  }, []);
  const closeSource = useCallback(() => {
    setPdf(null);
    window.setTimeout(() => opener.current?.focus(), 0);
  }, []);
  useEffect(() => {
    if (!pdf) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if ((e.target as HTMLElement).closest("input, textarea, dialog, .left-panel--overlay")) return;
      closeSource();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pdf, closeSource]);

  // A ledger row points at its line on the paper; the paper scrolls only when that line is out of view.
  const onPoint = useCallback((claim: ClaimItem | null, via: "pointer" | "keyboard") => {
    const r = claim ? regionOf(claim) : null;
    setPointed(r);
    if (!r) return;
    const el = document.querySelector<HTMLElement>(`.cert__paperwrap [data-region="${CSS.escape(r)}"]`);
    const wrap = el?.closest<HTMLElement>(".cert__paperwrap");
    if (!el || !wrap) return;
    const a = el.getBoundingClientRect();
    const b = wrap.getBoundingClientRect();
    if (a.top >= b.top && a.bottom <= b.bottom) return;
    // Scroll the paper's own column only (scrollIntoView would also move the page and the header).
    wrap.scrollTo({ top: wrap.scrollTop + (a.top - b.top) - (b.height - a.height) / 2, behavior: via === "pointer" && !reducedMotion() ? "smooth" : "auto" });
  }, []);

  // ------------------------------------------------------------------ decisions (D10, D13, D15)
  const decide = useCallback(
    async (claim: ClaimItem, status: ClaimStatus, note?: string | null, quiet = false) => {
      setBanner(null);
      try {
        await api.patchClaim(runId, claim.id, { status, note: note ?? null, expected_fingerprint: claim.fingerprint });
        setRowNotes((n) => {
          const { [claim.key]: _drop, ...rest } = n;
          return rest;
        });
        const v = await load();
        if (quiet || !v) return;
        const verb =
          status === "removed"
            ? claim.kind === "edited"
              ? "Extracted value restored."
              : "Removed from the certificate."
            : status === "resolved"
              ? claim.verdict === "needs_signoff"
                ? "Confirmed. The note is on the record."
                : "Overridden. The reason is on the record."
              : status === "open" && claim.status === "removed"
                ? "Put back on the certificate."
                : "Decision undone.";
        setAnnounce(verb);
        toast({
          message: verb,
          action:
            status === "open"
              ? undefined
              : {
                  label: "Undo",
                  run: () => {
                    const now = viewRef.current?.claims.find((c) => c.key === claim.key);
                    if (now) void decide(now, "open", null, true).then(() => toast({ message: "Decision undone.", hold: 4000 }));
                  },
                },
        });
      } catch (e) {
        if (e instanceof ApiError && e.code === "item_changed") {
          await load();
          setRowNotes((n) => ({ ...n, [claim.key]: ITEM_CHANGED }));
        } else if (e instanceof ApiError && (e.code === "issued" || e.code === "run_active")) {
          await load();
          setBanner(e.code === "issued" ? BLOCKED_TEXT.issued : BLOCKED_TEXT.run_active);
        } else if (quiet) {
          toast({ message: e instanceof ApiError ? e.message : "Couldn't undo. Retry.", tone: "error" });
        }
        throw e;
      }
    },
    [runId, load, toast],
  );

  // D12: a printed value the broker changes; the server adds an "edited" sign-off claim.
  const edit = useCallback(
    async (column: string, field: CertificateField, value: string) => {
      const v = viewRef.current;
      if (!v?.artifact_id || v.version == null) throw new Error("No draft to edit.");
      try {
        const res = await api.editCell(v.artifact_id, { block_id: "certificate", row_key: field.key, column_key: column, value, base_version: v.version });
        await load();
        toast({
          message: `${field.label} changed. Sign it off in the checks before issue.`,
          action: {
            label: "Undo",
            run: () =>
              void api.undo(res.artifact.id, res.previous_version, res.version).then(
                () => void load().then(() => toast({ message: "Change undone.", hold: 4000 })),
                (err: unknown) => toast({ message: err instanceof ApiError ? err.message : "Couldn't undo. Retry.", tone: "error" }),
              ),
          },
        });
      } catch (e) {
        if (e instanceof ApiError && (e.code === "version_conflict" || e.code === "issued")) {
          await load();
          throw new Error("The draft changed. It is reloaded; try the edit again.");
        }
        throw e instanceof ApiError ? new Error(e.message) : e;
      }
    },
    [load, toast],
  );

  // ------------------------------------------------------------------ issue (D5, D13, D16)
  const issue = async () => {
    const v = viewRef.current;
    if (!v || v.version == null || issuing) return;
    if (!v.can_issue) {
      setBanner(v.blocked.map((b) => BLOCKED_TEXT[b]).join(" "));
      return;
    }
    setIssuing(true);
    setBanner(null);
    try {
      const res = await api.issueCertificate(runId, v.version);
      setView(res.certificate);
      setPdf(null);
      setAnnounce(`Version ${res.issue.version} issued.`);
      toast({
        message: `Certificate issued for ${res.certificate.certificate?.holder_name ?? "the holder"}.`,
        action: { label: "Download PDF", run: () => window.location.assign(api.certificatePdfUrl(runId, res.issue.version)) },
      });
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "";
      const fresh = await load();
      if (code === "version_changed") {
        setBanner(`${BLOCKED_TEXT.version_changed} You were looking at version ${v.version}; version ${fresh?.version ?? "?"} is current.`);
      } else if (code === "claims_open") {
        const n = fresh?.counts?.open ?? 0;
        setBanner(`${n} ${n === 1 ? "check still needs" : "checks still need"} you. Nothing was issued.`);
      } else if (code === "run_active" || code === "no_coverage" || code === "issue_failed") {
        setBanner(BLOCKED_TEXT[code]);
      } else {
        setBanner(e instanceof ApiError ? e.message : "Couldn't issue. Retry.");
      }
    } finally {
      setIssuing(false);
    }
  };

  const newVersion = async () => {
    setBusy("version");
    try {
      const v = await api.newCertificateVersion(runId);
      setView(v);
      setAnnounce(`Version ${v.version} is a draft. The issued version stays on the record.`);
      toast({ message: `Version ${v.version} started. The issued version stays on the record.`, hold: 6000 });
    } catch (e) {
      toast({ message: e instanceof ApiError ? e.message : "Couldn't start a new version. Retry.", tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  const runAction = async (kind: "cancel" | "retry") => {
    if (!view) return;
    setBusy(kind);
    try {
      if (kind === "cancel") await api.cancelRun(view.run.id);
      else await api.retryRun(view.run.id);
      await load();
    } catch (e) {
      toast({ message: e instanceof ApiError ? e.message : "Couldn't do that. Retry.", tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  const marks = useMemo(() => {
    const m = new Map<string, ClaimState>();
    for (const c of view?.claims ?? []) {
      const s = stateOf(c);
      if (s === "removed") continue; // removed content is off the paper (D13)
      const r = regionOf(c);
      const prev = m.get(r);
      if (!prev || STATE_ORDER.indexOf(s) < STATE_ORDER.indexOf(prev)) m.set(r, s);
    }
    return m;
  }, [view?.claims]);

  if (loadError && !view) {
    return (
      <div className="workspace">
        <main className="pane" id="main" tabIndex={-1}>
          <div className="pane__body pane__body--center">
            <div className="state-block" role="alert">
              <p>{loadError}</p>
              <button type="button" className="btn btn--secondary" onClick={() => void load()}>
                Retry
              </button>
              <a href="#/certificates" className="link-btn">
                Back to Certificates
              </a>
            </div>
          </div>
        </main>
      </div>
    );
  }

  const run = view?.run ?? null;
  const issued = view ? isIssued(view) : false;
  const editable = view ? isDraftEditable(view) : false;
  const cert = view?.certificate ?? null;
  const open = view?.counts?.open ?? 0;
  const holder = cert?.holder_name ?? (run?.parameters.holder_name as string | undefined) ?? null;
  const panelOpen = policiesOpen ?? Boolean(run?.active);

  return (
    <div className="workspace cert-ws">
      <LeftPanel viewport={viewport} label="Certificate panel" folded chat={false} uploadsLabel="Show policies" onReveal={() => setPoliciesOpen(true)}>
        <div className="panel-scroll">
          <Accordion title="Policies" count={run?.documents.length ?? 0} open={panelOpen} onToggle={setPoliciesOpen}>
            {run ? (
              <RunProgress run={run} busy={busy === "cancel" || busy === "retry" ? busy : null} pageCounts={pageCounts} onCancel={() => void runAction("cancel")} onRetry={() => void runAction("retry")} />
            ) : (
              <div className="skeleton-lines" aria-hidden>
                <span />
                <span />
              </div>
            )}
            {editable && run && run.documents.length < MAX_POLICY_PDFS && (
              <button type="button" className="btn btn--secondary btn--sm cert__add" onClick={() => setAdding(true)}>
                <FilePlus size={16} aria-hidden /> Add document
              </button>
            )}
          </Accordion>
          <Accordion title="Input parameters">{run && <CertParams run={run} />}</Accordion>
          {view && view.issues.length > 0 && (
            <Accordion title="Issued versions" count={view.issues.length}>
              <ul className="cert__issues">
                {view.issues.map((i) => (
                  <li key={i.id}>
                    <a href={api.certificatePdfUrl(runId, i.version)} className="link-btn" download>
                      Version {i.version}, issued {formatDate(i.issued_at)}
                    </a>
                  </li>
                ))}
              </ul>
            </Accordion>
          )}
          <a href={certificateNewHref(view?.client)} className="btn btn--ghost btn--block new-run">
            <Plus size={16} aria-hidden /> New certificate
          </a>
        </div>
      </LeftPanel>

      <main className="pane cert" id="main" tabIndex={-1} aria-labelledby="cert-title">
        <div className="toolbar cert__toolbar">
          <h1 id="cert-title" className="toolbar__title toolbar__title--named">
            <Glyph name="certificate" size={30} />
            <span className="toolbar__heading">
              <span className="toolbar__name">{view?.client ?? "Unnamed client"} · Certificate</span>
              <span className="toolbar__meta">
                {view?.client_source === "extracted" && <span className="toolbar__source">name from the documents</span>}
                <span className="toolbar__version">
                  {holder ? `For ${holder}. ` : ""}Run {runId}
                  {view?.version != null ? `, version ${view.version}` : ""}
                </span>
              </span>
            </span>
          </h1>
          {view && cert && (
            <div className="cert__actions">
              {issued && view.issue ? (
                <>
                  <span className="cert__issued">
                    <Glyph name="done" size={20} />
                    <span>
                      <span className="status-word">Issued</span> {formatDate(view.issue.issued_at)}
                    </span>
                  </span>
                  <a href={api.certificatePdfUrl(runId, view.issue.version)} className="btn btn--primary" download>
                    <DownloadSimple size={16} aria-hidden /> Download PDF
                  </a>
                  <button type="button" className="btn btn--ghost" onClick={() => void newVersion()} disabled={busy === "version"}>
                    {busy === "version" ? "Starting..." : "New version"}
                  </button>
                </>
              ) : (
                <IssueGate view={view} open={open} issuing={issuing} onIssue={() => void issue()} />
              )}
            </div>
          )}
        </div>

        {banner && (
          <div className="cert__banner" role="alert">
            <Info size={18} aria-hidden />
            <p>{banner}</p>
            <button type="button" className="icon-btn icon-btn--sm" aria-label="Dismiss" onClick={() => setBanner(null)}>
              <X size={14} aria-hidden />
            </button>
          </div>
        )}

        {!view ? (
          <div className="pane__body pane__body--center" aria-busy="true">
            <ReadingMark size={40} />
            <span className="sr-only" role="status">
              Loading the certificate
            </span>
          </div>
        ) : run?.active ? (
          <div className="pane__body pane__body--center">
            <div className="state-block cert__reading">
              <ReadingMark size={40} />
              <p className="state-block__title">
                Reading {run.documents.filter((d) => d.status !== "done" && d.status !== "failed").length || run.documents.length}{" "}
                {run.documents.length === 1 ? "document" : "documents"}
              </p>
              <p className="hint">The checks run as soon as every document is read. Your earlier decisions carry over.</p>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPoliciesOpen(true)}>
                Show the documents
              </button>
            </div>
          </div>
        ) : !cert ? (
          <div className="pane__body pane__body--center">
            <div className="state-block" role="alert">
              <p className="state-block__title">The certificate could not be drafted.</p>
              <p className="hint">{run?.error ?? "No document could be read."}</p>
              {run?.retryable && (
                <button type="button" className="btn btn--primary" onClick={() => void runAction("retry")} disabled={busy != null}>
                  {busy === "retry" ? "Retrying..." : "Retry"}
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="cert__body">
            <div className="cert__paperwrap">
              {view.notices.length > 0 && !pdf && (
                <ul className="cert__notices" aria-label="Notices">
                  {view.notices.map((n, i) => (
                    <li key={i} className="cert__notice">
                      <Info size={16} aria-hidden /> {n.message}
                    </li>
                  ))}
                </ul>
              )}
              {pdf ? (
                <section className="cert__source" aria-label={`Source: ${pdf.filename}`}>
                  <div className="source-pane__head">
                    <span className={`doc-letter hue-${hueOf(pdf.letter)}`} aria-hidden>
                      {pdf.letter}
                    </span>
                    <span className="source-pane__name">{pdf.filename}</span>
                    <button type="button" className="btn btn--ghost btn--sm" onClick={closeSource}>
                      Back to certificate <kbd>Esc</kbd>
                    </button>
                  </div>
                  <Suspense fallback={<div className="pdf__frame"><div className="pdf__skeleton" aria-hidden /></div>}>
                    <PdfViewer target={pdf} />
                  </Suspense>
                </section>
              ) : (
                <CertificatePaper
                  cert={cert}
                  chips={view.chips}
                  onChip={openSource}
                  pointed={pointed}
                  marks={marks}
                  status={issued && view.issue ? `Issued ${formatDate(view.issue.issued_at)}` : `Draft, version ${view.version}`}
                  issued={issued}
                  onEdit={editable ? edit : undefined}
                />
              )}
            </div>
            <Ledger
              view={view}
              readOnly={!editable}
              onDecide={(c, s, n) => decide(c, s, n)}
              onAddDocument={() => setAdding(true)}
              onPoint={onPoint}
              onChip={openSource}
              rowNotes={rowNotes}
            />
          </div>
        )}
        <p className="sr-only" role="status" aria-live="polite">
          {announce}
        </p>
      </main>
      {adding && view && (
        <AddDocumentDialog
          view={view}
          workspace={workspace}
          onClose={() => setAdding(false)}
          onAdded={(v, n) => {
            setView(v);
            setPoliciesOpen(true);
            setAnnounce(`Reading ${n} new ${n === 1 ? "document" : "documents"}.`);
            toast({ message: `Reading ${n} new ${n === 1 ? "document" : "documents"}. The checks run again after.`, hold: 5000 });
          }}
        />
      )}
    </div>
  );
}

function IssueGate({ view, open, issuing, onIssue }: { view: CertificateView; open: number; issuing: boolean; onIssue: () => void }) {
  const id = useId();
  const why = view.can_issue
    ? "Every check is handled"
    : view.blocked.includes("claims_open")
      ? `${open} ${open === 1 ? "check" : "checks"} to resolve`
      : view.blocked.includes("no_coverage")
        ? "No policy in force on the date"
        : view.blocked.includes("run_active")
          ? "Reading documents"
          : "Not ready to issue";
  return (
    <div className="issue">
      <button type="button" className="btn btn--primary" aria-disabled={!view.can_issue || issuing || undefined} aria-describedby={id} onClick={onIssue}>
        {issuing ? (
          <>
            <span className="spinner spinner--sm spinner--on-primary" aria-hidden /> Issuing...
          </>
        ) : (
          "Issue certificate"
        )}
      </button>
      <span id={id} className={`issue__count${view.can_issue ? " is-clear" : ""}`}>
        {why}
      </span>
    </div>
  );
}

/** What the broker asked for (read-only here; New certificate starts a different one). */
function CertParams({ run }: { run: Run }) {
  const p = run.parameters as {
    holder_name?: string;
    holder_address?: string;
    wording?: Partial<Record<(typeof WORDINGS)[number], boolean>>;
    certificate_date?: string;
    description?: string;
  };
  const asked = WORDINGS.filter((w) => p.wording?.[w]);
  return (
    <dl className="params">
      <div className="params__row">
        <dt>Holder</dt>
        <dd className="params__multi">
          {p.holder_name}
          {p.holder_address ? `\n${p.holder_address}` : ""}
        </dd>
      </div>
      <div className="params__row">
        <dt>Wording</dt>
        <dd>{asked.length ? asked.map((w) => WORDING_LABEL[w]).join(", ") : "None asked for"}</dd>
      </div>
      <div className="params__row">
        <dt>Date</dt>
        <dd>{formatDate(p.certificate_date)}</dd>
      </div>
      <div className="params__row">
        <dt>Description</dt>
        <dd>{p.description || "None"}</dd>
      </div>
    </dl>
  );
}

/** Add document (D2): read only the new documents; the draft keeps its facts and decisions. */
function AddDocumentDialog({
  view,
  workspace,
  onClose,
  onAdded,
}: {
  view: CertificateView;
  workspace: Workspace;
  onClose: () => void;
  onAdded: (v: CertificateView, n: number) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const { closing, close } = useDialogClose(ref);
  const titleId = useId();
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  const room = MAX_POLICY_PDFS - view.run.documents.length;
  const add = async () => {
    if (uploading) return setError("Wait for the uploads to finish.");
    if (!docs.length) return setError("Choose or upload a document first.");
    setBusy(true);
    setError(null);
    try {
      const v = await api.addCertificateDocuments(view.run.id, docs.map((d) => d.id));
      onAdded(v, docs.length);
      close();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't add the documents. Retry.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <dialog ref={ref} className={`cdialog dialog-motion dialog-motion--enter${closing ? " is-closing" : ""}`} aria-labelledby={titleId} onClose={onClose}>
      <header className="cdialog__head">
        <h2 id={titleId} className="cdialog__title">
          Add a document
        </h2>
        <button type="button" className="icon-btn" aria-label="Close" onClick={close}>
          <X size={18} aria-hidden />
        </button>
      </header>
      <div className="cdialog__body">
        <p className="hint">
          A missing endorsement or declarations page. Only the new documents are read; your decisions carry over. Room for {room} more.
        </p>
        <PolicyPicker
          workspace={workspace}
          client={view.client ?? ""}
          selected={docs}
          onChange={setDocs}
          exclude={view.run.documents.map((d) => d.document_id)}
          max={room}
          onBusy={setUploading}
        />
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <footer className="cdialog__foot">
        <button type="button" className="btn btn--ghost" onClick={close} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="btn btn--primary" onClick={() => void add()} disabled={busy}>
          {busy ? "Adding..." : docs.length > 1 ? `Add ${docs.length} and read` : "Add and read"}
        </button>
      </footer>
    </dialog>
  );
}
