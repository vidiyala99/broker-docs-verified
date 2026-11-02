import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Left-panel design fix (LEFT-PANEL-BRIEF.md): Upload folds when a run is Done, compact document
// rows fold past six, chat collapses and takes the remaining height. Needs the dev backend seeded
// and serving (see README.md).

const here = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.resolve(here, "../../tests/fixtures/quotes");
const API = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function axe(page: Page, label: string) {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const summary = res.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(" ")}`);
  expect(summary, `axe on ${label}`).toEqual([]);
}

type SeedRun = { id: number; status: string; documents: { filename: string }[] };

async function seededRuns(page: Page): Promise<SeedRun[]> {
  return (await (await page.request.get(`${API}/api/runs?limit=50`)).json()).runs as SeedRun[];
}

test("Done run: Upload folds to a summary, chat takes the rest, both toggle by keyboard", async ({ page }) => {
  const runs = await seededRuns(page);
  const done = runs.find((r) => r.status === "done")!;
  const n = done.documents.length;
  await page.goto(`/#/runs/${done.id}`);
  await expect(page.locator(".report")).toBeVisible();

  const panel = page.locator(".left-panel");
  const upload = panel.locator(".accordion__toggle").first();
  await expect(upload).toHaveAttribute("aria-expanded", "false");
  await expect(upload).toContainText(`${n} documents, all read`);
  await expect(panel.locator(".doc-row").first()).toBeHidden();

  // Chat owns the remaining height: well over its 240px floor on a 900px window.
  const chat = panel.locator(".chat");
  const chatToggle = panel.locator(".chat__toggle");
  await expect(chatToggle).toHaveAttribute("aria-expanded", "true");
  expect((await chat.boundingBox())!.height).toBeGreaterThan(400);
  await axe(page, "done, upload folded");

  // Enter opens Upload (instant, no fade class on keyboard), rows show page counts.
  await upload.focus();
  await page.keyboard.press("Enter");
  await expect(upload).toHaveAttribute("aria-expanded", "true");
  await expect(panel.locator(".accordion__body").first()).not.toHaveClass(/fade-in/);
  await expect(panel.locator(".doc-row")).toHaveCount(n);
  await expect(panel.locator(".doc-row__pages").first()).toHaveText(/\d+ pages?/);

  // Chat folds to a docked handle that names what is inside; Upload then owns the panel.
  await chatToggle.click();
  await expect(chatToggle).toHaveAttribute("aria-expanded", "false");
  await expect(chatToggle).toContainText(/Ask about these quotes|\d+ messages?/);
  await expect(panel.locator("#chat-input")).toBeHidden();
  expect((await chat.boundingBox())!.height).toBeLessThan(64);
  await axe(page, "done, upload open, chat folded");
  await chatToggle.focus();
  await page.keyboard.press("Enter");
  await expect(chatToggle).toHaveAttribute("aria-expanded", "true");
  await expect(panel.locator("#chat-input")).toBeVisible();
});

test("Failed, cancelled and partial runs keep Upload open", async ({ page }) => {
  const runs = await seededRuns(page);
  for (const status of ["failed", "cancelled", "partial"]) {
    const run = runs.find((r) => r.status === status)!;
    await page.goto(`/#/runs/${run.id}`);
    await expect(page.locator(".run-head")).toBeVisible();
    await expect(page.locator(".left-panel .accordion__toggle").first()).toHaveAttribute("aria-expanded", "true");
  }
  // A failed row keeps its reason on a second line.
  const failed = runs.find((r) => r.status === "failed")!;
  await page.goto(`/#/runs/${failed.id}`);
  await expect(page.locator(".doc-row--failed .doc-row__news").first()).toBeVisible();
});

test("Long file list folds past six; rejected rows never fold", async ({ page }) => {
  await page.goto("/");
  const names = ["a_clean", "b_clean", "c_clean", "d_clean", "a_multicol", "b_multicol", "c_multicol", "d_multicol", "a_scanned", "b_scanned"];
  await page.locator(".upload input[type=file]").setInputFiles(names.map((n) => path.join(FIX, `carrier_${n}.pdf`)));
  await expect(page.locator(".file-row--rejected")).toHaveCount(2);
  const more = page.locator(".more-row__btn");
  await expect(more).toHaveText("+3 more");
  await expect(more).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".file-row--ready")).toHaveCount(5);
  // The fold sits where the folded rows are, before the rejected ones.
  const rows = await page.locator(".file-rows > li").evaluateAll((ls) => ls.map((l) => l.className));
  expect(rows.indexOf("more-row")).toBe(5);
  await axe(page, "launch, 10 files folded");

  await more.focus();
  await page.keyboard.press("Enter");
  await expect(more).toHaveText("Show fewer");
  await expect(more).toHaveAttribute("aria-expanded", "true");
  await expect(more).toBeFocused();
  await expect(page.locator(".file-row--ready")).toHaveCount(8);
});

test("Under 1280 the strip's Show chat and Show uploads open that section", async ({ page }) => {
  const runs = await seededRuns(page);
  const done = runs.find((r) => r.status === "done")!;
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto(`/#/runs/${done.id}`);
  await expect(page.locator(".report")).toBeVisible();
  await page.getByRole("button", { name: "Show uploads" }).click();
  const overlay = page.locator(".left-panel--overlay");
  await expect(overlay.locator(".accordion__toggle").first()).toHaveAttribute("aria-expanded", "true");
  await axe(page, "1024 overlay, uploads");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Show chat" }).click();
  await expect(overlay.locator(".chat__toggle")).toHaveAttribute("aria-expanded", "true");
  await expect(overlay.locator("#chat-input")).toBeVisible();
});
