/**
 * Scroll `el` into view inside `container` only. Element.scrollIntoView also scrolls every
 * ancestor, including overflow:hidden panes, which shifts the toolbar out of view.
 */
export function scrollWithin(container: HTMLElement, el: HTMLElement, block: "start" | "center" = "center") {
  const c = container.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  const delta = r.top - c.top;
  const target =
    block === "start" ? container.scrollTop + delta - 16 : container.scrollTop + delta - (container.clientHeight - r.height) / 2;
  container.scrollTo({ top: Math.max(0, target) });
  // A cell inside a sideways-scrolling table: bring its column into view too.
  const strip = el.closest<HTMLElement>(".table-scroll");
  if (strip) {
    const s = strip.getBoundingClientRect();
    if (r.left < s.left + 180 || r.right > s.right) strip.scrollLeft += r.left - s.left - 200;
  }
}
