import { useId, useMemo, useRef, useState } from "react";
import { LockSimple, MagnifyingGlass, Table, TextAlignLeft } from "@phosphor-icons/react";
import type { Template, TemplateSummary } from "../api/types";
import { useFadeOnChange } from "../ui/motion";
import { isLocked, isTableKind, sectionNumbers } from "./draft";

export function StepTemplate({
  summaries,
  templates,
  selected,
  onSelect,
}: {
  summaries: TemplateSummary[] | null;
  templates: Record<string, Template>;
  selected: string | null;
  onSelect: (slug: string) => void;
}) {
  const [query, setQuery] = useState("");
  const searchId = useId();
  // Typing filters instantly; a pointer change (Clear search, the field's clear button) re-settles the options with a
  // short opacity lift (ui/motion.tsx).
  const optionsRef = useRef<HTMLFieldSetElement>(null);
  useFadeOnChange(optionsRef, query.trim().toLowerCase());

  const visible = useMemo(() => {
    if (!summaries) return null;
    const q = query.trim().toLowerCase();
    if (!q) return summaries;
    return summaries.filter((s) => {
      const full = templates[s.slug];
      const hay = [s.name, s.description, ...(full?.blocks.map((b) => b.title) ?? [])].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [summaries, templates, query]);

  const previewSlug = selected ?? visible?.[0]?.slug ?? null;
  const preview = previewSlug ? templates[previewSlug] ?? null : null;

  return (
    <div className="tpl">
      <div className="tpl__pick">
        <div className="field">
          <label htmlFor={searchId} className="field__label">
            Search templates
          </label>
          <div className="search">
            <MagnifyingGlass size={16} aria-hidden className="search__icon" />
            <input
              id={searchId}
              type="search"
              className="input search__input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Name or section, for example cyber"
              autoComplete="off"
            />
          </div>
        </div>

        {visible == null ? (
          <div className="tpl__skeleton" aria-busy="true">
            <span className="sr-only" role="status">
              Loading templates
            </span>
            <span aria-hidden />
            <span aria-hidden />
          </div>
        ) : visible.length === 0 ? (
          <div className="wizard-empty" role="status">
            <p>No template matches "{query.trim()}".</p>
            <button type="button" className="link-btn" onClick={() => setQuery("")}>
              Clear search
            </button>
          </div>
        ) : (
          <fieldset ref={optionsRef} className="tpl__options">
            <legend className="sr-only">Template</legend>
            {visible.map((s) => {
              const on = s.slug === selected;
              const sections = templates[s.slug]?.blocks.filter((b) => !isLocked(b)).length;
              return (
                <label key={s.slug} className={`tpl-option${on ? " is-selected" : ""}`}>
                  <input type="radio" name="template" value={s.slug} checked={on} onChange={() => onSelect(s.slug)} className="tpl-option__radio" />
                  <span className="tpl-option__name">{s.name}</span>
                  <span className="tpl-option__desc">{s.description}</span>
                  <span className="tpl-option__meta">
                    {sections ?? s.block_count - 2} sections, plus cover page and contents
                  </span>
                </label>
              );
            })}
          </fieldset>
        )}
      </div>

      <aside className="tpl__preview" aria-label={preview ? `Preview: ${preview.name}` : "Preview"} aria-live="polite">
        {preview ? <TemplatePreview tpl={preview} /> : visible && visible.length > 0 ? <div className="skeleton-lines" aria-hidden><span /><span /><span /></div> : null}
      </aside>
    </div>
  );
}

function TemplatePreview({ tpl }: { tpl: Template }) {
  const nums = sectionNumbers(tpl.blocks);
  return (
    <>
      <h3 className="tpl__preview-title">{tpl.name} template</h3>
      <ol className="contents" aria-label={`${tpl.name} sections`}>
        {tpl.blocks.map((b, i) => {
          const table = isTableKind(b.kind, tpl.kinds);
          return (
            <li key={b.id} className={`contents__row${isLocked(b) ? " is-locked" : ""}`}>
              <span className="contents__num" aria-hidden>
                {nums[i] ?? <LockSimple size={14} />}
              </span>
              <span className="contents__title">
                {b.title}
                {isLocked(b) && <span className="sr-only">, locked</span>}
              </span>
              {!isLocked(b) && (
                <span className="contents__kind">
                  {table ? <Table size={14} aria-hidden /> : <TextAlignLeft size={14} aria-hidden />}
                  {table ? "Table" : "Prose"}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      <p className="tpl__theme">
        <span className="tpl__swatch" style={{ background: tpl.theme.heading_color }} aria-hidden />
        The Word export uses {tpl.theme.font_family} at {tpl.theme.body_size_pt} pt, with headings in this color.
      </p>
    </>
  );
}
