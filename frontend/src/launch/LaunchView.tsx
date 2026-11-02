import { useState } from "react";
import { ArrowRight, ClockCounterClockwise } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import type { Run, Workspace } from "../api/types";
import { Accordion } from "../ui/Accordion";
import { LeftPanel, type Viewport } from "../shell/layout";
import { Chat } from "../run/Chat";
import { DropZone, FileRows, useUploads } from "./Upload";
import { RecentRuns } from "./RecentRuns";

export function LaunchView({
  workspace,
  runs,
  runsError,
  onRetryRuns,
  viewport,
}: {
  workspace: Workspace;
  runs: Run[] | null;
  runsError: string | null;
  onRetryRuns: () => void;
  viewport: Viewport;
}) {
  const uploads = useUploads(workspace.limits);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const ready = uploads.rows.filter((r) => r.status === "ready" && r.doc);
  const busy = uploads.rows.some((r) => r.status === "uploading");

  const start = async () => {
    setStarting(true);
    setStartError(null);
    try {
      const res = await api.startRun(ready.map((r) => r.doc!.id));
      uploads.clear();
      window.location.hash = `#/runs/${res.run.id}`;
    } catch (e) {
      setStartError(e instanceof ApiError ? e.message : "Couldn't start the run. Retry.");
    } finally {
      setStarting(false);
    }
  };

  const hasRuns = runs != null && runs.length > 0;

  return (
    <div className="workspace">
      <LeftPanel viewport={viewport} label="Compare Quotes panel">
        <div className="panel-scroll">
          <Accordion title="Upload" count={ready.length} defaultOpen>
            <div className="upload">
              {hasRuns || runs == null ? (
                <DropZone limits={workspace.limits} onFiles={uploads.add} />
              ) : (
                uploads.rows.length === 0 && <p className="hint">Add quote PDFs with the drop zone on the right.</p>
              )}
              <FileRows rows={uploads.rows} onRemove={uploads.remove} />
              {startError && (
                <p className="inline-error" role="alert">
                  {startError}
                </p>
              )}
              <button
                type="button"
                className="btn btn--primary btn--block"
                disabled={!ready.length || busy || starting}
                onClick={() => void start()}
              >
                {starting ? "Starting..." : ready.length ? `Compare ${ready.length} ${ready.length === 1 ? "quote" : "quotes"}` : "Compare quotes"}
                {!starting && <ArrowRight size={16} aria-hidden />}
              </button>
              {!workspace.model_configured && (
                <p className="hint">No model key is set on the server, so runs will fail until one is configured.</p>
              )}
            </div>
          </Accordion>
          <Accordion title="Input parameters">
            <p className="hint">Compare Quotes has no parameters. Column order follows the upload order.</p>
          </Accordion>
        </div>
        <Chat runId={0} artifact={null} onOpen={() => undefined} onEdited={() => undefined} />
      </LeftPanel>

      <main className="pane" id="main" tabIndex={-1} aria-labelledby="pane-title">
        <div className="tabs" role="presentation">
          <span className="tab is-active">
            <ClockCounterClockwise size={16} aria-hidden /> All runs
          </span>
        </div>
        <div className="pane__body pane__body--launch">
          <h1 id="pane-title" className="page-title">
            Compare Quotes
          </h1>
          {runsError ? (
            <div className="notice notice--error" role="alert">
              <span className="notice__text">{runsError}</span>
              <button type="button" className="btn btn--secondary btn--sm" onClick={onRetryRuns}>
                Retry
              </button>
            </div>
          ) : runs == null ? (
            <div className="runs-skeleton" aria-hidden>
              <span />
              <span />
              <span />
            </div>
          ) : hasRuns ? (
            <>
              <h2 className="pane__subtitle">Recent runs</h2>
              <RecentRuns runs={runs} />
            </>
          ) : (
            <div className="launch-empty">
              <DropZone limits={workspace.limits} onFiles={uploads.add} large />
              {uploads.rows.length > 0 && <p className="hint">Your files are listed in the Upload panel. Start the comparison there.</p>}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
