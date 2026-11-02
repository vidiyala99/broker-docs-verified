import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// T7 Proposal Builder happy path (spec section 6, "Frontend"): template, details, outline edit,
// generate with the dev FakeProvider (backend/dev_seed.py serve, no tokens), open, export.
// axe on every wizard step at 1440 and 1024, step 3 at 390. Screenshots: qa-screenshots/wizard/.

const here = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.resolve(here, "../../tests/fixtures/quotes");
const SHOTS = path.resolve(here, "../qa-screenshots/wizard");

async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), animations: "disabled" });
}

/** Wizard steps must be fully clean: every axe violation fails, not only serious ones. */
async function axe(page: Page, label: string, strict = true) {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const bad = strict ? res.violations : res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const summary = bad.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(" ")}`);
  console.log(`axe ${label}: ${res.violations.length} violations`, summary);
  expect(summary, `axe on ${label}`).toEqual([]);
}

async function noSideScroll(page: Page) {
  // Polls: the compact stepper follows the viewport state React sets after the resize event.
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), {
      message: "no horizontal page scroll",
    })
    .toBeLessThanOrEqual(0);
}

const blockTitles = (page: Page) => page.locator(".blocks .block__title").allTextContents();

test("proposal wizard: template, details, outline edit, generate, open, export", async ({ page }) => {
  // ---------------------------------------------------------------- entry point and step 1
  await page.goto("/");
  await page.getByRole("link", { name: "Proposals, new proposal" }).click();
  await expect(page).toHaveURL(/#\/proposals\/new/);
  await expect(page.getByRole("heading", { name: "New proposal" })).toBeVisible();
  await expect(page.locator(".stepper [aria-current=step]")).toContainText("Template");
  await expect(page.locator(".tpl-option")).toHaveCount(2);
  await expect(page.locator(".contents__row").first()).toBeVisible();

  // Continue without a choice: inline error, no step change.
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Choose a template to continue.")).toBeVisible();

  // Search filters by section title; empty search offers a way back.
  await page.getByLabel("Search templates").fill("cyber");
  await expect(page.locator(".tpl-option")).toHaveCount(1);
  await expect(page.locator(".tpl-option")).toContainText("Premier");
  await page.getByLabel("Search templates").fill("zzz");
  await expect(page.getByText('No template matches "zzz".')).toBeVisible();
  await page.getByRole("button", { name: "Clear search" }).click();

  await page.getByRole("radio", { name: /Standard/ }).check();
  await expect(page.locator(".tpl__preview-title")).toHaveText("Standard template");
  await shot(page, "1440-step1-template");
  await axe(page, "step 1 at 1440");

  // ---------------------------------------------------------------- step 2
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.locator(".stepper [aria-current=step]")).toContainText("Details");
  await expect(page.locator("#step-heading")).toBeFocused();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Add at least one quote PDF.")).toBeVisible();

  await page.locator(".details__drop input[type=file]").setInputFiles([
    path.join(FIX, "carrier_a_clean.pdf"),
    path.join(FIX, "carrier_c_clean.pdf"),
  ]);
  const quotes = page.getByRole("list", { name: "Quotes in this proposal" }).locator(".file-row--ready");
  await expect(quotes).toHaveCount(2);
  // Column order follows the order the files were chosen, whatever order the uploads finish in.
  await expect(quotes.nth(0)).toContainText("carrier_a_clean.pdf");
  await expect(quotes.nth(1)).toContainText("carrier_c_clean.pdf");
  await expect(page.getByText("Add at least one quote PDF.")).toHaveCount(0);

  await page.getByLabel(/Client name/).fill("Cedar Hollow LLC");
  await page.getByLabel(/^Broker name/).fill("Dana Ruiz");
  await page.getByLabel(/Brokerage name/).fill("Ruiz and Partners");
  await page.getByLabel(/Additional context/).fill("Renewal; the client wants a lower retention.");
  await expect(page.locator("#c-additional_context")).toHaveText(/^\d+ \/ 4,000$/);
  await page.getByRole("button", { name: "Choose from uploaded files" }).click();
  await expect(page.locator(".picker__row").first()).toBeVisible();
  await shot(page, "1440-step2-details");
  await axe(page, "step 2 at 1440");

  // ---------------------------------------------------------------- step 3: outline edit
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.locator(".stepper [aria-current=step]")).toContainText("Outline");
  const before = await blockTitles(page);
  expect(before.slice(0, 2)).toEqual(["Locked: Cover Page", "Locked: Table of Contents"]);
  // Locked blocks have no move, edit or delete controls.
  await expect(page.locator(".block.is-locked button")).toHaveCount(0);

  // Keyboard reorder: Enter on "Move down", focus stays on the moved block, live region announces.
  const firstMovable = before[2]!.replace(/^Section 1: /, "");
  const down = page.getByRole("button", { name: `Move ${firstMovable} down` });
  await down.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: `Move ${firstMovable} down` })).toBeFocused();
  await expect(page.locator(".blocks .block__title").nth(3)).toHaveText(`Section 2: ${firstMovable}`);
  await expect(page.locator(".outline-step [role=status]")).toContainText(`${firstMovable} moved to position 2`);
  // The first numbered block cannot move above the locked blocks.
  const top = (await blockTitles(page))[2]!.replace(/^Section 1: /, "");
  await expect(page.getByRole("button", { name: `Move ${top} up` })).toBeDisabled();

  // Add a narrative section and title it.
  await page.getByRole("button", { name: "Add section" }).click();
  await page.getByRole("button", { name: "Add Narrative" }).click();
  const newTitle = page.locator("#f-narrative_2-title");
  await expect(newTitle).toBeFocused();
  await newTitle.fill("Why we recommend Northbeam");
  await page.locator("#f-narrative_2-ai_instruction").fill("Explain the recommendation in two sentences.");

  // Delete Next Steps, with Undo in the toast.
  await page.getByRole("button", { name: "Delete Next Steps" }).click();
  await expect(page.locator(".toast")).toContainText("Removed Next Steps.");
  await expect(page.locator(".blocks .block__title", { hasText: "Next Steps" })).toHaveCount(0);

  // Client-side rule: one table per fact section (D4), shown inline and in the summary.
  await page.locator('[data-focus="client_details:edit"]').click();
  await page.locator("#fs-client_details-premium").check();
  await page.getByRole("button", { name: "Generate proposal" }).click();
  const summary = page.locator(".error-summary");
  await expect(summary).toBeFocused();
  await expect(summary).toContainText("A fact section can have only one table.");
  await expect(page.locator(".block.has-error")).toHaveCount(1);
  await shot(page, "1440-step3-error-d4");
  await axe(page, "step 3 with errors at 1440");
  await page.locator("#fs-client_details-premium").uncheck();
  await expect(summary).toHaveCount(0);

  // Server 422 invalid_request: fields[].loc mapped onto the block and field (string indexes, as the API sends).
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "invalid_request",
          message: "The proposal settings are not valid.",
          fields: [
            { loc: ["body", "parameters", "outline", "2", "title"], message: "The server rejected this title." },
            { loc: ["body", "parameters", "client_name"], message: "The server rejected this name." },
          ],
        },
      }),
    });
  });
  await page.getByRole("button", { name: "Generate proposal" }).click();
  await expect(summary).toContainText("The server rejected this title.");
  await expect(summary).toContainText("Client name: The server rejected this name.");
  const serverBlock = page.locator(".block").nth(2);
  await expect(serverBlock.locator(".field__error")).toContainText("The server rejected this title.");
  await expect(serverBlock.locator("input.input")).toHaveAttribute("aria-invalid", "true");
  await shot(page, "1440-step3-error-422");
  // The detail-field link jumps to step 2 and focuses the field.
  await summary.getByRole("button", { name: /Client name/ }).click();
  await expect(page.locator("#f-client_name")).toBeFocused();
  await expect(page.locator("#e-client_name")).toContainText("The server rejected this name.");
  await page.locator("#f-client_name").fill("Cedar Hollow, LLC");
  await expect(page.locator("#e-client_name")).toHaveCount(0);
  await page.unroute("**/api/runs");
  await page.getByRole("button", { name: "Continue" }).click();
  // Any outline change drops the stale server errors.
  await page.locator('[data-focus="executive_summary:edit"]').click();
  await page.locator("#f-executive_summary-title").fill("Summary");
  await expect(page.locator(".blocks .block__title", { hasText: "Summary" }).first()).toBeVisible();
  await expect(page.locator(".error-summary")).toHaveCount(0);
  const openEditors = page.locator(".block.is-open [aria-expanded=true]");
  while ((await openEditors.count()) > 0) await openEditors.first().click();
  await shot(page, "1440-step3-outline");
  await axe(page, "step 3 at 1440");

  // ---------------------------------------------------------------- 1024: all three steps
  await page.setViewportSize({ width: 1024, height: 768 });
  await axe(page, "step 3 at 1024");
  await shot(page, "1024-step3-outline");
  await page.locator(".stepper").getByRole("button", { name: /Details/ }).click();
  await axe(page, "step 2 at 1024");
  await shot(page, "1024-step2-details");
  await page.locator(".stepper").getByRole("button", { name: /Template/ }).click();
  await axe(page, "step 1 at 1024");
  await shot(page, "1024-step1-template");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();

  // ---------------------------------------------------------------- 390: step 3 works on a phone
  await page.setViewportSize({ width: 390, height: 844 });
  await noSideScroll(page);
  await expect(page.getByRole("button", { name: "Generate proposal" })).toBeInViewport();
  const moveUp = page.getByRole("button", { name: "Move Why we recommend Northbeam up" });
  await moveUp.scrollIntoViewIfNeeded();
  const box = (await moveUp.boundingBox())!;
  expect(box.width).toBeGreaterThanOrEqual(44);
  expect(box.height).toBeGreaterThanOrEqual(44);
  await moveUp.click();
  const titles390 = await blockTitles(page);
  expect(titles390[titles390.length - 2]).toContain("Why we recommend Northbeam");
  await page.locator('[data-focus="narrative_2:edit"]').click();
  await expect(page.locator("#f-narrative_2-title")).toBeVisible();
  await noSideScroll(page);
  await shot(page, "0390-step3-outline-editing");
  await axe(page, "step 3 at 390");
  await page.locator('[data-focus="narrative_2:edit"]').click();
  await page.locator(".wizard__body").evaluate((el) => (el.scrollTop = 0));
  await shot(page, "0390-step3-outline");

  // ---------------------------------------------------------------- draft survives leaving the wizard
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("link", { name: "Workflows" }).click();
  await expect(page.locator(".toast")).toContainText("Proposal draft kept.");
  await page.locator(".toast__action").click();
  await expect(page.locator(".stepper [aria-current=step]")).toContainText("Outline");
  await expect(page.locator(".blocks .block__title", { hasText: "Why we recommend Northbeam" })).toBeVisible();

  // ---------------------------------------------------------------- step 4: generate, progress, report
  await page.getByRole("button", { name: "Generate proposal" }).click();
  await expect(page).toHaveURL(/#\/runs\/\d+/);
  await expect(page.locator(".doc-row")).toHaveCount(2);
  // The new run is a proposal right away: Proposals rail item and crumbs, not Compare Quotes.
  await expect(page.locator(".crumbs")).toContainText("Proposals");
  await expect(page.getByRole("link", { name: "Proposals, new proposal" })).toHaveAttribute("aria-current", "page");
  await shot(page, "1440-step4-progress");
  await expect(page.locator(".report")).toBeVisible({ timeout: 90_000 });
  await expect(page.locator(".doc-row--done")).toHaveCount(2);
  await expect(page.locator(".toolbar__title")).toContainText("Insurance Proposal for Cedar Hollow, LLC");
  await expect(page.locator("#h-cover")).toHaveText("Cover Page");
  await expect(page.locator("#section-toc")).toContainText("Why we recommend Northbeam");
  await expect(page.locator("#section-toc")).not.toContainText("Next Steps");
  await expect(page.locator(".report .chip").first()).toBeVisible();
  await page.getByRole("button", { name: "Input parameters" }).click();
  await expect(page.locator(".params")).toContainText("Standard");
  await shot(page, "1440-report");
  await axe(page, "proposal report at 1440", false);

  // Preview hides citation chips; toggling back shows them.
  const previewBtn = page.getByRole("button", { name: "Preview without citations" });
  await previewBtn.click();
  await expect(previewBtn).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".report .chip").first()).toBeHidden();
  await shot(page, "1440-report-preview");
  await previewBtn.click();
  await expect(page.locator(".report .chip").first()).toBeVisible();

  // Export: DOCX (new) and PDF.
  const [docx] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export DOCX, Word document" }).click()]);
  expect(docx.suggestedFilename()).toMatch(/\.docx$/);
  const [pdf] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export PDF, full report" }).click()]);
  expect(pdf.suggestedFilename()).toMatch(/\.pdf$/);

  // The draft was cleared by Generate: a new proposal starts at step 1.
  await page.goto("/#/proposals/new");
  await expect(page.locator(".stepper [aria-current=step]")).toContainText("Template");
  await expect(page.locator(".tpl-option.is-selected")).toHaveCount(0);
});
