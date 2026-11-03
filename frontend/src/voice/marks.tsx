// Drawn marks for the Phase A voice directions (VOICE-BRIEF.md). The owner asked for drawn marks tied to insurance
// work instead of a restyled stock set, so these are hand-drawn on purpose, one grid and one rule per direction:
//   A "Schedule"    line marks, 1.5px stroke, square caps, mitre joins (ACORD form furniture)
//   B "Bound"       filled ink silhouettes with even-odd knockouts (stamp dies, binder clips)
//   C "Paper trail" paper sheets with document-hue letter tabs (the source documents themselves)
// Decorative: every mark is aria-hidden; the row text carries the meaning.
import type { QueueGroup } from "../api/types";
import type { Voice } from "./voice";

const line = { fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "square", strokeLinejoin: "miter" } as const;
const thin = { fill: "none", stroke: "currentColor", strokeWidth: 1.25, strokeLinecap: "round", strokeLinejoin: "round" } as const;

/** Source documents behind a Compare row: "4 carriers" -> 4 (capped for the drawing). */
export function sheetCount(summary: string | undefined): number {
  const m = summary?.match(/(\d+)\s+carriers?/);
  return Math.max(2, Math.min(4, m ? Number(m[1]) : 2));
}

/* ------------------------------------------------------------------ workflow marks (32 grid) */

function WorkflowA({ slug }: { slug: string }) {
  if (slug === "policy-check") {
    // Expiring sheet behind, renewal sheet in front with a ticked box: "checked against".
    return (
      <g {...line}>
        <path d="M10.75 8.75V4.75h15.5v19h-4" />
        <rect x="5.75" y="8.75" width="16.5" height="18.5" />
        <rect x="9.25" y="13.25" width="5" height="5" />
        <path d="M10.4 15.9l1.3 1.3 2.1-2.5" />
        <path d="M17.25 15.75h1.5M9.25 22.75h9.5" />
      </g>
    );
  }
  // A quote schedule: header band, three carrier columns, ruled rows.
  return (
    <g {...line}>
      <rect x="5.75" y="4.75" width="20.5" height="22.5" />
      <path d="M5.75 9.75h20.5M12.75 9.75v17.5M19.25 9.75v17.5M5.75 15.75h20.5M5.75 21.5h20.5" />
    </g>
  );
}

function WorkflowB({ slug }: { slug: string }) {
  if (slug === "policy-check") {
    // A renewal seal: round die with ribbon tails and a knocked-out check.
    return (
      <g fill="currentColor">
        <path d="M11 21.5l-2.5 7.5 4-1.6 2.5 3.1 2-7z M21 21.5l2.5 7.5-4-1.6-2.5 3.1-2-7z" opacity="0.55" />
        <path
          fillRule="evenodd"
          d="M16 3.5a10 10 0 1 1 0 20 10 10 0 0 1 0-20z M11.3 13.4l1.6-1.6 2.2 2.2 4.4-4.6 1.6 1.6-6 6.2z"
        />
      </g>
    );
  }
  // A binder-clipped quote packet: back sheet, front sheet with knocked-out lines, the clip on top.
  return (
    <g fill="currentColor">
      <rect x="9" y="7" width="18" height="21" rx="1" opacity="0.4" />
      <path fillRule="evenodd" d="M5 10h18v19H5z M8.5 16h11v1.6h-11z M8.5 20h11v1.6h-11z M8.5 24h7v1.6h-7z" />
      <path d="M10 8.5h8l-1 3.5h-6z" />
      <path d="M11.2 8.5V4h1.6v4.5zM15.2 8.5V4h1.6v4.5z" />
    </g>
  );
}

const HUES = ["var(--doc-1-mark)", "var(--doc-2-mark)", "var(--doc-3-mark)", "var(--doc-4-mark)"];

/** A sheet fanned around the bottom centre of a 48x36 frame, with its document-hue tab on the top-right corner. */
function FanSheet({ angle, tab, dashed, lines }: { angle: number; tab?: string; dashed?: boolean; lines?: boolean }) {
  return (
    <g transform={`rotate(${angle} 24 34)`}>
      <rect x="15" y="6" width="18" height="25" rx="1.5" fill="var(--color-surface)" stroke="currentColor" strokeWidth="1.25" strokeDasharray={dashed ? "2.6 2" : undefined} />
      {tab && <rect x="24.5" y="3" width="7" height="4.5" rx="1" fill={tab} stroke="currentColor" strokeWidth="0.75" />}
      {lines && <path {...thin} d="M18.5 13h11M18.5 17.5h11M18.5 22h7" />}
    </g>
  );
}

