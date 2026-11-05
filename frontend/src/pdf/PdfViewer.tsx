import { useCallback, useEffect, useRef, useState } from "react";
import { CaretDown, CaretUp, MagnifyingGlassMinus, MagnifyingGlassPlus, ArrowsOutLineHorizontal } from "@phosphor-icons/react";
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { api } from "../api/client";
import { scrollWithin } from "../ui/scroll";
import type { Chip } from "../api/types";

GlobalWorkerOptions.workerSrc = workerUrl;

export interface PdfTarget {
  documentId: number;
  filename: string;
  letter: string;
  page: number;
  /** Highlight for this page; null when opened without a chip. */
  chip: Chip | null;
  /** Loss runs (R5, D13): a spreadsheet's rendered sheet page is its own one-page PDF. `documentId` is then the
   * cache key of that page (lossruns/sheetTarget), not the document's id. */
  url?: string;
  /** Opening zoom (default Fit width). A rendered sheet page is a landscape grid of small type: it opens readable. */
  zoom?: number;
  /** Caption under the page, replacing "Source <chip>, page <n>" (a sheet page names its row, not a page). */
  caption?: string;
}

const docCache = new Map<number, Promise<PDFDocumentProxy>>();

function loadDoc(documentId: number, url?: string): Promise<PDFDocumentProxy> {
  let p = docCache.get(documentId);
  if (!p) {
    p = getDocument({ url: url ?? api.fileUrl(documentId) }).promise;
    p.catch(() => docCache.delete(documentId));
    docCache.set(documentId, p);
  }
  return p;
}

/**
 * Source viewer (state table): page skeleton while loading, "Couldn't open this file" with
 * Retry on error, dashed passage boxes when the chip is verified, a page-level dashed box
 * when the page was read from a scan.
 */
