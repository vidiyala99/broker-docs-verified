import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api/client";
import type { Line, Run, Workspace } from "./api/types";
import { LaunchView } from "./launch/LaunchView";
import { RunView } from "./run/RunView";
import { NarrowNotice, Rail, TopBar, type Crumb, type Section } from "./shell/Shell";
import { IntegrationsPage } from "./integrations/IntegrationsPage";
import { POLICY_WORKFLOW, PROPOSAL_WORKFLOW, WORKFLOW_NAME } from "./config";
import { useViewport } from "./shell/layout";
import { ToastProvider } from "./ui/Toast";
import { ProposalWizard } from "./wizard/ProposalWizard";
import { PolicyWizard } from "./policy/PolicyWizard";
import { PERSONAL_LIMIT, PersonalLinesPage } from "./personal/PersonalLinesPage";
import { lineFromHash } from "./personal/LineChoice";

// `line` is the entry preset from the hash query (`#/?line=personal`, `#/policy-check/new?line=personal`, slice 3b T6).
type Route = { runId: number | null; wizard: boolean; policy: boolean; integrations: boolean; personal: boolean; line: Line | null };

function parseRoute(hash: string): Route {
  const m = /^#\/runs\/(\d+)/.exec(hash);
  return {
    runId: m ? Number(m[1]) : null,
    wizard: /^#\/proposals\/new/.test(hash),
    policy: /^#\/policy-check\/new/.test(hash),
    integrations: /^#\/integrations\b/.test(hash),
    personal: /^#\/personal-lines\b/.test(hash),
    line: lineFromHash(hash),
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

  useEffect(() => {
    loadWorkspace();
  }, [loadWorkspace]);

  // The routed run drives the rail, the crumbs and which line's runs the side list shows. Always read it directly:
  // the recent-runs list is filtered by line (D9), so the run may not be in it, and its line can change (re-tag).
  const [routeRun, setRouteRun] = useState<Run | null>(null);
  useEffect(() => {
    if (route.runId == null) return;
    let live = true;
    api.run(route.runId).then((r) => live && setRouteRun(r), () => undefined);
    return () => {
      live = false;
    };
  }, [route.runId]);
  const runOfRoute = routeRun?.id === route.runId ? routeRun : null;
  const proposalRun = runOfRoute?.workflow_name === PROPOSAL_WORKFLOW;
  const policyRun = runOfRoute?.workflow_name === POLICY_WORKFLOW;
  const personalRun = runOfRoute?.line === "personal";
  // An entry screen opened from Personal lines (`?line=personal`) belongs to Personal lines.
  const personalEntry = route.line === "personal" && route.runId == null && !route.wizard && !route.personal && !route.integrations;

  // Which runs the shell lists: Personal lines = personal, newest 100 (D5); a run view = the run's own line (unknown
  // until the run loads); everywhere else = the entry line, commercial by default (Workflows = commercial, D9).
  const listLine: Line | null = route.personal ? "personal" : route.runId != null ? (runOfRoute?.line ?? null) : (route.line ?? "commercial");
  const listLimit = route.personal ? PERSONAL_LIMIT : 20;
  const listSeq = useRef(0);
  const loadRuns = useCallback(() => {
    if (listLine == null) return;
    const seq = ++listSeq.current;
    setRunsError(null);
    api.runs(listLimit, listLine).then(
      (r) => {
        if (seq === listSeq.current) setRuns(r);
      },
      (e: unknown) => {
        if (seq === listSeq.current) setRunsError(e instanceof ApiError ? e.message : "Couldn't load recent runs.");
      },
    );
  }, [listLine, listLimit]);

  // A different list (line or size) must not show the previous one while it loads.
  useEffect(() => {
    setRuns(null);
  }, [listLine, listLimit]);

  useEffect(() => {
    loadRuns();
  }, [route.runId, route.wizard, route.policy, route.personal, loadRuns]);

  useEffect(() => {
    document.title = route.integrations
      ? "Integrations"
      : route.personal
        ? "Personal lines"
        : route.wizard
          ? "New proposal"
          : route.policy
            ? "New policy check"
            : route.runId
              ? `Run ${route.runId}, ${proposalRun ? "Proposal" : policyRun ? "Policy check" : "Compare Quotes"}`
              : "Compare Quotes";
  }, [route.runId, route.wizard, route.policy, route.integrations, route.personal, proposalRun, policyRun]);

  // Rail: Personal lines for its page and its entry screens; a run view highlights its workflow whatever its line
  // (the rail names the tool, the crumb names the book; PERSONAL-LINES-BRIEF.md).
  const section: Section = route.integrations
    ? "integrations"
    : route.personal || personalEntry
      ? "personal-lines"
      : route.wizard || proposalRun
        ? "proposals"
        : route.policy || policyRun
          ? "policy-check"
          : "workflows";
  // A personal run's crumbs start with Personal lines; their links keep the personal preset.
  const toPersonal: Crumb = { label: "Personal lines", href: "#/personal-lines" };
  const preset = personalRun ? "?line=personal" : "";
  const runCrumbs: Crumb[] = proposalRun
    ? [{ label: "Proposals", href: "#/proposals/new" }, { label: `Run ${route.runId}` }]
    : policyRun
      ? [{ label: "Policy check", href: `#/policy-check/new${preset}` }, { label: `Run ${route.runId}` }]
      : personalRun
        ? [{ label: WORKFLOW_NAME, href: `#/${preset}` }, { label: `Run ${route.runId}` }]
        : [{ label: "Workflows", href: "#/" }, { label: WORKFLOW_NAME, href: "#/" }, { label: `Run ${route.runId}` }];
  const crumbs: Crumb[] = route.integrations
    ? [{ label: "Integrations" }]
    : route.personal
      ? [{ label: "Personal lines" }]
      : route.wizard
        ? [{ label: "Proposals" }, { label: "New proposal" }]
        : route.policy
          ? personalEntry
            ? [toPersonal, { label: "Policy check" }, { label: "New check" }]
            : [{ label: "Policy check" }, { label: "New check" }]
          : route.runId
            ? personalRun
              ? [toPersonal, ...runCrumbs]
              : runCrumbs
            : personalEntry
              ? [toPersonal, { label: WORKFLOW_NAME }]
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
          {viewport === "narrow" && !route.wizard && !route.policy && !route.integrations && !route.personal && !policyRun && <NarrowNotice />}
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
          ) : route.personal ? (
            <PersonalLinesPage runs={runs} runsError={runsError} onRetry={loadRuns} />
          ) : route.policy ? (
            <PolicyWizard workspace={workspace} viewport={viewport} line={route.line ?? "commercial"} />
          ) : route.runId ? (
            <RunView
              key={route.runId}
              runId={route.runId}
              workspace={workspace}
              runs={runs}
              viewport={viewport}
              onRunsChanged={loadRuns}
              onRunChanged={setRouteRun}
            />
          ) : (
            <LaunchView
              workspace={workspace}
              runs={runs}
              runsError={runsError}
              onRetryRuns={loadRuns}
              viewport={viewport}
              line={route.line ?? "commercial"}
            />
          )}
        </div>
      </div>
    </ToastProvider>
  );
}
