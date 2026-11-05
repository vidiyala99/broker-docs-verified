import { ShieldCheck } from "@phosphor-icons/react";
import type { Line, Workspace } from "../api/types";
import { QueueView } from "../queue/QueueView";
import { useQueue } from "../queue/useQueue";
import { isEmpty } from "../queue/queueUtil";
import { Glyph, WorkflowMark } from "../voice/marks";
import { acceptOf, compareUploads, DropZone, useUploads } from "./Upload";
import "../personal/personal.css";

/** Where a book's start buttons go (slice 6: Compare opens its own upload page, `#/compare/new`). */
export function compareHref(line: Line): string {
  return `#/compare/new${line === "personal" ? "?line=personal" : ""}`;
}

export function checkHref(line: Line): string {
  return `#/policy-check/new${line === "personal" ? "?line=personal" : ""}`;
}

const BOOK: Record<Line, { title: string; sub: string; compare: string; empty: string }> = {
  commercial: {
    title: "Commercial",
    sub: "Business policies: liability, property, commercial auto and workers' comp.",
    compare: "Compare quotes",
    empty: "No commercial work yet.",
  },
  personal: {
    title: "Personal lines",
    sub: "Homeowners, auto and other household policies.",
    compare: "Compare homeowners quotes",
    empty: "No personal-lines work yet.",
  },
};

function Actions({ line }: { line: Line }) {
  return (
    <div className="personal__actions">
      <a href={compareHref(line)} className="btn btn--primary">
        <WorkflowMark slug="compare-quotes" size={18} /> {BOOK[line].compare}
      </a>
      <a href={checkHref(line)} className="btn btn--secondary">
        <WorkflowMark slug="policy-check" size={18} /> Check a renewal
      </a>
    </div>
  );
}

/**
 * A book's home (slice 6, spec 4.2; Activity/queue change 2): the book's whole work queue under its own name, so the
 * Commercial home is no longer titled "Compare Quotes" (it lists loss reviews and checks too) and matches Personal
 * lines. One primary, Compare quotes (to `#/compare/new`), and Check a renewal. No left panel. The empty state keeps a
 * large drop zone: dropped files go into the shared upload store and the page moves to `#/compare/new`, where they keep
 * uploading. Decisions in COMPARE-MATCH-BRIEF.md.
 */
export function LaunchView({ workspace, line }: { workspace: Workspace; line: Line }) {
  const data = useQueue(line);
  const empty = data.queue != null && !data.error && isEmpty(data.queue);
  const uploads = useUploads(workspace.limits, compareUploads);
  const book = BOOK[line];
  const drop = (files: File[]) => {
    uploads.add(files);
    window.location.hash = compareHref(line);
  };
  return (
    <div className="workspace">
      <main className="pane personal" id="main" tabIndex={-1} aria-labelledby="book-title">
        <div className="personal__body">
          <header className="personal__head">
            <h1 id="book-title" className="page-title">
              {book.title}
            </h1>
            <p className="personal__sub">{book.sub}</p>
            {/* One place per state for the two actions: the header, or the empty state (no duplicate CTAs). */}
            {!empty && <Actions line={line} />}
          </header>

          <QueueView
            data={data}
            line={line}
            empty={
              <div className="queue-empty queue-empty--launch">
                <Glyph name="blank" size={32} className="muted-glyph" />
                <h2 className="queue-empty__title">{book.empty}</h2>
                <DropZone
                  limits={workspace.limits}
                  onFiles={drop}
                  large
                  title="Drop quotes to compare"
                  accept={acceptOf(workspace.limits)}
                  limitsLine="PDF, Word and Excel quotes, proposals and summaries"
                />
                <a href={checkHref(line)} className="btn btn--secondary queue-empty__alt">
                  <ShieldCheck size={18} aria-hidden /> Check a renewal instead
                </a>
              </div>
            }
          />
        </div>
      </main>
    </div>
  );
}