export function PdfViewer({ target }: { target: PdfTarget }) {
  // The loaded document and the id it was loaded for, set together, so a page is never drawn
  // (or highlighted) under another document's name.
  const [loaded, setLoaded] = useState<{ id: number; doc: PDFDocumentProxy } | null>(null);
  const doc = loaded && loaded.id === target.documentId ? loaded.doc : null;
  const [error, setError] = useState(false);
  const [page, setPage] = useState(target.page);
  const [zoom, setZoom] = useState<number | "fit">(target.zoom ?? "fit");
  useEffect(() => setZoom(target.zoom ?? "fit"), [target.documentId, target.zoom]);
  const [pageInput, setPageInput] = useState(String(target.page));
  const [attempt, setAttempt] = useState(0);
  const [rendering, setRendering] = useState(true);
  /** What the canvas currently shows. The highlight is drawn only over the page it belongs to. */
  const [drawn, setDrawn] = useState<{ id: number; page: number } | null>(null);
  const [size, setSize] = useState<{ w: number; h: number; scale: number } | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const [frameWidth, setFrameWidth] = useState(0);

  useEffect(() => {
    setPage(target.page);
    setPageInput(String(target.page));
  }, [target.page, target.chip]);

  useEffect(() => {
    let live = true;
    const id = target.documentId;
    setRendering(true);
    setError(false);
    loadDoc(id, target.url).then(
      (d) => live && setLoaded({ id, doc: d }),
      () => live && setError(true),
    );
    return () => {
      live = false;
    };
  }, [target.documentId, attempt]);

  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => e && setFrameWidth(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!doc || !canvas.current || !frameWidth) return;
    let task: RenderTask | null = null;
    let live = true;
    const id = target.documentId;
    const n = Math.min(Math.max(1, page), doc.numPages);
    setRendering(true);
    doc.getPage(n).then((p) => {
      if (!live || !canvas.current) return;
      const base = p.getViewport({ scale: 1 });
      const scale = zoom === "fit" ? Math.max(0.3, (frameWidth - 32) / base.width) : zoom;
      const vp = p.getViewport({ scale });
      const ratio = window.devicePixelRatio || 1;
      const c = canvas.current;
      c.width = Math.floor(vp.width * ratio);
      c.height = Math.floor(vp.height * ratio);
      c.style.width = `${vp.width}px`;
      c.style.height = `${vp.height}px`;
      const ctx = c.getContext("2d");
      if (!ctx) return;
      task = p.render({ canvasContext: ctx, canvas: c, viewport: vp, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined });
      task.promise.then(
        () => {
          if (!live) return;
          setSize({ w: vp.width, h: vp.height, scale: vp.width / base.width });
          setDrawn({ id, page: n });
          setRendering(false);
        },
        () => undefined,
      );
    }, () => live && setError(true));
    return () => {
      live = false;
      task?.cancel();
    };
    // target.documentId is read only to label the drawing; `doc` already follows it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, page, zoom, frameWidth]);

  // Drawn and current: the canvas shows this document's page, not the previous target's.
  const ready = !rendering && drawn != null && drawn.id === target.documentId && drawn.page === page;

  // Scroll the highlight into view once the page is drawn.
  useEffect(() => {
    if (!ready || !frame.current) return;
    const hl = frame.current.querySelector<HTMLElement>(".pdf-highlight");
    if (hl) scrollWithin(frame.current, hl);
  }, [ready]);

  const total = doc?.numPages ?? 0;
  const goto = useCallback(
    (n: number) => {
      if (!total) return;
      const p = Math.min(Math.max(1, n), total);
      setPage(p);
      setPageInput(String(p));
    },
    [total],
  );
  const currentScale = typeof zoom === "number" ? zoom : size?.scale ?? 1;

  const chip = target.chip && target.chip.page_no === page ? target.chip : null;

  return (
    <div className="pdf">
      <div className="pdf__toolbar" role="toolbar" aria-label={`${target.filename} controls`}>
        <button type="button" className="icon-btn" aria-label="Previous page" disabled={page <= 1} onClick={() => goto(page - 1)}>
          <CaretUp size={16} aria-hidden />
        </button>
        <button type="button" className="icon-btn" aria-label="Next page" disabled={!total || page >= total} onClick={() => goto(page + 1)}>
          <CaretDown size={16} aria-hidden />
        </button>
        <form
          className="pdf__page"
          onSubmit={(e) => {
            e.preventDefault();
            goto(Number(pageInput) || page);
          }}
        >
          <input
            className="pdf__page-input"
            inputMode="numeric"
            aria-label="Page number"
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value.replace(/\D/g, ""))}
            onBlur={() => goto(Number(pageInput) || page)}
          />
          <span className="pdf__total">/ {total || "..."}</span>
        </form>
        <span className="pdf__spacer" />
        <button type="button" className="icon-btn" aria-label="Zoom out" onClick={() => setZoom(Math.max(0.4, +(currentScale - 0.2).toFixed(2)))}>
          <MagnifyingGlassMinus size={16} aria-hidden />
        </button>
        <button type="button" className="btn btn--ghost btn--sm" aria-pressed={zoom === "fit"} onClick={() => setZoom("fit")}>
          <ArrowsOutLineHorizontal size={16} aria-hidden /> Fit width
        </button>
        <button type="button" className="icon-btn" aria-label="Zoom in" onClick={() => setZoom(Math.min(3, +(currentScale + 0.2).toFixed(2)))}>
          <MagnifyingGlassPlus size={16} aria-hidden />
        </button>
      </div>
      <div className="pdf__frame" ref={frame} tabIndex={0} role="region" aria-label={`${target.filename}, page ${page} of ${total || "unknown"}`}>
        {error ? (
          <div className="pdf__error" role="alert">
            <p>Couldn't open this file.</p>
            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => {
                docCache.delete(target.documentId);
                setAttempt((a) => a + 1);
              }}
            >
              Retry
            </button>
          </div>
        ) : (
          <div className="pdf__page-wrap" style={size ? { width: size.w, height: size.h } : undefined}>
            {!ready && <div className="pdf__skeleton" aria-hidden />}
            {!ready && <span className="sr-only" role="status">Loading page</span>}
            <canvas ref={canvas} className={ready ? "" : "is-hidden"} data-drawn={drawn && ready ? `${drawn.id}:${drawn.page}` : undefined} />
            {ready && size && chip && (
              chip.verified && chip.rects.length ? (
                // One dashed box around the cited passage (the union of its line rects).
                (() => {
                  const k = size.w / chip.page_width;
                  const x0 = Math.min(...chip.rects.map((r) => r[0]));
                  const y0 = Math.min(...chip.rects.map((r) => r[1]));
                  const x1 = Math.max(...chip.rects.map((r) => r[2]));
                  const y1 = Math.max(...chip.rects.map((r) => r[3]));
                  return (
                    <span
                      className="pdf-highlight"
                      style={{ left: x0 * k - 6, top: y0 * k - 6, width: (x1 - x0) * k + 12, height: (y1 - y0) * k + 12 }}
                      aria-hidden
                    />
                  );
                })()
              ) : (
                <span className="pdf-highlight pdf-highlight--page" aria-hidden />
              )
            )}
          </div>
        )}
      </div>
      {chip && (
        <p className="pdf__caption">
          {target.caption ?? `Source ${chip.label}, page ${chip.page_no}`}
          {!chip.verified && ". Read from scan, unverified: the whole page is marked"}
        </p>
      )}
    </div>
  );
}