function WorkflowC({ slug, count }: { slug: string; count: number }) {
  if (slug === "policy-check") {
    // Expiring (dashed, the old) behind the renewal; one line of the renewal sits in the dashed citation box.
    return (
      <g>
        <FanSheet angle={-13} dashed tab={HUES[0]} />
        <g transform="rotate(6 24 34)">
          <rect x="15" y="6" width="18" height="25" rx="1.5" fill="var(--color-surface)" stroke="currentColor" strokeWidth="1.25" />
          <rect x="24.5" y="3" width="7" height="4.5" rx="1" fill={HUES[2]} stroke="currentColor" strokeWidth="0.75" />
          <path {...thin} d="M18.5 12.5h11M18.5 25h7" />
          <rect x="17" y="15.5" width="14" height="5.5" rx="0.75" fill="none" stroke="var(--color-primary)" strokeWidth="1.2" strokeDasharray="1.8 1.4" />
          <path {...thin} d="M18.75 18.25h10.5" />
        </g>
      </g>
    );
  }
  // One sheet per source document, fanned like a hand of cards; the first document sits on top.
  const step = count > 3 ? 9 : 11;
  const angles = Array.from({ length: count }, (_, i) => (i - (count - 1) / 2) * step);
  return (
    <g>
      {angles
        .map((a, i) => ({ a, i }))
        .reverse()
        .map(({ a, i }) => (
          <FanSheet key={i} angle={a} tab={HUES[i]} lines={i === 0} />
        ))}
    </g>
  );
}

export function WorkflowMark({ voice, slug, summary, className }: { voice: Voice; slug: string; summary?: string; className?: string }) {
  const wide = voice === "c";
  return (
    <svg className={`vmark vmark--workflow ${className ?? ""}`} viewBox={wide ? "0 0 48 36" : "0 0 32 32"} aria-hidden focusable="false">
      {voice === "a" ? <WorkflowA slug={slug} /> : voice === "b" ? <WorkflowB slug={slug} /> : <WorkflowC slug={slug} count={sheetCount(summary)} />}
    </svg>
  );
}

/* ------------------------------------------------------------------ group marks (24 grid) */

function GroupA({ group }: { group: QueueGroup }) {
  switch (group) {
    case "in_progress": // a form being read: the reading line moves down the box
      return (
        <g {...line}>
          <rect x="4.75" y="3.75" width="14.5" height="16.5" />
          <path className="vmark__read" d="M7.75 8h8.5" />
        </g>
      );
    case "needs_review": // a sheet under the loupe
      return (
        <g {...line}>
          <path d="M14.25 3.75H3.75v16.5h7" />
          <path d="M6.75 8h5M6.75 11.5h3" />
          <circle cx="15" cy="14" r="3.75" />
          <path d="M17.9 16.9l2.6 2.6" />
        </g>
      );
    case "ready": // sign here: the signature line with its cross
      return (
        <g {...line}>
          <rect x="3.75" y="3.75" width="16.5" height="16.5" />
          <path d="M7 16.25h10M7.6 11.6l2.3 2.3M9.9 11.6l-2.3 2.3" />
        </g>
      );
    case "needs_attention": // a torn sheet with a mark
      return (
        <g {...line}>
          <path d="M4.75 3.75h14.5v9.5l-2.25 1.75 1.75 2-2 1.5.5 1.75H4.75z" />
          <path d="M11 7.5v4.5M11 15v.5" />
        </g>
      );
    default: // filed: the ticked box
      return (
        <g {...line}>
          <rect x="4.75" y="4.75" width="14.5" height="14.5" />
          <path d="M8 12.25l2.75 2.75 5.25-6" />
        </g>
      );
  }
}

