import { useId, useRef, useState, type DragEvent } from "react";
import { FilePdf, Trash, UploadSimple, WarningCircle } from "@phosphor-icons/react";
import { api, ApiError, uploadFile } from "../api/client";
import type { DocumentInfo, Limits } from "../api/types";
import { MoreRow, useFold } from "../ui/Fold";

export interface FileRow {
  key: string;
  name: string;
  size: number;
  status: "uploading" | "ready" | "rejected";
  progress: number;
  doc?: DocumentInfo;
  message?: string;
}

export function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

export function limitsText(l: Limits): string {
  return `PDF, up to ${l.max_pages} pages and ${l.max_megabytes} MB each, ${l.max_documents_per_run} files per comparison`;
}

/** DG6 drop zone: states the limits, "Choose files" primary button, inline rejections naming the limit. */
export function DropZone({
  limits,
  onFiles,
  large,
  disabled,
  title = "Drop quote PDFs to compare",
  accept = "application/pdf,.pdf",
  limitsLine,
  buttonLabel = "Choose files",
  multiple = true,
}: {
  limits: Limits;
  onFiles: (files: File[]) => void;
  large?: boolean;
  disabled?: boolean;
  title?: string;
  /** File picker filter; the server still checks the content (policy check AMS: .json,.csv). */
  accept?: string;
  /** Replaces the PDF limits line (AMS exports have their own caps). */
  limitsLine?: string;
  buttonLabel?: string;
  multiple?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (disabled) return;
    const files = Array.from(e.dataTransfer.files);
    if (files.length) onFiles(files);
  };
  return (
    <div
      className={`dropzone${large ? " dropzone--large" : ""}${over ? " is-over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <UploadSimple size={large ? 28 : 20} aria-hidden className="dropzone__icon" />
      <p className="dropzone__title">{title}</p>
      <p className="dropzone__limits">{limitsLine ?? limitsText(limits)}</p>
      <button type="button" className="btn btn--primary" disabled={disabled} onClick={() => input.current?.click()}>
        {buttonLabel}
      </button>
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
    </div>
  );
}

/** Upload state shared by the drop zones (one request per file for per-row progress). */
export function useUploads(limits: Limits | null) {
  const [rows, setRows] = useState<FileRow[]>([]);
  const seq = useRef(0);

  const patch = (key: string, p: Partial<FileRow>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...p } : r)));

  const add = (files: File[]) => {
    const max = limits?.max_documents_per_run ?? 8;
    let accepted = rows.filter((r) => r.status !== "rejected").length;
    const next: FileRow[] = [];
    for (const f of files) {
      seq.current += 1;
      const key = `f${seq.current}`;
      if (accepted >= max) {
        next.push({ key, name: f.name, size: f.size, status: "rejected", progress: 0, message: `A comparison takes at most ${max} files. Remove one to add this file.` });
        continue;
      }
      accepted += 1;
      next.push({ key, name: f.name, size: f.size, status: "uploading", progress: 0 });
      uploadFile(f, (p) => patch(key, { progress: p }))
        .then((res) => {
          const doc = res.documents[0];
          if (doc) patch(key, { status: "ready", progress: 1, doc });
          else patch(key, { status: "rejected", message: res.rejected[0]?.message ?? "This file was not accepted." });
        })
        .catch((e: unknown) => patch(key, { status: "rejected", message: e instanceof ApiError ? e.message : "Upload failed. Retry." }));
    }
    setRows((rs) => [...rs, ...next]);
  };

  const remove = (key: string) => {
    const row = rows.find((r) => r.key === key);
    setRows((rs) => rs.filter((r) => r.key !== key));
    if (row?.doc) api.deleteDocument(row.doc.id).catch(() => undefined);
  };

  const clear = () => setRows([]);
  return { rows, add, remove, clear };
}

/**
 * Compact rows, same shape as the run panel's document rows: letter, filename, page count, status
 * glyph, Remove. A second line only for news (upload progress, a warning, a rejection). Past six
 * rows the quiet ones fold into "+N more"; uploading and rejected rows never fold.
 */
export function FileRows({ rows, onRemove }: { rows: FileRow[]; onRemove: (key: string) => void }) {
  const listId = useId();
  const fold = useFold(rows, (r) => r.status !== "ready" || Boolean(r.doc?.warning));
  if (!rows.length) return null;
  const letters = new Map<string, string>();
  rows.filter((r) => r.status === "ready").forEach((r, i) => letters.set(r.key, String.fromCharCode(65 + i)));
  return (
    <ul className="file-rows file-rows--compact" id={listId} aria-label="Files for this run">
      {fold.entries.map((e) => {
        if (e.more) return <MoreRow key="more" hidden={fold.hidden} expanded={fold.expanded} onToggle={fold.toggle} noun="files" controls={listId} />;
        const r = e.item;
        const letter = letters.get(r.key) ?? null;
        const pages = r.status === "ready" ? r.doc?.page_count : null;
        return (
          <li key={r.key} className={`file-row file-row--${r.status}`}>
            {letter ? (
              <span className={`doc-letter hue-${((letter.charCodeAt(0) - 65) % 5) + 1}`} aria-hidden>
                {letter}
              </span>
            ) : (
              <FilePdf size={20} aria-hidden className="file-row__icon" />
            )}
            <span className="file-row__name" title={r.name}>
              {letter && <span className="sr-only">Document {letter}: </span>}
              {r.name}
            </span>
            {pages != null && (
              <span className="file-row__pages">
                {pages} {pages === 1 ? "page" : "pages"}
                <span className="sr-only">, {formatBytes(r.doc!.size_bytes)}</span>
              </span>
            )}
            {/* Ready rows: the letter badge is the status; a warning carries its glyph on the news line. */}
            {r.status === "rejected" && <WarningCircle size={16} weight="fill" aria-hidden className="danger-glyph file-row__glyph" />}
            <button type="button" className="icon-btn file-row__remove" aria-label={`Remove ${r.name}`} onClick={() => onRemove(r.key)} disabled={r.status === "uploading"}>
              <Trash size={16} aria-hidden />
            </button>
            {r.status === "uploading" && (
              <span className="file-row__news">
                <span className="progress" role="progressbar" aria-label={`Uploading ${r.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(r.progress * 100)}>
                  <span className="progress__bar" style={{ transform: `scaleX(${r.progress})` }} />
                </span>
              </span>
            )}
            {r.status === "ready" && r.doc?.warning && (
              <span className="file-row__news file-row__warn">
                <WarningCircle size={14} weight="fill" aria-hidden /> {r.doc.warning}
              </span>
            )}
            {r.status === "rejected" && (
              <span className="file-row__news file-row__error" role="alert">
                {r.message}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
