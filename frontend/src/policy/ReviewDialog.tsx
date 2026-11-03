import { lazy, Suspense, useEffect, useId, useMemo, useRef, useState } from "react";
import { CaretLeft, CaretRight, X } from "@phosphor-icons/react";
import { POLICY_ROLES, type Chip as ChipData, type DiscrepancyItem, type PolicyRole, type Run } from "../api/types";
import type { PdfTarget } from "../pdf/PdfViewer";
import { hueOf } from "../report/Chip";
import { ItemHead, ResolveControls, type useResolve } from "./Discrepancies";
import { ROLE_LABEL } from "./draft";
import { slotChips, SlotView } from "./Slot";

const PdfViewer = lazy(() => import("../pdf/PdfViewer").then((m) => ({ default: m.PdfViewer })));

/** Renewal first: it is the document being checked (D10). */
const SOURCE_ORDER: PolicyRole[] = ["renewal", "expiring", "quote", "ams"];

function firstSource(item: DiscrepancyItem, chips: Record<string, ChipData>): { role: PolicyRole; chip: ChipData } | null {
  for (const role of SOURCE_ORDER) {
    const c = slotChips(item, role, chips)[0];
    if (c) return { role, chip: c };
  }
  return null;
}

/**
 * One-at-a-time review (spec section 6): "Discrepancy 3 of 14", Previous / Next, the item on the
 * left and the cited source beside it. Native <dialog> with showModal(): focus stays inside, Esc
 * closes, the page behind is inert. A chip here shows its source in the right pane.
 */
export function ReviewDialog({
  run,
  items,
  index,
  animate,
  onIndex,
  onClose,
  chips,
  editable,
  resolve,
}: {
  run: Run;
  items: DiscrepancyItem[];
  index: number;
  animate: boolean;
  onIndex: (i: number) => void;
  onClose: () => void;
  chips: Record<string, ChipData>;
  editable: boolean;
  resolve: ReturnType<typeof useResolve>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const headId = useId();
  const titleId = useId();
  const sourceId = useId();
  const i = Math.min(index, items.length - 1);
  const item = items[i];
  const [source, setSource] = useState<{ role: PolicyRole; chip: ChipData } | null>(() => (item ? firstSource(item, chips) : null));

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);

  useEffect(() => {
    if (item) setSource(firstSource(item, chips));
    // The focused control may belong to the previous item (a chip, the note editor): keep focus
    // in the dialog, on its heading, instead of letting it fall to the page behind.
    window.setTimeout(() => {
      const a = document.activeElement;
      if (!a || a === document.body || !ref.current?.contains(a)) document.getElementById(headId)?.focus();
    }, 0);
    // Only when the item changes; a resolve on the same item keeps the chosen source.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id]);

  const go = (n: number) => {
    if (n < 0 || n >= items.length) return;
    onIndex(n);
  };

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, .pdf__toolbar")) return;
      if (e.key === "ArrowRight") {
        e.preventDefault();
        go(i + 1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        go(i - 1);
      }
    };
    d.addEventListener("keydown", onKey);
    return () => d.removeEventListener("keydown", onKey);
  });

  const target: PdfTarget | null = useMemo(
    () =>
      source
        ? { documentId: source.chip.document_id, filename: source.chip.filename, letter: source.chip.letter, page: source.chip.page_no, chip: source.chip }
        : null,
    [source],
  );

  if (!item) return null;

  return (
    <dialog
      ref={ref}
      className={`review${animate ? " review--animate" : ""}`}
      aria-labelledby={headId}
      onClose={onClose}
      onCancel={(e) => {
        // Esc inside the note editor closes the editor, not the dialog.
        if ((document.activeElement as HTMLElement | null)?.closest("textarea")) e.preventDefault();
      }}
    >
      <header className="review__head">
        <h2 id={headId} className="review__count" aria-live="polite" tabIndex={-1}>
          Discrepancy {i + 1} of {items.length}
        </h2>
        <div className="review__nav">
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => go(i - 1)} disabled={i === 0}>
            <CaretLeft size={16} aria-hidden /> Previous
          </button>
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => go(i + 1)} disabled={i === items.length - 1}>
            Next <CaretRight size={16} aria-hidden />
          </button>
        </div>
        <button type="button" className="icon-btn review__close" aria-label="Close review" onClick={() => ref.current?.close()}>
          <X size={18} aria-hidden />
        </button>
      </header>
      <div className="review__body">
        <section className="review__detail" aria-labelledby={titleId}>
          <ItemHead item={item} titleId={titleId} as="h3" />
          <p className="disc__prose">{item.prose.map((s) => s.text).join("")}</p>
          <div className="review__slots">
            {POLICY_ROLES.map((role) => (
              <SlotView
                key={role}
                item={item}
                role={role}
                docs={run.documents}
                chips={chips}
                selectedPassage={source && source.role === role ? `${role}:${source.chip.passage_id}` : null}
                onOpen={(chip) => {
                  setSource({ role, chip });
                  if (window.matchMedia("(max-width: 899px)").matches) document.getElementById(sourceId)?.scrollIntoView({ block: "start" });
                }}
              />
            ))}
          </div>
          <ResolveControls item={item} editable={editable} resolve={resolve}>
            {target && (
              <a href={`#${sourceId}`} className="link-btn review__jump" onClick={(e) => {
                e.preventDefault();
                document.getElementById(sourceId)?.scrollIntoView({ block: "start" });
              }}>
                Show source
              </a>
            )}
          </ResolveControls>
        </section>
        <section className="review__source" id={sourceId} aria-label={target ? `Source: ${target.filename}` : "Source"}>
          {target && source ? (
            <>
              <div className="source-pane__head">
                <span className={`doc-letter hue-${hueOf(target.letter)}`} aria-hidden>
                  {target.letter}
                </span>
                <span className="source-pane__name">
                  {ROLE_LABEL[source.role]}: {target.filename}
                </span>
              </div>
              <Suspense fallback={<div className="pdf__frame"><div className="pdf__skeleton" aria-hidden /></div>}>
                <PdfViewer target={target} />
              </Suspense>
            </>
          ) : (
            <div className="state-block review__nosource">
              <p className="state-block__title">No cited source for this item.</p>
              <p className="hint">None of its slots has a verified value to open. Check the documents directly.</p>
            </div>
          )}
        </section>
      </div>
    </dialog>
  );
}
