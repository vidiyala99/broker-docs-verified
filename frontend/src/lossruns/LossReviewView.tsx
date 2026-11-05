import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState } from "react";
import { ArrowLeft, CheckCircle, X } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import { useRunEvents } from "../api/useRunEvents";
import type { ClaimRow, LossChip as LossChipData, LossReview, Run, RunDocument, Workspace } from "../api/types";
import { Accordion } from "../ui/Accordion";
import { useToast } from "../ui/Toast";
import { LeftPanel, type Viewport } from "../shell/layout";
import { RunProgress } from "../run/RunProgress";
import { Glyph, ReadingMark } from "../voice/marks";
import type { PdfTarget } from "../pdf/PdfViewer";
import { ByFile, type FileActions } from "./ByFile";
import { CardWord, cardTitle, Checks, type CheckActions } from "./Checks";
import { ClaimsTable, type RowActions, type TableQuery } from "./ClaimsTable";
import { checkCards, chipTarget, day, docName, groupOf, lossNewHref, parseCents, usd, type CheckCard } from "./lossUtil";
import "./lossruns.css";

const PdfViewer = lazy(() => import("../pdf/PdfViewer").then((m) => ({ default: m.PdfViewer })));

/** Phone is for status (PRODUCT.md): under 720px the review shows totals and checks, no table and no actions. */
function usePhone(): boolean {
  const q = "(max-width: 719px)";
  const [on, setOn] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const f = () => setOn(m.matches);
    m.addEventListener("change", f);
    return () => m.removeEventListener("change", f);
  }, []);
  return on;
}

type Editor =
  | { mode: "confirm" | "leave_out" | "fix"; row: ClaimRow }
  | { mode: "add"; documentId: number | null };

