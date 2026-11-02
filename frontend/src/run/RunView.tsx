import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Copy,
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
import { api, ApiError, downloadExport } from "../api/client";
import { useRunEvents } from "../api/useRunEvents";
import type { Artifact, Chip, EditResult, Run, RunDocument, Workspace } from "../api/types";
import { Accordion } from "../ui/Accordion";
import { useToast } from "../ui/Toast";
import { Tip } from "../ui/Tip";
import { LeftPanel, type Viewport } from "../shell/layout";
import { RecentRuns } from "../launch/RecentRuns";
import { Report, type ReportActions } from "../report/Report";
import { Outline, warningSummary, type TrustFilter } from "../report/Outline";
import type { CellRef } from "../report/Table";
import { hueOf } from "../report/Chip";
import type { PdfTarget } from "../pdf/PdfViewer";

const PdfViewer = lazy(() => import("../pdf/PdfViewer").then((m) => ({ default: m.PdfViewer })));
import { Chat } from "./Chat";
import { RunProgress } from "./RunProgress";

type Tab = "report" | number; // number = document id of an open PDF

export function RunView({
  runId,
  runs,
  viewport,
  onRunsChanged,
}: {
  runId: number;
  workspace: Workspace;
  runs: Run[] | null;
  viewport: Viewport;
  onRunsChanged: () => void;
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
  const [outlineOpen, setOutlineOpen] = useState(viewport !== "narrow");
  const [focusCount, setFocusCount] = useState(0);
  const [filter, setFilter] = useState<TrustFilter | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [exporting, setExporting] = useState<"xlsx" | "pdf" | null>(null);
  const [announce, setAnnounce] = useState("");
  const [reconnecting, setReconnecting] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [scrollRoot, setScrollRoot] = useState<HTMLDivElement | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const wide = viewport === "wide";

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
      if (t.closest("input, textarea, .left-panel--overlay")) return;
      if (split || tab !== "report") exitSplit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [split, tab, exitSplit]);

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

  const exportAs = async (format: "xlsx" | "pdf") => {
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
  const summary = artifact ? warningSummary(artifact) : null;
  const running = Boolean(run?.active);

  const reportBody = (
    <div className="report-scroll" ref={setScrollRoot} data-filter={filter ?? undefined}>
      {artifact && actions ? (
        <Report artifact={artifact} actions={actions} />
      ) : (
        <RunPlaceholder run={run} artifactError={artifactError} onRetryRun={() => void retryRun()} onReload={() => void loadArtifact()} />
      )}
    </div>
  );

  return (
    <div className="workspace">
      <LeftPanel viewport={viewport} label="Run panel">
        <div className="panel-scroll">
          <Accordion title="Upload" count={run?.documents.length ?? 0} defaultOpen>
            {run ? (
              <RunProgress run={run} busy={busy} onCancel={() => void cancelRun()} onRetry={() => void retryRun()} />
            ) : (
              <div className="skeleton-lines" aria-hidden>
                <span />
                <span />
              </div>
            )}
            {reconnecting && <p className="hint" role="status">Reconnecting to the run...</p>}
          </Accordion>
          <Accordion title="Input parameters">
            <p className="hint">Compare Quotes has no parameters. Column order follows the upload order.</p>
          </Accordion>
          <Accordion title="Recent runs" count={runs?.length}>
            {runs && <RecentRuns runs={runs} compact currentId={runId} />}
          </Accordion>
          <a href="#/" className="btn btn--ghost btn--block new-run">
            <Plus size={16} aria-hidden /> New comparison
          </a>
        </div>
        {run && <Chat runId={run.id} artifact={artifact} onOpen={openSource} onEdited={(r) => afterEdit(r, "Edit applied")} />}
      </LeftPanel>

      <main className="pane" id="main" tabIndex={-1} aria-labelledby="pane-title">
        <nav className="tabs" aria-label="Report and sources">
          <a href="#/" className="tab">
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
            {running ? <span className="spinner spinner--sm" aria-hidden /> : <FileText size={16} aria-hidden />}
            Report
            {running && <span className="sr-only"> (generating)</span>}
          </button>
          {pdfs.map((p) => (
            <span key={p.documentId} className={`tab tab--pdf hue-${hueOf(p.letter)}${tab === p.documentId ? " is-active" : ""}`}>
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
            Compare Quotes, run {runId}
            {artifact && (
              <span className="toolbar__version">
                Version {artifact.version}
                {artifact.note?.startsWith("edit") || artifact.note?.startsWith("chat edit") ? ", edited" : ""}
              </span>
            )}
          </h1>
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
            <Tip text="Export XLSX: tables only">
              <button type="button" className="icon-btn" aria-label="Export XLSX, tables only" disabled={!artifact || exporting != null} onClick={() => void exportAs("xlsx")}>
                {exporting === "xlsx" ? <span className="spinner spinner--sm" aria-hidden /> : <FileXls size={18} aria-hidden />}
              </button>
            </Tip>
            <span className="toolbar__sep" aria-hidden />
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
            <Tip text={outlineOpen ? "Hide outline" : "Show outline"}>
              <button
                type="button"
                className="icon-btn"
                aria-label={outlineOpen ? "Hide outline" : "Show outline"}
                aria-expanded={outlineOpen}
                disabled={!artifact}
                onClick={() => setOutlineOpen((o) => !o)}
              >
                <ListBullets size={18} aria-hidden />
              </button>
            </Tip>
          </div>
        </div>

        <div className={`pane__body${split && activePdf ? " is-split" : ""}${splitAnim ? " split-anim" : ""}`}>
          {showReport && (
            <div className="report-area">
              {reportBody}
              {artifact && !split && (outlineOpen ? (
                <Outline artifact={artifact} filter={filter} onFilter={setFilter} scrollRoot={scrollRoot} focusFirstCount={focusCount} />
              ) : (
                <Tip text={summary ? `Outline. ${summary}` : "Outline"} side="bottom">
                  <button
                    type="button"
                    className="outline-handle"
                    aria-label={summary ? `Outline. ${summary}` : "Show outline"}
                    onClick={() => {
                      setOutlineOpen(true);
                      setFocusCount((n) => n + 1);
                    }}
                  >
                    <ListBullets size={18} aria-hidden />
                    {summary && <Warning size={16} className="outline-handle__mark" aria-hidden />}
                  </button>
                </Tip>
              ))}
            </div>
          )}
          {activePdf && (split || tab === activePdf.documentId) && (
            <section className="source-pane" aria-label={`Source: ${activePdf.filename}`}>
              <div className="source-pane__head">
                <span className={`doc-letter hue-${hueOf(activePdf.letter)}`} aria-hidden>
                  {activePdf.letter}
                </span>
                <span className="source-pane__name">{activePdf.filename}</span>
                <button type="button" className="btn btn--ghost btn--sm" onClick={exitSplit}>
                  {split ? "Exit" : "Back to report"} <kbd>Esc</kbd>
                </button>
              </div>
              <Suspense fallback={<div className="pdf__frame"><div className="pdf__skeleton" aria-hidden /></div>}><PdfViewer target={activePdf} /></Suspense>
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
        <span className="spinner spinner--lg" aria-hidden />
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
