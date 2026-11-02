// Proposal Builder draft: lives in the browser until Generate (spec section 4).
import type { BlockKind, DocumentInfo, FieldError, OutlineBlock, ProposalParameters, Template, TemplateKind } from "../api/types";

export type StepIndex = 0 | 1 | 2 | 3;

export const STEPS = ["Template", "Details", "Outline", "Create"] as const;

export interface Draft {
  step: StepIndex;
  /** Furthest step reached; done steps are clickable in the stepper. */
  reached: StepIndex;
  templateSlug: string | null;
  /** Slug the outline was copied from; a different template replaces the outline. */
  outlineFrom: string | null;
  outline: OutlineBlock[];
  /** True once the user changed the outline copy (reorder, edit, add, delete). */
  outlineEdited: boolean;
  docs: DocumentInfo[];
  client_name: string;
  broker_name: string;
  brokerage_name: string;
  additional_context: string;
  no_ai_prose: boolean;
}

export const EMPTY_DRAFT: Draft = {
  step: 0,
  reached: 0,
  templateSlug: null,
  outlineFrom: null,
  outline: [],
  outlineEdited: false,
  docs: [],
  client_name: "",
  broker_name: "",
  brokerage_name: "",
  additional_context: "",
  no_ai_prose: false,
};

const KEY = "brokerdocs.proposal-draft.v1";

export function loadDraft(): Draft {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return EMPTY_DRAFT;
    const parsed = JSON.parse(raw) as Partial<Draft>;
    return { ...EMPTY_DRAFT, ...parsed };
  } catch {
    return EMPTY_DRAFT;
  }
}

export function saveDraft(d: Draft) {
  try {
    if (isBlank(d)) window.sessionStorage.removeItem(KEY);
    else window.sessionStorage.setItem(KEY, JSON.stringify(d));
  } catch {
    /* storage full or blocked: the draft still lives in memory */
  }
}

