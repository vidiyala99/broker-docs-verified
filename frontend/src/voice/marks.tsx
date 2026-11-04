// The "Paper trail" mark set (VOICE-BRIEF.md, DESIGN.md "Voice"). Drawn for this product on purpose (the owner asked
// for drawn marks tied to insurance work, not a restyled stock set): one 24 grid, 1.5px strokes, round joins, paper
// sheets as the base shape. Each workflow and each queue state has its own silhouette so the marks read at 20 to 28px.
// Colour: currentColor for the drawing; document hues (DG11 `--doc-N-mark`) only on the small sheet tabs; primary only
// for the citation box, the reading line and the ribbon. Every mark is decorative (aria-hidden): text carries meaning.
import type { QueueGroup } from "../api/types";

const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round" } as const;
const PAPER = "var(--color-surface)";
const CITE = "var(--color-primary)";

/** A sheet on the 24 grid. */
function Sheet({ x, y, w, h, dashed }: { x: number; y: number; w: number; h: number; dashed?: boolean }) {
  return <rect x={x} y={y} width={w} height={h} rx="1.5" {...S} fill={PAPER} strokeDasharray={dashed ? "2.2 2" : undefined} />;
}

/** A coloured document tab on a sheet's top edge (decoration: the document hue, never text). */
function Tab({ x, y, hue }: { x: number; y: number; hue: number }) {
  return <rect x={x} y={y} width="4.5" height="2.75" rx="0.75" fill={`var(--doc-${hue}-mark)`} stroke="currentColor" strokeWidth="0.75" />;
}

export type GlyphName =
  | "compare"
  | "policy-check"
  | "proposal"
  | "personal"
  | "commercial"
  | "certificate"
  | "tasks"
  | "integrations"
  | "assistant"
  | "search"
  | "analytics"
  | "insights"
  | "vault"
  | "documents"
  | "reading"
  | "review"
  | "ready"
  | "attention"
  | "done"
  | "blank";

/** `tabs`: draw the document-hue tabs (rows, buttons, headers); the rail stays monochrome. */
function draw(name: GlyphName, tabs: boolean) {
  switch (name) {
    case "compare": // two quotes side by side: the comparison
      return (
        <>
          <Sheet x={2.75} y={5.75} w={8.25} h={15} />
          <Sheet x={13} y={5.75} w={8.25} h={15} />
          {tabs && <Tab x={4.5} y={3.5} hue={1} />}
          {tabs && <Tab x={14.75} y={3.5} hue={2} />}
          <path {...S} d="M5.5 11.5h3M15.75 11.5h3" />
        </>
      );
    case "policy-check": // the renewal laid over the expiring policy (dashed), ticked
      return (
        <>
          <Sheet x={8.75} y={2.75} w={12.5} h={15} dashed />
          <Sheet x={2.75} y={6.25} w={12.5} h={15} />
          {tabs && <Tab x={4.5} y={4} hue={3} />}
          <path {...S} strokeWidth="1.75" d="M6 14l2.25 2.25L12.25 11.5" />
        </>
      );
    case "proposal": // a bound document: the spine on the left
      return (
        <>
          <Sheet x={4.75} y={2.75} w={14.5} h={18.5} />
          <path d="M4.75 4.25a1.5 1.5 0 0 1 1.5-1.5h1.5v18.5h-1.5a1.5 1.5 0 0 1-1.5-1.5z" fill="currentColor" />
          <path {...S} d="M10.5 8h5.5M10.5 11.5h5.5M10.5 15h3.5" />
        </>
      );
    case "personal": // a household: the house
      return <path {...S} d="M3.75 11 12 4l8.25 7M6 9.25v11h12v-11M10 20.25v-5.5h4v5.5" />;
    case "commercial": // a business: an office block on its ground line (line-move B; the book tab, not the rail)
      return (
        <>
          <rect x="5.25" y="3.75" width="13.5" height="16.5" rx="1.5" {...S} fill={PAPER} />
          <path {...S} d="M8.75 7.75h1.5M13.75 7.75h1.5M8.75 11.25h1.5M13.75 11.25h1.5M10.5 20.25v-4.5h3v4.5M3.75 20.25h16.5" />
        </>
      );
    case "certificate": // a certificate: a sheet with its seal
      return (
        <>
          <Sheet x={3.75} y={3.75} w={16.5} h={12.5} />
          <path {...S} d="M7 7.5h10M7 10.5h6" />
          <circle cx="16" cy="16.5" r="2.75" {...S} fill={PAPER} />
          <path {...S} d="M14.75 19v2.25l1.25-.75 1.25.75V19" />
        </>
      );
    case "tasks": // a clipboard with ticked lines
      return (
        <>
          <Sheet x={4.75} y={4.25} w={14.5} h={17} />
          <path {...S} d="M9 4.25V3h6v1.25M8 10l1.25 1.25L11.5 9M13.5 10.25H16M8 15.5l1.25 1.25L11.5 14.5M13.5 15.75H16" />
        </>
      );
    case "integrations": // two systems' sheets joined
      return (
        <>
          <Sheet x={2.75} y={4.75} w={7.5} h={10} />
          <Sheet x={13.75} y={9.25} w={7.5} h={10} />
          <path {...S} d="M10.25 9.75h2a1.5 1.5 0 0 1 1.5 1.5v2.25" />
        </>
      );
    case "assistant": // a note in the margin
      return <path {...S} d="M4.75 4.75h14.5v11h-8.5l-4.25 3.5v-3.5H4.75zM8 8.5h8M8 12h5" />;
    case "search": // looking through the sheets
      return (
        <>
          <path {...S} d="M14.25 3.75H4.75v16.5h6" />
          <path {...S} d="M7.5 8h4M7.5 11.5h2" />
          <circle cx="15" cy="14" r="3.75" {...S} />
          <path {...S} d="m17.75 16.75 2.5 2.5" />
        </>
      );
    case "analytics": // a schedule's figures plotted
      return <path {...S} d="M4.75 3.75v16.5h15.5M8 16l3.5-4.5 3 2.5 4.5-6" />;
    case "insights": // a highlighted line on a sheet
      return (
        <>
          <Sheet x={4.75} y={2.75} w={14.5} h={18.5} />
          <rect x="7.25" y="10" width="9.5" height="3.5" rx="0.75" fill="currentColor" opacity="0.3" />
          <path {...S} d="M8 7h8M8 11.75h8M8 16.5h5" />
        </>
      );
    case "documents": // the document stack
      return (
        <>
          <Sheet x={7.75} y={2.75} w={12.5} h={15.5} />
          <Sheet x={3.75} y={5.75} w={12.5} h={15.5} />
          <path {...S} d="M6.75 11h6.5M6.75 14.5h4" />
        </>
      );
    case "vault": // the file drawer
      return <path {...S} d="M3.75 4.75h16.5v14.5H3.75zM3.75 12h16.5M10 8.25h4M10 15.5h4" />;
    case "reading": // a page being read: the reading line travels down (CSS `.vmark__read`)
      return (
        <>
          <Sheet x={4.75} y={2.75} w={14.5} h={18.5} />
          <path className="vmark__read" d="M7.25 7h9.5" stroke={CITE} strokeWidth="2" strokeLinecap="round" />
        </>
      );
    case "review": // a line in the dashed citation box (the source viewer's passage highlight)
      return (
        <>
          <Sheet x={4.75} y={2.75} w={14.5} h={18.5} />
          <rect x="6.75" y="8.75" width="10.5" height="6" rx="1" fill="none" stroke={CITE} strokeWidth="1.75" strokeDasharray="2.2 1.6" />
          <path {...S} d="M9 11.75h6" />
        </>
      );
    case "ready": // a bookmarked page, ready to hand over
      return (
        <>
          <Sheet x={4.75} y={2.75} w={14.5} h={18.5} />
          <path d="M12.5 2.75h4v8.5l-2-1.6-2 1.6z" fill={CITE} />
          <path {...S} d="M7.75 14h8.5M7.75 17.5h5" />
        </>
      );
    case "attention": // a page with its corner missing
      return (
        <>
          <path d="M4.75 4.25a1.5 1.5 0 0 1 1.5-1.5h11.5a1.5 1.5 0 0 1 1.5 1.5v9.5l-7.5 7.5h-5.5a1.5 1.5 0 0 1-1.5-1.5z" {...S} fill={PAPER} />
          <path {...S} strokeDasharray="1.6 1.8" d="M19.25 15.5v5.75H13.5" />
          <path {...S} d="M8 7.75h8M8 11.25h5" />
        </>
      );
    case "done": // filed under seal
      return (
        <>
          <Sheet x={4.75} y={2.75} w={14.5} h={18.5} />
          <path {...S} d="M8 7.5h8M8 11h5" />
          <circle cx="14.5" cy="16" r="3" {...S} fill={PAPER} />
        </>
      );
    default: // blank: an empty sheet (empty states)
      return (
        <>
          <Sheet x={4.75} y={2.75} w={14.5} h={18.5} />
          <path {...S} strokeDasharray="1.5 2" d="M8 8h8M8 12h8M8 16h5" />
        </>
      );
  }
}

