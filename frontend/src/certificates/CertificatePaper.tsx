import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { PencilSimple } from "@phosphor-icons/react";
import type { CertificateBlock, CertificateField, Chip as ChipData } from "../api/types";
import { ChipList, type OpenSource } from "../report/Chip";
import { CANCELLATION, DISCLAIMER, printedDate, SAMPLE_NOTE, WORDING_LABEL, type ClaimState } from "./certUtil";
import { StateGlyph } from "./StateGlyph";

const HEAD_KEYS = new Set(["cert:carrier", "cert:naic", "cert:policy_number", "cert:effective_date", "cert:expiration_date"]);
const uniq = (ids: number[]) => [...new Set(ids)];

export interface PaperProps {
  cert: CertificateBlock;
  chips: Record<string, ChipData>;
  onChip: OpenSource;
  /** The region a ledger row points at (dashed passage box). */
  pointed: string | null;
  /** The worst claim state per region, drawn in the paper's left margin. */
  marks: Map<string, ClaimState>;
  /** "Draft, version 2" or "Issued Nov 3, 2026". */
  status: string;
  issued?: boolean;
  /** D12: a printed value the broker changes adds a sign-off row. Omitted = read-only. */
  onEdit?: (column: string, field: CertificateField, value: string) => Promise<void>;
}

/**
 * The certificate as the broker's document: one sheet, own layout (C6), serif for the paper's title and names, Inter
 * for the values. Every filled value carries its citation chip; a value that is not verified prints "Not stated".
 */
