import type { ReactNode } from "react";
import {
  Archive,
  Briefcase,
  ChartLine,
  ChatCircleText,
  FileText,
  Files,
  Heartbeat,
  Lightbulb,
  ListChecks,
  MagnifyingGlass,
  PlugsConnected,
  ShieldCheck,
  Stamp,
} from "@phosphor-icons/react";
import { APP_NAME, WORKFLOW_NAME } from "../config";
import { Tip } from "../ui/Tip";

interface RailItem {
  label: string;
  icon: ReactNode;
  active?: boolean;
}

// DG1: the full rail from the reference; only Workflows is live in slice 1.
const GROUPS: RailItem[][] = [
  [
    { label: "Workflows", icon: <Briefcase size={20} aria-hidden />, active: true },
    { label: "Proposals", icon: <FileText size={20} aria-hidden /> },
    { label: "Personal lines", icon: <Heartbeat size={20} aria-hidden /> },
    { label: "Policy check", icon: <ShieldCheck size={20} aria-hidden /> },
    { label: "Certificates", icon: <Stamp size={20} aria-hidden /> },
    { label: "Tasks", icon: <ListChecks size={20} aria-hidden /> },
    { label: "Integrations", icon: <PlugsConnected size={20} aria-hidden /> },
  ],
  [
    { label: "Assistant", icon: <ChatCircleText size={20} aria-hidden /> },
    { label: "Market search", icon: <MagnifyingGlass size={20} aria-hidden /> },
    { label: "Analytics", icon: <ChartLine size={20} aria-hidden /> },
    { label: "Insights", icon: <Lightbulb size={20} aria-hidden /> },
  ],
  [{ label: "Vault", icon: <Archive size={20} aria-hidden /> }],
];

export function Rail() {
  return (
    <nav className="rail" aria-label="Main">
      <a href="#/" className="rail__home" aria-label={`${APP_NAME} home`}>
        <span className="glyph" aria-hidden />
      </a>
      {GROUPS.map((group, gi) => (
        <ul key={gi} className="rail__group">
          {group.map((item) => (
            <li key={item.label}>
              {item.active ? (
                <Tip text={item.label} side="right">
                  <a href="#/" className="rail__item is-active" aria-current="page" aria-label={item.label}>
                    {item.icon}
                  </a>
                </Tip>
              ) : (
                <Tip text={`${item.label}: coming in a later slice`} side="right">
                  <span
                    className="rail__item is-disabled"
                    role="button"
                    aria-disabled="true"
                    tabIndex={0}
                    aria-label={`${item.label}, coming in a later slice`}
                  >
                    {item.icon}
                  </span>
                </Tip>
              )}
            </li>
          ))}
        </ul>
      ))}
      <div className="rail__bottom">
        <Tip text="Documents: coming in a later slice" side="right">
          <span className="rail__item is-disabled" role="button" aria-disabled="true" tabIndex={0} aria-label="Documents, coming in a later slice">
            <Files size={20} aria-hidden />
          </span>
        </Tip>
      </div>
    </nav>
  );
}

export function TopBar({ crumb }: { crumb: string | null }) {
  return (
    <header className="topbar">
      <a href="#/" className="wordmark">
        <span className="glyph" aria-hidden />
        {APP_NAME}
      </a>
      <nav aria-label="Breadcrumb" className="crumbs">
        <ol>
          <li>
            <a href="#/">Workflows</a>
          </li>
          <li>
            {crumb ? <a href="#/">{WORKFLOW_NAME}</a> : <span aria-current="page">{WORKFLOW_NAME}</span>}
          </li>
          {crumb && (
            <li>
              <span aria-current="page">{crumb}</span>
            </li>
          )}
        </ol>
      </nav>
      <span className="topbar__spacer" />
      <span className="topbar__workspace">Local workspace</span>
      <span className="avatar" aria-hidden>
        LW
      </span>
    </header>
  );
}

export function NarrowNotice() {
  return (
    <p className="narrow-notice" role="note">
      Best viewed on a wider screen. The report stays readable here; tables scroll sideways.
    </p>
  );
}
