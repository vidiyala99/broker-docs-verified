// Rail glyph legibility, Phase A previews (frontend/RAIL-BRIEF.md). Dev server only: `?rail=a|b|c` before the hash
// swaps the 13 rail glyphs for one direction; without the flag the rail renders the shipped `Glyph` set unchanged.
// Same rules as marks.tsx: 24 grid, round joins, currentColor, paper fill, decorative (aria-hidden).
import type { ReactNode } from "react";
import { Glyph, type GlyphName } from "./marks";

export type RailVariant = "a" | "b" | "c";

/** The preview flag, read once per page load (hash routing keeps the query string). */
export function railVariant(): RailVariant | null {
  if (!import.meta.env.DEV || typeof window === "undefined") return null;
  const v = new URLSearchParams(window.location.search).get("rail");
  return v === "a" || v === "b" || v === "c" ? v : null;
}

const PAPER = "var(--color-surface)";
const st = (w = 1.5) => ({ fill: "none", stroke: "currentColor", strokeWidth: w, strokeLinecap: "round", strokeLinejoin: "round" }) as const;

function Sheet({ x, y, w, h, dashed, sw = 1.5 }: { x: number; y: number; w: number; h: number; dashed?: boolean; sw?: number }) {
  return <rect x={x} y={y} width={w} height={h} rx="1.5" {...st(sw)} fill={PAPER} strokeDasharray={dashed ? "2.2 2" : undefined} />;
}

/* A, "Silhouettes": every item keeps the paper material but gets its own outline and mass, so it reads by shape
   before detail. Kept from the shipped set where the outline was already unique (compare, house, policy check,
   integrations, analytics); redrawn where the outline was a plain sheet. */
function drawA(name: GlyphName): ReactNode {
  const S = st();
  switch (name) {
    case "proposal": // bound cover: heavy filled spine, a label window, no ruled lines
      return (
        <>
          <Sheet x={5} y={2.75} w={14.25} h={18.5} />
          <path d="M5 4.25a1.5 1.5 0 0 1 1.5-1.5h3.25v18.5H6.5a1.5 1.5 0 0 1-1.5-1.5z" fill="currentColor" />
          <rect x="12" y="7" width="4.75" height="4" rx="0.75" {...S} />
        </>
      );
    case "certificate": // landscape certificate, the rosette breaking its lower edge
      return (
        <>
          <Sheet x={3} y={3.75} w={18} h={11.75} />
          <path {...S} d="M6.25 7.5h8M6.25 10.5h4.5" />
          <path {...S} fill={PAPER} d="M14.75 18.5v3.25l1.75-1 1.75 1V18.5" />
          <circle cx="16.5" cy="15.75" r="3.5" {...S} fill={PAPER} />
          <circle cx="16.5" cy="15.75" r="1.25" fill="currentColor" />
        </>
      );
    case "tasks": // clipboard: the clip is solid, two ticked rows
      return (
        <>
          <Sheet x={4.75} y={4.5} w={14.5} h={17} />
          <rect x="8.25" y="2.25" width="7.5" height="4.25" rx="1.25" fill="currentColor" />
          <path {...S} d="M8 11.75l1.5 1.5 3-3.25M14.25 12h2.25M8 17.25l1.5 1.5 3-3.25M14.25 17.5h2.25" />
        </>
      );
    case "assistant": // a note in the margin: a round balloon (the only round outline besides the loupe), one written line
      return (
        <>
          <path
            {...S}
            fill={PAPER}
            d="M12 4c4.56 0 8.25 3.13 8.25 7s-3.69 7-8.25 7c-1.1 0-2.15-.18-3.1-.5L4.5 19.75l1.3-3.75C4.6 14.75 3.75 13 3.75 11c0-3.87 3.69-7 8.25-7z"
          />
          <path {...S} d="M8.5 11h7" />
        </>
      );
    case "search": // the loupe is the shape; the lines it reads sit inside the lens
      return (
        <>
          <circle cx="10.25" cy="10.25" r="6.5" {...S} fill={PAPER} />
          <path {...st(2.25)} d="m15 15 5.25 5.25" />
          <path {...S} d="M7.5 9h5.5M7.5 11.75h3.5" />
        </>
      );
    case "insights": // a sticky note on the file: tinted, one corner folded
      return (
        <>
          <path {...S} fill="currentColor" fillOpacity="0.2" d="M4.75 4.75h14.5v9l-5.5 5.5h-9z" />
          <path {...S} d="M19.25 13.75h-4a1.5 1.5 0 0 0-1.5 1.5v4M8 9h8" />
        </>
      );
    case "vault": // archive box: the lid overhangs, a handle slot in front
      return (
        <>
          <path {...S} fill={PAPER} d="M4.75 9v9.75a1.5 1.5 0 0 0 1.5 1.5h11.5a1.5 1.5 0 0 0 1.5-1.5V9" />
          <rect x="3" y="4.25" width="18" height="4.75" rx="1" {...S} fill={PAPER} />
          <path {...st(2)} d="M10 13.5h4" />
        </>
      );
    case "documents": // a file folder with its tab
      return (
        <>
          <path {...S} fill={PAPER} d="M3.25 6.75a1.5 1.5 0 0 1 1.5-1.5h4.5l2 2.25h8a1.5 1.5 0 0 1 1.5 1.5v9.25a1.5 1.5 0 0 1-1.5 1.5H4.75a1.5 1.5 0 0 1-1.5-1.5z" />
          <path {...S} d="M3.25 10.75h17.5" />
        </>
      );
    case "analytics": // the plotted schedule, with its latest figure marked
      return (
        <>
          <path {...S} d="M4.75 3.75v16.5h15.5M8 16l3.5-4.5 3 2.5 4.5-6" />
          <circle cx="19" cy="8" r="1.6" fill="currentColor" />
        </>
      );
    default:
      return null; // compare, personal, policy-check, integrations: the shipped drawing (see RailGlyph)
  }
}