export function Glyph({ name, size = 20, tabs = false, className = "" }: { name: GlyphName; size?: number; tabs?: boolean; className?: string }) {
  return (
    <svg className={`vmark vmark--${name} ${className}`} width={size} height={size} viewBox="0 0 24 24" aria-hidden focusable="false">
      {draw(name, tabs)}
    </svg>
  );
}

const WORKFLOW_GLYPH: Record<string, GlyphName> = { "compare-quotes": "compare", "policy-check": "policy-check", proposal: "proposal", certificate: "certificate" };

/** A workflow's mark, with its document tabs. */
export function WorkflowMark({ slug, size = 28, className = "" }: { slug: string; size?: number; className?: string }) {
  return <Glyph name={WORKFLOW_GLYPH[slug] ?? "compare"} size={size} tabs className={`vmark--workflow ${className}`} />;
}

const GROUP_GLYPH: Record<QueueGroup, GlyphName> = {
  in_progress: "reading",
  needs_review: "review",
  ready: "ready",
  needs_attention: "attention",
  done_recently: "done",
};

export function GroupMark({ group }: { group: QueueGroup }) {
  return <Glyph name={GROUP_GLYPH[group]} size={22} className="vmark--group queue-group__icon" />;
}

/** The reading mark: replaces the spinner wherever the system is reading documents. */
export function ReadingMark({ size = 20, className = "" }: { size?: number; className?: string }) {
  return <Glyph name="reading" size={size} className={`vmark--reading ${className}`} />;
}

/** The seal pressed on a row that reaches Done recently (lands once on pointer actions; DESIGN.md Voice). */
export function Seal({ lands }: { lands: boolean }) {
  return (
    <svg className={`seal${lands ? " seal--lands" : ""}`} width="16" height="16" viewBox="0 0 16 16" aria-hidden focusable="false">
      <circle cx="8" cy="8" r="6.75" fill="var(--color-surface)" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="8" cy="8" r="4.5" fill="none" stroke="currentColor" strokeWidth="0.75" strokeDasharray="1 1.2" />
      <path d="m5.6 8.1 1.6 1.6 3.2-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
