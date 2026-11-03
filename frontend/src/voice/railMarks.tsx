// The rail's marks, "Silhouettes" (owner-picked direction A, frontend/RAIL-BRIEF.md; rules in DESIGN.md "Voice").
// At 20px a 1.5px stroke is about one device pixel, so interior lines carry nothing: each rail item reads by its own
// outline and mass. Same material as marks.tsx (24 grid, 1.5 strokes, round joins, paper fill, currentColor,
// decorative). Items whose shipped outline was already unique use the shared `Glyph`; the rest are drawn here.
import type { ReactNode } from "react";
import { Glyph, type GlyphName } from "./marks";

const PAPER = "var(--color-surface)";
const st = (w = 1.5) => ({ fill: "none", stroke: "currentColor", strokeWidth: w, strokeLinecap: "round", strokeLinejoin: "round" }) as const;

function Sheet({ x, y, w, h }: { x: number; y: number; w: number; h: number }) {
  return <rect x={x} y={y} width={w} height={h} rx="1.5" {...st()} fill={PAPER} />;
}

function draw(name: GlyphName): ReactNode {
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
      return null; // compare, personal, policy-check, integrations: the shared drawing already has its own outline
  }
}

/** A rail item's mark (20px, monochrome; active and disabled colours come from `.rail__item`). */
export function RailGlyph({ name }: { name: GlyphName }) {
  const body = draw(name);
  if (!body) return <Glyph name={name} />;
  return (
    <svg className={`vmark vmark--${name} vmark--rail`} width={20} height={20} viewBox="0 0 24 24" aria-hidden focusable="false">
      {body}
    </svg>
  );
}
