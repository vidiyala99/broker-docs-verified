/**
 * PHASE A THROWAWAY PROTOTYPES (slice 4, C11). Dev-only route `#/dev/certificates/<a|b|c>/<runId>`, mounted by App
 * only when `import.meta.env.DEV`. Three directions for the certificate draft view, rendered against the seeded demo
 * run (dev_seed run 11). Decisions stay on this page (no PATCH, no Issue): the owner picks a direction, then phase B
 * builds the real screen with the API types in `api/types.ts`. Delete this folder in phase B.
 */
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CheckCircle, Eraser, FilePlus, Signature, Stamp, X, XCircle, ArrowRight, CaretDown } from "@phosphor-icons/react";
import { request } from "../../api/client";
import type { Chip as ChipData, EvidenceState } from "../../api/types";
import { ChipList, hueOf, type OpenSource } from "../../report/Chip";
import type { PdfTarget } from "../../pdf/PdfViewer";
import { Glyph, ReadingMark } from "../../voice/marks";
import "./prototypes.css";

const PdfViewer = lazy(() => import("../../pdf/PdfViewer").then((m) => ({ default: m.PdfViewer })));

// ---------------------------------------------------------------- the API shape (backend/API.md, slice 4)

interface Cell {
  value: string | null;
  passage_ids: number[];
  state: EvidenceState;
  edited: boolean;
}
interface CertField {
  key: string;
  label: string;
  cell: Cell;
}
interface CertPolicy {
  letter: string;
  insurer: string;
  document_id: number;
  line: string;
  line_label: string;
  wording: string[];
  fields: CertField[];
}
interface CertificateBlock {
  certificate_date: string;
  holder_name: string;
  holder_address: string;
  description: string;
  producer: CertField;
  insured: CertField[];
  insurers: { letter: string; name: string; naic: string }[];
  policies: CertPolicy[];
}
interface Claim {
  id: string;
  key: string;
  kind: "in_force" | "wording" | "insured" | "description" | "edited";
  title: string;
  verdict: "supported" | "not_supported" | "needs_signoff";
  reason: string;
  condition: string | null;
  wording: string | null;
  policy_letter: string | null;
  removable: boolean;
  prose: { text: string; passage_ids: number[] }[];
  status: "open" | "resolved" | "removed";
  note: string | null;
  changed_since_resolved: boolean;
  needs_action: boolean;
}
interface CertificateView {
  run: { id: number; documents: { document_id: number; letter: string; filename: string }[] };
  client: string | null;
  version: number;
  certificate: CertificateBlock;
  claims: Claim[];
  chips: Record<string, ChipData>;
  can_issue: boolean;
  blocked: string[];
}

// ---------------------------------------------------------------- claim state (prototype, local decisions)

type ClaimState = "supported" | "not_supported" | "signoff" | "overridden" | "confirmed" | "removed";
type Decision = { status: "open" | "resolved" | "removed"; note: string | null };

const STATE_WORD: Record<ClaimState, string> = {
  supported: "Supported",
  not_supported: "Not supported",
  signoff: "Needs sign-off",
  overridden: "Overridden",
  confirmed: "Confirmed",
  removed: "Removed",
};

function stateOf(c: Claim, d: Decision): ClaimState {
  if (d.status === "removed") return "removed";
  if (c.verdict === "supported") return "supported";
  if (d.status === "resolved") return c.verdict === "needs_signoff" ? "confirmed" : "overridden";
  return c.verdict === "needs_signoff" ? "signoff" : "not_supported";
}
const needsYou = (s: ClaimState) => s === "not_supported" || s === "signoff";

/** Where a claim sits on the certificate, so the list and the paper can point at each other. */
function regionOf(c: Claim): string {
  if (c.kind === "wording") return `p-${c.policy_letter}-${c.wording}`;
  if (c.kind === "in_force") return `p-${c.policy_letter}-dates`;
  if (c.kind === "edited") return `p-${c.policy_letter ?? c.key.split(":")[1]}`;
  return c.kind; // insured, description
}

