import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Slice 6, lane B (T7): the Commercial home, the new comparison page (#/compare/new), the comparison's format labels,
// worse/best marks and the Key difference. Spec docs/specs/2026-11-05-compare-match-design.md sections 4-5 with its
// ledger (R3, R4, R5, R10); decisions in frontend/COMPARE-MATCH-BRIEF.md. Needs the dev backend on a fresh seed
// (backend/dev_seed.py: run 13 is the three_format comparison for Larchmont Tile and Stone LLC).

const here = path.dirname(fileURLToPath(import.meta.url));
const CMP = path.resolve(here, "../../tests/fixtures/compare");
const QUOTES = path.resolve(here, "../../tests/fixtures/quotes");
const SHOTS = path.resolve(here, "../qa-screenshots/compare-match");
const API = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const CLIENT = "Larchmont Tile and Stone LLC";

async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(SHOTS, `e2e-${name}.png`), animations: "disabled" });
}

async function axe(page: Page, label: string) {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const summary = res.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(" ")}`);
  console.log(`axe ${label}: ${res.violations.length} violations`, summary);
  expect(summary, `axe on ${label}`).toEqual([]);
}

async function noSideScroll(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(0);
}

/** Run 13 of the seed: found by its client, so the spec does not depend on run numbering. */
async function matchRun(page: Page): Promise<number> {
  const runs = (await (await page.request.get(`${API}/api/runs?limit=100`)).json()).runs as { id: number; client_name: string | null }[];
  const run = runs.find((r) => r.client_name === CLIENT);
  expect(run, "seeded three_format comparison").toBeTruthy();
  return run!.id;
}

const file = (name: string, from = CMP) => path.join(from, name);
const gate = (page: Page) => page.locator(".compare-new__gate");
const compare = (page: Page) => page.locator(".compare-new__go .btn--primary");

test("Commercial home is titled by its book and opens the upload page", async ({ page }) => {
  await page.goto("/#/");
  await expect(page).toHaveTitle("Commercial");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 1, name: "Commercial" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveText("Commercial");
  // No Upload or Input parameters accordion and no left panel on the home (spec 4.2).
  await expect(page.locator(".left-panel")).toHaveCount(0);
  await expect(main.getByRole("link", { name: "Check a renewal" })).toHaveAttribute("href", "#/policy-check/new");
  await main.getByRole("link", { name: "Compare quotes" }).click();
  await expect(page).toHaveURL(/#\/compare\/new$/);
  await expect(page.getByRole("heading", { level: 1, name: "New comparison" })).toBeVisible();
  // The old personal entry is the Personal lines page now.
  await page.goto("/#/?line=personal");
  await expect(page).toHaveURL(/#\/personal-lines$/);
});

test("New comparison: formats, refusals per file, the 2-quote minimum and Additional context", async ({ page }) => {
  await page.goto("/#/compare/new");
  const input = page.locator(".compare-new input[type=file]");
  await expect(input).toHaveAttribute("accept", /\.pdf.*\.docx.*\.xlsx/);
  await expect(compare(page)).toHaveAttribute("aria-disabled", "true");
  await expect(gate(page)).toHaveText("Add two quotes to compare.");
  await axe(page, "new comparison empty 1440");

  // R4: one ready quote keeps Compare blocked, and says why.
  await input.setInputFiles([{ name: "kestrel_quote.pdf", mimeType: "application/pdf", buffer: await readBytes(file("cmp_tri_a_quote.pdf")) }]);
  await expect(page.locator(".quote-row--ready")).toHaveCount(1);
  await expect(page.locator(".quote-row--ready")).toContainText("PDF, 2 pages");
  await expect(compare(page)).toHaveAttribute("aria-disabled", "true");
  await expect(gate(page)).toHaveText("Add one more quote to compare.");
  await expect(compare(page)).toHaveAttribute("aria-describedby", (await gate(page).getAttribute("id"))!);

  // Word and Excel quotes are read; .doc and .xls are refused per file with the server's message (rejected[]).
  await input.setInputFiles([
    { name: "brackwater_quote.docx", mimeType: "application/octet-stream", buffer: await readBytes(file("cmp_tri_b_quote.docx")) },
    { name: "old_quote.doc", mimeType: "application/msword", buffer: Buffer.from("not really a word file") },
    { name: "old_summary.xls", mimeType: "application/vnd.ms-excel", buffer: Buffer.from("not really an excel file") },
    { name: "hollis_bay_summary.xlsx", mimeType: "application/octet-stream", buffer: await readBytes(file("cmp_tri_c_summary.xlsx")) },
  ]);
  await expect(page.locator(".quote-row--ready")).toHaveCount(3);
  await expect(page.locator(".quote-row--ready .doc-letter")).toHaveText(["A", "B", "C"]);
  await expect(page.locator(".quote-row--ready").nth(1)).toContainText("Word, 2 pages");
  await expect(page.locator(".quote-row--ready").nth(2)).toContainText(/Excel, \d+ pages?/);
  const refused = page.locator(".quote-row--rejected");
  await expect(refused).toHaveCount(2);
  await expect(refused.nth(0)).toContainText("Save it as .docx and upload again.");
  await expect(refused.nth(1)).toContainText("Save it as .xlsx and upload again.");
  await expect(compare(page)).toHaveText("Compare 3 quotes");
  await expect(compare(page)).not.toHaveAttribute("aria-disabled", "true");
  await expect(gate(page)).toHaveText("Columns A to C, in the order added.");

  // Additional context: optional, capped at 1,000 characters, counted.
  const context = page.getByLabel(/^Additional context/);
  await expect(context).toHaveAttribute("placeholder", "Leave blank. BrokerDocs already knows what a quote comparison needs.");
  await context.fill("x".repeat(1100));
  await expect(context).toHaveValue("x".repeat(1000));
  await expect(page.getByText("1,000 of 1,000 characters")).toBeVisible();
  await context.fill("Client cares most about additional insured wording for its GC contracts.");
  await page.getByLabel(/^Client/).fill(CLIENT);
  await shot(page, "1440-new-files");
  await axe(page, "new comparison with files 1440");

  // The start request carries the documents in order, the book, the client and the context (the answer is
  // intercepted, so nothing is created and the seed stays as it is).
  await page.route(/\/api\/runs$/, (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 400, json: { error: { code: "invalid_run", message: "Intercepted by the test." } } })
      : route.fallback(),
  );
  const [post] = await Promise.all([
    page.waitForRequest((r) => r.method() === "POST" && /\/api\/runs$/.test(r.url())),
    compare(page).click(),
  ]);
  const body = post.postDataJSON();
  expect(body).toMatchObject({
    workflow: "compare-quotes",
    line: "commercial",
    client_name: CLIENT,
    parameters: { additional_context: "Client cares most about additional insured wording for its GC contracts." },
  });
  expect(body.document_ids).toHaveLength(3);
  await expect(page.getByRole("alert").filter({ hasText: "Intercepted by the test." })).toBeVisible();

  // Removing a ready quote re-letters the rest (list order = column order).
  await page.getByRole("button", { name: "Remove kestrel_quote.pdf" }).click();
  await expect(page.locator(".quote-row--ready .doc-letter")).toHaveText(["A", "B"]);
});

test("A drop on the empty home carries the files to the upload page", async ({ page }) => {
  await page.route(/\/api\/queue\?line=/, async (route) => {
    const res = await route.fetch();
    const q = await res.json();
    route.fulfill({ response: res, json: { ...q, in_progress: [], needs_review: [], ready: [], needs_attention: [], done_recently: [] } });
  });
  await page.goto("/#/");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "No commercial work yet." })).toBeVisible();
  await main.locator(".queue-empty input[type=file]").setInputFiles([file("carrier_a_clean.pdf", QUOTES), file("carrier_c_clean.pdf", QUOTES)]);
  await expect(page).toHaveURL(/#\/compare\/new$/);
  await expect(page.locator(".quote-row")).toHaveCount(2);
  await expect(page.locator(".quote-row--ready")).toHaveCount(2);
  await expect(compare(page)).toHaveText("Compare 2 quotes");
});

test("Comparison: format labels, marks in words, the Key difference and a Word source", async ({ page }) => {
  const id = await matchRun(page);
  await page.goto(`/#/runs/${id}`);
  await expect(page.locator(".report")).toBeVisible({ timeout: 30_000 });

  // Column heads name each document's format and kind (spec 5).
  const premium = page.locator("#section-premium");
  await expect(premium.locator(".col-head__kind")).toHaveText(["PDF quote", "Word quote", "Spreadsheet"]);

  // Worse: outlined value + diamond glyph, reason in words for screen readers (never colour alone).
  const ai = page.locator("#cell-additional_insured-B");
  await expect(ai.locator("[data-mark=worse]")).toHaveText("Scheduled only");
  await expect(ai.locator(".mark-glyph svg")).toBeVisible();
  await expect(ai).toHaveAttribute("aria-label", /worse than other quotes: Others: Blanket/);
  await expect(page.locator("[data-mark=worse]")).toHaveCount(5);
  // Best: success colour plus the LOWEST status word; "lowest premium" for screen readers.
  const best = page.locator("#cell-total_premium-B");
  await expect(best.locator("[data-mark=best]")).toHaveText("$16,975.00");
  await expect(best.locator(".mark-best__word")).toHaveText("Lowest");
  await expect(best).toHaveAttribute("aria-label", /lowest premium/);
  // The four term rows sit at the top of the endorsements table.
  await expect(page.locator("#section-endorsements tbody th").first()).toHaveText("Additional insured");

  // Key difference: first section, segments joined with spaces, a chip per cited cell, where it came from.
  const kd = page.locator(".key-difference");
  await expect(page.locator(".report-section").first()).toHaveClass(/key-difference/);
  await expect(kd.getByRole("heading", { name: "Key difference" })).toBeVisible();
  const text = (await kd.locator(".key-difference__text").innerText()).replace(/\s+/g, " ");
  expect(text).toContain("has the lowest premium ($16,975.00)");
  expect(text).not.toMatch(/\.[A-Z] \(/); // "...$16,975.00.B (" was the eval report's joined-with-"" reading
  await expect(kd.locator("button.chip")).not.toHaveCount(0);
  await expect(kd.locator(".key-difference__source")).toHaveText(/Written by rule from the marked cells\.|Drafted by the model/);
  await shot(page, "1440-report-kd");
  await axe(page, "comparison report 1440");

  // Marks stay under an evidence filter (spec 5).
  await page.locator(".trust__item--verified").click();
  await expect(page.locator("[data-mark=worse]")).toHaveCount(5);
  await expect(best.locator(".mark-best__word")).toBeVisible();
  await page.locator(".trust__item--verified").click();

  // A Word quote's chip opens its rendered page, and the source pane offers the original file.
  const chip = best.locator("button.chip").first();
  await expect(chip).toHaveAttribute("aria-label", /Rendered from the Word file/);
  await chip.click();
  const pane = page.locator(".source-pane");
  await expect(pane).toBeVisible();
  const original = pane.getByRole("link", { name: /Download original Word file/ });
  await expect(original).toHaveAttribute("href", /\/api\/documents\/\d+\/original$/);
  const res = await page.request.get(await original.getAttribute("href") as string);
  expect(res.ok()).toBeTruthy();
  expect(res.headers()["content-disposition"]).toContain("attachment");
});

