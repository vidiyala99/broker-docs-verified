import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowRight } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import type { FieldError, OutlineBlock, Template, TemplateSummary, Workspace } from "../api/types";
import { useToast } from "../ui/Toast";
import type { Viewport } from "../shell/layout";
import {
  clearDraft,
  DETAIL_FIELDS,
  EMPTY_DRAFT,
  isBlank,
  isLocked,
  loadDraft,
  mapServerErrors,
  saveDraft,
  STEPS,
  toParameters,
  validateOutline,
  type Draft,
  type Errors,
  type StepIndex,
} from "./draft";
import { Stepper } from "./Stepper";
import { StepTemplate } from "./StepTemplate";
import { StepDetails } from "./StepDetails";
import { StepOutline } from "./StepOutline";
import "./wizard.css";

let mounted = 0;

const copyBlocks =(blocks: OutlineBlock[]) => blocks.map((b) => ({ ...b, fact_sections: [...b.fact_sections] }));

/** T7: the Proposal Builder wizard. Generate posts a proposal-builder run and opens the run view (step 4). */
export function ProposalWizard({ workspace, viewport }: { workspace: Workspace; viewport: Viewport }) {
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>(loadDraft);
  const [summaries, setSummaries] = useState<TemplateSummary[] | null>(null);
  const [templates, setTemplates] = useState<Record<string, Template>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [stepError, setStepError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Errors>({});
  const [submitted, setSubmitted] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [uploadsBusy, setUploadsBusy] = useState(false);
  const [focusKey, setFocusKey] = useState<{ path: string; n: number } | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const generated = useRef(false);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  // ------------------------------------------------------------ templates
  const loadTemplates = useCallback(async () => {
    setLoadError(null);
    try {
      const list = await api.templates();
      setSummaries(list);
      const full = await Promise.all(list.map((t) => api.template(t.slug)));
      setTemplates(Object.fromEntries(full.map((t) => [t.slug, t])));
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : "Couldn't load the templates.");
    }
  }, []);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  const tpl = draft.templateSlug ? templates[draft.templateSlug] ?? null : null;

  // ------------------------------------------------------------ draft persistence and leave warning
  useEffect(() => saveDraft(draft), [draft]);

  useEffect(() => {
    const onUnload = (e: BeforeUnloadEvent) => {
      if (generated.current || isBlank(draftRef.current)) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, []);

  useEffect(() => {
    mounted += 1;
    return () => {
      mounted -= 1;
      // Leaving the wizard in-app keeps the draft (sessionStorage); say so, with a way back.
      // Deferred so a StrictMode remount (dev) does not count as leaving.
      window.setTimeout(() => {
        if (mounted > 0 || generated.current || isBlank(draftRef.current)) return;
        toast({ message: "Proposal draft kept.", action: { label: "Back to draft", run: () => (window.location.hash = "#/proposals/new") } });
      }, 0);
    };
  }, [toast]);

  const update = useCallback((patch: Partial<Draft>) => {
    setStepError(null);
    setDraft((d) => ({ ...d, ...patch }));
    // Editing a field clears its error (server or client); the next Generate checks again.
    const keys = Object.keys(patch).map((k) => (k === "docs" ? "document_ids" : k));
    setErrors((e) => (keys.some((k) => k in e) ? Object.fromEntries(Object.entries(e).filter(([k]) => !keys.includes(k))) : e));
  }, []);

  const setOutline = useCallback(
    (next: OutlineBlock[]) => {
      setDraft((d) => ({ ...d, outline: next, outlineEdited: true }));
    },
    [],
  );

  // Revalidate live once the user has tried to generate; server errors are dropped on any change.
  useEffect(() => {
    if (submitted && tpl) setErrors((prev) => {
      const next = validateOutline(draft.outline, tpl);
      for (const k of Object.keys(prev)) if ((DETAIL_FIELDS as readonly string[]).includes(k)) next[k] = prev[k]!;
      return next;
    });
  }, [draft.outline, submitted, tpl]);

  // Copy the template's blocks into the draft when a template is chosen (or the outline came from another one).
  useEffect(() => {
    if (!tpl || draft.outlineFrom === tpl.slug) return;
    setDraft((d) => ({ ...d, outline: copyBlocks(tpl.blocks), outlineFrom: tpl.slug, outlineEdited: false }));
  }, [tpl, draft.outlineFrom]);

  const chooseTemplate = (slug: string) => {
    const prev = draftRef.current;
    if (prev.templateSlug === slug) return;
    setStepError(null);
    setDraft((d) => ({ ...d, templateSlug: slug }));
    if (prev.outlineEdited && prev.outlineFrom && prev.outlineFrom !== slug) {
      const name = templates[slug]?.name ?? "the new template";
      toast({
        message: `Outline replaced with the ${name} sections.`,
        action: {
          label: "Undo",
          run: () =>
            setDraft((d) => ({ ...d, templateSlug: prev.templateSlug, outline: prev.outline, outlineFrom: prev.outlineFrom, outlineEdited: true })),
        },
      });
    }
  };

  // ------------------------------------------------------------ navigation
  const goTo = useCallback((step: StepIndex, focusHeading = true) => {
    setStepError(null);
    setDraft((d) => ({ ...d, step, reached: (Math.max(d.reached, step) as StepIndex) }));
    if (focusHeading) window.setTimeout(() => headingRef.current?.focus(), 0);
  }, []);

  const next = () => {
    if (draft.step === 0) {
      if (!draft.templateSlug) return setStepError("Choose a template to continue.");
      return goTo(1);
    }
    if (draft.step === 1) {
      if (uploadsBusy) return setStepError("Wait for the uploads to finish.");
      if (!draft.docs.length) return setStepError("Add at least one quote PDF.");
      return goTo(2);
    }
  };

  const focusField = (path: string) => {
    const detail = (DETAIL_FIELDS as readonly string[]).includes(path) || path === "document_ids";
    if (detail && draft.step !== 1) goTo(1, false);
    setFocusKey((f) => ({ path, n: (f?.n ?? 0) + 1 }));
  };

  // ------------------------------------------------------------ generate (step 4)
  const generate = async () => {
    if (!tpl) return goTo(0);
    setSubmitted(true);
    const clientErrors = validateOutline(draft.outline, tpl);
    if (!draft.docs.length) clientErrors.document_ids = "Add at least one quote PDF.";
    setErrors(clientErrors);
    if (Object.keys(clientErrors).length) return;
    setGenerating(true);
    try {
      const res = await api.startProposal(
        draft.docs.map((d) => d.id),
        toParameters(draft),
      );
      generated.current = true;
      clearDraft();
      setDraft(EMPTY_DRAFT);
      window.location.hash = `#/runs/${res.run.id}`;
    } catch (e) {
      if (e instanceof ApiError && e.code === "invalid_request") {
        const mapped = mapServerErrors(e.extra.fields as FieldError[] | undefined);
        setErrors(Object.keys(mapped).length ? mapped : { outline: e.message });
      } else if (e instanceof ApiError && e.code === "invalid_run") {
        setErrors({ document_ids: e.message });
      } else {
        setErrors({ _: e instanceof ApiError ? e.message : "Couldn't start the proposal. Retry." });
      }
    } finally {
      setGenerating(false);
    }
  };

  const discard = () => {
    const prev = draftRef.current;
    clearDraft();
    setDraft(EMPTY_DRAFT);
    setErrors({});
    setSubmitted(false);
    toast({ message: "Draft discarded.", action: { label: "Undo", run: () => setDraft(prev) } });
  };

  const sections = draft.outline.filter((b) => !isLocked(b)).length;
  const summary = useMemo(() => {
    const parts: string[] = [];
    if (tpl) parts.push(tpl.name);
    if (draft.docs.length) parts.push(`${draft.docs.length} ${draft.docs.length === 1 ? "quote" : "quotes"}`);
    if (tpl && draft.step >= 2) parts.push(`${sections} ${sections === 1 ? "section" : "sections"}`);
    return parts.join(", ");
  }, [tpl, draft.docs.length, draft.step, sections]);

  const stepName = STEPS[draft.step];

  return (
    <div className="workspace">
      <main className="pane wizard" id="main" tabIndex={-1} aria-labelledby="wizard-title">
        <header className="wizard__head">
          <h1 id="wizard-title" className="wizard__title">
            New proposal
          </h1>
          <Stepper step={draft.step} reached={draft.reached} onGo={goTo} compact={viewport === "narrow"} />
        </header>

        <div className="wizard__body">
          <section className="wizard__step" aria-labelledby="step-heading">
            <h2 id="step-heading" ref={headingRef} tabIndex={-1} className="wizard__step-title">
              <span className="sr-only">
                Step {draft.step + 1} of {STEPS.length},{" "}
              </span>
              {draft.step === 0 ? "Choose a template" : draft.step === 1 ? "Add quotes and details" : "Edit the outline"}
            </h2>

            {loadError ? (
              <div className="notice notice--error" role="alert">
                <span className="notice__text">{loadError}</span>
                <button type="button" className="btn btn--secondary btn--sm" onClick={() => void loadTemplates()}>
                  Retry
                </button>
              </div>
            ) : draft.step === 0 ? (
              <StepTemplate summaries={summaries} templates={templates} selected={draft.templateSlug} onSelect={chooseTemplate} />
            ) : draft.step === 1 ? (
              <StepDetails
                draft={draft}
                update={update}
                workspace={workspace}
                limits={tpl?.limits ?? null}
                errors={errors}
                onBusy={setUploadsBusy}
                focusKey={focusKey}
              />
            ) : tpl ? (
              <StepOutline
                tpl={tpl}
                outline={draft.outline}
                edited={draft.outlineEdited}
                setOutline={setOutline}
                onReset={() => setDraft((d) => ({ ...d, outline: copyBlocks(tpl.blocks), outlineEdited: false }))}
                errors={errors}
                onFocusField={focusField}
                focusKey={focusKey}
              />
            ) : (
              <div className="skeleton-lines" aria-hidden>
                <span />
                <span />
                <span />
              </div>
            )}
          </section>
        </div>

        <footer className="wizard__foot">
          {draft.step > 0 ? (
            <button type="button" className="btn btn--secondary" onClick={() => goTo((draft.step - 1) as StepIndex)} disabled={generating}>
              <ArrowLeft size={16} aria-hidden /> Back
            </button>
          ) : (
            <a href="#/" className="btn btn--ghost">
              Cancel
            </a>
          )}
          <div className="wizard__foot-mid">
            {stepError ? (
              <p className="inline-error" role="alert">
                {stepError}
              </p>
            ) : (
              summary && (
                <p className="wizard__summary" aria-label={`Draft: ${summary}`}>
                  {summary}
                </p>
              )
            )}
            {!isBlank(draft) && (
              <button type="button" className="link-btn link-btn--small wizard__discard" onClick={discard} disabled={generating}>
                Discard draft
              </button>
            )}
          </div>
          {draft.step < 2 ? (
            <button type="button" className="btn btn--primary" onClick={next} disabled={!summaries}>
              Continue <ArrowRight size={16} aria-hidden />
            </button>
          ) : (
            <button type="button" className="btn btn--primary" onClick={() => void generate()} disabled={generating}>
              {generating ? (
                <>
                  <span className="spinner spinner--sm spinner--on-primary" aria-hidden /> Generating...
                </>
              ) : (
                "Generate proposal"
              )}
            </button>
          )}
        </footer>
        <p className="sr-only" role="status" aria-live="polite">
          {generating ? "Generating the proposal." : `${stepName} step.`}
        </p>
      </main>
    </div>
  );
}