/** The docked editor under the register: a row's confirm / fix / leave out, or a missed claim. Inline, never a modal. */
function RowEditor({ editor, review, onClose, onSave }: {
  editor: Editor;
  review: LossReview;
  onClose: () => void;
  onSave: (e: Editor, values: Record<string, string>) => Promise<void>;
}) {
  const id = useId();
  const row = editor.mode === "add" ? null : editor.row;
  const [values, setValues] = useState<Record<string, string>>((): Record<string, string> =>
    row
      ? { paid: row.effective.paid != null ? (row.effective.paid / 100).toFixed(2) : "", reserve: row.effective.reserve != null ? (row.effective.reserve / 100).toFixed(2) : "", incurred: row.effective.incurred != null ? (row.effective.incurred / 100).toFixed(2) : "", note: "" }
      : { claim_number: "", claimant: "", date_of_loss: "", status: "open", paid: "", reserve: "", incurred: "", note: "", document_id: editor.mode === "add" && editor.documentId != null ? String(editor.documentId) : "" },
  );
  const [error, setError] = useState<{ field: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: { target: { value: string } }) => setValues((v) => ({ ...v, [k]: e.target.value }));
  const title =
    editor.mode === "add"
      ? "Add a missed claim"
      : editor.mode === "confirm"
        ? `Confirm claim ${row!.claim_number}`
        : editor.mode === "fix"
          ? `Fix the amounts of claim ${row!.claim_number}`
          : `Leave claim ${row!.claim_number} out of totals`;
  const save = async () => {
    if ((editor.mode === "confirm" || editor.mode === "leave_out") && !values.note?.trim()) {
      setError({ field: "note", message: "Add a note: it is kept with the decision." });
      return;
    }
    if (editor.mode === "add" && !values.claim_number?.trim()) {
      setError({ field: "claim_number", message: "Enter the claim number as the carrier prints it." });
      return;
    }
    for (const k of ["paid", "reserve", "incurred"]) {
      if (editor.mode !== "confirm" && editor.mode !== "leave_out" && values[k]?.trim() && parseCents(values[k]!) == null) {
        setError({ field: k, message: "Enter an amount like 12,400.00." });
        return;
      }
    }
    setBusy(true);
    try {
      await onSave(editor, values);
    } catch (e) {
      setError({ field: "form", message: e instanceof ApiError || e instanceof Error ? e.message : "Couldn't save. Retry." });
      setBusy(false);
    }
  };
  const err = (f: string) => (error?.field === f ? <p id={`${id}-${f}-e`} className="inline-error" role="alert">{error.message}</p> : null);
  const field = (k: string, label: string, opts: { type?: string; hint?: string; wide?: boolean } = {}) => (
    <div className={`field lr-ed__field${opts.wide ? " lr-ed__field--wide" : ""}`}>
      <label htmlFor={`${id}-${k}`} className="field__label">{label}</label>
      <input
        id={`${id}-${k}`}
        className="input"
        inputMode={opts.type === "money" ? "decimal" : undefined}
        type={opts.type === "date" ? "date" : "text"}
        value={values[k] ?? ""}
        onChange={set(k)}
        aria-invalid={error?.field === k || undefined}
        aria-describedby={error?.field === k ? `${id}-${k}-e` : undefined}
      />
      {err(k)}
    </div>
  );
  const docs = review.totals?.documents ?? [];
  return (
    <section className="lr-ed" aria-labelledby={`${id}-t`} onKeyDown={(e) => e.key === "Escape" && onClose()}>
      <div className="lr-ed__head">
        <h3 id={`${id}-t`} className="lr-ed__title">{title}</h3>
        <button type="button" className="icon-btn icon-btn--sm" aria-label="Close" onClick={onClose}><X size={14} aria-hidden /></button>
      </div>
      {row && (
        <p className="hint">
          {row.claimant ?? "No claimant"}, {day(row.date_of_loss)}. Read from the file: paid {usd(row.extracted.paid) || "none"}, reserve {usd(row.extracted.reserve) || "none"}, incurred {usd(row.extracted.incurred) || "none"}.
        </p>
      )}
      <div className="lr-ed__grid">
        {editor.mode === "add" && (
          <>
            {field("claim_number", "Claim number")}
            {field("claimant", "Claimant", { wide: true })}
            {field("date_of_loss", "Date of loss", { type: "date" })}
            <div className="field lr-ed__field">
              <label htmlFor={`${id}-doc`} className="field__label">From file</label>
              <select id={`${id}-doc`} className="input" value={values.document_id} onChange={set("document_id")}>
                <option value="">None (not on any file)</option>
                {docs.map((d) => <option key={d.document_id} value={d.document_id}>{d.letter} {d.filename}</option>)}
              </select>
            </div>
            <div className="field lr-ed__field">
              <label htmlFor={`${id}-status`} className="field__label">Status</label>
              <select id={`${id}-status`} className="input" value={values.status} onChange={set("status")}>
                <option value="open">Open</option>
                <option value="closed">Closed</option>
                <option value="reopened">Reopened</option>
              </select>
            </div>
          </>
        )}
        {(editor.mode === "add" || editor.mode === "fix") && (
          <>
            {field("paid", "Paid", { type: "money" })}
            {field("reserve", "Reserve", { type: "money" })}
            {field("incurred", "Incurred", { type: "money" })}
          </>
        )}
        {(editor.mode === "confirm" || editor.mode === "leave_out") && (
          <div className="field lr-ed__field lr-ed__field--full">
            <label htmlFor={`${id}-note`} className="field__label">{editor.mode === "confirm" ? "Why the row is right" : "Why it stays out of totals"}</label>
            <textarea id={`${id}-note`} className="input" rows={2} maxLength={500} value={values.note} onChange={set("note")} autoFocus aria-invalid={error?.field === "note" || undefined} aria-describedby={error?.field === "note" ? `${id}-note-e` : undefined} />
            {err("note")}
          </div>
        )}
      </div>
      {err("form")}
      <div className="cact__row cact__row--end">
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => void save()}>
          {busy ? "Saving..." : editor.mode === "add" ? "Add claim" : editor.mode === "confirm" ? "Confirm" : editor.mode === "fix" ? "Save amounts" : "Leave out"}
        </button>
      </div>
    </section>
  );
}

