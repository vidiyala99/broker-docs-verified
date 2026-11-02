import { useEffect, useRef, useState } from "react";
import { ArrowsClockwise, ArrowsSplit, CheckCircle, PencilSimple, Scan, Warning } from "@phosphor-icons/react";
import type { Artifact, EvidenceState } from "../api/types";
import { scrollWithin } from "../ui/scroll";

export type TrustFilter = EvidenceState | "edited";

interface CountDef {
  key: TrustFilter;
  label: string;
  icon: React.ReactNode;
  always?: boolean;
}

const COUNTS: CountDef[] = [
  { key: "verified", label: "Verified", icon: <CheckCircle size={16} aria-hidden />, always: true },
  { key: "scan_read", label: "Read from scan", icon: <Scan size={16} aria-hidden />, always: true },
  { key: "not_found", label: "Couldn't verify", icon: <Warning size={16} aria-hidden />, always: true },
  { key: "conflicting", label: "Conflicting values", icon: <ArrowsSplit size={16} aria-hidden /> },
  { key: "failed", label: "Extraction failed", icon: <ArrowsClockwise size={16} aria-hidden /> },
  { key: "edited", label: "Edited", icon: <PencilSimple size={16} aria-hidden /> },
];

export function countOf(artifact: Artifact, key: TrustFilter): number {
  const c = artifact.counts as unknown as Record<string, number | undefined>;
  return c[key] ?? 0;
}

/** "Outline. 3 unverified, 1 failed" for the collapsed handle (DESIGN.md open item, decided). */
export function warningSummary(artifact: Artifact): string | null {
  const unverified = countOf(artifact, "scan_read") + countOf(artifact, "not_found");
  const conflicting = countOf(artifact, "conflicting");
  const failed = countOf(artifact, "failed");
  const parts = [
    unverified ? `${unverified} unverified` : "",
    conflicting ? `${conflicting} conflicting` : "",
    failed ? `${failed} failed` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

/**
 * DG2: trust counts in the outline header, each a filter that marks matching cells and
 * scrolls to the first one. Then the section list with the current section marked.
 */
export function Outline({
  artifact,
  filter,
  onFilter,
  scrollRoot,
  focusFirstCount,
}: {
  artifact: Artifact;
  filter: TrustFilter | null;
  onFilter: (f: TrustFilter | null) => void;
  scrollRoot: HTMLElement | null;
  focusFirstCount: number;
}) {
  const [current, setCurrent] = useState<string | null>(artifact.content.sections[0]?.id ?? null);
  const firstCount = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (focusFirstCount) firstCount.current?.focus();
  }, [focusFirstCount]);

  useEffect(() => {
    if (!scrollRoot) return;
    const els = Array.from(scrollRoot.querySelectorAll<HTMLElement>("[data-section]"));
    const io = new IntersectionObserver(
      (entries) => {
        const top = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) setCurrent((top.target as HTMLElement).dataset.section ?? null);
      },
      { root: scrollRoot, rootMargin: "0px 0px -70% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [scrollRoot, artifact]);

  const choose = (key: TrustFilter) => {
    const next = filter === key ? null : key;
    onFilter(next);
    if (next && scrollRoot) {
      const first = scrollRoot.querySelector<HTMLElement>(`[data-state="${next}"]`);
      if (first) scrollWithin(scrollRoot, first);
    }
  };

  const go = (id: string) => {
    const el = document.getElementById(`section-${id}`);
    if (el && scrollRoot) scrollWithin(scrollRoot, el, "start");
    document.getElementById(`h-${id}`)?.focus({ preventScroll: true });
  };

  const shown = COUNTS.filter((c) => c.always || countOf(artifact, c.key) > 0);

  return (
    <nav className="outline" aria-label="Report outline">
      <div className="trust" role="group" aria-label="Evidence in the tables. Choose one to mark matching cells">
        {shown.map((c, i) => {
          const n = countOf(artifact, c.key);
          return (
            <button
              key={c.key}
              ref={i === 0 ? firstCount : undefined}
              type="button"
              className={`trust__item trust__item--${c.key}`}
              aria-pressed={filter === c.key}
              disabled={n === 0}
              onClick={() => choose(c.key)}
            >
              {c.icon}
              <span className="trust__label">{c.label}</span>
              <span className="trust__count">{n}</span>
            </button>
          );
        })}
      </div>
      <ol className="outline__list">
        {artifact.content.sections.map((s) => (
          <li key={s.id} className={`outline__item level-${s.level}`}>
            <a
              href={`#section-${s.id}`}
              aria-current={current === s.id ? "location" : undefined}
              onClick={(e) => {
                e.preventDefault();
                go(s.id);
              }}
            >
              {s.number}. {s.title}
              {s.stale && <span className="sr-only"> (may be out of date)</span>}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
