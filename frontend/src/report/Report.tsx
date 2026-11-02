import { useState } from "react";
import { ArrowsClockwise, ClockCounterClockwise, Info, Warning, X } from "@phosphor-icons/react";
import type { Artifact, Block, Chip as ChipData, Notice, Section, Segment } from "../api/types";
import { ChipList, type OpenSource } from "./Chip";
import { ReportTable, type TableActions } from "./Table";

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

function SectionView({ section, actions }: { section: Section; actions: ReportActions }) {
  const Tag = section.level <= 1 ? "h2" : "h3";
  const skeleton = actions.regenerating && section.blocks.some((b) => b.type === "paragraph" || b.type === "list");
  return (
    <section className={`report-section level-${section.level}`} id={`section-${section.id}`} aria-labelledby={`h-${section.id}`} data-section={section.id}>
      <div className="report-section__head">
        <Tag id={`h-${section.id}`} className="report-section__title" tabIndex={-1}>
          {section.number}. {section.title}
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
  return (
    <article className="report" aria-label={artifact.title}>
      <Notices notices={artifact.content.notices} actions={actions} />
      {artifact.content.sections.map((s) => (
        <SectionView key={s.id} section={s} actions={actions} />
      ))}
    </article>
  );
}