export function CertificatePaper({ cert, chips, onChip, pointed, marks, status, issued, onEdit }: PaperProps) {
  const titleId = useId();
  const region = (id: string, children: ReactNode, className = "", as: "div" | "li" = "div") => {
    const Tag = as;
    const mark = marks.get(id);
    return (
      <Tag key={id} className={`creg${pointed === id ? " is-pointed" : ""} ${className}`} data-region={id}>
        {mark && !id.startsWith("f-") && (
          <span className="creg__mark" aria-hidden>
            <StateGlyph state={mark} size={16} />
          </span>
        )}
        {children}
      </Tag>
    );
  };
  const insuredIds = uniq(cert.insured.flatMap((f) => f.cell.passage_ids));
  return (
    <article className={`paper${issued ? " paper--issued" : ""}`} aria-labelledby={titleId}>
      <header className="paper__head">
        <div>
          <h2 id={titleId} className="paper__title">
            Certificate of insurance
          </h2>
          <p className="paper__legal">{DISCLAIMER}</p>
          <p className="paper__sample">{SAMPLE_NOTE}</p>
        </div>
        <dl className="paper__date">
          <dt>Date</dt>
          <dd>{printedDate(cert.certificate_date)}</dd>
          <dt>Status</dt>
          <dd>{status}</dd>
        </dl>
      </header>

      <div className="paper__grid2">
        <section className="pbox">
          <h3 className="pbox__label">Producer</h3>
          <p className="pbox__value pbox__value--gap">{cert.producer?.cell.value ?? "Not stated"}</p>
        </section>
        {region(
          "insured",
          <>
            <h3 className="pbox__label">Insured</h3>
            {cert.insured.map((f) => (
              <Value key={f.key} field={f} column="insured" onEdit={onEdit} className={f.key === "cert:named_insured" ? "pbox__name" : ""} />
            ))}
            <ChipList ids={insuredIds.slice(0, 1)} chips={chips} onOpen={onChip} />
          </>,
          "pbox",
        )}
      </div>

      <section className="pbox">
        <h3 className="pbox__label">Insurers affording coverage</h3>
        {cert.insurers.length ? (
          <table className="ptable">
            <thead>
              <tr>
                <th scope="col">Insurer</th>
                <th scope="col">Name</th>
                <th scope="col" className="num">
                  NAIC
                </th>
              </tr>
            </thead>
            <tbody>
              {cert.insurers.map((i) => (
                <tr key={i.letter}>
                  <td>
                    <span className="pletter">{i.letter}</span>
                  </td>
                  <td>{i.name}</td>
                  <td className="num">{i.naic ?? "Not stated"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="pbox__value pbox__value--gap">Not stated</p>
        )}
      </section>

      <section className="pbox">
        <h3 className="pbox__label">Coverages</h3>
        {cert.policies.length === 0 ? (
          <p className="pbox__value pbox__value--gap">No coverage is printed.</p>
        ) : (
          <ol className="pcov">
            {cert.policies.map((p) => {
              const field = (k: string) => p.fields.find((f) => f.key === k);
              const num = field("cert:policy_number");
              const eff = field("cert:effective_date");
              const exp = field("cert:expiration_date");
              const limits = p.fields.filter((f) => !HEAD_KEYS.has(f.key));
              return (
                <li key={p.letter} className="pcov__row" data-policy={p.letter}>
                  <div className="pcov__line">
                    <span className="pletter" title={p.insurer ? `Insurer ${p.insurer}` : undefined}>
                      {p.insurer ?? "?"}
                    </span>
                    <span className="pcov__name">{p.line_label}</span>
                    {num && <Value field={num} column={p.letter} onEdit={onEdit} className="pcov__num" inline />}
                  </div>
                  {region(
                    `p-${p.letter}-dates`,
                    <>
                      {eff && (
                        <span className="pcov__date">
                          <span className="pk">Effective</span> <Value field={eff} column={p.letter} onEdit={onEdit} inline />
                        </span>
                      )}
                      {exp && (
                        <span className="pcov__date">
                          <span className="pk">Expires</span> <Value field={exp} column={p.letter} onEdit={onEdit} inline />
                        </span>
                      )}
                      <ChipList ids={uniq([...(eff?.cell.passage_ids ?? []), ...(num?.cell.passage_ids ?? [])]).slice(0, 1)} chips={chips} onOpen={onChip} />
                    </>,
                    "pcov__dates",
                  )}
                  <div className="pcov__limits">
                    <dl>
                      {limits.map((f) =>
                        region(
                          `f-${p.letter}-${f.key}`,
                          <>
                            <dt>{f.label}</dt>
                            <dd>
                              <Value field={f} column={p.letter} onEdit={onEdit} inline />
                            </dd>
                          </>,
                          `plimit${f.cell.state === "verified" || f.cell.edited ? "" : " is-gap"}`,
                        ),
                      )}
                    </dl>
                    <ChipList ids={uniq(limits.flatMap((f) => f.cell.passage_ids)).slice(0, 1)} chips={chips} onOpen={onChip} />
                  </div>
                  {p.wording.length > 0 && (
                    <div className="pcov__wording">
                      <h4 className="pbox__label">Wording</h4>
                      <ul>
                        {p.wording.map((w) => region(`p-${p.letter}-${w}`, <span>{WORDING_LABEL[w]}</span>, "pword", "li"))}
                      </ul>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </section>

      {region(
        "description",
        <>
          <h3 className="pbox__label">Description of operations</h3>
          <p className={`pbox__value pbox__desc${cert.description ? "" : " pbox__value--gap"}`}>{cert.description || "None"}</p>
        </>,
        "pbox",
      )}

      <div className="paper__grid2">
        <section className="pbox">
          <h3 className="pbox__label">Certificate holder</h3>
          <p className="pbox__value pbox__holder">{cert.holder_name}</p>
          {cert.holder_address && <p className="pbox__value pbox__addr">{cert.holder_address}</p>}
        </section>
        <section className="pbox">
          <h3 className="pbox__label">Cancellation</h3>
          <p className="paper__legal">{CANCELLATION}</p>
          <p className="paper__sample">{SAMPLE_NOTE}</p>
        </section>
      </div>
    </article>
  );
}

/** A printed value; on a draft it can be edited in place (D12), which adds a sign-off row to the checks. */
function Value({
  field,
  column,
  onEdit,
  className = "",
  inline = false,
}: {
  field: CertificateField;
  column: string;
  onEdit?: PaperProps["onEdit"];
  className?: string;
  inline?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(field.cell.value ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const errId = useId();
  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);
  const Tag = inline ? "span" : "p";
  const gap = field.cell.state !== "verified" && !field.cell.edited;
  if (editing && onEdit) {
    const save = async () => {
      const v = text.trim();
      if (!v) return setError("Enter a value, or cancel.");
      if (v === (field.cell.value ?? "")) return setEditing(false);
      setBusy(true);
      try {
        await onEdit(column, field, v);
        setEditing(false);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Couldn't save. Retry.");
      } finally {
        setBusy(false);
      }
    };
    return (
      <span className="pedit">
        <input
          ref={input}
          className="input pedit__input"
          aria-label={`${field.label}, new value`}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errId : undefined}
          value={text}
          maxLength={200}
          onChange={(e) => {
            setText(e.target.value);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") void save();
            if (e.key === "Escape") {
              e.stopPropagation();
              setEditing(false);
              window.setTimeout(() => button.current?.focus(), 0);
            }
          }}
        />
        <span className="pedit__row">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn--primary btn--sm" onClick={() => void save()} disabled={busy}>
            {busy ? "Saving..." : "Save"}
          </button>
        </span>
        {error && (
          <span id={errId} className="inline-error" role="alert">
            {error}
          </span>
        )}
        <span className="hint pedit__hint">A changed value is printed as you type it and needs your sign-off before issue.</span>
      </span>
    );
  }
  return (
    <Tag className={`pbox__value pval${gap ? " pbox__value--gap" : ""} ${className}`}>
      {field.cell.value ?? "Not stated"}
      {field.cell.edited && (
        <span className="pval__edited">
          <PencilSimple size={12} aria-hidden />
          <span className="sr-only"> (edited)</span>
        </span>
      )}
      {onEdit && (
        <button
          ref={button}
          type="button"
          className="pval__edit"
          aria-label={`Edit ${field.label}`}
          onClick={() => {
            setText(field.cell.value && field.cell.value !== "Not stated" ? field.cell.value : "");
            setEditing(true);
          }}
        >
          <PencilSimple size={14} aria-hidden />
        </button>
      )}
    </Tag>
  );
}