/* B and C share one mark per item, drawn on the 24 grid. B lays it on a faint shared sheet; C draws it alone. */
function mark(name: GlyphName, w: number): ReactNode {
  const S = st(w);
  switch (name) {
    case "compare": // two quotes, one line read across both
      return (
        <>
          <rect x="3.5" y="4.5" width="7" height="15" rx="1.25" {...S} />
          <rect x="13.5" y="4.5" width="7" height="15" rx="1.25" {...S} />
          <path {...S} d="M2.5 12h19" />
        </>
      );
    case "proposal":
      return (
        <>
          <rect x="5" y="3" width="14" height="18" rx="1.5" {...S} />
          <path d="M5 4.5A1.5 1.5 0 0 1 6.5 3h3v18h-3A1.5 1.5 0 0 1 5 19.5z" fill="currentColor" />
        </>
      );
    case "personal":
      return <path {...S} d="M3.5 11 12 3.75 20.5 11M6 9v11.25h12V9M10 20.25v-5.5h4v5.5" />;
    case "policy-check": // the renewal ticked against the dashed expiring box
      return (
        <>
          <rect x="3.5" y="6.5" width="12" height="13" rx="1.5" {...S} strokeDasharray="2.4 2.2" />
          <path {...S} d="M7.5 12.5l3.25 3.25L20.5 5" />
        </>
      );
    case "certificate": // the seal with its ribbons
      return (
        <>
          <path {...S} d="M8.5 14.5v6.5l3.5-2 3.5 2v-6.5" />
          <circle cx="12" cy="9.5" r="6" {...S} fill={PAPER} />
          <circle cx="12" cy="9.5" r="2.25" fill="currentColor" />
        </>
      );
    case "tasks": // a checklist, first row ticked
      return (
        <>
          <rect x="3.5" y="4.5" width="5.5" height="5.5" rx="1" {...S} />
          <rect x="3.5" y="14" width="5.5" height="5.5" rx="1" {...S} />
          <path {...S} d="M4.75 7.25l1.25 1.25 2.25-2.5M12 7.25h8.5M12 16.75h8.5" />
        </>
      );
    case "integrations": // two systems linked
      return (
        <>
          <rect x="2.5" y="8.75" width="11" height="6.5" rx="3.25" {...S} />
          <rect x="10.5" y="8.75" width="11" height="6.5" rx="3.25" {...S} />
        </>
      );
    case "assistant":
      return <path {...S} d="M3.5 4.5h17v11.5h-9l-5 4v-4h-3z" />;
    case "search":
      return (
        <>
          <circle cx="10" cy="10" r="6.5" {...S} />
          <path {...S} d="m15 15 5.5 5.5" />
        </>
      );
    case "analytics": // figures as bars
      return <path {...S} d="M5.75 20v-7M12 20V7.5M18.25 20V4" />;
    case "insights": // a sticky note, one corner folded (three plain lines read as a menu button)
      return (
        <>
          <path {...S} fill="currentColor" fillOpacity="0.2" d="M3.75 3.75h16.5v10.5l-6 6H3.75z" />
          <path {...S} d="M20.25 14.25h-4.5a1.5 1.5 0 0 0-1.5 1.5v4.5" />
        </>
      );
    case "vault": // locked
      return (
        <>
          <path {...S} d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
          <rect x="5" y="10.5" width="14" height="10" rx="1.5" {...S} />
          <path {...S} d="M12 14.5v2.5" />
        </>
      );
    case "documents": // clipped together
      return <path {...S} transform="rotate(40 12 12)" d="M16 6.5v9.5a4 4 0 0 1-8 0V5.75a2.75 2.75 0 0 1 5.5 0v9.75a1.5 1.5 0 0 1-3 0V7.5" />;
    default:
      return null;
  }
}

const B_SCALE = 0.68;

function draw(name: GlyphName, variant: RailVariant): ReactNode {
  if (variant === "a") return drawA(name);
  if (variant === "c") return mark(name, 1.75);
  // B: the shared sheet, quiet, with the item's mark at full weight in its middle.
  return (
    <>
      <rect x="4.75" y="2.75" width="14.5" height="18.5" rx="1.5" fill={PAPER} stroke="currentColor" strokeWidth="1.25" strokeOpacity="0.4" />
      <g transform={`translate(12 12.25) scale(${B_SCALE}) translate(-12 -12)`}>{mark(name, 1.75 / B_SCALE)}</g>
    </>
  );
}

/** A rail item's glyph: the shipped `Glyph` unless a preview direction is on. */
export function RailGlyph({ name, variant }: { name: GlyphName; variant: RailVariant | null }) {
  const body = variant ? draw(name, variant) : null;
  if (!body) return <Glyph name={name} />;
  return (
    <svg className={`vmark vmark--${name} vmark--rail-${variant}`} width={20} height={20} viewBox="0 0 24 24" aria-hidden focusable="false">
      {body}
    </svg>
  );
}
