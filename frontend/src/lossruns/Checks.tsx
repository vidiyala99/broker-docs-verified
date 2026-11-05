import { useId, useState, type ReactNode } from "react";
import { CaretDown, CheckCircle, CircleDashed, NotEquals, Scales, Scan, Signature, Stamp, Warning } from "@phosphor-icons/react";
import type { LossChip as LossChipData, LossReview } from "../api/types";
import { hueOf } from "../report/Chip";
import { Collapse } from "../ui/motion";
import { keyboardAction } from "../ui/input";
import { checkCards, docLetter, docName, groupOf, METRIC_LABEL, scopeLabel, usd, type CheckCard } from "./lossUtil";

/**
 * The chip's visible text, short enough for a table row: a PDF page reads "Driftwood p.2" (the carrier's first word; the
 * full label is the accessible name), a sheet row "Sheet 2024, row 17", a CSV row "Row 6" (its only sheet is the file).
 */
export function chipText(chip: LossChipData): string {
  if (chip.kind === "page") {
    const m = /^(\S+).*?\s(p\.\d+)$/.exec(chip.label);
    return m ? `${m[1]} ${m[2]}` : chip.label;
  }
  const stem = chip.filename.replace(/\.(xlsx|csv)$/i, "");
  return chip.label.replace(new RegExp(`^Sheet ${stem.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}, row`), "Row");
}

/** A loss-run chip (D13): the letter that travels with the file's hue, then "Driftwood p.2" or "Sheet 2024, row 17". */
export function LossChip({ chip, onOpen, scan = false }: { chip: LossChipData; onOpen: (c: LossChipData) => void; scan?: boolean }) {
  return (
    <button
      type="button"
      className={`chip lr-chip hue-${hueOf(chip.letter)}${scan ? " chip--scan" : ""}`}
      aria-label={`Source ${chip.letter}, ${chip.label}${scan ? ". Read from scan, unverified" : ""}`}
      onClick={() => onOpen(chip)}
    >
      {scan && <Scan size={12} weight="bold" aria-hidden />}
      <span className="lr-chip__letter">{chip.letter}</span>
      <span className="lr-chip__text">{chipText(chip)}</span>
    </button>
  );
}

type Word = { glyph: ReactNode; word: string; tone: string };

/** Silhouette first, the word always, colour third (DESIGN.md "Loss runs"). */
export function cardWord(card: CheckCard): Word {
  const lead = card.lead;
  if (groupOf(card) === "handled") {
    if (lead.kind === "conflict") return { glyph: <Stamp size={16} weight="bold" />, word: "Picked by you", tone: "handled-stamp" };
    if (lead.kind === "unverified") return { glyph: <Signature size={16} weight="bold" />, word: "Handled row by row", tone: "handled" };
    return { glyph: <Stamp size={16} weight="bold" />, word: "Accepted by you", tone: "handled-stamp" };
  }
  switch (card.verdict) {
    case "off": {
      const inc = card.checks.find((c) => c.metric === "incurred") ?? lead;
      return { glyph: <NotEquals size={16} weight="bold" />, word: `Off by ${usd(Math.abs(inc.difference ?? 0))}`, tone: "off" };
    }
    case "needs_review":
      return { glyph: <CircleDashed size={16} weight="bold" />, word: "Couldn't check", tone: "review" };
    case "conflicting":
      return { glyph: <Scales size={16} weight="bold" />, word: "Conflicting", tone: "conflict" };
    case "unverified":
      return { glyph: <Warning size={16} weight="bold" />, word: "Not verified", tone: "unverified" };
    default:
      return { glyph: <CheckCircle size={16} weight="bold" />, word: "Matches", tone: "match" };
  }
}

export function CardWord({ card }: { card: CheckCard }) {
  const w = cardWord(card);
  return (
    <span className={`lr-word lr-word--${w.tone}`}>
      <span aria-hidden className="lr-word__g">{w.glyph}</span>
      <span className="status-word">{w.word}</span>
    </span>
  );
}

export function cardTitle(card: CheckCard, review: LossReview | null): string {
  const c = card.lead;
  if (c.kind === "total") return `${docName(review, c.document_id)}: ${scopeLabel(c)}`;
  if (c.kind === "conflict") return `Claim ${c.claim_key ?? ""} has two values for the same valuation date`;
  return c.title.replace(/ in (lr_[^ ]+|[^ ]+\.(pdf|xlsx|csv))/, ` in ${docName(review, c.document_id)}`);
}

export interface CheckActions {
  /** Open the rows a check covers (its API `filter`) in the claims table. */
  show: (card: CheckCard) => void;
  addClaim: (documentId: number | null) => void;
  accept: (card: CheckCard, note: string) => Promise<void>;
  pick: (card: CheckCard, documentId: number) => Promise<void>;
  reopen: (card: CheckCard) => Promise<void>;
  openChip: (chip: LossChipData) => void;
}