export function StateGlyph({ state, size = 18 }: { state: ClaimState; size?: number }) {
  const p = { size, "aria-hidden": true as const, className: `cstate cstate--${state}` };
  switch (state) {
    case "supported":
      return <CheckCircle {...p} weight="regular" />;
    case "not_supported":
      return <XCircle {...p} weight="bold" />;
    case "signoff":
      return <Signature {...p} weight="bold" />;
    case "confirmed":
      return <Signature {...p} weight="regular" />;
    case "overridden":
      return <Stamp {...p} weight="regular" />;
    case "removed":
      return <Eraser {...p} weight="regular" />;
  }
}

const WORDING_LABEL: Record<string, string> = {
  additional_insured: "Additional insured",
  waiver_of_subrogation: "Waiver of subrogation",
  primary_noncontributory: "Primary and non-contributory",
};

// ---------------------------------------------------------------- data + local decisions

function useCertificate(runId: number) {
  const [view, setView] = useState<CertificateView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  useEffect(() => {
    request<CertificateView>(`/api/certificates/${runId}`).then(
      (v) => {
        setView(v);
        setDecisions(Object.fromEntries(v.claims.map((c) => [c.key, { status: c.status, note: c.note }])));
      },
      (e: unknown) => setError(e instanceof Error ? e.message : "Couldn't load the certificate."),
    );
  }, [runId]);
  const decide = (key: string, d: Decision) => setDecisions((all) => ({ ...all, [key]: d }));
  return { view, error, decisions, decide };
}

interface Ctx {
  view: CertificateView;
  states: Record<string, ClaimState>;
  decisions: Record<string, Decision>;
  decide: (key: string, d: Decision) => void;
  open: number;
  onChip: OpenSource;
}

// ---------------------------------------------------------------- the certificate paper (shared by A, B, C)

function field(p: CertPolicy, key: string) {
  return p.fields.find((f) => f.key === key);
}
const LIMIT_KEYS = (p: CertPolicy) =>
  p.fields.filter((f) => !["cert:carrier", "cert:naic", "cert:policy_number", "cert:effective_date", "cert:expiration_date"].includes(f.key));

function uniq(ids: number[]) {
  return [...new Set(ids)];
}

/** One region of the paper: carries the region id, the active (pointed-at) state and an optional margin slot. */
function Region({ id, ctx, active, children, margin, as = "div", className = "" }: {
  id: string;
  ctx: Ctx;
  active: string | null;
  children: ReactNode;
  margin?: (region: string) => ReactNode;
  as?: "div" | "tr" | "li";
  className?: string;
}) {
  const Tag = as;
  return (
    <Tag className={`creg${active === id ? " is-pointed" : ""} ${className}`} data-region={id}>
      {children}
      {margin && as !== "tr" && <span className="creg__margin">{margin(id)}</span>}
      {void ctx}
    </Tag>
  );
}

function Row({ id, notes, children }: { id: string; notes?: (row: string) => ReactNode; children: ReactNode }) {
  if (!notes) return <>{children}</>;
  return (
    <div className="prow">
      <div className="prow__main">{children}</div>
      <div className="prow__notes">{notes(id)}</div>
    </div>
  );
}

