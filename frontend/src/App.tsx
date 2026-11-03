import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { api, ApiError } from "./api/client";
import { pointerAction } from "./ui/input";
import { reducedMotion } from "./ui/motion";
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
import { PersonalLinesPage } from "./personal/PersonalLinesPage";
import { ActivityPage } from "./activity/ActivityPage";
import { lineFromHash } from "./personal/LineChoice";
import { lineMoveVariant } from "./run/lineMove";
import { TasksPage } from "./tasks/TasksPage";
import { taskIdFromHash } from "./tasks/taskUtil";
import { ReadingMark } from "./voice/marks";

// `line` is the entry preset from the hash query (`#/?line=personal`, `#/policy-check/new?line=personal`, slice 3b T6).
// `taskId` is the Tasks deep link (`#/tasks?task=<id>`, slice 3b T7, O7).
type Route = {
  runId: number | null;
  wizard: boolean;
  policy: boolean;
  integrations: boolean;
  personal: boolean;
  tasks: boolean;
  /** Work queue W2: `#/activity` (`?line=` presets its filter). */
  activity: boolean;
  taskId: number | null;
  line: Line | null;
};

function parseRoute(hash: string): Route {
  const m = /^#\/runs\/(\d+)/.exec(hash);
  return {
    runId: m ? Number(m[1]) : null,
    wizard: /^#\/proposals\/new/.test(hash),
    policy: /^#\/policy-check\/new/.test(hash),
    integrations: /^#\/integrations\b/.test(hash),
    personal: /^#\/personal-lines\b/.test(hash),
    tasks: /^#\/tasks\b/.test(hash),
    activity: /^#\/activity\b/.test(hash),
    taskId: /^#\/tasks\b/.test(hash) ? taskIdFromHash(hash) : null,
    line: lineFromHash(hash),
  };
}

