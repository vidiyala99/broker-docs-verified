import type { GlyphName } from "../voice/marks";
import { RailGlyph } from "../voice/railMarks";
import { APP_NAME } from "../config";
import { Tip } from "../ui/Tip";
import { useRailTravel } from "../run/lineMove";

export type Section = "workflows" | "proposals" | "personal-lines" | "policy-check" | "tasks" | "integrations";

interface RailItem {
  label: string;
  glyph: GlyphName;
  /** Live items link somewhere; the rest are "coming in a later slice". */
  href?: string;
  section?: Section;
  tip?: string;
}

// DG1: the full rail from the reference. Live: Workflows (slice 1), Proposals (slice 2, the wizard),
// Policy check (slice 3, the new-check wizard), Integrations (slice 3b, a static mock), Personal lines (slice 3b T6,
// runs tagged personal) and Tasks (slice 3b T7). `tip` is the tooltip and accessible name of a live item.
// Marks: `voice/railMarks.tsx` (one outline per item, RAIL-BRIEF.md).
const GROUPS: RailItem[][] = [
  [
    { label: "Workflows", glyph: "compare", href: "#/", section: "workflows" },
    { label: "Proposals", glyph: "proposal", href: "#/proposals/new", section: "proposals", tip: "Proposals: new proposal" },
    { label: "Personal lines", glyph: "personal", href: "#/personal-lines", section: "personal-lines" },
    { label: "Policy check", glyph: "policy-check", href: "#/policy-check/new", section: "policy-check", tip: "Policy check: new check" },
    { label: "Certificates", glyph: "certificate" },
    { label: "Tasks", glyph: "tasks", href: "#/tasks", section: "tasks" },
    { label: "Integrations", glyph: "integrations", href: "#/integrations", section: "integrations" },
  ],
  [
    { label: "Assistant", glyph: "assistant" },
    { label: "Market search", glyph: "search" },
    { label: "Analytics", glyph: "analytics" },
    { label: "Insights", glyph: "insights" },
  ],
  [{ label: "Vault", glyph: "vault" }],
];

export function Rail({ section }: { section: Section }) {
  useRailTravel(section);
  return (
    <nav className="rail" aria-label="Main">
      <a href="#/" className="rail__home" aria-label={`${APP_NAME} home`}>
        <span className="glyph" aria-hidden />
      </a>
      {GROUPS.map((group, gi) => (
        <ul key={gi} className="rail__group">
          {group.map((item) => (
            <li key={item.label}>
              {item.href ? (
                <Tip text={item.tip ?? item.label} side="right">
                  <a
                    href={item.href}
                    className={`rail__item${item.section === section ? " is-active" : ""}`}
                    aria-current={item.section === section ? "page" : undefined}
                    aria-label={item.tip ? item.tip.replace(": ", ", ") : item.label}
                  >
                    <RailGlyph name={item.glyph} />
                  </a>
                </Tip>
              ) : (
                <Tip text={`${item.label}: coming in a later slice`} side="right">
                  <span
                    className="rail__item is-disabled"
                    role="button"
                    aria-disabled="true"
                    tabIndex={-1}
                    aria-label={`${item.label}, coming in a later slice`}
                  >
                    <RailGlyph name={item.glyph} />
                  </span>
                </Tip>
              )}
            </li>
          ))}
        </ul>
      ))}
      <div className="rail__bottom">
        <Tip text="Documents: coming in a later slice" side="right">
          <span className="rail__item is-disabled" role="button" aria-disabled="true" tabIndex={-1} aria-label="Documents, coming in a later slice">
            <RailGlyph name="documents" />
          </span>
        </Tip>
      </div>
    </nav>
  );
}

export interface Crumb {
  label: string;
  href?: string;
  /** PROTOTYPE (line-move): the book crumb fades in when it just changed. */
  refile?: boolean;
}

export function TopBar({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <header className="topbar">
      <a href="#/" className="wordmark">
        <span className="glyph" aria-hidden />
        {APP_NAME}
      </a>
      <nav aria-label="Breadcrumb" className="crumbs">
        <ol>
          {crumbs.map((c, i) => (
            <li key={`${c.label}-${i}`} className={c.refile ? "is-refiled" : undefined}>
              {i === crumbs.length - 1 ? (
                <span aria-current="page">{c.label}</span>
              ) : c.href ? (
                <a href={c.href}>{c.label}</a>
              ) : (
                <span>{c.label}</span>
              )}
            </li>
          ))}
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
