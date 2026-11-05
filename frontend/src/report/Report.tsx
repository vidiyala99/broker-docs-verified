import { useMemo, useState } from "react";
import { ArrowsClockwise, ClockCounterClockwise, Info, PencilSimple, Warning, X } from "@phosphor-icons/react";
import type { Artifact, Block, Cell, Chip as ChipData, KeyDifferenceBlock, Notice, Section, Segment } from "../api/types";
import { ChipList, type OpenSource } from "./Chip";
import { cellDomId, ReportTable, type TableActions } from "./Table";
import { keyboardAction } from "../ui/input";
import { reducedMotion } from "../ui/motion";

export function Segments({
  segments,
  chips,
  onOpen,
}: {
  segments: Segment[];
  chips: Record<string, ChipData>;
  onOpen: OpenSource;
}) {
  return (
    <>
      {segments.map((s, i) => (
        <span key={i}>
          {i > 0 && !/\s$/.test(segments[i - 1]!.text) && !/^[\s.,;:)]/.test(s.text) && segments[i - 1]!.passage_ids.length === 0 ? " " : ""}
          {s.text}
          {s.passage_ids.length > 0 && (
            <>
              {" "}
              <ChipList ids={s.passage_ids} chips={chips} onOpen={onOpen} />{" "}
            </>
          )}
        </span>
      ))}
    </>
  );
}

export interface ReportActions extends TableActions {
  onRegenerate: () => void;
  regenerating: boolean;
  onRetryProse: () => void;
}

