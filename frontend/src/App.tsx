import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api/client";
import type { Run, Workspace } from "./api/types";
import { LaunchView } from "./launch/LaunchView";
import { RunView } from "./run/RunView";
import { NarrowNotice, Rail, TopBar } from "./shell/Shell";
import { useViewport } from "./shell/layout";
import { ToastProvider } from "./ui/Toast";

function parseRoute(hash: string): { runId: number | null } {
  const m = /^#\/runs\/(\d+)/.exec(hash);
  return { runId: m ? Number(m[1]) : null };
}

export function App() {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [wsError, setWsError] = useState<string | null>(null);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const viewport = useViewport();

  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const loadWorkspace = useCallback(() => {
    setWsError(null);
    api.workspace().then(setWorkspace, (e: unknown) => setWsError(e instanceof ApiError ? e.message : "Can't reach the local server."));
  }, []);

  const loadRuns = useCallback(() => {
    setRunsError(null);
    api.runs(20).then(setRuns, (e: unknown) => setRunsError(e instanceof ApiError ? e.message : "Couldn't load recent runs."));
  }, []);

  useEffect(() => {
    loadWorkspace();
    loadRuns();
  }, [loadWorkspace, loadRuns]);

  useEffect(() => {
    loadRuns();
    document.title = route.runId ? `Run ${route.runId}, Compare Quotes` : "Compare Quotes";
  }, [route.runId, loadRuns]);

  return (
    <ToastProvider>
      <a className="skip-link" href="#main" onClick={(e) => {
        e.preventDefault();
        document.getElementById("main")?.focus();
      }}>
        Skip to content
      </a>
      <div className={`app vp-${viewport}`}>
        <Rail />
        <div className="app__main">
          <TopBar crumb={route.runId ? `Run ${route.runId}` : null} />
          {viewport === "narrow" && <NarrowNotice />}
          {wsError ? (
            <main className="pane" id="main" tabIndex={-1}>
              <div className="pane__body pane__body--center">
                <div className="state-block" role="alert">
                  <p className="state-block__title">Can't reach the local server.</p>
                  <p>{wsError}</p>
                  <button type="button" className="btn btn--primary" onClick={() => { loadWorkspace(); loadRuns(); }}>
                    Retry
                  </button>
                </div>
              </div>
            </main>
          ) : !workspace ? (
            <main className="pane" id="main" tabIndex={-1} aria-busy="true">
              <div className="pane__body pane__body--center">
                <span className="spinner spinner--lg" aria-hidden />
                <span className="sr-only" role="status">Loading</span>
              </div>
            </main>
          ) : route.runId ? (
            <RunView key={route.runId} runId={route.runId} workspace={workspace} runs={runs} viewport={viewport} onRunsChanged={loadRuns} />
          ) : (
            <LaunchView workspace={workspace} runs={runs} runsError={runsError} onRetryRuns={loadRuns} viewport={viewport} />
          )}
        </div>
      </div>
    </ToastProvider>
  );
}