export function clearDraft() {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

/** A draft worth warning about: the user chose or typed something. */
export function isBlank(d: Draft): boolean {
  return (
    d.templateSlug == null &&
    d.docs.length === 0 &&
    !d.client_name.trim() &&
    !d.broker_name.trim() &&
    !d.brokerage_name.trim() &&
    !d.additional_context.trim()
  );
}

export function hasDraft(): boolean {
  return !isBlank(loadDraft());
}

// ---------------------------------------------------------------- outline helpers

export const isLocked = (b: Pick<OutlineBlock, "kind">) => b.kind === "cover_page" || b.kind === "table_of_contents";

export function isTableKind(kind: BlockKind, kinds: TemplateKind[]): boolean {
  if (isLocked({ kind })) return false;
  const k = kinds.find((x) => x.kind === kind);
  return k ? k.table : kind !== "narrative";
}

/** Document numbering: locked blocks have none, the rest count 1, 2, 3 (as in the artifact). */
export function sectionNumbers(outline: OutlineBlock[]): (number | null)[] {
  let n = 0;
  return outline.map((b) => (isLocked(b) ? null : ++n));
}

const ID_RE = /^[a-z][a-z0-9_]{0,39}$/;

export function newBlockId(kind: BlockKind, outline: OutlineBlock[]): string {
  const taken = new Set(outline.map((b) => b.id));
  const base = kind.slice(0, 30);
  for (let i = 2; i < 1000; i++) {
    const id = `${base}_${i}`;
    if (!taken.has(id) && ID_RE.test(id)) return id;
  }
  return `${base}_${Date.now() % 100000}`;
}

/** Fact sections that already have a table, mapped to the block that owns it (D4). */
export function tableOwners(outline: OutlineBlock[], kinds: TemplateKind[]): Map<string, string> {
  const owners = new Map<string, string>();
  for (const b of outline) {
    if (!isTableKind(b.kind, kinds)) continue;
    for (const fs of b.fact_sections) if (!owners.has(fs)) owners.set(fs, b.id);
  }
  return owners;
}

export function blockFromKind(kind: TemplateKind, outline: OutlineBlock[], kinds: TemplateKind[]): OutlineBlock {
  const owners = tableOwners(outline, kinds);
  const fact_sections = kind.table ? kind.fact_sections.filter((fs) => !owners.has(fs)) : [...kind.fact_sections];
  return {
    id: newBlockId(kind.kind, outline),
    kind: kind.kind,
    title: kind.title,
    description: kind.description,
    ai_instruction: "",
    fact_sections,
    locked: false,
  };
}

// ---------------------------------------------------------------- validation

/** Errors keyed by path without the "body.parameters." prefix: "outline.3.title", "client_name", "outline". */
export type Errors = Record<string, string>;

export function pathOf(loc: FieldError["loc"]): string {
  const parts = loc.map(String);
  while (parts[0] === "body" || parts[0] === "parameters") parts.shift();
  return parts.join(".");
}

/** Map a 422 `fields` list onto paths. Several messages on one path are joined. */
export function mapServerErrors(fields: FieldError[] | undefined): Errors {
  const out: Errors = {};
  for (const f of fields ?? []) {
    const p = pathOf(f.loc) || "outline";
    out[p] = out[p] ? `${out[p]} ${f.message}` : f.message;
  }
  return out;
}

/** Client mirror of the server outline rules (backend/API.md "Proposal templates", D3, D4). */
export function validateOutline(outline: OutlineBlock[], tpl: Template): Errors {
  const e: Errors = {};
  const L = tpl.limits;
  if (outline.length > L.max_blocks) e.outline = `A proposal can have at most ${L.max_blocks} sections. Delete ${outline.length - L.max_blocks} to continue.`;
  if (!outline.some((b) => !isLocked(b))) e.outline = "Add at least one section besides the cover page and contents.";
  const owners = new Map<string, number>();
  const nums = sectionNumbers(outline);
  outline.forEach((b, i) => {
    const title = b.title.trim();
    if (!isLocked(b)) {
      if (!title) e[`outline.${i}.title`] = "Enter a title for this section.";
      else if (title.length > L.max_title_chars) e[`outline.${i}.title`] = `Shorten the title to ${L.max_title_chars} characters or fewer.`;
      else if (/[\r\n]/.test(b.title)) e[`outline.${i}.title`] = "Keep the title on one line.";
      if (b.ai_instruction.length > L.max_instruction_chars)
        e[`outline.${i}.ai_instruction`] = `Shorten the instruction to ${L.max_instruction_chars} characters or fewer.`;
    }
    const table = isTableKind(b.kind, tpl.kinds);
    if (table && b.fact_sections.length === 0) e[`outline.${i}.fact_sections`] = "Pick at least one fact section for this table.";
    if (b.fact_sections.length > L.max_fact_sections_per_block)
      e[`outline.${i}.fact_sections`] = `Pick at most ${L.max_fact_sections_per_block} fact sections.`;
    if (table) {
      b.fact_sections.forEach((fs, m) => {
        const first = owners.get(fs);
        if (first != null) {
          const fsTitle = tpl.fact_sections.find((x) => x.id === fs)?.title ?? fs;
          e[`outline.${i}.fact_sections.${m}`] =
            `${fsTitle} already has a table in section ${nums[first]}${outline[first]!.title.trim() ? `, ${outline[first]!.title.trim()}` : ""}. A fact section can have only one table.`;
        } else owners.set(fs, i);
      });
    }
  });
  return e;
}

/** Errors that belong to block `i` (any path under outline.i). */
export function blockErrors(errors: Errors, i: number): [string, string][] {
  const prefix = `outline.${i}.`;
  return Object.entries(errors).filter(([k]) => k.startsWith(prefix));
}

export function toParameters(d: Draft): ProposalParameters {
  return {
    template_id: d.templateSlug ?? "",
    client_name: d.client_name.trim(),
    broker_name: d.broker_name.trim(),
    brokerage_name: d.brokerage_name.trim(),
    additional_context: d.additional_context.trim(),
    no_ai_prose: d.no_ai_prose,
    outline: d.outline.map(({ locked: _locked, ...b }) => ({ ...b, title: b.title.trim() })),
  };
}

export const DETAIL_FIELDS = ["client_name", "broker_name", "brokerage_name", "additional_context", "no_ai_prose"] as const;