/** DG5 run-level rows: full width at the top, at most two; only informational rows dismiss. */
export function Notices({ notices, actions }: { notices: Notice[]; actions: ReportActions }) {
  const [dismissed, setDismissed] = useState<string[]>([]);
  const visible = notices.filter((n) => n.kind !== "conflicting_values" && !dismissed.includes(n.message)).slice(0, 2);
  if (!visible.length) return null;
  return (
    <div className="notices">
      {visible.map((n) => {
        const info = n.kind === "info";
        return (
          <div key={n.message} className={`notice notice--${n.kind}`} role={info ? "status" : "alert"}>
            {info ? <Info size={16} aria-hidden /> : <Warning size={16} aria-hidden />}
            <span className="notice__text">{n.message}</span>
            {n.kind === "compose_fallback" && n.retryable && (
              <button type="button" className="btn btn--secondary btn--sm" onClick={actions.onRetryProse} disabled={actions.regenerating}>
                {actions.regenerating ? "Writing prose..." : "Retry prose"}
              </button>
            )}
            {n.kind === "documents_failed" && n.retryable && actions.onRetryRun && (
              <button type="button" className="btn btn--secondary btn--sm" onClick={actions.onRetryRun}>
                <ArrowsClockwise size={16} aria-hidden /> Retry
              </button>
            )}
            {(info || n.dismissible) && (
              <button
                type="button"
                className="icon-btn"
                aria-label="Dismiss"
                onClick={() => setDismissed((d) => [...d, n.message])}
              >
                <X size={16} aria-hidden />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function BlockView({ block, actions, skeleton }: { block: Block; actions: ReportActions; skeleton: boolean }) {
  switch (block.type) {
    case "heading": {
      const Tag = block.level <= 2 ? "h3" : "h4";
      return <Tag className="report-subheading">{block.text}</Tag>;
    }
    case "paragraph":
      if (skeleton) return <ProseSkeleton />;
      return (
        <p className="prose">
          <Segments segments={block.segments} chips={actions.chips} onOpen={actions.onOpen} />
        </p>
      );
    case "list":
      if (skeleton) return <ProseSkeleton />;
      return (
        <div className="prose-list">
          {block.title && <p className="prose-list__title">{block.title}</p>}
          <ul>
            {block.items.map((item, i) => (
              <li key={i}>
                <Segments segments={item} chips={actions.chips} onOpen={actions.onOpen} />
              </li>
            ))}
          </ul>
        </div>
      );
    case "table":
      return <ReportTable block={block} actions={actions} />;
    case "discrepancy":
      // Policy Check runs render their items in the discrepancy view; this is the fallback.
      return (
        <article className="issue">
          <h3 className="report-subheading">{block.title}</h3>
          <p className="prose">{block.prose.map((p) => p.text).join("")}</p>
        </article>
      );
    case "certificate":
      // Certificate runs render in the certificate view (certificates/CertificateRunView.tsx); this is the fallback.
      return (
        <article className="issue">
          <h3 className="report-subheading">Certificate for {block.holder_name}</h3>
          <p className="prose">
            {block.policies.length} {block.policies.length === 1 ? "policy" : "policies"} printed, certificate date {block.certificate_date}.
          </p>
        </article>
      );
    case "claim":
      return (
        <article className="issue">
          <h3 className="report-subheading">{block.title}</h3>
          <p className="prose">
            <Segments segments={block.prose} chips={actions.chips} onOpen={actions.onOpen} />
          </p>
        </article>
      );
    case "key_difference":
      return null; // rendered by its section (KeyDifferenceView)
    case "issue":
      return (
        <article className="issue">
          <h3 className="report-subheading">
            {block.code}: {block.title}
          </h3>
          <dl className="issue__props">
            <dt>Severity</dt>
            <dd className="status-word">{block.severity}</dd>
            <dt>Category</dt>
            <dd>{block.category}</dd>
            <dt>Impact</dt>
            <dd>
              <Segments segments={block.impact} chips={actions.chips} onOpen={actions.onOpen} />
            </dd>
            <dt>Affected layers</dt>
            <dd>{block.affected_layers}</dd>
            <dt>Follow-form</dt>
            <dd>{block.follow_form}</dd>
          </dl>
        </article>
      );
  }
}

function ProseSkeleton() {
  return (
    <div className="skeleton-lines" aria-hidden>
      <span />
      <span />
      <span />
    </div>
  );
}

type CellIndex = Map<string, Cell>;
const cellKey = (row: string, column: string) => `${row}\u0000${column}`;

/** Every table cell of the report by (row key, column letter): what a Key difference segment cites (R10). */
function indexCells(artifact: Artifact): CellIndex {
  const index: CellIndex = new Map();
  for (const s of artifact.content.sections)
    for (const b of s.blocks)
      if (b.type === "table") for (const r of b.rows) for (const [col, cell] of Object.entries(r.cells)) index.set(cellKey(r.key, col), cell);
  return index;
}

/** Segments carry no reliable spacing (a model segment may end without a space, a template one may start with one):
 *  join with one space unless either side already has it or the next starts with closing punctuation. */
function joinSpace(prev: string | undefined, next: string): string {
  return prev != null && !/\s$/.test(prev) && !/^[\s.,;:)]/.test(next) ? " " : "";
}

/** R10: a cited cell shows its current source chips; an edited one shows "edited by you", which takes the reader to the
 *  cell (its own source is the broker, not a page). A cell with neither (Not stated) shows nothing. */
function CellChips({ refs, cells, actions }: { refs: KeyDifferenceBlock["segments"][number]["cells"]; cells: CellIndex; actions: ReportActions }) {
  const ids: number[] = [];
  const edited: { row: string; column: string }[] = [];
  for (const ref of refs) {
    const cell = cells.get(cellKey(ref.row, ref.column));
    if (!cell) continue;
    if (cell.edited) edited.push(ref);
    else for (const id of cell.passage_ids) if (!ids.includes(id)) ids.push(id);
  }
  if (!ids.length && !edited.length) return null;
  return (
    <>
      <ChipList ids={ids} chips={actions.chips} onOpen={actions.onOpen} />
      {edited.map((ref) => (
        <span key={`${ref.row}-${ref.column}`} className="chips">
          <button
            type="button"
            className="chip chip--edited"
            aria-label={`${ref.column}: edited by you. Go to the cell`}
            onClick={() => {
              const td = document.getElementById(cellDomId(ref.row, ref.column));
              if (!td) return;
              td.scrollIntoView({ block: "center", behavior: keyboardAction() || reducedMotion() ? "auto" : "smooth" });
              td.focus({ preventScroll: true });
            }}
          >
            <PencilSimple size={12} weight="bold" aria-hidden />
            edited by you
          </button>
        </span>
      ))}
    </>
  );
}

/** Spec 3.3 / 5 with R3 and R10: the Key difference, first in the report, one canvas panel. Sentence segments with their
 *  cell chips, where it came from (model or rule), and the stale tag with Rewrite when the marks moved since. */
function KeyDifferenceView({ section, block, cells, actions }: { section: Section; block: KeyDifferenceBlock; cells: CellIndex; actions: ReportActions }) {
  const stale = Boolean(block.stale || section.stale);
  return (
    <section className={`report-section level-1 key-difference${stale ? " is-stale" : ""}`} id={`section-${section.id}`} aria-labelledby={`h-${section.id}`} data-section={section.id}>
      <div className="report-section__head">
        <h2 id={`h-${section.id}`} className="report-section__title" tabIndex={-1}>
          {section.title || block.title}
        </h2>
        {stale && (
          <span className="stale">
            <span className="stale-tag">
              <ClockCounterClockwise size={14} aria-hidden />
              May be out of date
            </span>
            <button type="button" className="link-btn" onClick={actions.onRegenerate} disabled={actions.regenerating}>
              {actions.regenerating ? "Rewriting..." : "Rewrite"}
            </button>
          </span>
        )}
      </div>
      {actions.regenerating ? (
        <ProseSkeleton />
      ) : (
        <p className="key-difference__text">
          {block.segments.map((s, i) => {
            const chips = s.cells.length > 0 ? <CellChips refs={s.cells} cells={cells} actions={actions} /> : null;
            // The chip stays on the line of the segment's last word (no chip alone at the start of a line).
            const cut = chips ? s.text.trimEnd().lastIndexOf(" ") + 1 : s.text.length;
            return (
              <span key={i}>
                {joinSpace(block.segments[i - 1]?.text, s.text)}
                {s.text.slice(0, cut)}
                {chips && (
                  <span className="nowrap">
                    {s.text.slice(cut)}
                    {chips}
                  </span>
                )}
              </span>
            );
          })}
        </p>
      )}
      <p className="key-difference__source">
        {block.source === "model" ? "Drafted by the model and checked against the marked cells." : "Written by rule from the marked cells."}
      </p>
    </section>
  );
}

function SectionView({ section, actions, cells }: { section: Section; actions: ReportActions; cells: CellIndex }) {
  const kd = section.blocks.find((b): b is KeyDifferenceBlock => b.type === "key_difference");
  if (kd) return <KeyDifferenceView section={section} block={kd} cells={cells} actions={actions} />;
  const Tag = section.level <= 1 ? "h2" : "h3";
  const skeleton = actions.regenerating && section.blocks.some((b) => b.type === "paragraph" || b.type === "list");
  return (
    <section className={`report-section level-${section.level}`} id={`section-${section.id}`} aria-labelledby={`h-${section.id}`} data-section={section.id}>
      <div className="report-section__head">
        <Tag id={`h-${section.id}`} className="report-section__title" tabIndex={-1}>
          {section.number ? `${section.number}. ` : ""}
          {section.title}
        </Tag>
        {section.stale && (
          <span className="stale">
            <span className="stale-tag">
              <ClockCounterClockwise size={14} aria-hidden />
              May be out of date
            </span>
            <button type="button" className="link-btn" onClick={actions.onRegenerate} disabled={actions.regenerating}>
              {actions.regenerating ? "Regenerating..." : "Regenerate prose"}
            </button>
          </span>
        )}
      </div>
      {skeleton && <span className="sr-only">Writing prose</span>}
      {section.blocks.map((b) => (
        <BlockView key={b.id} block={b} actions={actions} skeleton={skeleton} />
      ))}
    </section>
  );
}

export function Report({ artifact, actions }: { artifact: Artifact; actions: ReportActions }) {
  const cells = useMemo(() => indexCells(artifact), [artifact]);
  return (
    <article className="report" aria-label={artifact.title}>
      <Notices notices={artifact.content.notices} actions={actions} />
      {artifact.content.sections.map((s) => (
        <SectionView key={s.id} section={s} actions={actions} cells={cells} />
      ))}
    </article>
  );
}
