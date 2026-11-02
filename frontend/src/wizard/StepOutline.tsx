import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, CaretDown, LockSimple, PencilSimple, Plus, Table, TextAlignLeft, Trash, Warning, WarningCircle } from "@phosphor-icons/react";
import type { OutlineBlock, Template, TemplateKind } from "../api/types";
import { useToast } from "../ui/Toast";
import { blockErrors, blockFromKind, isLocked, isTableKind, sectionNumbers, tableOwners, type Errors } from "./draft";

const FIELD_NAMES: Record<string, string> = {
  title: "Title",
  ai_instruction: "Instruction",
  fact_sections: "Fact sections",
  kind: "Kind",
  id: "Section id",
  description: "Description",
};

const DETAIL_LABELS: Record<string, string> = {
  client_name: "Client name",
  broker_name: "Broker name",
  brokerage_name: "Brokerage name",
  additional_context: "Additional context",
  no_ai_prose: "Plain text option",
  document_ids: "Quote PDFs",
  template_id: "Template",
};

type FocusTarget = { id: string; el: "up" | "down" | "edit" | "title" | "add" | "first" };

export function StepOutline({
  tpl,
  outline,
  edited,
  setOutline,
  onReset,
  errors,
  onFocusField,
  focusKey,
}: {
  tpl: Template;
  outline: OutlineBlock[];
  edited: boolean;
  setOutline: (o: OutlineBlock[]) => void;
  onReset: () => void;
  errors: Errors;
  onFocusField: (path: string) => void;
  focusKey: { path: string; n: number } | null;
}) {
  const toast = useToast();
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [library, setLibrary] = useState(false);
  const [announce, setAnnounce] = useState("");
  const [flash, setFlash] = useState<{ id: string; n: number } | null>(null);
  const pendingFocus = useRef<FocusTarget | null>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const nums = sectionNumbers(outline);
  const owners = tableOwners(outline, tpl.kinds);
  const firstMovable = outline.findIndex((b) => !isLocked(b));
  const errorCount = Object.keys(errors).length;

  // Focus follows the moved, added or removed block (keyboard users keep their place).
  useLayoutEffect(() => {
    const t = pendingFocus.current;
    if (!t) return;
    pendingFocus.current = null;
    if (t.el === "add") return addRef.current?.focus();
    const el = document.querySelector<HTMLElement>(`[data-focus="${t.id}:${t.el}"]`);
    if (el && !(el as HTMLButtonElement).disabled) return el.focus();
    const other = t.el === "up" ? "down" : t.el === "down" ? "up" : "edit";
    document.querySelector<HTMLElement>(`[data-focus="${t.id}:${other}"]`)?.focus();
  });

  // Moved-row cue: the tint fades out (opacity only, so reduced motion keeps it; DESIGN.md motion).
  useEffect(() => {
    if (!flash) return;
    const li = document.querySelector<HTMLElement>(`[data-block="${flash.id}"]`);
    li?.animate?.([{ opacity: 1 }, { opacity: 0 }], { duration: 300, easing: "cubic-bezier(0.23, 1, 0.32, 1)", pseudoElement: "::after" });
  }, [flash]);

  // Error summary takes focus when errors appear after Generate.
  const prevCount = useRef(0);
  useEffect(() => {
    if (errorCount > 0 && prevCount.current === 0) {
      // Open every block that has a field error so the inline message is visible.
      setOpen((s) => {
        const next = new Set(s);
        for (const k of Object.keys(errors)) {
          const m = /^outline\.(\d+)\./.exec(k);
          const b = m ? outline[Number(m[1])] : undefined;
          if (b) next.add(b.id);
        }
        return next;
      });
      window.setTimeout(() => summaryRef.current?.focus(), 0);
    }
    prevCount.current = errorCount;
  }, [errorCount, errors, outline]);

  // Jump to a field from the error summary.
  useEffect(() => {
    if (!focusKey) return;
    const m = /^outline\.(\d+)\.?([a-z_]*)\.?(\d*)/.exec(focusKey.path);
    if (!m) return;
    const b = outline[Number(m[1])];
    if (!b) return;
    setOpen((s) => new Set(s).add(b.id));
    const field = m[2] || "title";
    window.setTimeout(() => {
      let el: HTMLElement | null = null;
      if (field === "fact_sections") {
        const fs = m[3] ? b.fact_sections[Number(m[3])] : undefined;
        el = (fs && document.getElementById(`fs-${b.id}-${fs}`)) || document.querySelector<HTMLElement>(`#f-${b.id}-fact_sections input`);
      } else el = document.getElementById(`f-${b.id}-${field}`);
      (el ?? document.querySelector<HTMLElement>(`[data-focus="${b.id}:edit"]`))?.focus();
    }, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey]);

  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < firstMovable || j >= outline.length) return;
    const next = [...outline];
    const [b] = next.splice(i, 1);
    next.splice(j, 0, b!);
    pendingFocus.current = { id: b!.id, el: dir < 0 ? "up" : "down" };
    setOutline(next);
    const pos = sectionNumbers(next)[j];
    setAnnounce(`${b!.title || "Section"} moved to position ${pos} of ${next.filter((x) => !isLocked(x)).length}.`);
    setFlash((f) => ({ id: b!.id, n: (f?.n ?? 0) + 1 }));
  };

  const remove = (i: number) => {
    const before = outline;
    const b = outline[i]!;
    const next = outline.filter((_, k) => k !== i);
    const neighbour = next[i] ?? next[i - 1];
    pendingFocus.current = neighbour && !isLocked(neighbour) ? { id: neighbour.id, el: "edit" } : { id: "", el: "add" };
    setOutline(next);
    setAnnounce(`${b.title || "Section"} removed.`);
    toast({
      message: `Removed ${b.title || "section"}.`,
      action: {
        label: "Undo",
        run: () => {
          pendingFocus.current = { id: b.id, el: "edit" };
          setOutline(before);
        },
      },
    });
  };

  const patch = (i: number, p: Partial<OutlineBlock>) => setOutline(outline.map((b, k) => (k === i ? { ...b, ...p } : b)));

  const add = (kind: TemplateKind) => {
    const b = blockFromKind(kind, outline, tpl.kinds);
    setOutline([...outline, b]);
    setOpen((s) => new Set(s).add(b.id));
    setLibrary(false);
    pendingFocus.current = { id: b.id, el: "title" };
    setAnnounce(`${b.title} added as section ${outline.filter((x) => !isLocked(x)).length + 1}.`);
  };

  const toggle = (id: string) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const full = outline.length >= tpl.limits.max_blocks;

  return (
    <div className="outline-step">
      <div className="outline-step__intro">
        <p className="hint">
          These are the sections of the proposal, numbered as they will appear. Tables are built from the verified values; the AI writes the text around them.
        </p>
        {edited && (
          <button type="button" className="link-btn" onClick={onReset}>
            Reset to the {tpl.name} outline
          </button>
        )}
      </div>

      {errorCount > 0 && (
        <div className="error-summary" role="alert" tabIndex={-1} ref={summaryRef} aria-labelledby="error-summary-title">
          <p id="error-summary-title" className="error-summary__title">
            <WarningCircle size={18} aria-hidden />
            {errorCount === 1 ? "Fix 1 problem to generate the proposal" : `Fix ${errorCount} problems to generate the proposal`}
          </p>
          <ul>
            {Object.entries(errors).map(([path, msg]) => (
              <li key={path}>
                <ErrorLink path={path} msg={msg} outline={outline} nums={nums} onGo={onFocusField} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <ol className="blocks" aria-label="Proposal outline">
        {outline.map((b, i) => {
          const locked = isLocked(b);
          const errs = blockErrors(errors, i);
          const isOpen = open.has(b.id);
          const table = isTableKind(b.kind, tpl.kinds);
          const num = nums[i];
          return (
            <li
              key={b.id}
              className={`block${locked ? " is-locked" : ""}${errs.length ? " has-error" : ""}${isOpen ? " is-open" : ""}`}
              data-block={b.id}
            >
              <div className="block__row">
                <span className="block__num" aria-hidden>
                  {locked ? <LockSimple size={16} /> : num}
                </span>
                <div className="block__main">
                  <span className="block__title" id={`t-${b.id}`}>
                    <span className="sr-only">{locked ? "Locked: " : `Section ${num}: `}</span>
                    {b.title || <span className="block__untitled">Untitled section</span>}
                  </span>
                  <span className="block__meta">
                    {locked ? (
                      <span className="block__kind">{b.kind === "cover_page" ? "Locked. Built from the title and your details." : "Locked. Lists the numbered sections."}</span>
                    ) : (
                      <>
                        <span className="block__kind">
                          {table ? <Table size={14} aria-hidden /> : <TextAlignLeft size={14} aria-hidden />}
                          {table ? (b.fact_sections.length > 1 ? `${b.fact_sections.length} tables` : "Table") : "Prose"}
                        </span>
                        {b.fact_sections.map((fs) => (
                          <span key={fs} className="tag">
                            {tpl.fact_sections.find((x) => x.id === fs)?.title ?? fs}
                          </span>
                        ))}
                      </>
                    )}
                    {errs.length > 0 && (
                      <span className="block__flag">
                        <Warning size={14} aria-hidden /> Needs a fix
                      </span>
                    )}
                  </span>
                </div>
                {!locked && (
                  <div className="block__actions">
                    <button
                      type="button"
                      className="icon-btn"
                      data-focus={`${b.id}:up`}
                      aria-label={`Move ${b.title || "section"} up`}
                      disabled={i <= firstMovable}
                      onClick={() => move(i, -1)}
                    >
                      <ArrowUp size={16} aria-hidden />
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      data-focus={`${b.id}:down`}
                      aria-label={`Move ${b.title || "section"} down`}
                      disabled={i >= outline.length - 1}
                      onClick={() => move(i, 1)}
                    >
                      <ArrowDown size={16} aria-hidden />
                    </button>
                    <button
                      type="button"
                      className="btn btn--secondary btn--sm"
                      data-focus={`${b.id}:edit`}
                      aria-expanded={isOpen}
                      aria-controls={`ed-${b.id}`}
                      aria-describedby={`t-${b.id}`}
                      onClick={() => toggle(b.id)}
                    >
                      <PencilSimple size={16} aria-hidden /> {isOpen ? "Done" : "Edit"}
                    </button>
                    <button type="button" className="icon-btn" aria-label={`Delete ${b.title || "section"}`} onClick={() => remove(i)}>
                      <Trash size={16} aria-hidden />
                    </button>
                  </div>
                )}
              </div>
              {!locked && isOpen && (
                <BlockEditor block={b} index={i} tpl={tpl} owners={owners} outline={outline} errors={errors} onPatch={(p) => patch(i, p)} />
              )}
            </li>
          );
        })}
      </ol>

      <div className="add-section">
        <button
          type="button"
          ref={addRef}
          className="btn btn--secondary"
          aria-expanded={library}
          aria-controls="kind-library"
          disabled={full}
          onClick={() => setLibrary((l) => !l)}
        >
          <Plus size={16} aria-hidden /> Add section
          <CaretDown size={14} aria-hidden className={`add-section__caret${library ? " is-open" : ""}`} />
        </button>
        {full && <p className="hint">The outline has {tpl.limits.max_blocks} blocks, the most a proposal can have.</p>}
        {library && (
          <ul id="kind-library" className="kinds" aria-label="Section types">
            {tpl.kinds.map((k) => (
              <li key={k.kind} className="kind">
                <div className="kind__text">
                  <span className="kind__title">
                    {k.table ? <Table size={16} aria-hidden /> : <TextAlignLeft size={16} aria-hidden />}
                    {k.title}
                  </span>
                  <span className="hint">{k.description}</span>
                </div>
                <button type="button" className="btn btn--secondary btn--sm" onClick={() => add(k)} aria-label={`Add ${k.title}`}>
                  Add
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>
    </div>
  );
}

function ErrorLink({
  path,
  msg,
  outline,
  nums,
  onGo,
}: {
  path: string;
  msg: string;
  outline: OutlineBlock[];
  nums: (number | null)[];
  onGo: (path: string) => void;
}) {
  const m = /^outline\.(\d+)\.?([a-z_]*)/.exec(path);
  if (m) {
    const i = Number(m[1]);
    const b = outline[i];
    const where = b ? `${nums[i] != null ? `Section ${nums[i]}` : "Locked block"}, ${b.title || "untitled"}` : `Block ${i + 1}`;
    const field = FIELD_NAMES[m[2] || ""] ?? "";
    return (
      <button type="button" className="link-btn" onClick={() => onGo(path)}>
        {where}
        {field ? `, ${field.toLowerCase()}` : ""}: {msg}
      </button>
    );
  }
  const label = DETAIL_LABELS[path];
  if (label) {
    return (
      <button type="button" className="link-btn" onClick={() => onGo(path)}>
        {label}: {msg}
      </button>
    );
  }
  return <span>{msg}</span>;
}

function BlockEditor({
  block,
  index,
  tpl,
  owners,
  outline,
  errors,
  onPatch,
}: {
  block: OutlineBlock;
  index: number;
  tpl: Template;
  owners: Map<string, string>;
  outline: OutlineBlock[];
  errors: Errors;
  onPatch: (p: Partial<OutlineBlock>) => void;
}) {
  const L = tpl.limits;
  const table = isTableKind(block.kind, tpl.kinds);
  const e = (f: string) => errors[`outline.${index}.${f}`];
  const titleErr = e("title");
  const instrErr = e("ai_instruction");
  const fsErr = e("fact_sections");
  const id = block.id;
  const kindTitle = tpl.kinds.find((k) => k.kind === block.kind)?.title;

  const toggleFs = (fs: string) =>
    onPatch({ fact_sections: block.fact_sections.includes(fs) ? block.fact_sections.filter((x) => x !== fs) : [...block.fact_sections, fs] });

  return (
    <div className="block__editor" id={`ed-${id}`}>
      {block.description && <p className="hint">{block.description}</p>}
      <div className="field">
        <div className="field__row">
          <label htmlFor={`f-${id}-title`} className="field__label">
            Title
          </label>
          <span className="counter" id={`c-${id}-title`}>
            {block.title.length} / {L.max_title_chars}
          </span>
        </div>
        <input
          id={`f-${id}-title`}
          data-focus={`${id}:title`}
          className="input"
          value={block.title}
          maxLength={L.max_title_chars}
          onChange={(ev) => onPatch({ title: ev.target.value.replace(/[\r\n]+/g, " ") })}
          aria-invalid={titleErr ? true : undefined}
          aria-describedby={`c-${id}-title${titleErr ? ` e-${id}-title` : ""}`}
        />
        {titleErr && (
          <p id={`e-${id}-title`} className="field__error">
            <WarningCircle size={16} aria-hidden /> {titleErr}
          </p>
        )}
      </div>

      <div className="field">
        <div className="field__row">
          <label htmlFor={`f-${id}-ai_instruction`} className="field__label">
            Instruction for the AI <span className="field__optional">optional</span>
          </label>
          <span className="counter" id={`c-${id}-ai_instruction`}>
            {block.ai_instruction.length} / {L.max_instruction_chars}
          </span>
        </div>
        <textarea
          id={`f-${id}-ai_instruction`}
          className="input textarea"
          rows={3}
          value={block.ai_instruction}
          maxLength={L.max_instruction_chars}
          onChange={(ev) => onPatch({ ai_instruction: ev.target.value })}
          aria-invalid={instrErr ? true : undefined}
          aria-describedby={`h-${id}-ai_instruction c-${id}-ai_instruction${instrErr ? ` e-${id}-ai_instruction` : ""}`}
        />
        <p id={`h-${id}-ai_instruction`} className="field__hint">
          What this section's text should cover. It can only use values from this section's fact sections.
        </p>
        {instrErr && (
          <p id={`e-${id}-ai_instruction`} className="field__error">
            <WarningCircle size={16} aria-hidden /> {instrErr}
          </p>
        )}
      </div>

      <fieldset className="facts" id={`f-${id}-fact_sections`} aria-describedby={`h-${id}-fs${fsErr ? ` e-${id}-fs` : ""}`}>
        <legend className="field__label">{table ? "Tables in this section" : "Facts this section can use"}</legend>
        <p id={`h-${id}-fs`} className="field__hint">
          {table
            ? `${kindTitle ?? "This section"} shows one table per fact section. A fact section can have only one table in the proposal.`
            : "The text may cite values from these fact sections. Pick none for text without figures."}
        </p>
        {fsErr && (
          <p id={`e-${id}-fs`} className="field__error">
            <WarningCircle size={16} aria-hidden /> {fsErr}
          </p>
        )}
        <ul className="facts__list">
          {tpl.fact_sections.map((fs) => {
            const on = block.fact_sections.includes(fs.id);
            const m = block.fact_sections.indexOf(fs.id);
            const itemErr = on ? errors[`outline.${index}.fact_sections.${m}`] : undefined;
            const owner = owners.get(fs.id);
            const elsewhere = table && owner && owner !== id ? outline.find((x) => x.id === owner) : undefined;
            const hintId = `fh-${id}-${fs.id}`;
            return (
              <li key={fs.id} className={`fact${itemErr ? " has-error" : ""}`}>
                <input
                  type="checkbox"
                  id={`fs-${id}-${fs.id}`}
                  checked={on}
                  onChange={() => toggleFs(fs.id)}
                  aria-invalid={itemErr ? true : undefined}
                  aria-describedby={hintId}
                />
                <div className="fact__text">
                  <label htmlFor={`fs-${id}-${fs.id}`} className="fact__name">
                    {fs.title}
                  </label>
                  <span id={hintId} className="fact__fields">
                    {fs.fields.map((f) => f.label).join(", ")}
                    {elsewhere && !itemErr ? `. Has a table in ${elsewhere.title || "another section"}.` : ""}
                    {itemErr ? ` ${itemErr}` : ""}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      </fieldset>
    </div>
  );
}
