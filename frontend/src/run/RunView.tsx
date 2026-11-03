import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Copy,
  Eye,
  FileDoc,
  FilePdf,
  FileXls,
  ListBullets,
  Plus,
  SquareSplitHorizontal,
  ThumbsDown,
  ThumbsUp,
  Warning,
  X,
  ClockCounterClockwise,
  FileText,
} from "@phosphor-icons/react";
import { api, ApiError, downloadExport, type ExportFormat } from "../api/client";
import { POLICY_WORKFLOW, PROPOSAL_WORKFLOW } from "../config";
import { useRunEvents } from "../api/useRunEvents";
import type { Artifact, Chip, EditResult, Line, Run, RunDocument, Workspace } from "../api/types";
import { LineChoice } from "../personal/LineChoice";
import { Accordion } from "../ui/Accordion";
import { useToast } from "../ui/Toast";
import { Tip } from "../ui/Tip";
import { LeftPanel, type Viewport } from "../shell/layout";
import { RecentRuns } from "../launch/RecentRuns";
import { Notices, Report, type ReportActions } from "../report/Report";
import { Discrepancies } from "../policy/Discrepancies";
import { ROLE_INFO } from "../policy/draft";
import { Outline, warningSummary, type TrustFilter } from "../report/Outline";
import type { CellRef } from "../report/Table";
import { hueOf } from "../report/Chip";
import type { PdfTarget } from "../pdf/PdfViewer";
import { keyboardAction } from "../ui/input";
import { tokenMs, usePresence } from "../ui/motion";

const PdfViewer = lazy(() => import("../pdf/PdfViewer").then((m) => ({ default: m.PdfViewer })));
import { Chat } from "./Chat";
import { RunProgress } from "./RunProgress";
import { RunClient } from "./RunClient";
import { ReadingMark, WorkflowMark } from "../voice/marks";

type Tab = "report" | number; // number = document id of an open PDF

