import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Line switch (frontend/LINE-SWITCH-BRIEF.md, DESIGN.md "Motion" and "Line move"): the Line of business segment is one
// joined track whose indicator moves visibly from the first frame on a pointer flip (220ms --ease-out), its check glyph
// cross-fades, and the Policy Check wizard names the chosen book in its crumbs and its title's book tab. Keyboard flips
// are instant. The Compare start screen leads its crumbs with the queue's book.

async function axe(page: Page, label: string) {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const summary = res.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(" ")}`);
  expect(summary, `axe on ${label}`).toEqual([]);
}

const crumbs = (page: Page) => page.getByRole("navigation", { name: "Breadcrumb" });
const seg = (page: Page) => page.getByRole("group", { name: "Line of business" });
const tab = (page: Page) => page.locator(".wizard__titlebar .booktab__tab");

type Sample = { t: number; x: number; checkOn: number; tab: number };

/** Samples the indicator, the incoming check glyph and the book tab on every frame after the next change event. */
async function armSampler(page: Page) {
  await page.evaluate(() => {
    const w = window as unknown as { __samples: Sample[] };
    w.__samples = [];
    const el = document.querySelector(".line-choice__seg") as HTMLElement;
    let t0 = 0;
    const step = () => {
      const t = performance.now() - t0;
      const checked = el.querySelector("input:checked")!.closest(".line-choice__opt")!.querySelector(".line-choice__check")!;
      const bookTab = document.querySelector(".wizard__titlebar .booktab__tab");
      w.__samples.push({
        t,
        x: new DOMMatrix(getComputedStyle(el, "::after").transform).m41,
        checkOn: Number(getComputedStyle(checked).opacity),
        tab: bookTab ? Number(getComputedStyle(bookTab).opacity) : 1,
      });
      if (t < 300) requestAnimationFrame(step);
    };
    el.addEventListener("change", () => {
      t0 = performance.now();
      requestAnimationFrame(step);
    }, { once: true });
  });
}

const samples = (page: Page) => page.evaluate(() => (window as unknown as { __samples: Sample[] }).__samples);
const target = (page: Page) => page.locator(".line-choice__opt").nth(1).evaluate((el) => (el as HTMLElement).offsetLeft);

test("pointer flip: the indicator moves from the first frame; crumbs and the book tab follow the choice", async ({ page }) => {
  await page.goto("/#/policy-check/new");
  await expect(seg(page).getByRole("radio", { name: "Commercial" })).toBeChecked();
  await expect(crumbs(page)).toHaveText(/^Commercial.*Policy check.*New check$/);
  await expect(tab(page)).toHaveText("Commercial");

  await armSampler(page);
  await seg(page).getByText("Personal", { exact: true }).click();
  await expect(seg(page).getByRole("radio", { name: "Personal" })).toBeChecked();
  await expect.poll(async () => (await samples(page)).at(-1)?.t ?? 0).toBeGreaterThan(300);
  const s = await samples(page);
  const end = await target(page);
  // Before (line switch audit): ~0.2px of 131px in the first 16ms and 18% after 80ms (--ease-in-out). Now the first
  // frames already show the move: past 10% within 40ms, and past half within 80ms.
  const early = s.filter((p) => p.t <= 40 && p.x > 0);
  expect(early.length, JSON.stringify(s.slice(0, 6))).toBeGreaterThan(0);
  expect(Math.max(...early.map((p) => p.x)), JSON.stringify(s.slice(0, 6))).toBeGreaterThan(end * 0.1);
  const by80 = s.filter((p) => p.t <= 80);
  expect(Math.max(...by80.map((p) => p.x))).toBeGreaterThan(end * 0.5);
  // The check glyph cross-fades in (an in-between opacity was seen), no visibility snap.
  expect(s.some((p) => p.checkOn > 0.05 && p.checkOn < 0.95), JSON.stringify(s.map((p) => p.checkOn))).toBe(true);
  // The book tab drops in on a pointer flip.
  expect(s.some((p) => p.tab < 0.95)).toBe(true);
  // Rests on the option (the indicator sits on the track's border box, so it is offset by the 1px edge).
  expect(Math.abs((await samples(page)).at(-1)!.x - end)).toBeLessThan(2);

  await expect(crumbs(page)).toHaveText(/^Personal lines.*Policy check.*New check$/);
  await expect(crumbs(page).getByRole("link", { name: "Personal lines" })).toHaveAttribute("href", "#/personal-lines");
  await expect(tab(page)).toHaveText("Personal lines");
  // The wizard posts the chosen line: Cancel still goes back to where it was opened from.
  await expect(page.getByRole("link", { name: "Cancel" })).toHaveAttribute("href", "#/");

  for (const [w, h] of [[1440, 900], [1024, 768], [390, 844]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await expect(tab(page)).toBeVisible();
    await page.waitForTimeout(250);
    await axe(page, `policy check wizard, personal picked, ${w}`);
  }
});

test("keyboard flip is instant: indicator, glyph, crumb and tab land on the first frame", async ({ page }) => {
  await page.goto("/#/policy-check/new?line=personal");
  await expect(seg(page).getByRole("radio", { name: "Personal" })).toBeChecked();
  await expect(crumbs(page)).toHaveText(/^Personal lines.*New check$/);
  await expect(tab(page)).toHaveText("Personal lines");

  await seg(page).getByRole("radio", { name: "Personal" }).focus();
  await armSampler(page);
  await page.keyboard.press("ArrowLeft");
  await expect(seg(page).getByRole("radio", { name: "Commercial" })).toBeChecked();
  await expect(seg(page).getByRole("radio", { name: "Commercial" })).toBeFocused();
  await expect.poll(async () => (await samples(page)).length).toBeGreaterThan(2);
  const first = (await samples(page))[0];
  expect(first.x, JSON.stringify(first)).toBeCloseTo(0, 0);
  expect(first.checkOn).toBe(1);
  expect(first.tab).toBe(1);
  await expect(crumbs(page)).toHaveText(/^Commercial.*Policy check.*New check$/);
  await expect(tab(page)).toHaveText("Commercial");
  await expect(page.locator(".crumbs li.is-refiled")).toHaveCount(0);
  // Opened from Personal lines: Cancel still goes back there.
  await expect(page.getByRole("link", { name: "Cancel" })).toHaveAttribute("href", "#/personal-lines");
});

// Slice 6 (lane B, allowed edit): the Compare start screen is now its own page (#/compare/new) rather than the book's
// queue, so its crumbs and title tab follow the Line choice like the Policy Check wizard's.
test("New comparison page: the crumbs and the book tab follow the Line choice", async ({ page }) => {
  await page.goto("/#/compare/new");
  await expect(crumbs(page)).toHaveText(/^Commercial.*Compare Quotes.*New comparison$/);
  await expect(tab(page)).toHaveText("Commercial");
  // Both halves keep one width, and the indicator lands on the picked half.
  const widths = await page.locator(".line-choice__opt").evaluateAll((els) => els.map((e) => (e as HTMLElement).offsetWidth));
  expect(Math.abs(widths[0] - widths[1]), widths.join(", ")).toBeLessThanOrEqual(1);
  await seg(page).getByText("Personal", { exact: true }).click();
  await expect(seg(page).getByRole("radio", { name: "Personal" })).toBeChecked();
  const left = await page.locator(".line-choice__opt").nth(1).evaluate((el) => (el as HTMLElement).offsetLeft);
  await expect
    .poll(() => page.locator(".line-choice__seg").evaluate((el) => new DOMMatrix(getComputedStyle(el, "::after").transform).m41))
    .toBeCloseTo(left + 1, 0);
  await expect(crumbs(page)).toHaveText(/^Personal lines.*Compare Quotes.*New comparison$/);
  await expect(tab(page)).toHaveText("Personal lines");
  await page.goto("/#/compare/new?line=personal");
  await expect(crumbs(page)).toHaveText(/^Personal lines.*Compare Quotes.*New comparison$/);
  await expect(page.getByRole("link", { name: "Cancel" })).toHaveAttribute("href", "#/personal-lines");
});

test("Tasks filter at 390: options size to text and the selection is the option's own fill", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/tasks");
  const filter = page.locator(".tasks__filter .line-choice__seg");
  await expect(filter).toBeVisible();
  await filter.getByText("Done", { exact: true }).click();
  await expect(filter.getByRole("radio", { name: "Done" })).toBeChecked();
  await page.mouse.move(0, 0);
  await expect.poll(() => filter.locator(".line-choice__opt").nth(1).evaluate((e) => getComputedStyle(e).backgroundColor)).toBe("rgb(238, 236, 253)");
  await expect.poll(() => filter.locator(".line-choice__opt").nth(0).evaluate((e) => getComputedStyle(e).backgroundColor)).toBe("rgba(0, 0, 0, 0)");
  expect(await filter.evaluate((el) => getComputedStyle(el, "::after").content)).toBe("none");
});