export function CertificatePaper({ ctx, active, margin, rowNotes, compact = false, idPrefix }: {
  ctx: Ctx;
  active: string | null;
  margin?: (region: string) => ReactNode;
  /** B: notes in the margin, one block per paper row (insured, each policy, description). */
  rowNotes?: (row: string) => ReactNode;
  compact?: boolean;
  idPrefix: string;
}) {
  const { view, states } = ctx;
  const cert = view.certificate;
  const removed = (key: string) => states[key] === "removed";
  const policies = cert.policies.filter((p) => !removed(`in_force:${p.letter}`));
  const date = new Date(cert.certificate_date + "T00:00:00").toLocaleDateString("en-US", { month: "2-digit", day: "2-digit", year: "numeric" });
  const description = removed("description") ? "" : cert.description;
  const insuredIds = uniq(cert.insured.flatMap((f) => f.cell.passage_ids));
  return (
    <article className={`paper${compact ? " paper--compact" : ""}`} aria-labelledby={`${idPrefix}-title`}>
      <header className="paper__head">
        <div>
          <h2 id={`${idPrefix}-title`} className="paper__title">Certificate of insurance</h2>
          <p className="paper__legal">
            Issued as a matter of information only. It confers no rights upon the holder and does not amend, extend or
            alter the coverage of the policies below.
          </p>
        </div>
        <dl className="paper__date">
          <dt>Date</dt>
          <dd>{date}</dd>
          <dt>Status</dt>
          <dd>Draft, version {view.version}</dd>
        </dl>
      </header>

      <Row notes={rowNotes} id="insured">
      <div className="paper__grid2">
        <section className="pbox">
          <h3 className="pbox__label">Producer</h3>
          <p className="pbox__value pbox__value--gap">{cert.producer.cell.value}</p>
        </section>
        <Region id="insured" ctx={ctx} active={active} margin={margin} className="pbox">
          <h3 className="pbox__label">Insured</h3>
          {cert.insured.map((f) => (
            <p key={f.key} className="pbox__value">{f.cell.value}</p>
          ))}
          <ChipList ids={insuredIds.slice(0, 1)} chips={view.chips} onOpen={ctx.onChip} />
        </Region>
      </div>
      </Row>

      <section className="pbox pbox--insurers">
        <h3 className="pbox__label">Insurers affording coverage</h3>
        <table className="ptable ptable--insurers">
          <thead>
            <tr><th scope="col">Insurer</th><th scope="col">Name</th><th scope="col" className="num">NAIC</th></tr>
          </thead>
          <tbody>
            {cert.insurers.map((i) => (
              <tr key={i.letter}><td><span className="pletter">{i.letter}</span></td><td>{i.name}</td><td className="num">{i.naic}</td></tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="pbox pbox--cov">
        <h3 className="pbox__label">Coverages</h3>
        <ol className="pcov">
          {policies.map((p) => {
            const num = field(p, "cert:policy_number")!;
            const eff = field(p, "cert:effective_date")!;
            const exp = field(p, "cert:expiration_date")!;
            const limits = LIMIT_KEYS(p);
            const wording = p.wording.filter((w) => !removed(`${w}:${p.letter}`));
            return (
              <li key={p.letter} className="pcov__item">
                <Row notes={rowNotes} id={`policy-${p.letter}`}>
                <div className="pcov__row">
                <div className="pcov__line">
                  <span className="pletter" title={`Insurer ${p.insurer}`}>{p.insurer}</span>
                  <span className="pcov__name">{p.line_label}</span>
                  <span className="pcov__num">{num.cell.value}</span>
                </div>
                <Region id={`p-${p.letter}-dates`} ctx={ctx} active={active} margin={margin} className="pcov__dates">
                  <span><span className="pk">Effective</span> {eff.cell.value}</span>
                  <span><span className="pk">Expires</span> {exp.cell.value}</span>
                  <ChipList ids={uniq([...eff.cell.passage_ids]).slice(0, 1)} chips={view.chips} onOpen={ctx.onChip} />
                </Region>
                <dl className="pcov__limits">
                  {limits.map((f) => (
                    <div key={f.key} className={f.cell.state === "verified" ? "" : "is-gap"}>
                      <dt>{f.label}</dt>
                      <dd>{f.cell.value}</dd>
                    </div>
                  ))}
                  <ChipList ids={uniq(limits.flatMap((f) => f.cell.passage_ids)).slice(0, 1)} chips={view.chips} onOpen={ctx.onChip} />
                </dl>
                {wording.length > 0 && (
                  <ul className="pcov__wording" aria-label="Wording on this policy">
                    {wording.map((w) => (
                      <Region key={w} as="li" id={`p-${p.letter}-${w}`} ctx={ctx} active={active} margin={margin} className="pword">
                        <span>{WORDING_LABEL[w]}</span>
                      </Region>
                    ))}
                  </ul>
                )}
                </div>
                </Row>
              </li>
            );
          })}
        </ol>
      </section>

      <Row notes={rowNotes} id="description">
        <Region id="description" ctx={ctx} active={active} margin={margin} className="pbox">
          <h3 className="pbox__label">Description of operations</h3>
          <p className={`pbox__value${description ? "" : " pbox__value--gap"}`}>{description || "None"}</p>
        </Region>
      </Row>

      <div className="paper__grid2">
        <section className="pbox">
          <h3 className="pbox__label">Certificate holder</h3>
          <p className="pbox__value pbox__holder">{cert.holder_name}</p>
          <p className="pbox__value pbox__addr">{cert.holder_address}</p>
        </section>
        <section className="pbox">
          <h3 className="pbox__label">Cancellation</h3>
          <p className="paper__legal">
            Should any of the policies above be cancelled before the expiration date, notice will be delivered in
            accordance with the policy provisions.
          </p>
        </section>
      </div>
    </article>
  );
}

// ---------------------------------------------------------------- one claim: status, evidence, actions

function ClaimActions({ c, ctx, compact = false }: { c: Claim; ctx: Ctx; compact?: boolean }) {
  const state = ctx.states[c.key]!;
  const [mode, setMode] = useState<"idle" | "note" | "add">("idle");
  const [note, setNote] = useState("");
  const noteRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (mode === "note") noteRef.current?.focus();
  }, [mode]);
  if (state === "supported") return null;
  if (state === "overridden" || state === "confirmed" || state === "removed") {
    return (
      <div className="cact">
        {ctx.decisions[c.key]?.note && (
          <p className="cact__note"><span className="cact__note-k">{state === "confirmed" ? "Note" : "Reason"}</span> {ctx.decisions[c.key]!.note}</p>
        )}
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => ctx.decide(c.key, { status: "open", note: null })}>
          {state === "removed" ? "Put back on the certificate" : state === "confirmed" ? "Undo confirm" : "Undo override"}
        </button>
      </div>
    );
  }
  const signoff = state === "signoff";
  const noteId = `note-${c.key}`;
  if (mode === "note") {
    return (
      <div className="cact cact--note">
        <label htmlFor={noteId} className="field__label">
          {signoff ? "Confirm the text as typed" : "Reason for the override"}
        </label>
        <p className="hint" id={`${noteId}-hint`}>Saved on the issued record. Not printed.</p>
        <textarea
          id={noteId}
          ref={noteRef}
          className="input textarea"
          rows={compact ? 2 : 3}
          aria-describedby={`${noteId}-hint`}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <div className="cact__row">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setMode("idle")}>Cancel</button>
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={!note.trim()}
            onClick={() => {
              ctx.decide(c.key, { status: "resolved", note: note.trim() });
              setMode("idle");
            }}
          >
            {signoff ? "Confirm" : "Override"}
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="cact">
      <div className="cact__row cact__row--start">
        {!signoff && (
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => setMode(mode === "add" ? "idle" : "add")} aria-expanded={mode === "add"}>
            <FilePlus size={16} aria-hidden /> Add document
          </button>
        )}
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setMode("note")}>
          {signoff ? "Confirm..." : "Override..."}
        </button>
        {c.removable && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => ctx.decide(c.key, { status: "removed", note: null })}>
            Remove from certificate
          </button>
        )}
      </div>
      {mode === "add" && (
        <p className="hint cact__stub">Prototype: opens the Policies picker (upload, or the client's documents). The new document is read, then this check runs again.</p>
      )}
    </div>
  );
}