function GroupB({ group }: { group: QueueGroup }) {
  switch (group) {
    case "in_progress": // a dater stamp: knob, stem, date-wheel band, foot
      return <path fill="currentColor" fillRule="evenodd" d="M12 2.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5z M11 7h2v4h-2z M5 11h14v5H5z M7 12.75h2.5v1.5H7z M10.75 12.75h2.5v1.5h-2.5z M14.5 12.75H17v1.5h-2.5z M3.5 17h17v3.5h-17z" />;
    case "needs_review": // the red pencil
      return <path fill="currentColor" fillRule="evenodd" d="M16 3.25l4.75 4.75L9 19.75H4.25V15z M14.4 6.6l3 3 .9-.9-3-3z" />;
    case "ready": // a binder clip: ready to go out
      return <path fill="currentColor" d="M5 10.5h14l-1.75 9.5H6.75z M8 10.5V4h1.5v6.5z M14.5 10.5V4H16v6.5z M8 3.25h8v1.5H8z" />;
    case "needs_attention": // a flag on the file
      return <path fill="currentColor" d="M4.5 3h1.75v18H4.5z M6.25 4h12.5l-2.75 4 2.75 4H6.25z" />;
    default: // the filed tray
      return <path fill="currentColor" fillRule="evenodd" d="M6 3.5h12v8H6z M8 6h8v1.25H8z M3 12.75h5.25l1.5 2.5h4.5l1.5-2.5H21v7.75H3z" />;
  }
}

function GroupC({ group }: { group: QueueGroup }) {
  const sheet = <rect x="4.75" y="3.25" width="14.5" height="17.5" rx="1.25" fill="var(--color-surface)" stroke="currentColor" strokeWidth="1.25" />;
  switch (group) {
    case "in_progress": // reading page by page
      return (
        <g>
          {sheet}
          <path {...thin} d="M7.5 8h9M7.5 11.5h9M7.5 15h5" opacity="0.45" />
          <path className="vmark__read" d="M6.25 7h11.5" stroke="var(--color-primary)" strokeWidth="1.5" />
        </g>
      );
    case "needs_review": // a line inside the dashed citation box (the PDF viewer's passage highlight)
      return (
        <g>
          {sheet}
          <path {...thin} d="M7.5 7.5h9M7.5 16.5h6" />
          <rect x="6.25" y="9.75" width="11.5" height="4.25" rx="0.75" fill="none" stroke="var(--color-primary)" strokeWidth="1.2" strokeDasharray="1.8 1.4" />
          <path {...thin} d="M7.75 11.9h8.5" />
        </g>
      );
    case "ready": // a bookmarked page, ready to hand to the client
      return (
        <g>
          {sheet}
          <path {...thin} d="M7.5 9h5M7.5 12.5h9M7.5 16h9" />
          <path d="M13.5 3.25h3.5v8l-1.75-1.4-1.75 1.4z" fill="var(--color-primary)" />
        </g>
      );
    case "needs_attention": // a page with its corner missing
      return (
        <g>
          <path d="M4.75 3.25h14.5v10.5l-6 7H4.75z" fill="var(--color-surface)" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round" />
          <path d="M19.25 13.75v7h-6" fill="none" stroke="currentColor" strokeWidth="1.1" strokeDasharray="1.6 1.6" />
          <path {...thin} d="M7.5 7.5h9M7.5 11h6" />
        </g>
      );
    default: // filed with the seal
      return (
        <g>
          {sheet}
          <path {...thin} d="M7.5 7.5h9M7.5 11h6" />
          <circle cx="14.5" cy="16" r="3" fill="none" stroke="currentColor" strokeWidth="1.25" />
          <path {...thin} d="M13.2 16l1 1 1.7-1.9" />
        </g>
      );
  }
}

export function GroupMark({ voice, group }: { voice: Voice; group: QueueGroup }) {
  return (
    <svg className="vmark vmark--group queue-group__icon" viewBox="0 0 24 24" aria-hidden focusable="false">
      {voice === "a" ? <GroupA group={group} /> : voice === "b" ? <GroupB group={group} /> : <GroupC group={group} />}
    </svg>
  );
}

/** B: the stamp word for a row's state (decorative; the status text beside it carries the meaning for AT). */
export function stampWord(group: QueueGroup, queueState: string, hasReason = false): string {
  switch (group) {
    case "in_progress":
      return "Received";
    case "needs_review":
      return "In review";
    case "ready":
      // A Ready row with a reason is a proposal that came back unfinished.
      return hasReason ? "Returned" : "Ready to propose";
    case "needs_attention":
      return "Returned";
    default:
      return queueState === "dismissed" ? "Dismissed" : "Filed";
  }
}
