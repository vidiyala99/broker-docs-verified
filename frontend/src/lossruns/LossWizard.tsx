import { useEffect, useId, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, FilePdf, FileXls, Trash, UploadSimple, WarningCircle } from "@phosphor-icons/react";
import { api, ApiError, uploadFile } from "../api/client";
import type { ClientDocument, DocumentInfo, Workspace } from "../api/types";
import type { Viewport } from "../shell/layout";
import { useStepTransition } from "../ui/motion";
import { Stepper } from "../wizard/Stepper";
import { Glyph } from "../voice/marks";
import { MAX_LOSS_FILES, SPREADSHEET } from "./lossUtil";
import "../wizard/wizard.css";
import "../certificates/certificates.css";
import "./lossruns.css";

const STEPS = ["Who", "Files", "Read"] as const;
type Step = 0 | 1 | 2;

type Row = { key: string; name: string; progress: number; doc?: DocumentInfo; error?: string };

/** A spreadsheet's sheets and mapped columns, from its upload: "Clm # → Claim number"; unmapped columns by name. */
function SheetSummary({ doc }: { doc: DocumentInfo }) {
  const s = doc.spreadsheet;
  if (!s) return null;
  return (
    <ul className="lr-sheets">
      {s.sheets.map((sh) => (
        <li key={sh.name}>
          <span className="lr-sheets__name">Sheet {sh.name}</span>{" "}
          {sh.has_claims_table ? (
            <>
              <span className="hint">{sh.data_rows.toLocaleString("en-US")} rows. </span>
              <span className="lr-sheets__map">{sh.mapped.map((m) => `${m.header} → ${m.label}`).join(", ")}</span>
              {sh.unmapped.length > 0 && <span className="hint">. Not used: {sh.unmapped.map((u) => u.header).join(", ")}</span>}
            </>
          ) : (
            <span className="hint">No claims table found on this sheet.</span>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * New loss review (slice 5a, spec section 2): Who (the client), Files (PDF and spreadsheet loss runs, or the client's
 * files), Read (the review page shows each file being read). The Policy Check and Certificates wizard pattern.
 */
export function LossWizard({ viewport, client: preset }: { workspace: Workspace; viewport: Viewport; client: string | null }) {
  const id = useId();
  const [step, setStep] = useState<Step>(0);
  const [reached, setReached] = useState<Step>(0);
  const [client, setClient] = useState(preset ?? "");
  const [past, setPast] = useState<string[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [known, setKnown] = useState<ClientDocument[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [stepError, setStepError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [drag, setDrag] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const stepRef = useRef<HTMLElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const seq = useRef(0);
  useStepTransition(stepRef, step);

  useEffect(() => {
    api.lossReviews().then((rs) => setPast([...new Set(rs.map((r) => r.client).filter((c): c is string => !!c))]), () => undefined);
  }, []);
  useEffect(() => {
    const name = client.trim();
    if (!name) return setKnown([]);
    let live = true;
    const t = window.setTimeout(() => api.clientDocuments(name).then((d) => live && setKnown(d), () => live && setKnown([])), 250);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [client]);

  const go = (s: Step) => {
    setStep(s);
    setReached((r) => (s > r ? s : r));
    setStepError(null);
    window.setTimeout(() => headingRef.current?.focus(), 0);
  };

  const uploaded = rows.filter((r) => r.doc).map((r) => r.doc!);
  const ids = [...new Set([...uploaded.map((d) => d.id), ...picked])];
  const busy = rows.some((r) => !r.doc && !r.error);

  const add = (files: FileList | File[]) => {
    const list = [...files];
    const room = MAX_LOSS_FILES - ids.length - rows.filter((r) => !r.doc && !r.error).length;
    list.forEach((f, i) => {
      const key = `u${++seq.current}`;
      if (i >= room) {
        setRows((rs) => [...rs, { key, name: f.name, progress: 0, error: `Up to ${MAX_LOSS_FILES} files in one review.` }]);
        return;
      }
      const sheet = SPREADSHEET.test(f.name);
      if (!sheet && !/\.pdf$/i.test(f.name)) {
        setRows((rs) => [...rs, { key, name: f.name, progress: 0, error: "Use a PDF, .xlsx or .csv file." }]);
        return;
      }
      setRows((rs) => [...rs, { key, name: f.name, progress: 0 }]);
      uploadFile(f, (p) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, progress: p } : r))), sheet ? "/api/documents/spreadsheets" : "/api/documents").then(
        (res) => {
          const doc = res.documents[0];
          const rej = res.rejected[0];
          setRows((rs) => rs.map((r) => (r.key === key ? (doc ? { ...r, doc, progress: 1 } : { ...r, error: rej?.message ?? "This file was not accepted." }) : r)));
        },
        (e: unknown) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, error: e instanceof ApiError ? e.message : "Upload failed. Retry." } : r))),
      );
    });
  };

  const start = async () => {
    if (!ids.length) {
      setStepError("Add at least one loss run.");
      return;
    }
    setStarting(true);
    try {
      const res = await api.startLossReview(ids, client);
      go(2);
      window.location.hash = `#/runs/${res.run.id}`;
    } catch (e) {
      setStepError(e instanceof ApiError ? e.message : "Couldn't start the review. Retry.");
      setStarting(false);
    }
  };

  return (
    <div className="workspace">
      <main className="pane wizard" id="main" tabIndex={-1} aria-labelledby="wizard-title">
        <header className="wizard__head">
          <h1 id="wizard-title" className="wizard__title">
            <Glyph name="analytics" size={34} />
            New loss review
          </h1>
          <Stepper step={step} reached={reached} onGo={(s) => s < 2 && (s < step || s <= reached) && go(s as Step)} compact={viewport === "narrow"} steps={STEPS} label="Loss review steps" />
        </header>
        <div className="wizard__body">
          <section ref={stepRef} className="wizard__step" aria-labelledby="step-heading">
            <h2 id="step-heading" ref={headingRef} tabIndex={-1} className="wizard__step-title">
              <span className="sr-only">Step {step + 1} of {STEPS.length}, </span>
              {step === 0 ? "Whose losses" : "The loss runs"}
            </h2>
            {step === 0 ? (
              <div className="details cwho">
                <div className="field">
                  <label htmlFor={`${id}-client`} className="field__label">Client</label>
                  <p className="hint" id={`${id}-client-hint`}>The insured. One review holds one client's loss runs, every carrier and year.</p>
                  <input id={`${id}-client`} className="input" value={client} maxLength={120} list={`${id}-past`} autoComplete="organization" aria-describedby={`${id}-client-hint`} onChange={(e) => setClient(e.target.value)} />
                  <datalist id={`${id}-past`}>{past.map((p) => <option key={p} value={p} />)}</datalist>
                </div>
              </div>
            ) : (
              <div className="details lr-files">
                <p className="hint">Carrier loss runs as PDF, Excel (.xlsx) or CSV, up to {MAX_LOSS_FILES}. Spreadsheets are read in code: each sheet shows the columns it maps.</p>
                <div
                  className={`lr-drop${drag ? " is-over" : ""}`}
                  onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
                  onDragLeave={() => setDrag(false)}
                  onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
                >
                  <UploadSimple size={24} aria-hidden />
                  <p>Drop loss runs here, or</p>
                  <button type="button" className="btn btn--secondary" onClick={() => input.current?.click()}>Choose files</button>
                  <input ref={input} type="file" multiple accept=".pdf,.xlsx,.csv,application/pdf" className="sr-only" aria-label="Choose loss run files" onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
                </div>
                {rows.length > 0 && (
                  <ul className="cpick__list lr-uplist">
                    {rows.map((r) => (
                      <li key={r.key} className="lr-uprow">
                        <span className="lr-uprow__glyph" aria-hidden>{SPREADSHEET.test(r.name) ? <FileXls size={20} /> : <FilePdf size={20} />}</span>
                        <span className="lr-uprow__main">
                          <span className="lr-uprow__name">{r.name}</span>
                          {r.error ? (
                            <span className="inline-error"><WarningCircle size={14} aria-hidden /> {r.error}</span>
                          ) : r.doc ? (
                            <>
                              <span className="hint">{r.doc.kind === "spreadsheet" ? `Spreadsheet, ${r.doc.spreadsheet?.data_rows.toLocaleString("en-US") ?? 0} rows` : `PDF, ${r.doc.page_count ?? "?"} pages`}</span>
                              <SheetSummary doc={r.doc} />
                            </>
                          ) : (
                            <span className="hint" role="status">Uploading {Math.round(r.progress * 100)}%</span>
                          )}
                        </span>
                        <button type="button" className="icon-btn icon-btn--sm" aria-label={`Remove ${r.name}`} onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))}>
                          <Trash size={16} aria-hidden />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {known.length > 0 && (
                  <fieldset className="cpick__group">
                    <legend className="group-title">{client.trim()}'s files</legend>
                    <ul className="cpick__list">
                      {known.map((d) => (
                        <li key={d.id}>
                          <label className={`cpick__row${picked.has(d.id) ? " is-on" : ""}`}>
                            <input type="checkbox" checked={picked.has(d.id)} onChange={(e) => setPicked((p) => { const n = new Set(p); if (e.target.checked) n.add(d.id); else n.delete(d.id); return n; })} />
                            <span className="cpick__main">{d.filename}</span>
                          </label>
                        </li>
                      ))}
                    </ul>
                  </fieldset>
                )}
              </div>
            )}
          </section>
        </div>
        <footer className="wizard__foot">
          {step > 0 ? (
            <button type="button" className="btn btn--secondary" onClick={() => go(0)} disabled={starting}><ArrowLeft size={16} aria-hidden /> Back</button>
          ) : (
            <a href="#/analytics" className="btn btn--ghost">Cancel</a>
          )}
          <div className="wizard__foot-mid">
            {stepError ? <p className="inline-error" role="alert">{stepError}</p> : step === 1 && ids.length > 0 && <p className="wizard__summary">{ids.length} {ids.length === 1 ? "file" : "files"}</p>}
          </div>
          {step === 0 ? (
            <button type="button" className="btn btn--primary" onClick={() => go(1)}>Continue <ArrowRight size={16} aria-hidden /></button>
          ) : (
            <button type="button" className="btn btn--primary" disabled={starting || busy} onClick={() => void start()}>
              {starting ? "Starting..." : busy ? "Uploading..." : "Read the files"}
            </button>
          )}
        </footer>
      </main>
    </div>
  );
}