export function App() {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [wsError, setWsError] = useState<string | null>(null);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [, setRunsError] = useState<string | null>(null);
  const viewport = useViewport();

  // Route change (motion audit): a pointer navigation to another page cross-fades the main pane only (View
  // Transitions, `view-transition-name` on #main in app.css, 150ms opacity); the rail and panels do not animate.
  // Keyboard navigation, reduced motion, in-page hash changes (filters, deep-link params) and browsers without the API
  // swap instantly. The state update still runs synchronously inside the transition callback (flushSync).
  useEffect(() => {
    const pageOf = (r: Route) => [r.runId, r.wizard, r.policy, r.integrations, r.personal, r.tasks, r.activity].join("|");
    let current = pageOf(parseRoute(window.location.hash));
    const on = () => {
      const next = parseRoute(window.location.hash);
      const page = pageOf(next);
      const crossFade = page !== current && pointerAction() && !reducedMotion() && typeof document.startViewTransition === "function";
      current = page;
      if (crossFade) document.startViewTransition(() => flushSync(() => setRoute(next)));
      else setRoute(next);
    };
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
  // Queue navigation: a run with a client (typed, or read from the documents as the queue shows it) is named by the
  // client in the crumbs and the tab title; proposals keep "Run N" (their title already names the client).
  const runClient = !proposalRun ? (runOfRoute?.client ?? runOfRoute?.client_name ?? null) : null;
  const lmVariant = lineMoveVariant();
  // An entry screen opened from Personal lines (`?line=personal`) belongs to Personal lines.
  const personalEntry =
    route.line === "personal" && route.runId == null && !route.wizard && !route.personal && !route.integrations && !route.tasks && !route.activity;

  // Which runs the shell lists: only the run view's "Recent work" section reads this list now (the run's own line,
  // unknown until the run loads). Personal lines and the Compare screen show the work queue; Activity loads its own.
  const listLine: Line | null = route.runId != null ? (runOfRoute?.line ?? null) : null;
  const listLimit = 20;
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
        if (seq === listSeq.current) setRunsError(e instanceof ApiError ? e.message : "Couldn't load recent work.");
      },
    );
  }, [listLine, listLimit]);

  // A different list (line or size) must not show the previous one while it loads.
  useEffect(() => {
    setRuns(null);
  }, [listLine, listLimit]);

  useEffect(() => {
    loadRuns();
  }, [route.runId, loadRuns]);

  useEffect(() => {
    document.title = route.activity
      ? "Activity"
      : route.tasks
      ? "Tasks"
      : route.integrations
      ? "Integrations"
      : route.personal
        ? "Personal lines"
        : route.wizard
          ? "New proposal"
          : route.policy
            ? "New policy check"
            : route.runId
              ? `${runClient ?? `Run ${route.runId}`}, ${proposalRun ? "Proposal" : policyRun ? "Policy check" : "Compare Quotes"}`
              : "Compare Quotes";
  }, [route.runId, route.wizard, route.policy, route.integrations, route.personal, route.tasks, route.activity, proposalRun, policyRun, runClient]);

  // Rail: Personal lines for its page and its entry screens; a run view highlights its workflow whatever its line
  // (the rail names the tool, the crumb names the book; PERSONAL-LINES-BRIEF.md).
  // Activity has no rail item: it highlights the book it was opened from (Personal lines preset, else Workflows).
  const section: Section = route.activity
    ? route.line === "personal"
      ? "personal-lines"
      : "workflows"
    : route.tasks
    ? "tasks"
    : route.integrations
    ? "integrations"
    : route.personal || personalEntry
      ? "personal-lines"
      : lmVariant === "c" && runOfRoute && !proposalRun
        ? personalRun
          ? "personal-lines"
          : "workflows"
      : route.wizard || proposalRun
        ? "proposals"
        : route.policy || policyRun
          ? "policy-check"
          : "workflows";
  // A personal run's crumbs start with Personal lines; their links keep the personal preset.
  const toPersonal: Crumb = { label: "Personal lines", href: "#/personal-lines" };
  const preset = personalRun ? "?line=personal" : "";
  const runLabel = runClient ?? `Run ${route.runId}`;
  // PROTOTYPE (line-move A/B/C): every run's crumbs lead with its book, Commercial included.
  const bookCrumb: Crumb = personalRun
    ? { ...toPersonal, refile: pointerAction(600) }
    : { label: "Commercial", href: "#/", refile: pointerAction(600) };
  const lmRunCrumbs: Crumb[] | null =
    lmVariant !== "0" && runOfRoute && !proposalRun
      ? [bookCrumb, { label: policyRun ? "Policy check" : WORKFLOW_NAME, href: policyRun ? `#/policy-check/new${preset}` : `#/${preset}` }, { label: runLabel }]
      : null;
  const runCrumbs: Crumb[] = proposalRun
    ? [{ label: "Proposals", href: "#/proposals/new" }, { label: `Run ${route.runId}` }]
    : policyRun
      ? [{ label: "Policy check", href: `#/policy-check/new${preset}` }, { label: runLabel }]
      : personalRun
        ? [{ label: WORKFLOW_NAME, href: `#/${preset}` }, { label: runLabel }]
        : [{ label: "Workflows", href: "#/" }, { label: WORKFLOW_NAME, href: "#/" }, { label: runLabel }];
  const crumbs: Crumb[] = route.activity
    ? [{ label: "Activity" }]
    : route.tasks
    ? [{ label: "Tasks" }]
    : route.integrations
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
            ? lmRunCrumbs
              ? lmRunCrumbs
              : personalRun
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
          {viewport === "narrow" && !route.wizard && !route.policy && !route.integrations && !route.personal && !route.tasks && !route.activity && !policyRun && <NarrowNotice />}
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
                <ReadingMark size={40} />
                <span className="sr-only" role="status">Loading</span>
              </div>
            </main>
          ) : route.wizard ? (
            <ProposalWizard workspace={workspace} viewport={viewport} />
          ) : route.tasks ? (
            <TasksPage targetId={route.taskId} />
          ) : route.activity ? (
            <ActivityPage preset={route.line} />
          ) : route.personal ? (
            <PersonalLinesPage />
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
              key={route.line ?? "commercial"}
              workspace={workspace}
              viewport={viewport}
              line={route.line ?? "commercial"}
            />
          )}
        </div>
      </div>
    </ToastProvider>
  );
}