function ClaimBody({ c, ctx }: { c: Claim; ctx: Ctx }) {
  const ids = uniq(c.prose.flatMap((s) => s.passage_ids));
  return (
    <p className="claim__reason">
      {c.prose.map((s) => s.text).join("")}{" "}
      <ChipList ids={ids} chips={ctx.view.chips} onOpen={ctx.onChip} />
    </p>
  );
}

// ---------------------------------------------------------------- shared page frame

function Header({ ctx, title, onIssue, note, issue = true }: { ctx: Ctx; title: string; onIssue: () => void; note?: string; issue?: boolean }) {
  const n = ctx.open;
  return (
    <div className="toolbar cproto__toolbar">
      <h1 className="toolbar__title toolbar__title--named">
        <Glyph name="certificate" size={30} />
        <span className="toolbar__heading">
          <span className="toolbar__name">{ctx.view.client} · Certificate</span>
          <span className="toolbar__meta">
            <span className="toolbar__version">For {ctx.view.certificate.holder_name}. Run {ctx.view.run.id}, version {ctx.view.version}. {title}</span>
          </span>
        </span>
      </h1>
      {issue && <IssueButton n={n} onIssue={onIssue} note={note} />}
    </div>
  );
}

function IssueButton({ n, onIssue, note }: { n: number; onIssue: () => void; note?: string }) {
  return (
    <div className="issue">
      {note && <span className="issue__note">{note}</span>}
      <button type="button" className="btn btn--primary" aria-disabled={n > 0 || undefined} onClick={() => n === 0 && onIssue()} aria-describedby="issue-why">
        Issue certificate
      </button>
      <span id="issue-why" className={`issue__count${n ? "" : " is-clear"}`} role="status" aria-live="polite">
        {n ? `${n} ${n === 1 ? "check" : "checks"} to resolve` : "Every check handled"}
      </span>
    </div>
  );
}