function reviewOf(res: unknown): LossReview {
  const r = res as { review?: LossReview } & LossReview;
  return r.review ?? r;
}

/** Phone: the totals and the checks by word and title (status only, PRODUCT.md). */
function PhoneStatus({ review }: { review: LossReview }) {
  const t = review.totals;
  const cards = checkCards(review.checks).filter((c) => groupOf(c) !== "matches");
  return (
    <div className="lr-phone">
      {t && (
        <section aria-labelledby="lr-ph-t">
          <h2 id="lr-ph-t" className="lr-h2">Totals</h2>
          <dl className="lr-phone__dl">
            <div><dt>Claims</dt><dd>{t.claims.toLocaleString("en-US")}<small>{t.open_claims} open</small></dd></div>
            <div><dt>Incurred</dt><dd>{usd(t.incurred.cents)}<small>from {t.incurred.from_claims} claims</small></dd></div>
            <div><dt>Paid</dt><dd>{usd(t.paid.cents)}</dd></div>
            <div><dt>Reserve</dt><dd>{usd(t.reserve.cents)}</dd></div>
            <div><dt>Recoveries</dt><dd>{usd(t.recovery.cents)}<small>from {t.recovery.from_claims} claims</small></dd></div>
            <div><dt>Deductibles</dt><dd>{usd(t.deductible.cents)}<small>from {t.deductible.from_claims} claims</small></dd></div>
          </dl>
          <p className="hint">{t.unreconciled ? "Unreconciled: a printed total does not tie yet. " : ""}The claims table and the fixes open on a wider screen.</p>
        </section>
      )}
      <section aria-labelledby="lr-ph-c">
        <h2 id="lr-ph-c" className="lr-h2">Checks</h2>
        <ul className="lr-phone__checks">
          {cards.map((c) => (
            <li key={c.key} className={`lr-item lr-item--${groupOf(c) === "to_resolve" ? c.verdict : "done"}`}>
              <CardWord card={c} />
              <p className="claim__title">{cardTitle(c, review)}</p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/**
 * A loss review (slice 5a, direction A "Register and ledger", owner-approved 2026-11-04 12:40): the claims register
 * with its counted totals as the table's own footing, beside the check ledger. A check's "Show the rows" scopes the
 * register and foots the printed line under the rows; "By file" lays the same review out per carrier file with every
 * printed total footed where it was printed (B's footing). Every broker action applies at once, says what it did in a
 * toast with Undo, and the totals read "Recounting..." until the server's new review lands.
 */
export function LossReviewView({ runId, viewport, onRunChanged }: { runId: number; workspace: Workspace; viewport: Viewport; onRunChanged?: (run: Run) => void }) {
  const toast = useToast();
  const phone = usePhone();
  const [review, setReview] = useState<LossReview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [view, setView] = useState<"claims" | "files">("claims");
  const [query, setQuery] = useState<TableQuery>({ sort: "claim_incurred", dir: "desc", quick: [], q: "", scope: null });
  const [scope, setScope] = useState<CheckCard | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [pdf, setPdf] = useState<PdfTarget | null>(null);
  const [recounting, setRecounting] = useState(false);
  const [pending, setPending] = useState<Map<number, string>>(new Map());
  const [panelOpen, setPanelOpen] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<"cancel" | "retry" | "done" | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const runChanged = useRef(onRunChanged);
  runChanged.current = onRunChanged;

  const load = useCallback(async () => {
    try {
      const r = await api.lossReview(runId);
      setReview(r);
      setLoadError(null);
      runChanged.current?.({ ...r.run, client: r.client, client_source: r.client_source });
      return r;
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : "Couldn't load this loss review.");
      return null;
    }
  }, [runId]);
  useEffect(() => {
    void load();
  }, [load]);

  const updateRun = (fn: (r: Run) => Run) => setReview((v) => (v ? { ...v, run: fn(v.run) } : v));
  useRunEvents(review?.run.id ?? null, Boolean(review?.run.active), {
    onStatus: (status, error, retryable) =>
      updateRun((r) => ({ ...r, status, error, retryable, active: !["done", "partial", "failed", "cancelled"].includes(status) })),
    onDocument: (doc: RunDocument) => updateRun((r) => ({ ...r, documents: r.documents.map((d) => (d.run_document_id === doc.run_document_id ? { ...d, ...doc } : d)) })),
    onArtifact: () => undefined,
    onEnd: () => {
      void load().then(() => setVersion((v) => v + 1));
    },
  });

  // ------------------------------------------------------------------ actions: apply at once, recount, toast + Undo
  const act = useCallback(
    async (call: () => Promise<unknown>, message: string, undo?: (res: unknown) => Promise<unknown>, rowId?: number, word?: string) => {
      if (rowId != null && word) setPending((p) => new Map(p).set(rowId, word));
      const slow = window.setTimeout(() => setRecounting(true), 350);
      try {
        const res = await call();
        setReview(reviewOf(res));
        setVersion((v) => v + 1);
        toast({
          message,
          action: undo
            ? {
                label: "Undo",
                run: () => {
                  void (async () => {
                    const s2 = window.setTimeout(() => setRecounting(true), 350);
                    try {
                      setReview(reviewOf(await undo(res)));
                      setVersion((v) => v + 1);
                      toast({ message: "Undone." });
                    } catch (e) {
                      toast({ message: e instanceof Error ? e.message : "Couldn't undo. Retry.", tone: "error" });
                    } finally {
                      window.clearTimeout(s2);
                      setRecounting(false);
                    }
                  })();
                },
              }
            : undefined,
        });
        return res;
      } catch (e) {
        if (e instanceof ApiError && e.code === "item_changed") {
          await load();
          setVersion((v) => v + 1);
          toast({ message: "This check changed while you were deciding. It shows its new values now.", tone: "error" });
          return null;
        }
        throw e;
      } finally {
        window.clearTimeout(slow);
        setRecounting(false);
        if (rowId != null) setPending((p) => {
          const n = new Map(p);
          n.delete(rowId);
          return n;
        });
      }
    },
    [toast, load],
  );

  const openChip = useCallback((chip: LossChipData, scan: boolean) => {
    opener.current = document.activeElement as HTMLElement | null;
    setPdf(chipTarget(chip, scan));
  }, []);
  const closeSource = useCallback(() => {
    setPdf(null);
    window.setTimeout(() => opener.current?.focus(), 0);
  }, []);
  useEffect(() => {
    if (!pdf) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if ((e.target as HTMLElement).closest("input, textarea, select, .left-panel--overlay")) return;
      closeSource();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pdf, closeSource]);

  const rowAct: RowActions = {
    openChip,
    edit: (row, mode) => setEditor({ mode, row }),
    undo: (row) => {
      if (!row.decision) return;
      const word = row.decision.kind === "edited" ? "edit" : row.decision.kind === "confirmed" ? "confirm" : "leave-out";
      void act(() => api.lossUndoDecision(runId, row.decision!.id), `Your ${word} on ${row.claim_number} is undone.`, undefined, row.id, "Undoing...").catch((e) =>
        toast({ message: e instanceof Error ? e.message : "Couldn't undo. Retry.", tone: "error" }),
      );
    },
    removeAdded: (row) => {
      void act(() => api.lossRemoveAdded(runId, row.id), `${row.claim_number} removed.`, undefined, row.id, "Removing...").catch((e) =>
        toast({ message: e instanceof Error ? e.message : "Couldn't remove it. Retry.", tone: "error" }),
      );
    },
  };

  const saveEditor = async (e: Editor, v: Record<string, string>) => {
    if (e.mode === "add") {
      const body: Record<string, string | number | null> = {
        claim_number: v.claim_number!.trim(),
        claimant: v.claimant?.trim() || null,
        date_of_loss: v.date_of_loss || null,
        status: v.status ?? "open",
        document_id: v.document_id ? Number(v.document_id) : null,
      };
      for (const k of ["paid", "reserve", "incurred"]) body[k] = v[k]?.trim() ? parseCents(v[k]!) : null;
      const where = body.document_id != null ? docName(review, Number(body.document_id)) : "the review";
      await act(() => api.lossAddClaim(runId, body), `${body.claim_number} added to ${where}.`, (res) => api.lossRemoveAdded(runId, (res as { row: ClaimRow }).row.id));
    } else if (e.mode === "fix") {
      const amounts: Record<string, number> = {};
      for (const k of ["paid", "reserve", "incurred"] as const) {
        const c = v[k]?.trim() ? parseCents(v[k]!) : null;
        if (c != null && c !== e.row.effective[k]) amounts[k] = c;
      }
      if (!Object.keys(amounts).length) {
        setEditor(null);
        return;
      }
      await act(
        () => api.lossRowDecision(runId, e.row.id, { kind: "edit", amounts }),
        `Amounts on ${e.row.claim_number} changed.`,
        (res) => api.lossUndoDecision(runId, (res as { decision: { id: number } }).decision.id),
        e.row.id,
        "Saving...",
      );
    } else {
      const kind = e.mode === "confirm" ? "confirm" : "leave_out";
      await act(
        () => api.lossRowDecision(runId, e.row.id, { kind, note: v.note!.trim() }),
        kind === "confirm" ? `${e.row.claim_number} confirmed. It counts in the totals.` : `${e.row.claim_number} left out of totals.`,
        (res) => api.lossUndoDecision(runId, (res as { decision: { id: number } }).decision.id),
        e.row.id,
        kind === "confirm" ? "Confirming..." : "Leaving out...",
      );
    }
    setEditor(null);
  };

  const checkAct: CheckActions = {
    show: (card) => {
      setView("claims");
      setScope(card);
      setQuery((q) => ({ ...q, quick: [], q: "", scope: card.lead.filter }));
    },
    addClaim: (documentId) => {
      setView("claims");
      setEditor({ mode: "add", documentId });
    },
    accept: async (card, note) => {
      const open = card.checks.filter((c) => c.needs_action);
      await act(
        async () => {
          let last: unknown = null;
          for (const c of open) last = await api.lossCheck(runId, c.id, { action: "accept", note }, c.fingerprint);
          return last;
        },
        `${cardTitle(card, review)}: accepted with your note.`,
        async () => {
          let last: unknown = null;
          for (const c of open) last = await api.lossCheck(runId, c.id, { action: "reopen" });
          return last;
        },
      );
    },
    pick: async (card, documentId) => {
      await act(
        () => api.lossCheck(runId, card.lead.id, { action: "pick", document_id: documentId }, card.lead.fingerprint),
        `Claim ${card.lead.claim_key ?? ""} now uses ${docName(review, documentId)}.`,
        () => api.lossCheck(runId, card.lead.id, { action: "reopen" }),
      ).catch((e) => toast({ message: e instanceof Error ? e.message : "Couldn't save. Retry.", tone: "error" }));
    },
    reopen: async (card) => {
      await act(
        async () => {
          let last: unknown = null;
          for (const c of card.checks.filter((x) => x.status === "resolved")) last = await api.lossCheck(runId, c.id, { action: "reopen" });
          return last;
        },
        `${cardTitle(card, review)}: open again.`,
      ).catch((e) => toast({ message: e instanceof Error ? e.message : "Couldn't save. Retry.", tone: "error" }));
    },
    openChip: (chip) => openChip(chip, false),
  };
  const fileAct: FileActions = {
    addClaim: (id) => {
      setView("claims");
      setEditor({ mode: "add", documentId: id });
    },
    toChecks: (card) => {
      const el = document.querySelector(`[data-check="${CSS.escape(card.key)}"]`);
      el?.scrollIntoView({ block: "center", behavior: keyboardInstant() });
      (el?.querySelector("button") as HTMLElement | null)?.focus();
    },
  };

  const runAction = async (kind: "cancel" | "retry") => {
    setBusy(kind);
    try {
      if (kind === "cancel") await api.cancelRun(runId);
      else await api.retryRun(runId);
      await load();
    } catch (e) {
      toast({ message: e instanceof ApiError ? e.message : "Couldn't do that. Retry.", tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  const markDone = async (state: "done" | "open") => {
    setBusy("done");
    try {
      const run = await api.patchRun(runId, { queue_state: state });
      setReview((v) => (v ? { ...v, run: { ...v.run, ...run } } : v));
      toast(
        state === "done"
          ? { message: "Filed as done.", action: { label: "Undo", run: () => void markDone("open") } }
          : { message: "Back in the queue." },
      );
    } catch (e) {
      toast({ message: e instanceof ApiError ? e.message : "Couldn't file it. Retry.", tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  if (loadError && !review) {
    return (
      <main className="pane" id="main" tabIndex={-1}>
        <div className="pane__body pane__body--center">
          <div className="state-block" role="alert">
            <p className="state-block__title">{loadError}</p>
            <button type="button" className="btn btn--primary" onClick={() => void load()}>Retry</button>
          </div>
        </div>
      </main>
    );
  }

  const run = review?.run ?? null;
  const reading = run?.active ?? false;
  const toResolve = review ? checkCards(review.checks).filter((c) => groupOf(c) === "to_resolve").length : 0;
  const filed = run?.queue_state === "done";
  const files = run?.documents.length ?? 0;
  const carriers = new Set(review?.totals?.documents.map((d) => d.carrier ?? d.filename)).size;
  const asOf = review?.totals?.as_of;
  // The scoped card follows the review: after an action its figures (and verdict) are the server's new ones.
  const liveScope = scope && review ? (checkCards(review.checks).find((c) => c.key === scope.key) ?? null) : null;

  return (
    <div className="workspace lr-ws">
      {!phone && (
        <LeftPanel viewport={viewport} label="Loss review panel" folded chat={false} uploadsLabel="Show files" onReveal={() => setPanelOpen(true)}>
          <div className="panel-scroll">
            <Accordion title="Files" count={files} open={panelOpen ?? reading} onToggle={setPanelOpen}>
              {run && <RunProgress run={run} busy={busy === "cancel" || busy === "retry" ? busy : null} onCancel={() => void runAction("cancel")} onRetry={() => void runAction("retry")} />}
            </Accordion>
            <a href={lossNewHref(review?.client)} className="btn btn--ghost btn--block new-run">New loss review</a>
          </div>
        </LeftPanel>
      )}
      <main className="pane lr" id="main" tabIndex={-1} aria-labelledby="lr-title">
        <div className="toolbar lr-toolbar">
          <h1 id="lr-title" className="toolbar__title toolbar__title--named">
            <Glyph name="analytics" size={30} tabs />
            <span className="toolbar__heading">
              <span className="toolbar__name">{review?.client ?? "Unnamed client"} · Loss review</span>
              <span className="toolbar__meta">
                {review?.client_source === "extracted" && <span className="toolbar__source">name from the documents</span>}
                <span className="toolbar__version">
                  {files} {files === 1 ? "file" : "files"}
                  {carriers ? ` from ${carriers} ${carriers === 1 ? "carrier" : "carriers"}` : ""}
                  {asOf ? `, valued as of ${day(asOf)}` : ""}. Run {runId}
                  {review?.version != null ? `, version ${review.version}` : ""}
                </span>
              </span>
            </span>
          </h1>
          {review?.synthetic && <span className="lr-synthetic">Synthetic sample data</span>}
          {review && !reading && !phone && (
            <div className="lr-view" role="radiogroup" aria-label="View">
              {(["claims", "files"] as const).map((v) => (
                <label key={v} className={`lr-view__opt${view === v ? " is-on" : ""}`}>
                  <input type="radio" name="lr-view" checked={view === v} onChange={() => setView(v)} />
                  {v === "claims" ? "All claims" : "By file"}
                </label>
              ))}
            </div>
          )}
          {review && !reading && (
            <div className="issue lr-gate">
              {filed ? (
                <>
                  <span className="cert__issued"><CheckCircle size={20} weight="bold" aria-hidden className="lr-done-glyph" /><span className="status-word">Done</span></span>
                  <button type="button" className="btn btn--ghost btn--sm" disabled={busy === "done"} onClick={() => void markDone("open")}>Move back to queue</button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    className="btn btn--primary"
                    aria-disabled={toResolve > 0 || undefined}
                    aria-describedby="lr-gate-why"
                    onClick={() => toResolve === 0 && void markDone("done")}
                  >
                    Mark review done
                  </button>
                  <span id="lr-gate-why" className={`issue__count${toResolve ? "" : " is-clear"}`}>
                    {toResolve ? `${toResolve} ${toResolve === 1 ? "check" : "checks"} to resolve` : "Every check is handled"}
                  </span>
                </>
              )}
            </div>
          )}
        </div>

        {!review ? (
          <div className="pane__body pane__body--center" aria-busy="true">
            <ReadingMark size={40} />
            <span className="sr-only" role="status">Loading</span>
          </div>
        ) : reading ? (
          <div className="pane__body pane__body--center lr-reading">
            <ReadingMark size={40} />
            <h2 className="lr-h2">Reading {files} {files === 1 ? "file" : "files"}</h2>
            <p className="hint">Spreadsheets are read in code; PDFs page by page, every row checked against its printed line.</p>
            {run && <RunProgress run={run} busy={busy === "cancel" || busy === "retry" ? busy : null} onCancel={() => void runAction("cancel")} onRetry={() => void runAction("retry")} />}
          </div>
        ) : phone ? (
          <PhoneStatus review={review} />
        ) : (
          <div className={`lr-body${pdf ? " has-source" : ""}`}>
            <section className="lr-register" aria-label={view === "claims" ? "Claims" : "Claims by file"}>
              {run && run.status === "partial" && run.error && (
                <p className="lr-banner" role="status">{run.error}</p>
              )}
              {view === "claims" ? (
                <>
                  <div className="lr-register__band">
                    <h2 className="lr-h2">Claims</h2>
                    {review.totals && (
                      <p className="lr-also">
                        Recoveries <b>{usd(review.totals.recovery.cents)}</b> from {review.totals.recovery.from_claims} claims. Deductibles{" "}
                        <b>{usd(review.totals.deductible.cents)}</b> from {review.totals.deductible.from_claims} claims.
                      </p>
                    )}
                  </div>
                  {scope && (
                    <div className="lr-scopebar">
                      <span>Showing the rows behind <b>{cardTitle(scope, review)}</b></span>
                      <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setScope(null); setQuery((q) => ({ ...q, scope: null })); }}>
                        <ArrowLeft size={16} aria-hidden /> All claims
                      </button>
                    </div>
                  )}
                  <ClaimsTable
                    runId={runId}
                    review={review}
                    query={query}
                    setQuery={(q) => {
                      if (!q.scope) setScope(null);
                      setQuery(q);
                    }}
                    version={version}
                    act={rowAct}
                    scope={liveScope}
                    recounting={recounting}
                    pending={pending}
                    narrow={viewport !== "wide"}
                  />
                </>
              ) : (
                <ByFile runId={runId} review={review} version={version} act={fileAct} rowAct={rowAct} pending={pending} />
              )}
              {editor && <RowEditor key={editor.mode === "add" ? "add" : `${editor.mode}${editor.row.id}`} editor={editor} review={review} onClose={() => setEditor(null)} onSave={saveEditor} />}
            </section>
            {pdf ? (
              <aside className="lr-source" aria-label="Source">
                <div className="lr-source__head">
                  <button type="button" className="btn btn--ghost btn--sm" onClick={closeSource}><ArrowLeft size={16} aria-hidden /> Back to checks</button>
                  <span className="lr-source__name">{pdf.filename}</span>
                </div>
                <Suspense fallback={<div className="pane__body pane__body--center"><ReadingMark size={32} /></div>}>
                  <PdfViewer target={pdf} />
                </Suspense>
              </aside>
            ) : (
              <Checks review={review} act={checkAct} scoped={scope?.key ?? null} />
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function keyboardInstant(): ScrollBehavior {
  return document.documentElement.dataset.input === "keyboard" || window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}