test("An edit that moves the marks stales the Key difference; Rewrite cites the edited cell as edited by you", async ({ page }) => {
  const id = await matchRun(page);
  await page.goto(`/#/runs/${id}`);
  await expect(page.locator(".report")).toBeVisible({ timeout: 30_000 });
  // A's premium below B's: the lowest premium moves to A (or, on a re-run, A's cited value changes): stale (R3).
  const cents = String(Date.now() % 100).padStart(2, "0");
  const cell = page.locator("#cell-total_premium-A");
  await cell.dblclick();
  await cell.locator("textarea").fill(`$15,900.${cents}`);
  await cell.locator("textarea").press("Enter");
  await expect(cell.locator("[data-mark=best]")).toHaveText(`$15,900.${cents}`);
  const kd = page.locator(".key-difference");
  await expect(kd.locator(".stale-tag")).toHaveText("May be out of date");
  await kd.getByRole("button", { name: "Rewrite" }).click();
  await expect(kd.locator(".stale-tag")).toHaveCount(0, { timeout: 20_000 });
  // R10: the edited premium is cited as "edited by you", which takes the reader to the cell.
  const edited = kd.getByRole("button", { name: "A: edited by you. Go to the cell" });
  await expect(edited).toBeVisible();
  await shot(page, "1440-kd-edited");
  await edited.click();
  await expect(cell).toBeFocused();
});