/** Printed, rows add up to, off by: one row per metric of the printed line. Couldn't check shows no sum, no difference. */
function Recon({ card }: { card: CheckCard }) {
  const off = card.checks.some((c) => c.verdict === "off");
  return (
    <table className="lr-recon">
      <thead>
        <tr>
          <th scope="col"><span className="sr-only">Total</span></th>
          <th scope="col" className="num">Printed</th>
          <th scope="col" className="num">Rows add up to</th>
          {off && <th scope="col" className="num">Off by</th>}
        </tr>
      </thead>
      <tbody>
        {card.checks.map((c) => (
          <tr key={c.id}>
            <th scope="row">{METRIC_LABEL[c.metric ?? ""] ?? c.metric}</th>
            <td className="num">{usd(c.printed)}</td>
            <td className={`num${c.verdict === "needs_review" ? " lr-muted" : ""}`}>{c.verdict === "needs_review" || c.computed == null ? "Not checked" : usd(c.computed)}</td>
            {off && <td className={`num${c.verdict === "off" ? " lr-diff" : ""}`}>{c.verdict === "off" ? usd(c.difference) : ""}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Accept with a note (accept needs one, API 422 `note_required`): inline, never a modal. */
function NoteEditor({ label, cta, onSave, onCancel }: { label: string; cta: string; onSave: (note: string) => Promise<void>; onCancel: () => void }) {
  const id = useId();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!note.trim()) {
      setError("Add a note: it is kept with the decision.");
      return;
    }
    setBusy(true);
    try {
      await onSave(note.trim());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save. Retry.");
      setBusy(false);
    }
  };
  return (
    <div className="lr-note">
      <label htmlFor={id} className="field__label">{label}</label>
      <textarea
        id={id}
        className="input lr-note__input"
        rows={2}
        maxLength={500}
        value={note}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-err` : undefined}
        onChange={(e) => setNote(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void save();
        }}
        autoFocus
      />
      {error && <p id={`${id}-err`} className="inline-error" role="alert">{error}</p>}
      <div className="cact__row cact__row--end">
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>Cancel</button>
        <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => void save()}>{busy ? "Saving..." : cta}</button>
      </div>
    </div>
  );
}

function rowsIn(card: CheckCard): number | null {
  return card.lead.rows ?? null;
}

function CardBody({ card, review, act, folded = false }: { card: CheckCard; review: LossReview | null; act: CheckActions; folded?: boolean }) {
  const [noting, setNoting] = useState(false);
  const c = card.lead;
  const group = groupOf(card);
  const chip = c.chips[0];
  const n = rowsIn(card);
  return (
    <>
      <div className="claim__head">
        <CardWord card={card} />
        {card.checks.some((x) => x.changed_since_resolved) && <span className="stale-tag">Changed since you resolved it</span>}
      </div>
      <p className="claim__title">{cardTitle(card, review)}</p>
      {folded ? (
        chip && <p className="claim__reason"><LossChip chip={chip} onOpen={act.openChip} /></p>
      ) : (
        <>
          {c.kind === "total" && <Recon card={card} />}
          {c.kind === "conflict" && (
            <ul className="lr-options">
              {c.options.map((o) => {
                const other = c.options.find((x) => x.document_id !== o.document_id);
                const diff = (k: "paid" | "reserve" | "incurred") => other != null && other[k] !== o[k];
                const picked = c.picked_document_id === o.document_id;
                return (
                  <li key={o.document_id} className={`lr-option${picked ? " is-picked" : ""}`}>
                    <span className="lr-option__file">
                      <span className={`lr-chip__letter hue-${hueOf(docLetter(review, o.document_id))} lr-letter`}>{docLetter(review, o.document_id)}</span>
                      {docName(review, o.document_id) || o.label}
                    </span>
                    <dl>
                      {(["paid", "reserve", "incurred"] as const).map((k) => (
                        <div key={k} className={diff(k) ? "is-diff" : undefined}>
                          <dt>{METRIC_LABEL[k]}</dt>
                          <dd>{usd(o[k] ?? null)}</dd>
                        </div>
                      ))}
                    </dl>
                    {group === "to_resolve" && (
                      <button type="button" className="btn btn--secondary btn--sm" onClick={() => void act.pick(card, o.document_id)}>
                        Use {docName(review, o.document_id) || o.label}
                      </button>
                    )}
                    {picked && <span className="lr-option__picked">Used</span>}
                  </li>
                );
              })}
            </ul>
          )}
          <p className="claim__reason">
            {c.kind === "total" && card.verdict === "off"
              ? `The printed total is higher than the ${n ?? ""} rows it covers: a claim may be missing from the rows, or a row may be wrong. `
              : c.kind === "unverified" && group === "to_resolve"
                ? `${c.message} `
                : c.kind === "conflict"
                  ? "Both files value it on the same date; it stays out of totals until you pick one. "
                  : card.verdict === "needs_review"
                    ? `${c.message} `
                    : ""}
            {chip && c.kind !== "conflict" && <LossChip chip={chip} onOpen={act.openChip} scan={c.kind === "unverified"} />}
          </p>
          {c.note && (
            <p className="claim__note">
              <span className="claim__k">Your note</span>
              {c.note}
            </p>
          )}
          {group === "to_resolve" && c.kind !== "conflict" && !noting && (
            <div className="cact__row lr-acts">
              <button type="button" className="btn btn--secondary btn--sm" onClick={() => act.show(card)}>
                {n ? `Show the ${n} ${n === 1 ? "row" : "rows"}` : "Show the rows"}
              </button>
              {c.kind === "total" && (
                <span className="cact__quiet">
                  {card.verdict === "off" && (
                    <button type="button" className="btn btn--ghost btn--sm" onClick={() => act.addClaim(c.document_id)}>Add a missed claim</button>
                  )}
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => setNoting(true)}>
                    {card.verdict === "off" ? "Accept the gap..." : "Accept with a note..."}
                  </button>
                </span>
              )}
            </div>
          )}
          <Collapse open={noting} animate={!keyboardAction()}>
            {noting && (
              <NoteEditor
                label={card.verdict === "off" ? "Why the gap is acceptable" : "Why this total can stand"}
                cta="Accept"
                onSave={async (note) => {
                  await act.accept(card, note);
                  setNoting(false);
                }}
                onCancel={() => setNoting(false)}
              />
            )}
          </Collapse>
          {group === "handled" && c.decided_by === "broker" && (
            <div className="cact__row lr-acts">
              <span className="cact__quiet">
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => void act.reopen(card)}>
                  {c.kind === "conflict" ? "Undo the pick" : "Undo accept"}
                </button>
              </span>
            </div>
          )}
          {group === "handled" && c.decided_by === "rows" && <p className="hint">Undo a row's decision from its row in the table.</p>}
        </>
      )}
    </>
  );
}

export function Checks({ review, act, scoped }: { review: LossReview; act: CheckActions; scoped: string | null }) {
  const cards = checkCards(review.checks);
  const NEED: Record<string, number> = { off: 0, needs_review: 1, conflicting: 2, unverified: 3 };
  const toResolve = cards.filter((c) => groupOf(c) === "to_resolve").sort((a, b) => (NEED[a.verdict] ?? 9) - (NEED[b.verdict] ?? 9));
  const handled = cards.filter((c) => groupOf(c) === "handled");
  const matches = cards.filter((c) => groupOf(c) === "matches");
  const [showMatches, setShowMatches] = useState(false);
  const matchTotals = matches.reduce((n, c) => n + c.checks.length, 0);
  const item = (card: CheckCard, folded = false) => (
    <li key={card.key} className={`ledger__item lr-item lr-item--${groupOf(card) === "to_resolve" ? card.verdict : "done"}${scoped === card.key ? " is-scoped" : ""}`} data-check={card.key}>
      <CardBody card={card} review={review} act={act} folded={folded} />
    </li>
  );
  return (
    <aside className="ledger lr-ledger" aria-labelledby="lr-checks-title">
      <h2 id="lr-checks-title" className="ledger__title">Checks</h2>
      <p className="ledger__sum">
        {cards.length} checks on {review.totals?.documents.length ?? 0} files.{" "}
        {toResolve.length ? `${toResolve.length} need you.` : "Every check is handled."}
      </p>
      <h3 className="ledger__group">To resolve <span className="count">{toResolve.length}</span></h3>
      {toResolve.length ? <ul className="ledger__list lr-resolve">{toResolve.map((c) => item(c))}</ul> : <p className="hint lr-clear">Nothing to resolve. The printed totals tie or carry your note.</p>}
      {handled.length > 0 && (
        <>
          <h3 className="ledger__group">Handled by you <span className="count">{handled.length}</span></h3>
          <ul className="ledger__list">{handled.map((c) => item(c))}</ul>
        </>
      )}
      <h3 className="ledger__group">
        <button type="button" className="ledger__fold" aria-expanded={showMatches} onClick={() => setShowMatches(!showMatches)}>
          <CaretDown size={16} aria-hidden className={showMatches ? "is-open" : ""} /> Matches{" "}
          <span className="count">{matches.length} printed {matches.length === 1 ? "line" : "lines"}, {matchTotals} totals</span>
        </button>
      </h3>
      <Collapse open={showMatches} animate={!keyboardAction()}>
        {showMatches && <ul className="ledger__list">{matches.map((c) => item(c, true))}</ul>}
      </Collapse>
      {review.notices.length > 0 && (
        <ul className="lr-notices">
          {review.notices.map((n, i) => <li key={i}>{n.message}</li>)}
        </ul>
      )}
    </aside>
  );
}
