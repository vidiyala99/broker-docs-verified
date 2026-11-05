import { useRef, useState, useSyncExternalStore, type DragEvent, type ReactNode } from "react";
import { File as FileGlyph, FileDoc, FilePdf, FileXls, UploadSimple } from "@phosphor-icons/react";
import { api, ApiError, uploadFile } from "../api/client";
import type { DocumentInfo, Limits, SourceFormat } from "../api/types";

// ---------------------------------------------------------------- formats (slice 6)

const EXT_OF_TYPE: Record<string, string> = {
  "application/pdf": ".pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
};

/** The picker filter for what POST /api/documents takes (`limits.accepts`, one list in backend/db.py): the MIME types
 *  plus their extensions, so every file dialog filters the same way. The server still checks the content. */
export function acceptOf(limits: Limits): string {
  const types = limits.accepts.split(",").map((t) => t.trim()).filter(Boolean);
  return [...types.map((t) => EXT_OF_TYPE[t]).filter(Boolean), ...types].join(",");
}

/** A file's format from its name, before the server says (upload rows still in flight or refused). */
export function formatOfName(name: string): SourceFormat | null {
  const ext = name.toLowerCase().split(".").pop();
  return ext === "pdf" || ext === "docx" || ext === "xlsx" ? ext : null;
}

export const FORMAT_WORD: Partial<Record<SourceFormat, string>> = { pdf: "PDF", docx: "Word", xlsx: "Excel" };

/** Format glyph (decorative): Phosphor's file family, one silhouette per format. */
export function FormatGlyph({ format, size = 20, className }: { format: SourceFormat | null | undefined; size?: number; className?: string }) {
  if (format === "docx") return <FileDoc size={size} aria-hidden className={className} />;
  if (format === "xlsx") return <FileXls size={size} aria-hidden className={className} />;
  if (format === null) return <FileGlyph size={size} aria-hidden className={className} />;
  return <FilePdf size={size} aria-hidden className={className} />;
}

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
  note,
}: {
  /** A second, quieter line under the limits line. */
  note?: ReactNode;
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
      {note && <p className="dropzone__note">{note}</p>}
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

/** Upload rows held outside React, so a list can outlive the screen that started it (slice 6: files dropped on the home
 *  page keep uploading and show on `#/compare/new`). */
export interface UploadStore {
  get: () => FileRow[];
  set: (fn: (rows: FileRow[]) => FileRow[]) => void;
  subscribe: (listener: () => void) => () => void;
}

export function createUploadStore(): UploadStore {
  let rows: FileRow[] = [];
  const listeners = new Set<() => void>();
  return {
    get: () => rows,
    set: (fn) => {
      rows = fn(rows);
      listeners.forEach((l) => l());
    },
    subscribe: (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
}

/** The new comparison's files: shared by the home page's empty-state drop zone and `#/compare/new` (spec 4.2). */
export const compareUploads = createUploadStore();

let uploadSeq = 0;

/** Upload state shared by the drop zones (one request per file for per-row progress). Pass a store to share the rows
 *  across screens; without one the rows live and die with the component. */
export function useUploads(limits: Limits | null, store?: UploadStore) {
  const [own] = useState(createUploadStore);
  const s = store ?? own;
  const rows = useSyncExternalStore(s.subscribe, s.get);
  const setRows = s.set;

  const patch = (key: string, p: Partial<FileRow>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...p } : r)));

  const add = (files: File[]) => {
    const max = limits?.max_documents_per_run ?? 8;
    let accepted = s.get().filter((r) => r.status !== "rejected").length;
    const next: FileRow[] = [];
    for (const f of files) {
      uploadSeq += 1;
      const key = `f${uploadSeq}`;
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
    const row = s.get().find((r) => r.key === key);
    setRows((rs) => rs.filter((r) => r.key !== key));
    if (row?.doc) api.deleteDocument(row.doc.id).catch(() => undefined);
  };

  const clear = () => setRows(() => []);
  return { rows, add, remove, clear };
}

