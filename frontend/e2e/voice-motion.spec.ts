import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Voice + motion lane (frontend/VOICE-BRIEF.md, DESIGN.md "Voice" and "Motion") against the dev backend
// (backend/dev_seed.py). Checks: the shared sliding segmented control moves its indicator on a pointer change and has
// no transform transition under reduced motion; the Compare queue at 390 has no sideways overflow (the row menu used
// to be clipped); each "N severity" pair in a queue row stays on one line; the paper layer is loaded (serif on titles
// and client names, Inter elsewhere); final screenshots with axe at 1440, 1024 and 390.

const here = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.resolve(here, "../qa-screenshots/voice");

async function axe(page: Page, label: string) {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const summary = res.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(" ")}`);
  console.log(`axe ${label}: ${res.violations.length} violations`, summary);
  expect(summary, `axe on ${label}`).toEqual([]);
}

/** Horizontal offset of the segment's sliding indicator (its ::after transform). */
const indicatorX = (page: Page) =>
  page.locator(".line-choice__seg").first().evaluate((el) => new DOMMatrix(getComputedStyle(el, "::after").transform).m41);

test("segmented control: the indicator slides to the picked option; reduced motion has no transform transition", async ({ page }) => {
  // Slice 6: the Commercial home has no line toggle; the shared segment now lives on the new comparison page.
  await page.goto("/#/compare/new");
  const seg = page.locator(".line-choice__seg").first();
  await expect(seg.getByRole("radio", { name: "Commercial" })).toBeChecked();
  expect(await indicatorX(page)).toBeCloseTo(0, 0);

  // Pointer change: the indicator is part-way through its move shortly after the click, then rests on the option.
  // Line switch (deliberate update): sampled in the page on every frame, not by round trips 20ms apart, because the
  // 220ms ease-out covers 95% of the move in about 100ms and a busy run could miss it from outside.
  await seg.evaluate((el) => {
    const w = window as unknown as { __segX: number[] };
    w.__segX = [];
    const t0 = performance.now();
    const step = () => {
      w.__segX.push(new DOMMatrix(getComputedStyle(el, "::after").transform).m41);
      if (performance.now() - t0 < 1500) requestAnimationFrame(step);
    };
    el.addEventListener("change", () => requestAnimationFrame(step), { once: true });
  });
  await seg.getByText("Personal", { exact: true }).click();
  // Line switch (deliberate update): the indicator now sits on the joined track's border box (left: -1px), so its
  // translate to option 1 is the option's offset plus the 1px track edge.
  const target = await seg.locator(".line-choice__opt").nth(1).evaluate((el) => (el as HTMLElement).offsetLeft + 1);
  await expect.poll(() => indicatorX(page)).toBeCloseTo(target, 0);
  const samples = await page.evaluate(() => (window as unknown as { __segX: number[] }).__segX);
  expect(samples.some((x) => x > 1 && x < target - 1), `an in-between position was seen: ${samples.join(", ")}`).toBe(true);
  const transition = await seg.evaluate((el) => getComputedStyle(el, "::after").transitionProperty);
  expect(transition).toContain("transform");

  // Reduced motion: no transform transition (the indicator jumps).
  await page.emulateMedia({ reducedMotion: "reduce" });
  const reduced = await seg.evaluate((el) => {
    const s = getComputedStyle(el, "::after");
    return { property: s.transitionProperty, duration: s.transitionDuration };
  });
  expect(reduced.property === "none" || reduced.duration === "0s", JSON.stringify(reduced)).toBe(true);
  await seg.getByText("Commercial", { exact: true }).click();
  expect(await indicatorX(page)).toBeCloseTo(0, 0);
  await page.emulateMedia({ reducedMotion: null });
});

test("390: the Compare queue has no sideways overflow and the row menus are inside the pane", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/");
  await expect(page.locator(".queue-item").first()).toBeVisible();
  const overflow = await page.evaluate(() =>
    [...document.querySelectorAll("main, main *")]
      // Queue navigation (deliberate update): text set to end in an ellipsis (the one-line meta's carriers) is a
      // chosen truncation, not sideways overflow.
      .filter((e) => !e.classList.contains("sr-only") && getComputedStyle(e).textOverflow !== "ellipsis")
      .filter((e) => getComputedStyle(e).overflowX !== "visible" && e.scrollWidth > e.clientWidth + 1)
      .map((e) => `${e.className} ${e.scrollWidth}/${e.clientWidth}`),
  );
  expect(overflow).toEqual([]);
  // Slice 6: the home is one LaunchView pane (main.pane); the old launch body wrapper is gone.
  const pane = await page.locator("main.pane").boundingBox();
  for (const menu of await page.locator(".queue-row__actions .row-menu").all()) {
    const box = await menu.boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(pane!.x + pane!.width);
  }
});

test("390: each 'N severity' pair stays on one line", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/personal-lines");
  const pairs = page.locator(".queue-sev");
  await expect(pairs.first()).toBeVisible();
  const lines = await pairs.evaluateAll((els) => els.map((e) => ({ text: e.textContent, boxes: new Set([...e.getClientRects()].map((r) => Math.round(r.top))).size })));
  for (const p of lines) expect(p.boxes, `"${p.text}" wrapped`).toBe(1);
});

test("paper layer: serif on titles, section headings and client names; Inter for the system", async ({ page }) => {
  await page.goto("/#/personal-lines");
  await expect(page.locator(".queue-row__client").first()).toBeVisible();
  const family = (sel: string) => page.locator(sel).first().evaluate((el) => getComputedStyle(el).fontFamily);
  for (const sel of [".page-title", ".queue-group__title", ".queue-row__client"]) expect(await family(sel)).toContain("Source Serif 4");
  for (const sel of [".queue-row__what", ".queue-row__status", ".queue-group__count", ".btn"]) expect(await family(sel)).toContain("Inter");
  await expect.poll(() => page.evaluate(() => document.fonts.check('600 19px "Source Serif 4"'))).toBe(true);
});

test("focus returns to the opener after pointer closes that play an exit (review dialog, task dialog, overlay)", async ({ page }) => {
  // Review dialog, closed with its X (pointer: 150ms exit before the native close).
  await page.goto("/#/runs/7");
  const opener = page.getByRole("button", { name: "Review one at a time" });
  await opener.click();
  await page.getByRole("button", { name: "Close review" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(opener).toBeFocused();

  // Task dialog opened from a row title, cancelled by pointer.
  await page.goto("/#/tasks");
  const title = page.locator(".task-title").first();
  await title.click();
  const dialog = page.getByRole("dialog", { name: "Edit task" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(title).toBeFocused();

  // Narrow left panel overlay: Hide panel by pointer slides it out; focus goes back to the strip's toggle.
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/#/runs/7");
  const show = page.locator(".left-strip").getByRole("button").first();
  await show.click();
  await page.getByRole("button", { name: "Hide panel" }).click();
  await expect(page.locator(".left-panel--overlay")).toHaveCount(0);
  await expect(show).toBeFocused();
});

const VIEWS: [string, string, string][] = [
  ["queue", "/#/", ".queue-group"],
  ["personal", "/#/personal-lines", ".queue-group"],
  ["run", "/#/runs/7", ".toolbar__title"],
  ["tasks", "/#/tasks", ".tasks__count"],
  ["activity", "/#/activity", ".run-link, .state-block"],
  ["policy-wizard", "/#/policy-check/new", ".wizard__title"],
  ["proposal-wizard", "/#/proposals/new", ".wizard__title"],
];

for (const [w, h] of [
  [1440, 900],
  [1024, 768],
  [390, 844],
] as const) {
  test(`final screenshots and axe 0 at ${w}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    for (const [name, url, ready] of VIEWS) {
      await page.goto(url);
      await expect(page.locator(ready).first()).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      if (w !== 1024) await page.screenshot({ path: path.join(SHOTS, `final-${w}-${name}.png`), animations: "disabled" });
      await axe(page, `${name} ${w}`);
    }
  });
}
