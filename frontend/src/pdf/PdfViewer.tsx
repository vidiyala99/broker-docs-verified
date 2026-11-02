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
}

const docCache = new Map<number, Promise<PDFDocumentProxy>>();

function loadDoc(documentId: number): Promise<PDFDocumentProxy> {
  let p = docCache.get(documentId);
  if (!p) {
    p = getDocument({ url: api.fileUrl(documentId) }).promise;
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
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState(false);
  const [page, setPage] = useState(target.page);
  const [zoom, setZoom] = useState<number | "fit">("fit");
  const [pageInput, setPageInput] = useState(String(target.page));
  const [attempt, setAttempt] = useState(0);
  const [rendering, setRendering] = useState(true);
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
    setDoc(null);
    setError(false);
    loadDoc(target.documentId).then(
      (d) => live && setDoc(d),
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
    setRendering(true);
    doc.getPage(Math.min(Math.max(1, page), doc.numPages)).then((p) => {
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
          setRendering(false);
        },
        () => undefined,
      );
    }, () => live && setError(true));
    return () => {
      live = false;
      task?.cancel();
    };
  }, [doc, page, zoom, frameWidth]);

  // Scroll the highlight into view once the page is drawn.
  useEffect(() => {
    if (rendering || !frame.current) return;
    const hl = frame.current.querySelector<HTMLElement>(".pdf-highlight");
    if (hl) scrollWithin(frame.current, hl);
  }, [rendering]);

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
      <div className="pdf__frame" ref={frame} tabIndex={0} aria-label={`${target.filename}, page ${page} of ${total || "unknown"}`}>
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
            {rendering && <div className="pdf__skeleton" aria-hidden />}
            {rendering && <span className="sr-only" role="status">Loading page</span>}
            <canvas ref={canvas} className={rendering ? "is-hidden" : ""} />
            {!rendering && size && chip && (
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
          Source {chip.label}, page {chip.page_no}
          {!chip.verified && ". Read from scan, unverified: the whole page is marked"}
        </p>
      )}
    </div>
  );
}
