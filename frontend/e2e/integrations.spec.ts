import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Slice 3b T5: Integrations mock (R7, INTEGRATIONS-BRIEF.md). Static page: notice, ten vendor
// names, inert Connect with a "Mock" explanation, live rail item. Needs the dev backend only for
// the shell's workspace call on other routes (see README.md).

const NOTICE = "Integrations are not designed yet. This page is a mock; nothing connects.";
const VENDORS = {
  "Agency management systems": ["Applied Epic", "AMS360", "Nexsure", "HawkSoft", "ImageRight"],
  "Documents and email": ["Gmail", "Outlook", "Google Drive", "OneDrive", "SharePoint"],
};
const ALL = Object.values(VENDORS).flat();

async function axe(page: Page, label: string) {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const summary = res.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(" ")}`);
  expect(summary, `axe on ${label}`).toEqual([]);
}

test("Rail item opens Integrations; notice, groups and all ten vendors show", async ({ page }) => {
  await page.goto("/");
  const rail = page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Integrations" });
  await rail.click();
  await expect(page).toHaveURL(/#\/integrations$/);
  await expect(page).toHaveTitle("Integrations");
  await expect(rail).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveText("Integrations");

  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 1, name: "Integrations" })).toBeVisible();
  const notice = main.getByRole("note");
  await expect(notice).toHaveText(NOTICE);
  await expect(notice).toBeVisible();

  for (const [group, names] of Object.entries(VENDORS)) {
    const region = main.getByRole("region", { name: group });
    await expect(region.getByRole("heading", { level: 2 })).toHaveText(group);
    await expect(region.getByRole("listitem")).toHaveCount(names.length);
    for (const name of names) await expect(region.getByRole("heading", { level: 3, name, exact: true })).toBeVisible();
  }
  // No logos: vendor tiles carry no images.
  await expect(main.locator(".integ-card img")).toHaveCount(0);
});

test("Connect is disabled, explains Mock to keyboard and screen readers, and does nothing", async ({ page }) => {
  await page.goto("/#/integrations");
  for (const name of ALL) {
    const btn = page.getByRole("button", { name: `Connect ${name}` });
    await expect(btn).toBeDisabled(); // aria-disabled="true"
    await expect(btn).toHaveText("Connect");
    await expect(btn).toHaveAccessibleDescription("Mock. Nothing connects.");
  }

  // Keyboard: the first Connect is reachable by Tab and shows the Mock tooltip; Escape hides it.
  await page.getByRole("link", { name: "Integrations" }).focus();
  const first = page.getByRole("button", { name: "Connect Applied Epic" });
  for (let i = 0; i < 5 && !(await first.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Tab");
  await expect(first).toBeFocused();
  await expect(page.locator(".tooltip")).toHaveText("Mock");
  await page.keyboard.press("Enter");
  await page.keyboard.press("Space");
  await expect(page).toHaveURL(/#\/integrations$/);
  await page.keyboard.press("Escape");
  await expect(page.locator(".tooltip")).toHaveCount(0);

  // Pointer: hover shows the same tooltip; a forced click changes nothing.
  const gmail = page.getByRole("button", { name: "Connect Gmail" });
  await gmail.hover();
  await expect(page.locator(".tooltip")).toHaveText("Mock");
  await gmail.click({ force: true });
  await expect(page).toHaveURL(/#\/integrations$/);
  await expect(page.getByRole("note")).toHaveText(NOTICE);
});

test("No network calls from the page itself", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  await page.waitForLoadState("networkidle");
  const calls: string[] = [];
  page.on("request", (r) => {
    if (!/\.(js|ts|tsx|css|svg|woff2?)(\?|$)/.test(r.url()) && !r.url().includes("/@")) calls.push(r.url());
  });
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Integrations" }).click();
  await expect(page.getByRole("note")).toBeVisible();
  await page.waitForTimeout(500);
  // The shell refreshes recent runs on every route change (App.tsx); nothing else may be called.
  expect(calls.filter((u) => !/\/api\/runs\?limit=/.test(u))).toEqual([]);
});

for (const vp of [
  { w: 1440, h: 900 },
  { w: 1024, h: 768 },
  { w: 390, h: 844 },
]) {
  test(`axe 0 at ${vp.w}`, async ({ page }) => {
    await page.setViewportSize({ width: vp.w, height: vp.h });
    await page.goto("/#/integrations");
    await expect(page.getByRole("note")).toBeVisible();
    await expect(page.getByRole("button", { name: /^Connect / })).toHaveCount(10);
    // No sideways scroll at any width.
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await axe(page, `integrations ${vp.w}`);
    // And with a Connect focused (tooltip open).
    await page.getByRole("button", { name: "Connect SharePoint" }).focus();
    await axe(page, `integrations ${vp.w}, focused Connect`);
  });
}
