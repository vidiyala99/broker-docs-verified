import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api/client";
import type { Run, Workspace } from "./api/types";
import { LaunchView } from "./launch/LaunchView";
import { RunView } from "./run/RunView";
import { NarrowNotice, Rail, TopBar, type Crumb, type Section } from "./shell/Shell";
import { IntegrationsPage } from "./integrations/IntegrationsPage";
import { POLICY_WORKFLOW, PROPOSAL_WORKFLOW, WORKFLOW_NAME } from "./config";
import { useViewport } from "./shell/layout";
import { ToastProvider } from "./ui/Toast";
import { ProposalWizard } from "./wizard/ProposalWizard";
import { PolicyWizard } from "./policy/PolicyWizard";

type Route = { runId: number | null; wizard: boolean; policy: boolean; integrations: boolean };

function parseRoute(hash: string): Route {
  const m = /^#\/runs\/(\d+)/.exec(hash);
  return {
    runId: m ? Number(m[1]) : null,
    wizard: /^#\/proposals\/new/.test(hash),
    policy: /^#\/policy-check\/new/.test(hash),
    integrations: /^#\/integrations\b/.test(hash),
  };
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

  // The routed run's workflow drives the rail and crumbs. A run just created by the wizard is not
  // in the recent-runs list yet, so fetch it directly instead of showing Compare Quotes crumbs.
  const [routeRun, setRouteRun] = useState<Run | null>(null);
  const listed = route.runId != null ? runs?.find((r) => r.id === route.runId) ?? null : null;
  useEffect(() => {
    if (route.runId == null || listed) return;
    let live = true;
    api.run(route.runId).then((r) => live && setRouteRun(r), () => undefined);
    return () => {
      live = false;
    };
  }, [route.runId, listed]);
  const runOfRoute = listed ?? (routeRun?.id === route.runId ? routeRun : null);
  const proposalRun = runOfRoute?.workflow_name === PROPOSAL_WORKFLOW;
  const policyRun = runOfRoute?.workflow_name === POLICY_WORKFLOW;

  useEffect(() => {
    loadRuns();
  }, [route.runId, route.wizard, route.policy, loadRuns]);

  useEffect(() => {
    document.title = route.integrations
      ? "Integrations"
      : route.wizard
      ? "New proposal"
      : route.policy
        ? "New policy check"
        : route.runId
          ? `Run ${route.runId}, ${proposalRun ? "Proposal" : policyRun ? "Policy check" : "Compare Quotes"}`
          : "Compare Quotes";
  }, [route.runId, route.wizard, route.policy, route.integrations, proposalRun, policyRun]);

  const section: Section = route.integrations
    ? "integrations"
    : route.wizard || proposalRun
      ? "proposals"
      : route.policy || policyRun
        ? "policy-check"
        : "workflows";
  const crumbs: Crumb[] = route.integrations
    ? [{ label: "Integrations" }]
    : route.wizard
    ? [{ label: "Proposals" }, { label: "New proposal" }]
    : route.policy
      ? [{ label: "Policy check" }, { label: "New check" }]
      : route.runId
        ? proposalRun
          ? [{ label: "Proposals", href: "#/proposals/new" }, { label: `Run ${route.runId}` }]
          : policyRun
            ? [{ label: "Policy check", href: "#/policy-check/new" }, { label: `Run ${route.runId}` }]
            : [{ label: "Workflows", href: "#/" }, { label: WORKFLOW_NAME, href: "#/" }, { label: `Run ${route.runId}` }]
        : [{ label: "Workflows", href: "#/" }, { label: WORKFLOW_NAME }];

  return (
    <ToastProvider>
      <a className="skip-link" href="#main" onClick={(e) => {
        e.preventDefault();
        document.getElementById("main")?.focus();
      }}>
        Skip to content
      </a>
      <div className={`app vp-${viewport}`}>
        <Rail section={section} />
        <div className="app__main">
          <TopBar crumbs={crumbs} />
          {viewport === "narrow" && !route.wizard && !route.policy && !route.integrations && !policyRun && <NarrowNotice />}
          {route.integrations ? (
            // Static mock: needs nothing from the server, so it renders even when the backend is down.
            <IntegrationsPage />
          ) : wsError ? (
            <main className="pane" id="main" tabIndex={-1}>
              <div className="pane__body pane__body--center">
                <div className="state-block" role="alert">
                  <p className="state-block__title">Can't reach the local server.</p>
                  <p>{wsError.startsWith("Can't reach") ? "Start the backend (see frontend/README.md), then retry." : wsError}</p>
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
          ) : route.wizard ? (
            <ProposalWizard workspace={workspace} viewport={viewport} />
          ) : route.policy ? (
            <PolicyWizard workspace={workspace} viewport={viewport} />
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