export function RunView({
  runId,
  runs,
  viewport,
  onRunsChanged,
  onRunChanged,
}: {
  runId: number;
  workspace: Workspace;
  runs: Run[] | null;
  viewport: Viewport;
  onRunsChanged: () => void;
  /** The shell's copy of this run (rail, crumbs, side list line); told about a re-tag at once and on rollback. */
  onRunChanged?: (run: Run) => void;
}) {
  const toast = useToast();
  const [run, setRun] = useState<Run | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"cancel" | "retry" | null>(null);
  const [pdfs, setPdfs] = useState<PdfTarget[]>([]);
  const [tab, setTab] = useState<Tab>("report");
  const [split, setSplit] = useState(false);
  const [splitAnim, setSplitAnim] = useState(false);
  const [outlineOpen, setOutlineOpen] = useState(viewport === "wide");
  // Motion lane: the outline's pointer show / hide (data-outline-motion), the split pane's pointer exit (a ghost of the
  // pane over the report's right half for --dur-fast), and PDF tabs entering / leaving (usePresence, pointer only).
  const [outlineMotion, setOutlineMotion] = useState<"enter" | "leave" | null>(null);
  const [leavingPdf, setLeavingPdf] = useState<PdfTarget | null>(null);
  const [prevSplitPdf, setPrevSplitPdf] = useState<PdfTarget | null>(null);
  const pdfTabs = usePresence(pdfs, (p) => p.documentId, !keyboardAction());
  const [focusCount, setFocusCount] = useState(0);
  const [filter, setFilter] = useState<TrustFilter | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const [preview, setPreview] = useState(false);
  const [announce, setAnnounce] = useState("");
  const [reconnecting, setReconnecting] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [scrollRoot, setScrollRoot] = useState<HTMLDivElement | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const wide = viewport === "wide";
  // Left panel shares: Upload is open until the run is Done (the user's own toggle wins until
  // the status changes, so a Retry reopens it); chat then takes the remaining height.
  const [uploadOverride, setUploadOverride] = useState<boolean | null>(null);
  const [chatOpen, setChatOpen] = useState(true);
  const [pageCounts, setPageCounts] = useState<Map<number, number | null>>();
  const runStatus = run?.status;
  useEffect(() => setUploadOverride(null), [runStatus]);
  // Folded while the run loads, so a Done run does not flash open then shut.
  const uploadOpen = uploadOverride ?? (runStatus != null && runStatus !== "done");

  useEffect(() => {
    let live = true;
    api.documents().then(
      (docs) => live && setPageCounts(new Map(docs.map((d) => [d.id, d.page_count]))),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [runId]);

  // ------------------------------------------------------------------ loading
  const loadArtifact = useCallback(async () => {
    try {
      setArtifact(await api.artifact(runId));
      setArtifactError(null);
    } catch (e) {
      if (e instanceof ApiError && e.code === "no_report") setArtifact(null);
      else setArtifactError(e instanceof ApiError ? e.message : "Couldn't load the report.");
    }
  }, [runId]);

  const loadRun = useCallback(async (): Promise<Run | null> => {
    try {
      const r = await api.run(runId);
      setRun(r);
      setLoadError(null);
      if (r.artifact) await loadArtifact();
      else setArtifact(null);
      return r;
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : "Couldn't load this run.");
      return null;
    }
  }, [runId, loadArtifact]);

  useEffect(() => {
    setRun(null);
    setArtifact(null);
    setPdfs([]);
    setTab("report");
    setSplit(false);
    setFilter(null);
    void loadRun();
  }, [loadRun]);

  // Slice 3b T6: re-tag Commercial / Personal (the only place to). Optimistic: the control, crumbs and side list switch
  // at once; a failed PATCH puts the old line back and says why. Changes while one is in flight are ignored.
  const retagging = useRef(false);
  const retag = useCallback(
    async (line: Line) => {
      if (!run || retagging.current || run.line === line) return;
      const before = run;
      const optimistic = { ...run, line };
      retagging.current = true;
      setRun(optimistic);
      onRunChanged?.(optimistic);
      try {
        const saved = await api.setRunLine(before.id, line);
        setRun((r) => (r ? { ...r, line: saved.line } : r));
        onRunChanged?.({ ...optimistic, line: saved.line });
        toast({ message: saved.line === "personal" ? "Moved to Personal lines." : "Moved to Commercial.", hold: 4000 });
      } catch (e) {
        setRun((r) => (r ? { ...r, line: before.line } : r));
        onRunChanged?.(before);
        toast({ message: e instanceof ApiError ? e.message : "Couldn't change the line. Retry.", tone: "error" });
      } finally {
        // The shell reloads its run list itself when the run's line changes (App.tsx listLine).
        retagging.current = false;
      }
    },
    [run, onRunChanged, toast],
  );

  // Work queue W4: the Client control beside Line. Optimistic like retag; "" clears (the queue then falls back to the
  // extracted named insured).
  const rename = useCallback(
    async (name: string) => {
      if (!run) return;
      const before = run.client_name;
      setRun((r) => (r ? { ...r, client_name: name || null } : r));
      try {
        const saved = await api.patchRun(run.id, { client_name: name });
        setRun((r) => (r ? { ...r, client_name: saved.client_name } : r));
        toast({ message: saved.client_name ? "Client name saved." : "Client name cleared.", hold: 4000 });
      } catch (e) {
        setRun((r) => (r ? { ...r, client_name: before } : r));
        toast({ message: e instanceof ApiError ? e.message : "Couldn't save the client name. Retry.", tone: "error" });
      }
    },
    [run, toast],
  );

  const updateDoc = (doc: RunDocument) =>
    setRun((r) => (r ? { ...r, documents: r.documents.map((d) => (d.run_document_id === doc.run_document_id ? { ...d, ...doc } : d)) } : r));

  useRunEvents(run?.id ?? null, Boolean(run && !run.terminal), {
    onStatus: (status, error, retryable) =>
      setRun((r) => (r ? { ...r, status, error, retryable, active: !["done", "partial", "failed", "cancelled"].includes(status) } : r)),
    onDocument: updateDoc,
    onUsage: (u) => setRun((r) => (r ? { ...r, ...u } : r)),
    onArtifact: () => void loadArtifact(),
    onEnd: () => {
      setReconnecting(false);
      void loadRun().then((r) => {
        // The replay can end with an earlier execution's run.end while a retry is still
        // running: follow the stream again from after that event.
        if (r && !r.terminal) setGeneration((g) => g + 1);
      });
      onRunsChanged();
    },
    onConnection: (s) => setReconnecting(s === "reconnecting"),
  }, generation);

  // ------------------------------------------------------------------ sources
  const openSource = useCallback(
    (chip: Chip, via: "pointer" | "keyboard") => {
      opener.current = document.activeElement as HTMLElement | null;
      const target: PdfTarget = { documentId: chip.document_id, filename: chip.filename, letter: chip.letter, page: chip.page_no, chip };
      setPdfs((list) => {
        const idx = list.findIndex((p) => p.documentId === chip.document_id);
        if (idx === -1) return [...list, target];
        const copy = [...list];
        copy[idx] = target;
        return copy;
      });
      setTab(chip.document_id);
      setSplitAnim(via === "pointer");
      setSplit(wide);
    },
    [wide],
  );

  useEffect(() => {
    if (!wide) setSplit(false);
    if (viewport === "narrow") setOutlineOpen(false);
  }, [wide, viewport]);

  const exitSplit = useCallback(() => {
    setSplit(false);
    setTab("report");
    window.setTimeout(() => opener.current?.focus(), 0);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, dialog, .left-panel--overlay")) return;
      if (split || tab !== "report") exitSplit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [split, tab, exitSplit]);

  // The split pane's exit ghost and the outline's motion state clear once their --dur-fast transition is over.
  useEffect(() => {
    if (!leavingPdf) return;
    const t = window.setTimeout(() => setLeavingPdf(null), tokenMs("--dur-fast", 150));
    return () => window.clearTimeout(t);
  }, [leavingPdf]);

  useEffect(() => {
    if (!outlineMotion) return;
    const t = window.setTimeout(() => setOutlineMotion(null), tokenMs("--dur-fast", 150));
    return () => window.clearTimeout(t);
  }, [outlineMotion]);

  const toggleOutline = (next: boolean) => {
    setOutlineOpen(next);
    setOutlineMotion(keyboardAction() ? null : next ? "enter" : "leave");
  };

  const closePdf = (docId: number) => {
    setPdfs((list) => list.filter((p) => p.documentId !== docId));
    if (tab === docId) {
      setTab("report");
      setSplit(false);
    }
  };

  // ------------------------------------------------------------------ edits
  const afterEdit = useCallback(
    (res: EditResult, verb = "Edited") => {
      setArtifact(res.artifact);
      setAnnounce(`Saved. ${res.stale_sections.length ? "Some prose may be out of date." : ""}`);
      const art = res.artifact;
      toast({
        message: `${verb}.`,
        action: {
          label: "Undo",
          run: () => {
            api.undo(art.id, res.previous_version, res.version).then(
              (u) => {
                setArtifact(u.artifact);
                setAnnounce("Edit undone.");
                toast({ message: "Edit undone.", hold: 4000 });
              },
              (e: unknown) => toast({ message: e instanceof ApiError ? e.message : "Couldn't undo. Retry.", tone: "error" }),
            );
          },
        },
      });
    },
    [toast],
  );

  const onSave = useCallback(
    async (ref: CellRef, value: string | null) => {
      if (!artifact) throw new Error("no report");
      try {
        const res = await api.editCell(artifact.id, { ...ref, value, base_version: artifact.version });
        afterEdit(res);
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : "Couldn't save.";
        setAnnounce(`Couldn't save. ${msg}`);
        if (e instanceof ApiError && e.code === "version_conflict") void loadArtifact();
        throw e;
      }
    },
    [artifact, afterEdit, loadArtifact],
  );

  const regenerate = useCallback(async () => {
    if (!artifact) return;
    setRegenerating(true);
    setAnnounce("Rewriting the report prose.");
    try {
      const res = await api.regenerate(artifact.id, artifact.version);
      setArtifact(res.artifact);
      toast(
        res.fallback
          ? { message: "The prose could not be rewritten, so the template report is shown.", tone: "error" }
          : { message: "Prose regenerated.", hold: 4000 },
      );
    } catch (e) {
      toast({ message: e instanceof ApiError ? e.message : "Couldn't regenerate the prose. Retry.", tone: "error" });
    } finally {
      setRegenerating(false);
    }
  }, [artifact, toast]);

  const cancelRun = async () => {
    if (!run) return;
    setBusy("cancel");
    try {
      const res = await api.cancelRun(run.id);
      setRun(res.run);
      onRunsChanged();
    } catch (e) {
      toast({ message: e instanceof ApiError ? e.message : "Couldn't cancel. Retry.", tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  const retryRun = useCallback(async () => {
    if (!run) return;
    setBusy("retry");
    try {
      const res = await api.retryRun(run.id);
      setRun(res.run);
      onRunsChanged();
    } catch (e) {
      toast({ message: e instanceof ApiError ? e.message : "Couldn't retry. Retry again in a moment.", tone: "error" });
    } finally {
      setBusy(null);
    }
  }, [run, onRunsChanged, toast]);

  const rate = async (rating: number) => {
    if (!artifact) return;
    const next = artifact.feedback?.rating === rating ? 0 : rating;
    try {
      const res = await api.feedback(artifact.id, next, artifact.version);
      setArtifact({ ...artifact, feedback: res.feedback });
      setAnnounce(next === 0 ? "Feedback cleared." : "Thanks. Feedback saved.");
    } catch (e) {
      toast({ message: e instanceof ApiError ? e.message : "Couldn't save feedback.", tone: "error" });
    }
  };

  const exportAs = async (format: ExportFormat) => {
    if (!artifact) return;
    setExporting(format);
    try {
      const { filename, note } = await downloadExport(artifact.id, format, artifact.version);
      toast({ message: `Downloaded ${filename}.${note ? ` ${note}` : ""}`, hold: 6000 });
    } catch {
      toast({ message: "Export failed.", tone: "error", action: { label: "Retry", run: () => void exportAs(format) } });
    } finally {
      setExporting(null);
    }
  };

  const copyText = async () => {
    if (!scrollRoot) return;
    const text = scrollRoot.querySelector(".report")?.textContent ?? "";
    try {
      await navigator.clipboard.writeText(text);
      toast({ message: "Report text copied.", hold: 3000 });
    } catch {
      toast({ message: "Couldn't copy. Select the text instead.", tone: "error" });
    }
  };

  // ------------------------------------------------------------------ derived
  const actions: ReportActions | null = useMemo(
    () =>
      artifact && run
        ? {
            chips: artifact.chips,
            onOpen: openSource,
            onRetryRun: !run.active && ["failed", "partial", "cancelled"].includes(run.status) ? () => void retryRun() : null,
            onSave,
            editable: !run.active && artifact.is_current,
            filter,
            onRegenerate: () => void regenerate(),
            onRetryProse: () => void regenerate(),
            regenerating,
          }
        : null,
    [artifact, run, openSource, retryRun, onSave, filter, regenerate, regenerating],
  );

  if (loadError) {
    return (
      <div className="workspace">
        <main className="pane" id="main" tabIndex={-1}>
          <div className="pane__body pane__body--center">
            <div className="state-block" role="alert">
              <p>{loadError}</p>
              <button type="button" className="btn btn--secondary" onClick={() => void loadRun()}>
                Retry
              </button>
              <a href="#/" className="link-btn">
                Back to Compare Quotes
              </a>
            </div>
          </div>
        </main>
      </div>
    );
  }

  const activePdf = typeof tab === "number" ? pdfs.find((p) => p.documentId === tab) ?? null : null;
  const showReport = tab === "report" || split;
  // The pane shown now, and the split pane that just went away. A pointer exit keeps the same <section> (and its PDF
  // canvas) for --dur-fast as a ghost; Esc and keyboard exits drop it at once. Derived during render, so the pane is
  // never unmounted and remounted.
  const panePdf = activePdf && (split || tab === activePdf.documentId) ? activePdf : null;
  const splitPdf = split ? panePdf : null;
  if (splitPdf !== prevSplitPdf) {
    setPrevSplitPdf(splitPdf);
    setLeavingPdf(prevSplitPdf && !panePdf && !keyboardAction() ? prevSplitPdf : null);
  }
  const shownPdf = panePdf ?? leavingPdf;
  const paneLeaving = !panePdf && leavingPdf != null;
  const summary = artifact ? warningSummary(artifact) : null;
  const running = Boolean(run?.active);
  const isProposal = run?.workflow_name === PROPOSAL_WORKFLOW;
  const isPolicy = run?.workflow_name === POLICY_WORKFLOW;
  const personal = run?.line === "personal";

  const reportBody = (
    <div className="report-scroll" ref={setScrollRoot} data-filter={(isProposal && preview ? undefined : filter) ?? undefined} data-preview={isProposal && preview ? "" : undefined}>
      {artifact && actions && run && isPolicy ? (
        <article className="report" aria-label={artifact.title}>
          <Notices notices={artifact.content.notices} actions={actions} />
          <Discrepancies run={run} artifact={artifact} chips={artifact.chips} onOpen={openSource} editable={!run.active} />
        </article>
      ) : artifact && actions ? (
        <Report artifact={artifact} actions={actions} />
      ) : (
        <RunPlaceholder run={run} artifactError={artifactError} onRetryRun={() => void retryRun()} onReload={() => void loadArtifact()} />
      )}
    </div>
  );

  return (
    <div className="workspace">
      <LeftPanel
        viewport={viewport}
        label="Run panel"
        onReveal={(s) => (s === "uploads" ? setUploadOverride(true) : setChatOpen(true))}
      >
        <div className="panel-scroll">
          <Accordion
            title="Upload"
            count={run?.documents.length ?? 0}
            open={uploadOpen}
            onToggle={setUploadOverride}
            summary={run ? uploadSummary(run) : undefined}
          >
            {run ? (
              <RunProgress run={run} busy={busy} pageCounts={pageCounts} onCancel={() => void cancelRun()} onRetry={() => void retryRun()} />
            ) : (
              <div className="skeleton-lines" aria-hidden>
                <span />
                <span />
              </div>
            )}
            {reconnecting && <p className="hint" role="status">Reconnecting to the run...</p>}
          </Accordion>
          <Accordion title="Input parameters">
            {isProposal && run ? (
              <ProposalParams parameters={run.parameters} />
            ) : isPolicy && run ? (
              <RoleParams run={run} />
            ) : (
              <p className="hint">Compare Quotes has no parameters. Column order follows the upload order.</p>
            )}
          </Accordion>
          <Accordion title="Recent runs" count={runs?.length}>
            {runs && <RecentRuns runs={runs} compact currentId={runId} />}
          </Accordion>
          <a
            href={isProposal ? "#/proposals/new" : `${isPolicy ? "#/policy-check/new" : "#/"}${personal ? "?line=personal" : ""}`}
            className="btn btn--ghost btn--block new-run"
          >
            <Plus size={16} aria-hidden /> {isProposal ? "New proposal" : isPolicy ? "New check" : "New comparison"}
          </a>
        </div>
        {run && (
          <Chat
            runId={run.id}
            artifact={artifact}
            proposal={isProposal}
            policy={isPolicy}
            open={chatOpen}
            onToggle={setChatOpen}
            onOpen={openSource}
            onEdited={(r) => afterEdit(r, "Edit applied")}
          />
        )}
      </LeftPanel>

      <main className="pane" id="main" tabIndex={-1} aria-labelledby="pane-title">
        <nav className="tabs" aria-label="Report and sources">
          <a href="#/activity" className="tab">
            <ClockCounterClockwise size={16} aria-hidden /> All runs
          </a>
          <button
            type="button"
            className={`tab${tab === "report" ? " is-active" : ""}`}
            aria-current={tab === "report" ? "true" : undefined}
            onClick={() => {
              setTab("report");
              setSplit(false);
            }}
          >
            {running ? <ReadingMark size={16} /> : <FileText size={16} aria-hidden />}
            Report
            {running && <span className="sr-only"> (generating)</span>}
          </button>
          {pdfTabs.map(({ item: p, leaving, entering }) => (
            <span
              key={p.documentId}
              className={`tab tab--pdf hue-${hueOf(p.letter)}${tab === p.documentId && !leaving ? " is-active" : ""}${leaving ? " tab--leaving" : entering ? " tab--entering" : ""}`}
              aria-hidden={leaving || undefined}
              inert={leaving || undefined}
            >
              <button
                type="button"
                className="tab__btn"
                aria-current={tab === p.documentId ? "true" : undefined}
                onClick={() => {
                  setTab(p.documentId);
                  setSplit(wide);
                  setSplitAnim(false);
                }}
              >
                <FilePdf size={16} aria-hidden />
                <span className="tab__label">
                  {p.letter}. {p.filename}
                </span>
              </button>
              <button type="button" className="icon-btn icon-btn--sm" aria-label={`Close ${p.filename}`} onClick={() => closePdf(p.documentId)}>
                <X size={14} aria-hidden />
              </button>
            </span>
          ))}
        </nav>

        <div className="toolbar">
          <h1 id="pane-title" className="toolbar__title">
            {run && <WorkflowMark slug={isPolicy ? "policy-check" : isProposal ? "proposal" : "compare-quotes"} size={30} />}
            {isProposal ? (artifact?.title ?? "Proposal") : isPolicy ? "Policy check" : "Compare Quotes"}, run {runId}
            {artifact && (
              <span className="toolbar__version">
                Version {artifact.version}
                {artifact.note?.startsWith("edit") || artifact.note?.startsWith("chat edit") ? ", edited" : ""}
              </span>
            )}
          </h1>
          {run && <LineChoice compact value={run.line} onChange={(l) => void retag(l)} />}
          {run && <RunClient key={run.id} value={run.client_name} onSave={(name) => void rename(name)} />}
          <div className="toolbar__actions" role="toolbar" aria-label="Report actions">
            <Tip text="Good report">
              <button type="button" className="icon-btn" aria-label="Good report" aria-pressed={artifact?.feedback?.rating === 1} disabled={!artifact} onClick={() => void rate(1)}>
                <ThumbsUp size={18} weight={artifact?.feedback?.rating === 1 ? "fill" : "regular"} aria-hidden />
              </button>
            </Tip>
            <Tip text="Bad report">
              <button type="button" className="icon-btn" aria-label="Bad report" aria-pressed={artifact?.feedback?.rating === -1} disabled={!artifact} onClick={() => void rate(-1)}>
                <ThumbsDown size={18} weight={artifact?.feedback?.rating === -1 ? "fill" : "regular"} aria-hidden />
              </button>
            </Tip>
            <span className="toolbar__sep" aria-hidden />
            <Tip text="Copy report text">
              <button type="button" className="icon-btn" aria-label="Copy report text" disabled={!artifact} onClick={() => void copyText()}>
                <Copy size={18} aria-hidden />
              </button>
            </Tip>
            <Tip text="Export PDF: full report">
              <button type="button" className="icon-btn" aria-label="Export PDF, full report" disabled={!artifact || exporting != null} onClick={() => void exportAs("pdf")}>
                {exporting === "pdf" ? <span className="spinner spinner--sm" aria-hidden /> : <FilePdf size={18} aria-hidden />}
              </button>
            </Tip>
            <Tip text="Export DOCX: Word document">
              <button type="button" className="icon-btn" aria-label="Export DOCX, Word document" disabled={!artifact || exporting != null} onClick={() => void exportAs("docx")}>
                {exporting === "docx" ? <span className="spinner spinner--sm" aria-hidden /> : <FileDoc size={18} aria-hidden />}
              </button>
            </Tip>
            <Tip text="Export XLSX: tables only">
              <button type="button" className="icon-btn" aria-label="Export XLSX, tables only" disabled={!artifact || exporting != null} onClick={() => void exportAs("xlsx")}>
                {exporting === "xlsx" ? <span className="spinner spinner--sm" aria-hidden /> : <FileXls size={18} aria-hidden />}
              </button>
            </Tip>
            <span className="toolbar__sep" aria-hidden />
            {isProposal && (
              <Tip text={preview ? "Show citations" : "Preview without citations"}>
                <button
                  type="button"
                  className="icon-btn"
                  aria-label="Preview without citations"
                  aria-pressed={preview}
                  disabled={!artifact}
                  onClick={() => {
                    setPreview((p) => !p);
                    setAnnounce(preview ? "Citations shown." : "Preview: citations hidden.");
                  }}
                >
                  <Eye size={18} aria-hidden />
                </button>
              </Tip>
            )}
            <Tip text={wide ? "Side by side" : "Side by side needs a wider window"}>
              <button
                type="button"
                className="icon-btn"
                aria-label="Side by side"
                aria-pressed={split}
                disabled={!wide || pdfs.length === 0}
                onClick={() => {
                  if (split) exitSplit();
                  else {
                    setTab(pdfs[pdfs.length - 1]!.documentId);
                    setSplit(true);
                    setSplitAnim(true);
                  }
                }}
              >
                <SquareSplitHorizontal size={18} aria-hidden />
              </button>
            </Tip>
            {!isPolicy && <Tip text={outlineOpen ? "Hide outline" : "Show outline"}>
              <button
                type="button"
                className="icon-btn"
                aria-label={outlineOpen ? "Hide outline" : "Show outline"}
                aria-expanded={outlineOpen}
                disabled={!artifact}
                onClick={() => toggleOutline(!outlineOpen)}
              >
                <ListBullets size={18} aria-hidden />
              </button>
            </Tip>}
          </div>
        </div>

        <div className={`pane__body${split && activePdf ? " is-split" : ""}${splitAnim ? " split-anim" : ""}`}>
          {showReport && (
            <div className="report-area" data-outline-motion={outlineMotion ?? undefined}>
              {reportBody}
              {/* While leaving (pointer hide) the outline stays for --dur-fast to slide out; the handle shows at once. */}
              {artifact && !split && !isPolicy && (outlineOpen || outlineMotion === "leave") && (
                <Outline artifact={artifact} hideTrust={isProposal && preview} filter={filter} onFilter={setFilter} scrollRoot={scrollRoot} focusFirstCount={focusCount} />
              )}
              {artifact && !split && !isPolicy && !outlineOpen && (
                <Tip text={summary ? `Outline. ${summary}` : "Outline"} side="bottom">
                  <button
                    type="button"
                    className="outline-handle"
                    aria-label={summary ? `Outline. ${summary}` : "Show outline"}
                    onClick={() => {
                      toggleOutline(true);
                      setFocusCount((n) => n + 1);
                    }}
                  >
                    <ListBullets size={18} aria-hidden />
                    {summary && <Warning size={16} className="outline-handle__mark" aria-hidden />}
                  </button>
                </Tip>
              )}
            </div>
          )}
          {shownPdf && (
            <section
              className={`source-pane${paneLeaving ? " source-pane--leaving" : ""}`}
              aria-label={`Source: ${shownPdf.filename}`}
              aria-hidden={paneLeaving || undefined}
              inert={paneLeaving || undefined}
            >
              <div className="source-pane__head">
                <span className={`doc-letter hue-${hueOf(shownPdf.letter)}`} aria-hidden>
                  {shownPdf.letter}
                </span>
                <span className="source-pane__name">{shownPdf.filename}</span>
                <button type="button" className="btn btn--ghost btn--sm" onClick={exitSplit}>
                  {split || paneLeaving ? "Exit" : "Back to report"} <kbd>Esc</kbd>
                </button>
              </div>
              <Suspense fallback={<div className="pdf__frame"><div className="pdf__skeleton" aria-hidden /></div>}><PdfViewer target={shownPdf} /></Suspense>
            </section>
          )}
        </div>
        <p className="sr-only" role="status" aria-live="polite">
          {announce}
        </p>
      </main>
    </div>
  );
}

/** One line for the folded Upload section: "3 documents, all read", "1 of 3 failed"... */
function uploadSummary(run: Run): string {
  const n = run.documents.length;
  const noun = `${n} ${n === 1 ? "document" : "documents"}`;
  const failed = run.documents.filter((d) => d.status === "failed").length;
  const read = run.documents.filter((d) => d.status === "done").length;
  if (run.active) return `${read + failed} of ${noun} read`;
  if (failed) return `${noun}, ${failed} failed`;
  if (read === n) return `${noun}, all read`;
  return `${noun}, ${read} read`;
}

/** Input parameters of a Policy Check run: which document fills each role (RunDocument.slot). */
function RoleParams({ run }: { run: Run }) {
  return (
    <dl className="params">
      {ROLE_INFO.map((r) => {
        const d = run.documents.find((x) => x.slot === r.role);
        return (
          <div key={r.role} className="params__row">
            <dt>{r.name}</dt>
            <dd>
              {d ? (
                <>
                  <span className={`doc-letter hue-${hueOf(d.letter)} role-params__letter`} aria-hidden>
                    {d.letter}
                  </span>
                  {d.filename}
                </>
              ) : (
                "Not used"
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** Input parameters of a proposal run (the stored, validated `run.parameters`). */
function ProposalParams({ parameters }: { parameters: Record<string, unknown> }) {
  const p = parameters as {
    client_name?: string;
    broker_name?: string;
    brokerage_name?: string;
    no_ai_prose?: boolean;
    outline?: { kind: string }[];
    template?: { name?: string };
  };
  const sections = (p.outline ?? []).filter((b) => b.kind !== "cover_page" && b.kind !== "table_of_contents").length;
  const rows: [string, string][] = [
    ["Template", p.template?.name ?? "Unknown"],
    ["Client", p.client_name || "Not set"],
    ["Broker", [p.broker_name, p.brokerage_name].filter(Boolean).join(", ") || "Not set"],
    ["Sections", String(sections)],
    ["Writing", p.no_ai_prose ? "Plain text, no AI writing" : "AI prose, checked against the values"],
  ];
  return (
    <dl className="params">
      {rows.map(([k, v]) => (
        <div key={k} className="params__row">
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function RunPlaceholder({
  run,
  artifactError,
  onRetryRun,
  onReload,
}: {
  run: Run | null;
  artifactError: string | null;
  onRetryRun: () => void;
  onReload: () => void;
}) {
  if (artifactError) {
    return (
      <div className="state-block" role="alert">
        <p>{artifactError}</p>
        <button type="button" className="btn btn--secondary" onClick={onReload}>
          Retry
        </button>
      </div>
    );
  }
  if (!run || run.status === "queued" || run.status === "running") {
    return (
      <div className="report-skeleton" aria-busy="true">
        <p className="report-skeleton__text">
          {run && <ReadingMark size={20} className="report-skeleton__mark" />}
          {run ? `Reading ${run.documents.length} ${run.documents.length === 1 ? "document" : "documents"}. Progress is in the Upload panel.` : "Loading run"}
        </p>
        <div className="skeleton-lines skeleton-lines--report" aria-hidden>
          <span />
          <span />
          <span />
          <span />
        </div>
        <div className="skeleton-table" aria-hidden />
      </div>
    );
  }
  if (run.status === "composing") {
    return (
      <div className="state-block state-block--center" role="status">
        <ReadingMark size={40} />
        <p>Writing report...</p>
      </div>
    );
  }
  if (run.status === "cancelled") {
    return (
      <div className="state-block">
        <p className="state-block__title">This run was cancelled before a report was written.</p>
        <p className="hint">Retry reads the documents that did not finish.</p>
        {(
          <button type="button" className="btn btn--primary" onClick={onRetryRun}>
            Retry run
          </button>
        )}
      </div>
    );
  }
  return (
    <div className="state-block" role="alert">
      <p className="state-block__title">No report was written.</p>
      <p>{run.error ?? "Every document failed. The reasons are listed in the Upload panel."}</p>
      {run.retryable && (
        <button type="button" className="btn btn--primary" onClick={onRetryRun}>
          Retry run
        </button>
      )}
    </div>
  );
}
