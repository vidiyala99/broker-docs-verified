import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Queue navigation (owner, 2026-11-03; frontend/QUEUE-NAV-BRIEF.md) against the dev seed (backend/dev_seed.py):
// the client name on every queue row links to its report; the menu says "Open comparison"; rows show the date the
// work started, and carriers when a client shows on more than one row (seeded: run 5, Cedar Hollow read from the
// documents, and run 6, Cedar Hollow typed); the report header and crumbs name the client with the run number as
// metadata, "Change" when a name exists and "Add client" when none does (run 1); axe 0 at 1440, 1024 and 390.

const here = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.resolve(here, "../qa-screenshots/queue-nav");
const API = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const CEDAR = "Cedar Hollow Hospitality LLC";

const row = (page: Page, id: number) => page.locator(`li[data-run-id="${id}"]`);
const nameLink = (page: Page, id: number) => row(page, id).locator("a.queue-row__client");
const crumbs = (page: Page) => page.getByRole("navigation", { name: "Breadcrumb" });

async function axe(page: Page, label: string) {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const summary = res.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(" ")}`);
  console.log(`axe ${label}: ${res.violations.length} violations`, summary);
  expect(summary, `axe on ${label}`).toEqual([]);
}

async function patchRun(page: Page, id: number, data: Record<string, unknown>) {
  const res = await page.request.patch(`${API}/api/runs/${id}`, { data });
  expect(res.ok(), `PATCH run ${id} ${JSON.stringify(data)}`).toBeTruthy();
}

test.beforeEach(async ({ page }) => {
  for (const id of [1, 2, 3, 4, 5, 6]) await patchRun(page, id, { line: "commercial", queue_state: "open" });
});

test("the client name links to the row's report; one primary action stays; keyboard order is name, action, menu", async ({ page }) => {
  await page.goto("/#/");
  const link = nameLink(page, 6);
  await expect(link).toHaveAttribute("href", "#/runs/6");
  // Rows can share a client: the link's accessible name says which piece of work it opens.
  await expect(link).toHaveAccessibleName(new RegExp(`^${CEDAR} ?, comparison started .+, Harborline`));
  await expect(row(page, 6).getByRole("button", { name: /^Build proposal/ })).toBeVisible();
  await expect(row(page, 6).locator(".btn--primary")).toHaveCount(1);
  // Every name link is unique on the page.
  const names = await page.locator("a.queue-row__client").evaluateAll((els) => els.map((e) => e.textContent));
  expect(new Set(names).size).toBe(names.length);

  // Keyboard: Tab from the name reaches the row's action, then its menu.
  await link.focus();
  await page.keyboard.press("Tab");
  await expect(row(page, 6).getByRole("button", { name: /^Build proposal/ })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(row(page, 6).getByRole("button", { name: /^Actions for / })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Shift+Tab");
  await expect(link).toBeFocused();
  await page.screenshot({ path: path.join(SHOTS, "after-1440-queue-focus.png"), animations: "disabled" });

  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/runs\/6$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(`${CEDAR} · Compare Quotes`);
});

test("the row menu says what it opens; a row without a next step has no Open link", async ({ page }) => {
  await page.goto("/#/");
  await row(page, 6).getByRole("button", { name: /^Actions for / }).click();
  await expect(page.getByRole("menuitem", { name: "Open comparison" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Open run" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  // Every seeded Needs attention row can be retried, so the queue answer is edited to make one that cannot.
  await page.route(/\/api\/queue\?line=/, async (route) => {
    const res = await route.fetch();
    const q = await res.json();
    q.needs_attention = q.needs_attention.map((i: { run_id: number }) => (i.run_id === 2 ? { ...i, retryable: false } : i));
    await route.fulfill({ response: res, json: q });
  });
  await page.reload();
  await expect(row(page, 2)).toBeVisible();
  await expect(row(page, 2).getByRole("link", { name: /^Open/ })).toHaveCount(0);
  await expect(row(page, 2).getByRole("button", { name: /^Retry/ })).toHaveCount(0);
  await expect(nameLink(page, 2)).toHaveAttribute("href", "#/runs/2");
});

test("rows show when the work started, and carriers when a client shows twice; one line at 1440, 1024 and 390", async ({ page }) => {
  const q = await (await page.request.get(`${API}/api/queue?line=commercial`)).json();
  const items = ["ready", "needs_attention"].flatMap((g) => q[g]) as { run_id: number; client: string | null; created_at: string }[];
  // Seeded: runs 5 and 6 (earlier suites may add more comparisons of the Cedar Hollow fixtures).
  expect(items.filter((i) => i.client === CEDAR).map((i) => i.run_id)).toEqual(expect.arrayContaining([5, 6]));
  for (const [w, h] of [
    [1440, 900],
    [1024, 768],
    [390, 844],
  ] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto("/#/");
    await expect(row(page, 6)).toBeVisible();
    for (const id of [5, 6]) {
      // Shared client: the carriers say which comparison this is (short names, the rest counted).
      await expect(row(page, id).locator(".queue-row__summary")).toContainText(/^Harborline, \S/);
    }
    await expect(row(page, 5).locator(".queue-row__name")).toContainText("name from the documents");
    await expect(row(page, 6).locator(".queue-row__name")).not.toContainText("name from the documents");
    if (w > 700) {
      await expect(row(page, 4).locator(".queue-row__summary")).toHaveText("Compare Quotes · 2 carriers", { useInnerText: true });
      await expect(row(page, 6).locator(".queue-row__when")).toHaveText(/^[A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2}/, { useInnerText: true });
    } else {
      // Phone: the mark names the workflow and the day is enough.
      await expect(row(page, 4).locator(".queue-row__summary")).toHaveText("2 carriers", { useInnerText: true });
      await expect(row(page, 6).locator(".queue-row__when")).toHaveText(/^[A-Z][a-z]{2} \d{1,2}$/, { useInnerText: true });
    }
    // One line each.
    const heights = await page.locator(".queue-row__what").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height));
    for (const hgt of heights) expect(hgt).toBeLessThan(26);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
      .toBeLessThanOrEqual(0);
    await page.screenshot({ path: path.join(SHOTS, `after-${w}-queue.png`), fullPage: true, animations: "disabled" });
    await axe(page, `queue ${w}`);
  }
});

test("report header and crumbs name the client; run number is metadata; Change or Add client", async ({ page }) => {
  // Run 5: no typed name, the documents' name (the queue's fallback), with the same tag.
  await page.goto("/#/runs/5");
  const title = page.locator(".toolbar__title");
  await expect(title).toContainText(`${CEDAR} · Compare Quotes`);
  await expect(title.locator(".toolbar__source")).toHaveText("name from the documents");
  await expect(title.locator(".toolbar__version")).toHaveText(/^Run 5(, version \d+)?/);
  await expect(crumbs(page)).toHaveText(new RegExp(`Compare Quotes.*${CEDAR}$`));
  await expect(page).toHaveTitle(`${CEDAR}, Compare Quotes`);
  await expect(page.getByRole("button", { name: `Change client: ${CEDAR}` })).toHaveText("Change");

  // Run 1: nothing typed, nothing read: the old title, "Add client".
  await page.goto("/#/runs/1");
  await expect(title).toContainText("Compare Quotes, run 1");
  await expect(page.getByRole("button", { name: "Add client" })).toBeVisible();
  await expect(crumbs(page)).toHaveText(/Run 1$/);

  for (const [w, h] of [
    [1440, 900],
    [1024, 768],
    [390, 844],
  ] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto("/#/runs/5");
    await expect(page.locator(".report")).toBeVisible();
    await expect(title).toContainText(CEDAR);
    await page.screenshot({ path: path.join(SHOTS, `after-${w}-report.png`), animations: "disabled" });
    await axe(page, `report ${w}`);
  }
});
