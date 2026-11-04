import { useEffect, useId, useRef, useState } from "react";
import { FilePdf, Trash, WarningCircle } from "@phosphor-icons/react";
import { api, ApiError, uploadFile } from "../api/client";
import type { ClientDocument, DocumentInfo, Workspace } from "../api/types";
import { DropZone, formatBytes } from "../launch/Upload";
import { formatDate } from "./certUtil";

interface Pending {
  key: string;
  name: string;
  progress: number;
  error?: string;
}

/** The client's documents with their "already read" marker (C3, D3), fetched as the client name settles. */
export function useClientDocuments(client: string) {
  const [docs, setDocs] = useState<ClientDocument[] | null>(null);
  useEffect(() => {
    const name = client.trim();
    if (!name) {
      setDocs(null);
      return;
    }
    let live = true;
    const t = window.setTimeout(() => {
      api.clientDocuments(name).then(
        (d) => live && setDocs(d),
        () => live && setDocs([]),
      );
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [client]);
  return docs;
}

/** "Read on Nov 3, 2026": the same bytes were read before, so picking them costs no new read (D3). */
export function readMarker(doc: Pick<DocumentInfo, "sha256">, known: ClientDocument[] | null): string | null {
  const hit = known?.find((k) => k.sha256 === doc.sha256 && k.already_read);
  return hit ? `Read on ${formatDate(hit.read_at)}` : null;
}

/**
 * Policies for a certificate: pick the client's documents (deduped by sha256 on the server) or upload. Used by the
 * wizard's Policies step and by Add document on a draft (D2).
 */
export function PolicyPicker({
  workspace,
  client,
  selected,
  onChange,
  exclude = [],
  max,
  onBusy,
}: {
  workspace: Workspace;
  client: string;
  selected: DocumentInfo[];
  onChange: (docs: DocumentInfo[]) => void;
  /** Document ids already on the run (Add document). */
  exclude?: number[];
  max: number;
  onBusy?: (busy: boolean) => void;
}) {
  const known = useClientDocuments(client);
  const [pending, setPending] = useState<Pending[]>([]);
  const seq = useRef(0);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const listId = useId();
  const busy = pending.some((p) => !p.error);
  useEffect(() => onBusy?.(busy), [busy, onBusy]);

  const excluded = new Set(exclude);
  const excludedSha = new Set(known?.filter((k) => excluded.has(k.id)).map((k) => k.sha256) ?? []);
  const picks = (known ?? []).filter((k) => !excluded.has(k.id) && !excludedSha.has(k.sha256));
  const isPicked = (d: { id: number; sha256: string }) => selected.some((s) => s.id === d.id || s.sha256 === d.sha256);
  const uploads = selected.filter((s) => !picks.some((p) => p.id === s.id));
  const full = selected.length >= max;

  const toggle = (d: ClientDocument) => {
    if (isPicked(d)) onChange(selected.filter((s) => s.id !== d.id && s.sha256 !== d.sha256));
    else if (!full) onChange([...selected, d]);
  };

  const add = (files: File[]) => {
    let room = max - selectedRef.current.length - pending.filter((p) => !p.error).length;
    const fresh: Pending[] = [];
    for (const f of files) {
      seq.current += 1;
      const key = `u${seq.current}`;
      if (room <= 0) {
        fresh.push({ key, name: f.name, progress: 0, error: `A certificate uses at most ${max} policy PDFs. Remove one to add this file.` });
        continue;
      }
      room -= 1;
      fresh.push({ key, name: f.name, progress: 0 });
      const patch = (p: Partial<Pending>) => setPending((rs) => rs.map((r) => (r.key === key ? { ...r, ...p } : r)));
      uploadFile(f, (fr) => patch({ progress: fr }))
        .then((res) => {
          const doc = res.documents[0];
          if (!doc) return patch({ error: res.rejected[0]?.message ?? "This file was not accepted." });
          setPending((rs) => rs.filter((r) => r.key !== key));
          if (selectedRef.current.some((s) => s.sha256 === doc.sha256)) return;
          selectedRef.current = [...selectedRef.current, doc];
          onChange(selectedRef.current);
        })
        .catch((e: unknown) => patch({ error: e instanceof ApiError ? e.message : "Upload failed. Retry." }));
    }
    setPending((rs) => [...rs, ...fresh]);
  };

  const l = workspace.limits;
  return (
    <div className="cpick">
      {client.trim() && (
        <section className="cpick__group" aria-labelledby={`${listId}-h`}>
          <h3 id={`${listId}-h`} className="group-title">
            {client.trim()}'s documents
          </h3>
          {known == null ? (
            <div className="skeleton-lines" aria-hidden>
              <span />
              <span />
            </div>
          ) : picks.length === 0 ? (
            <p className="hint">No other documents for this client yet. Upload the declarations and endorsements below.</p>
          ) : (
            <ul className="cpick__list" aria-label={`${client.trim()}'s documents`}>
              {picks.map((d) => {
                const on = isPicked(d);
                return (
                  <li key={d.id}>
                    <label className={`cpick__row${on ? " is-on" : ""}${!on && full ? " is-off" : ""}`}>
                      <input type="checkbox" checked={on} disabled={!on && full} onChange={() => toggle(d)} />
                      <FilePdf size={20} aria-hidden className="file-row__icon" />
                      <span className="cpick__main">
                        <span className="file-row__name" title={d.filename}>
                          {d.filename}
                        </span>
                        <span className="file-row__meta">
                          {`${d.page_count ?? "?"} ${d.page_count === 1 ? "page" : "pages"}`}
                          {d.already_read ? (
                            <span className="cpick__read">Read on {formatDate(d.read_at)}, no new read</span>
                          ) : (
                            <span>, not read for a certificate yet</span>
                          )}
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}

      <section className="cpick__group" aria-labelledby={`${listId}-u`}>
        <h3 id={`${listId}-u`} className="group-title">
          Upload
        </h3>
        <div className="details__drop">
          <DropZone
            limits={l}
            onFiles={add}
            title="Drop declarations and endorsement PDFs"
            limitsLine={`PDF, up to ${l.max_pages} pages and ${l.max_megabytes} MB each, ${max} files per certificate`}
            disabled={full}
          />
        </div>
        {(uploads.length > 0 || pending.length > 0) && (
          <ul className="file-rows" aria-label="Uploaded for this certificate">
            {uploads.map((d) => {
              const marker = readMarker(d, known);
              return (
                <li key={d.id} className="file-row file-row--ready">
                  <FilePdf size={20} aria-hidden className="file-row__icon" />
                  <div className="file-row__main">
                    <span className="file-row__name" title={d.filename}>
                      {d.filename}
                    </span>
                    <span className="file-row__meta">
                      {d.warning ? (
                        <span className="file-row__warn">
                          <WarningCircle size={14} aria-hidden /> {d.warning}
                        </span>
                      ) : (
                        <>
                          {d.page_count} {d.page_count === 1 ? "page" : "pages"}, {formatBytes(d.size_bytes)}
                          {marker && <span className="cpick__read">{marker}, no new read</span>}
                        </>
                      )}
                    </span>
                  </div>
                  <button type="button" className="icon-btn" aria-label={`Remove ${d.filename}`} onClick={() => onChange(selected.filter((s) => s.id !== d.id))}>
                    <Trash size={16} aria-hidden />
                  </button>
                </li>
              );
            })}
            {pending.map((p) => (
              <li key={p.key} className={`file-row file-row--${p.error ? "rejected" : "uploading"}`}>
                <FilePdf size={20} aria-hidden className="file-row__icon" />
                <div className="file-row__main">
                  <span className="file-row__name" title={p.name}>
                    {p.name}
                  </span>
                  {p.error ? (
                    <span className="file-row__error" role="alert">
                      {p.error}
                    </span>
                  ) : (
                    <span className="file-row__meta">Uploading {Math.round(p.progress * 100)}%</span>
                  )}
                </div>
                {p.error && (
                  <button type="button" className="icon-btn" aria-label={`Dismiss ${p.name}`} onClick={() => setPending((rs) => rs.filter((r) => r.key !== p.key))}>
                    <Trash size={16} aria-hidden />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