test("R5: Policy Check's uploaded-files picker offers PDF records only", async ({ page }) => {
  await page.goto("/#/policy-check/new");
  if (await page.getByRole("button", { name: "Discard draft" }).isVisible()) await page.getByRole("button", { name: "Discard draft" }).click();
  await page.locator(".details__group").nth(0).getByRole("button", { name: "Choose from uploaded files" }).click();
  const list = page.locator(".details__group").nth(0).locator(".picker__list");
  await expect(list).toBeVisible();
  await expect(list.getByText(/\.pdf$/).first()).toBeVisible();
  await expect(list.getByText(/\.(docx|xlsx)$/)).toHaveCount(0);
});

for (const vp of [
  { w: 1440, h: 900 },
  { w: 1024, h: 768 },
  { w: 390, h: 844 },
]) {
  test(`axe 0 at ${vp.w}: home, new comparison, comparison report`, async ({ page }) => {
    await page.setViewportSize({ width: vp.w, height: vp.h });
    await page.goto("/#/");
    await expect(page.getByRole("heading", { level: 1, name: "Commercial" })).toBeVisible();
    await noSideScroll(page);
    await axe(page, `home ${vp.w}`);

    await page.goto("/#/compare/new");
    await page.locator(".compare-new input[type=file]").setInputFiles([
      { name: "brackwater_quote.docx", mimeType: "application/octet-stream", buffer: await readBytes(file("cmp_tri_b_quote.docx")) },
      { name: "old_quote.doc", mimeType: "application/msword", buffer: Buffer.from("not really a word file") },
    ]);
    await expect(page.locator(".quote-row--rejected")).toHaveCount(1);
    await expect(page.locator(".quote-row--ready")).toHaveCount(1);
    await noSideScroll(page);
    await shot(page, `${vp.w}-new`);
    await axe(page, `new comparison ${vp.w}`);

    await page.goto(`/#/runs/${await matchRun(page)}`);
    await expect(page.locator(".key-difference")).toBeVisible({ timeout: 30_000 });
    await page.locator("#cell-additional_insured-B").scrollIntoViewIfNeeded();
    await expect(page.locator("#cell-additional_insured-B [data-mark=worse]")).toBeVisible();
    await shot(page, `${vp.w}-report`);
    await axe(page, `comparison report ${vp.w}`);
  });
}

async function readBytes(p: string): Promise<Buffer> {
  const { readFile } = await import("node:fs/promises");
  return readFile(p);
}