function SourcePane({ target, onClose }: { target: PdfTarget; onClose: () => void }) {
  return (
    <section className="cproto__source" aria-label={`Source: ${target.filename}`}>
      <div className="source-pane__head">
        <span className={`doc-letter hue-${hueOf(target.letter)}`} aria-hidden>{target.letter}</span>
        <span className="source-pane__name">{target.filename}</span>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
          Back to certificate <kbd>Esc</kbd>
        </button>
      </div>
      <Suspense fallback={<div className="pdf__frame"><div className="pdf__skeleton" aria-hidden /></div>}>
        <PdfViewer target={target} />
      </Suspense>
    </section>
  );
}

function useSource() {
  const [pdf, setPdf] = useState<PdfTarget | null>(null);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && setPdf(null);
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  const onChip: OpenSource = (chip) => setPdf({ documentId: chip.document_id, filename: chip.filename, letter: chip.letter, page: chip.page_no, chip });
  return { pdf, setPdf, onChip };
}

const ORDER: ClaimState[] = ["not_supported", "signoff", "overridden", "confirmed", "removed", "supported"];

// ---------------------------------------------------------------- A: Proof and ledger (side by side)

function DirectionA({ ctx, pdf, closePdf }: { ctx: Ctx; pdf: PdfTarget | null; closePdf: () => void }) {
  const [pointed, setPointed] = useState<string | null>(null);
  const [showSupported, setShowSupported] = useState(false);
  const groups = useMemo(() => {
    const list = [...ctx.view.claims].sort((a, b) => ORDER.indexOf(ctx.states[a.key]!) - ORDER.indexOf(ctx.states[b.key]!));
    return {
      todo: list.filter((c) => needsYou(ctx.states[c.key]!)),
      handled: list.filter((c) => ["overridden", "confirmed", "removed"].includes(ctx.states[c.key]!)),
      supported: list.filter((c) => ctx.states[c.key] === "supported"),
    };
  }, [ctx.view.claims, ctx.states]);
  const point = (c: Claim | null) => {
    const r = c ? regionOf(c) : null;
    setPointed(r);
    if (r) document.querySelector(`.cproto--a .paper [data-region="${r}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  };
  const mark = (region: string) => {
    const cs = ctx.view.claims.filter((c) => regionOf(c) === region);
    if (!cs.length) return null;
    const worst = cs.map((c) => ctx.states[c.key]!).sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b))[0]!;
    return <StateGlyph state={worst} size={16} />;
  };
  const row = (c: Claim) => {
    const s = ctx.states[c.key]!;
    return (
      <li key={c.key} className={`ledger__item ledger__item--${s}`} onMouseEnter={() => point(c)} onMouseLeave={() => point(null)} onFocus={() => point(c)}>
        <div className="claim__head">
          <StateGlyph state={s} />
          <span className={`status-word cword cword--${s}`}>{STATE_WORD[s]}</span>
        </div>
        <h3 className="claim__title">{c.title}</h3>
        {s !== "supported" && <ClaimBody c={c} ctx={ctx} />}
        <ClaimActions c={c} ctx={ctx} />
      </li>
    );
  };
  return (
    <div className="cproto__body cproto__body--a">
      <div className="cproto__paperwrap">
        {pdf ? <SourcePane target={pdf} onClose={closePdf} /> : <CertificatePaper ctx={ctx} active={pointed} margin={mark} idPrefix="a" />}
      </div>
      <aside className="ledger" aria-label="Checks">
        <h2 className="ledger__title">Checks</h2>
        <p className="ledger__sum">
          {ctx.view.claims.length} claims on this certificate. {groups.todo.length} need you.
        </p>
        {groups.todo.length > 0 && (
          <section aria-labelledby="a-todo">
            <h3 id="a-todo" className="ledger__group">To resolve <span className="count">{groups.todo.length}</span></h3>
            <ol className="ledger__list">{groups.todo.map(row)}</ol>
          </section>
        )}
        {groups.handled.length > 0 && (
          <section aria-labelledby="a-handled">
            <h3 id="a-handled" className="ledger__group">Handled by you <span className="count">{groups.handled.length}</span></h3>
            <ol className="ledger__list">{groups.handled.map(row)}</ol>
          </section>
        )}
        <section aria-labelledby="a-sup">
          <h3 id="a-sup" className="ledger__group">
            <button type="button" className="ledger__fold" aria-expanded={showSupported} onClick={() => setShowSupported((v) => !v)}>
              <CaretDown size={14} aria-hidden className={showSupported ? "is-open" : ""} /> Supported by the documents <span className="count">{groups.supported.length}</span>
            </button>
          </h3>
          {showSupported && <ol className="ledger__list">{groups.supported.map(row)}</ol>}
        </section>
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------- B: Marked proof (notes in the paper's margin)

function DirectionB({ ctx, pdf, closePdf, onIssue }: { ctx: Ctx; pdf: PdfTarget | null; closePdf: () => void; onIssue: () => void }) {
  const [pointed, setPointed] = useState<string | null>(null);
  const openClaims = ctx.view.claims.filter((c) => needsYou(ctx.states[c.key]!));
  const next = () => {
    const first = document.querySelector<HTMLElement>(".cproto--b .mnote--not_supported, .cproto--b .mnote--signoff");
    first?.scrollIntoView({ block: "center", behavior: "smooth" });
    first?.querySelector<HTMLElement>("button")?.focus();
  };
  const rowOf = (c: Claim) => (c.kind === "insured" || c.kind === "description" ? c.kind : `policy-${c.policy_letter ?? c.key.split(":")[1]}`);
  const margin = (row: string) => {
    const cs = ctx.view.claims
      .filter((c) => rowOf(c) === row)
      .sort((a, b) => ORDER.indexOf(ctx.states[a.key]!) - ORDER.indexOf(ctx.states[b.key]!));
    if (!cs.length) return null;
    return (
      <span className="mnotes">
        {cs.map((c) => {
          const s = ctx.states[c.key]!;
          if (s === "supported")
            return (
              <span key={c.key} className="mtick" title={`${c.title}: supported`}>
                <StateGlyph state={s} size={16} />
                <span className="mtick__t">{c.kind === "in_force" ? "In force on the certificate date" : c.kind === "insured" ? "Same insured on every policy" : `${WORDING_LABEL[c.wording ?? ""] ?? "Supported"}: endorsement found`}</span>
                <ChipList ids={uniq(c.prose.flatMap((p) => p.passage_ids)).slice(0, 1)} chips={ctx.view.chips} onOpen={ctx.onChip} />
              </span>
            );
          return (
            <span key={c.key} className={`mnote mnote--${s}`} onMouseEnter={() => setPointed(regionOf(c))} onMouseLeave={() => setPointed(null)}>
              <span className="claim__head">
                <StateGlyph state={s} />
                <span className={`status-word cword cword--${s}`}>{STATE_WORD[s]}</span>
              </span>
              <span className="mnote__title">{c.kind === "wording" ? WORDING_LABEL[c.wording ?? ""] : c.title}</span>
              {needsYou(s) && <ClaimBody c={c} ctx={ctx} />}
              <ClaimActions c={c} ctx={ctx} compact />
            </span>
          );
        })}
      </span>
    );
  };
  return (
    <div className="cproto__body cproto__body--b">
      {pdf && (
        <div className="bdrawer">
          <SourcePane target={pdf} onClose={closePdf} />
        </div>
      )}
      <div className="bproof">
        <CertificatePaper ctx={ctx} active={pointed} rowNotes={margin} idPrefix="b" />
      </div>
      <div className="gatebar" role="region" aria-label="Issue">
        <span className="gatebar__count">
          {openClaims.length ? (
            <>
              <StateGlyph state="not_supported" size={16} /> {openClaims.length} {openClaims.length === 1 ? "mark" : "marks"} on the proof need you
            </>
          ) : (
            <>
              <StateGlyph state="supported" size={16} /> Every mark handled
            </>
          )}
        </span>
        {openClaims.length > 0 && (
          <button type="button" className="btn btn--secondary btn--sm" onClick={next}>
            Next mark <ArrowRight size={14} aria-hidden />
          </button>
        )}
        <IssueButton n={ctx.open} onIssue={onIssue} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- C: Checks first (the certificate as a preview)

function DirectionC({ ctx, pdf, closePdf }: { ctx: Ctx; pdf: PdfTarget | null; closePdf: () => void }) {
  const ordered = useMemo(
    () => [...ctx.view.claims].sort((a, b) => ORDER.indexOf(ctx.states[a.key]!) - ORDER.indexOf(ctx.states[b.key]!)),
    [ctx.view.claims, ctx.states],
  );
  const [selected, setSelected] = useState<string | null>(null);
  const sel = ordered.find((c) => c.key === selected) ?? ordered.find((c) => needsYou(ctx.states[c.key]!)) ?? ordered[0]!;
  const done = ctx.view.claims.length - ctx.open;
  const selRegion = regionOf(sel);
  useEffect(() => {
    document.querySelector(`.cpreview [data-region="${selRegion}"]`)?.scrollIntoView({ block: "center", behavior: "instant" as ScrollBehavior });
  }, [selRegion]);
  return (
    <div className="cproto__body cproto__body--c">
      <section className="checks" aria-labelledby="c-title">
        <div className="checks__head">
          <h2 id="c-title" className="checks__title">Checks</h2>
          <p className="checks__progress">
            <span className="checks__bar" aria-hidden>
              {ctx.view.claims.map((c) => (
                <span key={c.key} className={`checks__tick checks__tick--${ctx.states[c.key]}`} />
              ))}
            </span>
            {done} of {ctx.view.claims.length} handled
          </p>
        </div>
        <ol className="checks__list">
          {ordered.map((c) => {
            const s = ctx.states[c.key]!;
            const isSel = c.key === sel.key;
            return (
              <li key={c.key} className={`crow crow--${s}${isSel ? " is-selected" : ""}`}>
                <button type="button" className="crow__btn" aria-expanded={isSel} onClick={() => setSelected(c.key)}>
                  <StateGlyph state={s} size={20} />
                  <span className="crow__text">
                    <span className="crow__title">{c.title}</span>
                    <span className={`status-word cword cword--${s}`}>{STATE_WORD[s]}</span>
                  </span>
                </button>
                {isSel && (
                  <div className="crow__detail">
                    <ClaimBody c={c} ctx={ctx} />
                    <ClaimActions c={c} ctx={ctx} />
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      </section>
      <aside className="cpreview" aria-label="Certificate preview">
        {pdf ? (
          <SourcePane target={pdf} onClose={closePdf} />
        ) : (
          <>
            <p className="cpreview__cap">
              Where this sits on the certificate: <strong>{sel.title}</strong>
            </p>
            <div className="cpreview__frame">
              <CertificatePaper ctx={ctx} active={regionOf(sel)} compact idPrefix="c" />
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------- the dev route

const NAMES: Record<string, string> = { a: "Proof and ledger", b: "Marked proof", c: "Checks first" };

export function CertificatePrototypes({ direction, runId }: { direction: string; runId: number }) {
  const { view, error, decisions, decide } = useCertificate(runId);
  const { pdf, setPdf, onChip } = useSource();
  const [issued, setIssued] = useState(false);
  if (error)
    return (
      <main className="pane" id="main" tabIndex={-1}>
        <div className="pane__body pane__body--center"><div className="state-block" role="alert"><p className="state-block__title">{error}</p></div></div>
      </main>
    );
  if (!view)
    return (
      <main className="pane" id="main" tabIndex={-1} aria-busy="true">
        <div className="pane__body pane__body--center"><ReadingMark size={40} /></div>
      </main>
    );
  const states = Object.fromEntries(view.claims.map((c) => [c.key, stateOf(c, decisions[c.key] ?? { status: c.status, note: c.note })]));
  const open = Object.values(states).filter(needsYou).length;
  const ctx: Ctx = { view, states, decisions, decide, open, onChip };
  const d = direction in NAMES ? direction : "a";
  return (
    <main className={`pane cproto cproto--${d}`} id="main" tabIndex={-1} aria-labelledby="cproto-title">
      <nav className="cproto__switch" aria-label="Prototype directions">
        <span className="cproto__flag">Prototype, phase A. Decisions stay on this page.</span>
        {Object.entries(NAMES).map(([k, name]) => (
          <a key={k} href={`#/dev/certificates/${k}/${runId}`} className={`tab${k === d ? " is-active" : ""}`} aria-current={k === d ? "page" : undefined}>
            {k.toUpperCase()}. {name}
          </a>
        ))}
      </nav>
      <span id="cproto-title" className="sr-only">Certificate draft, direction {NAMES[d]}</span>
      <Header ctx={ctx} title="" onIssue={() => setIssued(true)} issue={d !== "b"} />
      {issued && (
        <p className="cproto__issued" role="status">
          Prototype: Issue would send version {view.version} (D5) and show the issued view.
          <button type="button" className="icon-btn icon-btn--sm" aria-label="Dismiss" onClick={() => setIssued(false)}><X size={14} aria-hidden /></button>
        </p>
      )}
      {d === "a" && <DirectionA ctx={ctx} pdf={pdf} closePdf={() => setPdf(null)} />}
      {d === "b" && <DirectionB ctx={ctx} pdf={pdf} closePdf={() => setPdf(null)} onIssue={() => setIssued(true)} />}
      {d === "c" && <DirectionC ctx={ctx} pdf={pdf} closePdf={() => setPdf(null)} />}
    </main>
  );
}

export function prototypeRoute(hash: string): { direction: string; runId: number } | null {
  const m = /^#\/dev\/certificates\/([abc])\/(\d+)/.exec(hash);
  return m ? { direction: m[1]!, runId: Number(m[2]) } : null;
}
