import { useEffect, useId, useRef, useState } from "react";
import { ArrowLeft, ArrowRight } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import type { DocumentInfo, Holder, Wording, Workspace } from "../api/types";
import type { Viewport } from "../shell/layout";
import { useStepTransition } from "../ui/motion";
import { Stepper } from "../wizard/Stepper";
import { Glyph } from "../voice/marks";
import { formatDate, LIMITS, MAX_POLICY_PDFS, todayIso, WORDING_LABEL, WORDINGS } from "./certUtil";
import { PolicyPicker } from "./PolicyPicker";
import "../wizard/wizard.css";
import "./certificates.css";

const STEPS = ["Who", "Policies", "Read"] as const;
type Step = 0 | 1 | 2;
type FieldName = "client" | "holder_name" | "holder_address" | "certificate_date" | "description";

/** The certificate wizard (spec section 2 steps 1 and 2): Who, Policies, then Read opens the run (progress, then the draft). */
export function CertificateWizard({ workspace, viewport, client: preset }: { workspace: Workspace; viewport: Viewport; client: string | null }) {
  const [step, setStep] = useState<Step>(0);
  const [reached, setReached] = useState<Step>(0);
  const [client, setClient] = useState(preset ?? "");
  const [holderPick, setHolderPick] = useState<number | "new">("new");
  const [holderName, setHolderName] = useState("");
  const [holderAddress, setHolderAddress] = useState("");
  const [wording, setWording] = useState<Record<Wording, boolean>>({ additional_insured: false, waiver_of_subrogation: false, primary_noncontributory: false });
  const [date, setDate] = useState(todayIso());
  const [description, setDescription] = useState("");
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<FieldName, string>>>({});
  const [stepError, setStepError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [holders, setHolders] = useState<Holder[] | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const stepRef = useRef<HTMLElement>(null);
  useStepTransition(stepRef, step);
  const id = useId();

  useEffect(() => setClient(preset ?? ""), [preset]);

  // Past holders for this client, last used first (C2); offered as picks above the holder fields.
  useEffect(() => {
    const name = client.trim();
    if (!name) {
      setHolders(null);
      return;
    }
    let live = true;
    const t = window.setTimeout(() => api.holders(name).then((h) => live && setHolders(h), () => live && setHolders([])), 250);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [client]);

  const pickHolder = (h: Holder | "new") => {
    setHolderPick(h === "new" ? "new" : h.id);
    setErrors((e) => ({ ...e, holder_name: undefined, holder_address: undefined }));
    if (h === "new") {
      setHolderName("");
      setHolderAddress("");
    } else {
      setHolderName(h.name);
      setHolderAddress(h.address);
    }
  };

  const go = (s: Step) => {
    setStep(s);
    setReached((r) => (s > r ? s : r));
    setStepError(null);
    window.setTimeout(() => headingRef.current?.focus(), 0);
  };

  const validateWho = () => {
    const e: Partial<Record<FieldName, string>> = {};
    if (!holderName.trim()) e.holder_name = "Enter the certificate holder's name.";
    else if (holderName.trim().length > LIMITS.holder_name) e.holder_name = `At most ${LIMITS.holder_name} characters.`;
    if (holderAddress.length > LIMITS.holder_address) e.holder_address = `At most ${LIMITS.holder_address} characters.`;
    if (description.length > LIMITS.description) e.description = `At most ${LIMITS.description} characters.`;
    if (!date) e.certificate_date = "Enter the certificate date.";
    setErrors(e);
    const first = Object.keys(e)[0];
    if (first) window.setTimeout(() => document.getElementById(`${id}-${first}`)?.focus(), 0);
    return !first;
  };

  const start = async () => {
    if (uploading) return setStepError("Wait for the uploads to finish.");
    if (!docs.length) return setStepError("Add at least one policy: its declarations page, and any endorsements.");
    setStarting(true);
    setStepError(null);
    try {
      const res = await api.startCertificate(
        docs.map((d) => d.id),
        {
          holder_name: holderName.trim(),
          holder_address: holderAddress.trim(),
          wording,
          certificate_date: date,
          description: description.trim(),
        },
        client,
      );
      window.location.hash = `#/runs/${res.run.id}`;
    } catch (e) {
      // D6: a bound the server enforces names its field; put the message on that field and go back to it.
      const fields = e instanceof ApiError ? (e.extra.fields as { loc: (string | number)[]; msg?: string }[] | undefined) : undefined;
      const field = fields?.[0]?.loc?.[2] as FieldName | undefined;
      if (e instanceof ApiError && field && field !== "client") {
        setErrors({ [field]: e.message });
        go(0);
      } else setStepError(e instanceof ApiError ? e.message : "Couldn't start the certificate. Retry.");
    } finally {
      setStarting(false);
    }
  };

  const fieldErr = (f: FieldName) =>
    errors[f] ? (
      <p id={`${id}-${f}-err`} className="inline-error" role="alert">
        {errors[f]}
      </p>
    ) : null;
  const described = (f: FieldName, hint?: string) => [hint, errors[f] ? `${id}-${f}-err` : null].filter(Boolean).join(" ") || undefined;

  return (
    <div className="workspace">
      <main className="pane wizard" id="main" tabIndex={-1} aria-labelledby="wizard-title">
        <header className="wizard__head">
          <h1 id="wizard-title" className="wizard__title">
            <Glyph name="certificate" size={34} />
            New certificate
          </h1>
          <Stepper step={step} reached={reached} onGo={(s) => (s < step || s <= reached) && s < 2 && go(s as Step)} compact={viewport === "narrow"} steps={STEPS} label="Certificate steps" />
        </header>

        <div className="wizard__body">
          <section ref={stepRef} className="wizard__step" aria-labelledby="step-heading">
            <h2 id="step-heading" ref={headingRef} tabIndex={-1} className="wizard__step-title">
              <span className="sr-only">
                Step {step + 1} of {STEPS.length},{" "}
              </span>
              {step === 0 ? "Who the certificate is for" : "The policies to certify"}
            </h2>
            {step === 0 ? (
              <div className="details cwho">
                <div className="field">
                  <label htmlFor={`${id}-client`} className="field__label">
                    Client
                  </label>
                  <p className="hint" id={`${id}-client-hint`}>
                    The insured. Their past holders and documents are offered below.
                  </p>
                  <input
                    id={`${id}-client`}
                    className="input"
                    value={client}
                    maxLength={120}
                    autoComplete="organization"
                    aria-describedby={`${id}-client-hint`}
                    onChange={(e) => {
                      setClient(e.target.value);
                      setHolderPick("new");
                    }}
                  />
                </div>

                <fieldset className="field cwho__holder">
                  <legend className="group-title">Certificate holder</legend>
                  {holders && holders.length > 0 && (
                    <div className="cholders" role="radiogroup" aria-label="Past holders">
                      <p className="hint">Past holders for {client.trim()}, last used first.</p>
                      {holders.map((h) => (
                        <label key={h.id} className={`cholder${holderPick === h.id ? " is-on" : ""}`}>
                          <input type="radio" name={`${id}-holder`} checked={holderPick === h.id} onChange={() => pickHolder(h)} />
                          <span className="cholder__main">
                            <span className="cholder__name">{h.name}</span>
                            <span className="cholder__meta">
                              {h.address ? `${h.address.split("\n").join(", ")}. ` : ""}Last used {formatDate(h.last_used_at)}
                            </span>
                          </span>
                        </label>
                      ))}
                      <label className={`cholder${holderPick === "new" ? " is-on" : ""}`}>
                        <input type="radio" name={`${id}-holder`} checked={holderPick === "new"} onChange={() => pickHolder("new")} />
                        <span className="cholder__main">
                          <span className="cholder__name cholder__name--new">New holder</span>
                        </span>
                      </label>
                    </div>
                  )}
                  <div className="field">
                    <label htmlFor={`${id}-holder_name`} className="field__label">
                      Name
                    </label>
                    <input
                      id={`${id}-holder_name`}
                      className="input"
                      value={holderName}
                      maxLength={LIMITS.holder_name}
                      aria-invalid={errors.holder_name ? true : undefined}
                      aria-describedby={described("holder_name")}
                      onChange={(e) => {
                        setHolderName(e.target.value);
                        setErrors((x) => ({ ...x, holder_name: undefined }));
                      }}
                    />
                    {fieldErr("holder_name")}
                  </div>
                  <div className="field">
                    <label htmlFor={`${id}-holder_address`} className="field__label">
                      Address <span className="field__optional">optional</span>
                    </label>
                    <textarea
                      id={`${id}-holder_address`}
                      className="input textarea"
                      rows={2}
                      value={holderAddress}
                      maxLength={LIMITS.holder_address}
                      aria-invalid={errors.holder_address ? true : undefined}
                      aria-describedby={described("holder_address")}
                      onChange={(e) => setHolderAddress(e.target.value)}
                    />
                    {fieldErr("holder_address")}
                  </div>
                </fieldset>

                <fieldset className="field cwho__wording">
                  <legend className="group-title">Wording the holder asked for</legend>
                  <p className="hint">Each one is checked against the endorsements before you can issue.</p>
                  {WORDINGS.map((w) => (
                    <label key={w} className="ccheck">
                      <input type="checkbox" checked={wording[w]} onChange={(e) => setWording((x) => ({ ...x, [w]: e.target.checked }))} />
                      {WORDING_LABEL[w]}
                    </label>
                  ))}
                </fieldset>

                <div className="field cwho__date">
                  <label htmlFor={`${id}-certificate_date`} className="field__label">
                    Certificate date
                  </label>
                  <p className="hint" id={`${id}-date-hint`}>
                    Each policy must be in force on this date.
                  </p>
                  <input
                    id={`${id}-certificate_date`}
                    type="date"
                    className="input"
                    value={date}
                    aria-invalid={errors.certificate_date ? true : undefined}
                    aria-describedby={described("certificate_date", `${id}-date-hint`)}
                    onChange={(e) => setDate(e.target.value)}
                  />
                  {fieldErr("certificate_date")}
                </div>

                <div className="field">
                  <label htmlFor={`${id}-description`} className="field__label">
                    Description of operations <span className="field__optional">optional</span>
                  </label>
                  <p className="hint" id={`${id}-desc-hint`}>
                    Printed as typed. You confirm it before issue. {description.length}/{LIMITS.description}
                  </p>
                  <textarea
                    id={`${id}-description`}
                    className="input textarea"
                    rows={3}
                    value={description}
                    maxLength={LIMITS.description}
                    aria-invalid={errors.description ? true : undefined}
                    aria-describedby={described("description", `${id}-desc-hint`)}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                  {fieldErr("description")}
                </div>
              </div>
            ) : (
              <div className="details">
                <p className="hint">Declarations pages and endorsements, up to {MAX_POLICY_PDFS}. One declarations document is one line of coverage.</p>
                <PolicyPicker workspace={workspace} client={client} selected={docs} onChange={setDocs} max={MAX_POLICY_PDFS} onBusy={setUploading} />
              </div>
            )}
          </section>
        </div>

        <footer className="wizard__foot">
          {step > 0 ? (
            <button type="button" className="btn btn--secondary" onClick={() => go(0)} disabled={starting}>
              <ArrowLeft size={16} aria-hidden /> Back
            </button>
          ) : (
            <a href="#/certificates" className="btn btn--ghost">
              Cancel
            </a>
          )}
          <div className="wizard__foot-mid">
            {stepError ? (
              <p className="inline-error" role="alert">
                {stepError}
              </p>
            ) : (
              step === 1 && docs.length > 0 && <p className="wizard__summary">{docs.length} {docs.length === 1 ? "document" : "documents"}</p>
            )}
          </div>
          {step === 0 ? (
            <button type="button" className="btn btn--primary" onClick={() => validateWho() && go(1)}>
              Continue <ArrowRight size={16} aria-hidden />
            </button>
          ) : (
            <button type="button" className="btn btn--primary" onClick={() => void start()} disabled={starting}>
              {starting ? (
                <>
                  <span className="spinner spinner--sm spinner--on-primary" aria-hidden /> Starting...
                </>
              ) : (
                "Read and check"
              )}
            </button>
          )}
        </footer>
        <p className="sr-only" role="status" aria-live="polite">
          {starting ? "Starting the certificate." : `${STEPS[step]} step.`}
        </p>
      </main>
    </div>
  );
}
