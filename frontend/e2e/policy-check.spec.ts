import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// T9 Policy Check UI (spec section 6, decision ledger D1 to D17) against the dev backend
// (backend/dev_seed.py: seeded runs 7 "four roles" and 8 "quote not read, no AMS", scripted model, no tokens).
// Rail entry, wizard (documents incl. AMS upload, roles, run check), discrepancy list (severity filters,
// Hide resolved, four-slot cards with chips, resolve and note), review dialog (n of N, Previous / Next,
// cited source beside it, AMS field highlight). axe (every violation) at 1440, 1024 and 390.

const here = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.resolve(here, "../../tests/fixtures/policy");
const SHOTS = path.resolve(here, "../qa-screenshots/policy-check");
const API = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), animations: "disabled" });
}

async function axe(page: Page, label: string) {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const summary = res.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(" ")}`);
  console.log(`axe ${label}: ${res.violations.length} violations`, summary);
  expect(summary, `axe on ${label}`).toEqual([]);
}

async function noSideScroll(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), {
      message: "no horizontal page scroll",
    })
    .toBeLessThanOrEqual(0);
}

interface RunLite {
  id: number;
  workflow_name: string;
  status: string;
  documents: { slot?: string }[];
}

/** The seeded Policy Check runs (dev_seed 7 and 8): the oldest with an AMS slot, and the oldest without. */
async function seededRuns(page: Page): Promise<{ full: number; notRead: number }> {
  const res = await page.request.get(`${API}/api/runs?limit=100`);
  const runs = ((await res.json()) as { runs: RunLite[] }).runs.filter((r) => r.workflow_name === "Policy Check").sort((a, b) => a.id - b.id);
  const full = runs.find((r) => r.documents.some((d) => d.slot === "ams") && r.status === "done");
  const notRead = runs.find((r) => !r.documents.some((d) => d.slot === "ams") && r.status === "partial");
  expect(full, "seeded four-role run (python -m backend.dev_seed seed)").toBeTruthy();
  expect(notRead, "seeded quote-not-read run").toBeTruthy();
  return { full: full!.id, notRead: notRead!.id };
}

test("policy check: rail entry, wizard with AMS, run, list", async ({ page }) => {
  await page.goto("/");
  // The rail item is live (slice 3); the other placeholders stay disabled.
  const rail = page.getByRole("link", { name: "Policy check, new check" });
  await expect(rail).toHaveAttribute("href", "#/policy-check/new");
  await expect(page.locator(".rail__item.is-disabled")).toHaveCount(10);
  await rail.click();
  await expect(page).toHaveURL(/#\/policy-check\/new/);
  await expect(rail).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "New policy check" })).toBeVisible();
  await expect(page.locator(".stepper [aria-current=step]")).toContainText("Documents");

  // Continue with nothing: inline error.
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Add at least two policy PDFs: the expiring policy and the renewal.")).toBeVisible();

  // Policy PDFs and the AMS export, each through its own endpoint; a PDF in the AMS zone is rejected inline.
  await page.locator(".details__group").nth(0).locator("input[type=file]").setInputFiles([
    path.join(FIX, "pc_homeowners_expiring.pdf"),
    path.join(FIX, "pc_homeowners_renewal.pdf"),
    path.join(FIX, "pc_homeowners_quote.pdf"),
  ]);
  await expect(page.locator(".details__group").nth(0).locator(".file-row--ready")).toHaveCount(3);
  const amsGroup = page.locator(".details__group").nth(1);
  await amsGroup.locator("input[type=file]").setInputFiles(path.join(FIX, "pc_homeowners_expiring.pdf"));
  await expect(amsGroup.locator(".file-row--rejected")).toBeVisible();
  await amsGroup.getByRole("button", { name: /Dismiss/ }).click();
  await amsGroup.locator("input[type=file]").setInputFiles(path.join(FIX, "pc_homeowners_ams.json"));
  await expect(amsGroup.locator(".file-row--ready")).toContainText("12 fields, 2 not compared");
  await shot(page, "1440-wizard-documents");
  await axe(page, "wizard documents 1440");

  // Roles: guessed from file names, renewal emphasised, letters follow the report's column order.
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.locator(".stepper [aria-current=step]")).toContainText("Roles");
  const select = (name: string) => page.getByRole("combobox", { name });
  await expect(select("Expiring policy").locator("option:checked")).toHaveText("pc_homeowners_expiring.pdf");
  await expect(select("Renewal").locator("option:checked")).toHaveText("pc_homeowners_renewal.pdf");
  await expect(select("Quote").locator("option:checked")).toHaveText("pc_homeowners_quote.pdf");
  await expect(select("AMS export").locator("option:checked")).toHaveText("pc_homeowners_ams.json");
  // A document already in another role cannot be picked twice.
  await expect(select("Quote").locator("option", { hasText: "pc_homeowners_renewal.pdf" })).toBeDisabled();
  // A required role left empty: inline error, focus on the field.
  await select("Renewal").selectOption("");
  await page.getByRole("button", { name: "Run check" }).click();
  await expect(page.getByText("Choose the renewal PDF.")).toBeVisible();
  await expect(select("Renewal")).toBeFocused();
  await select("Renewal").selectOption({ label: "pc_homeowners_renewal.pdf" });
  await shot(page, "1440-wizard-roles");
  await axe(page, "wizard roles 1440");

  // Run check: the run view, then the discrepancy list.
  await page.getByRole("button", { name: "Run check" }).click();
  await expect(page).toHaveURL(/#\/runs\/\d+/);
  await expect(page.getByRole("link", { name: "Policy check, new check" })).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".disc").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".doc-row__role")).toHaveText(["Expiring", "Quote", "Renewal", "AMS"]);
  await expect(page.locator(".disc-head__count")).toHaveText(/^\d+ discrepanc(y|ies), 0 resolved$/);
});

test("policy check: list, filters, resolve, note, review dialog", async ({ page }) => {
  const { full } = await seededRuns(page);
  await page.goto(`/#/runs/${full}`);
  await expect(page.locator(".toolbar__title")).toContainText(`Policy check, run ${full}`);
  await expect(page.locator(".disc")).toHaveCount(10);
  await expect(page.locator(".disc-head__count")).toHaveText("10 discrepancies, 1 resolved");

  // Four slots in role order on every card; the AMS chip names its field.
  const first = page.locator(".disc").first();
  await expect(first.locator(".slot__role")).toHaveText([/Expiring/, /Quote/, /Renewal/, /AMS/]);
  await expect(first.locator(".sev__word")).toHaveText("Critical");
  await expect(first.locator("[data-role=ams] .chip")).toHaveText("AMS · Cov C");
  await expect(first.locator("[data-role=quote] .chip--scan")).toBeVisible();
  await expect(first.locator("[data-role=quote]")).toContainText("Read from scan, unverified");
  // Needs review (renewal could not be verified), N/A, and "changed since resolved".
  const review = page.locator('[data-item="disc-deductible-wind-hail"]');
  await expect(review.locator("[data-role=renewal]")).toContainText("Needs review");
  await expect(review.locator("[data-role=ams]")).toContainText("N/A");
  await expect(page.locator('[data-item="disc-total-premium"] .stale-tag')).toHaveText("Changed since resolved");
  await shot(page, "1440-list");
  await axe(page, "list 1440");

  // Severity filters (multi-select, counts never change) and Hide resolved.
  await page.getByRole("button", { name: /^Critical 2/ }).click();
  await expect(page.locator(".disc")).toHaveCount(2);
  await page.getByRole("button", { name: /^Medium 2/ }).click();
  await expect(page.locator(".disc")).toHaveCount(4);
  await page.getByRole("checkbox", { name: /Hide resolved/ }).check();
  await expect(page.locator(".disc")).toHaveCount(3);
  await page.getByRole("button", { name: /^All 10/ }).click();
  await expect(page.locator(".disc")).toHaveCount(9);
  await shot(page, "1440-filtered");

  // Resolve: the card stays in place while Hide resolved is on, the count updates; Reopen restores.
  const card = page.locator('[data-item="disc-coverage-a-limit"]');
  await card.getByRole("button", { name: "Mark resolved" }).click();
  await expect(card.locator(".disc__resolved")).toHaveText("Resolved");
  await expect(page.locator(".disc-head__count")).toHaveText("10 discrepancies, 2 resolved");
  await expect(page.locator(".disc")).toHaveCount(9);
  await card.getByRole("button", { name: "Add note" }).click();
  await card.getByLabel("Note").fill("Insured lowered Coverage A after the appraisal.");
  await card.getByRole("button", { name: "Save note" }).click();
  await expect(card.locator(".disc__note")).toContainText("Insured lowered Coverage A after the appraisal.");
  await page.getByRole("checkbox", { name: /Hide resolved/ }).uncheck();
  await page.getByRole("checkbox", { name: /Hide resolved/ }).check();
  await expect(page.locator(".disc")).toHaveCount(8);
  await page.getByRole("checkbox", { name: /Hide resolved/ }).uncheck();
  await card.getByRole("button", { name: "Edit note" }).click();
  await card.getByLabel("Note").fill("");
  await card.getByRole("button", { name: "Save note" }).click();
  await expect(card.locator(".disc__note")).toHaveCount(0);
  await card.getByRole("button", { name: "Reopen" }).click();
  await expect(page.locator(".disc-head__count")).toHaveText("10 discrepancies, 1 resolved");

  // A card chip opens the cited page side by side (existing source view).
  await first.locator("[data-role=renewal] .chip").click();
  await expect(page.locator(".source-pane .pdf-highlight")).toBeVisible({ timeout: 20_000 });
  await page.keyboard.press("Escape");

  // One-at-a-time review: n of N, Previous / Next, source beside the item, AMS field highlight.
  const opener = page.getByRole("button", { name: "Review one at a time" });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "Discrepancy 1 of 10" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Previous", exact: true })).toBeDisabled();
  await expect(dialog.locator(".source-pane__name")).toHaveText("Renewal: pc_homeowners_renewal.pdf");
  await expect(dialog.locator(".pdf-highlight")).toBeVisible({ timeout: 20_000 });
  await dialog.locator("[data-role=ams] .chip").click();
  await expect(dialog.locator(".source-pane__name")).toHaveText("AMS: pc_homeowners_ams.json");
  await expect(dialog.locator(".pdf__caption")).toHaveText("Source AMS · Cov C, page 1");
  await expect(dialog.locator(".pdf-highlight:not(.pdf-highlight--page)")).toBeVisible({ timeout: 20_000 });
  // The box is one field line, not the whole page.
  const box = await dialog.locator(".pdf-highlight").boundingBox();
  const pageBox = await dialog.locator(".pdf__page-wrap").boundingBox();
  expect(box!.height).toBeLessThan(pageBox!.height / 10);
  await shot(page, "1440-review-ams");
  await axe(page, "review 1440");
  await dialog.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Discrepancy 2 of 10" })).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("dialog", { name: "Discrepancy 3 of 10" })).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("dialog", { name: "Discrepancy 2 of 10" })).toBeVisible();
  // Resolve inside the dialog updates the list behind it.
  await page.getByRole("dialog").getByRole("button", { name: "Mark resolved" }).click();
  await expect(page.locator(".disc-head__count")).toHaveText("10 discrepancies, 2 resolved");
  await page.getByRole("dialog").getByRole("button", { name: "Reopen" }).click();
  await expect(page.locator(".disc-head__count")).toHaveText("10 discrepancies, 1 resolved");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test("policy check: Not read, N/A, and 1024 / 390 layouts", async ({ page }) => {
  const { full, notRead } = await seededRuns(page);

  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto(`/#/runs/${notRead}`);
  const first = page.locator(".disc").first();
  await expect(first).toBeVisible();
  await expect(first.locator("[data-role=quote]")).toContainText("Not read");
  await expect(first.locator("[data-role=quote]")).not.toContainText("N/A");
  await expect(first.locator("[data-role=ams]")).toContainText("N/A");
  await expect(first.locator("[data-role=ams]")).toContainText("No document");
  await expect(page.getByRole("link", { name: "Policy check, new check" })).toHaveAttribute("aria-current", "page");
  await noSideScroll(page);
  await shot(page, "1024-not-read");
  await axe(page, "not read 1024");
  await page.getByRole("button", { name: "Review one at a time" }).click();
  await expect(page.getByRole("dialog", { name: /Discrepancy 1 of \d+/ })).toBeVisible();
  await expect(page.getByRole("dialog").locator(".pdf-highlight")).toBeVisible({ timeout: 20_000 });
  await shot(page, "1024-review");
  await axe(page, "review 1024");
  await page.keyboard.press("Escape");

  await page.goto("/#/policy-check/new");
  await expect(page.getByRole("heading", { name: "New policy check" })).toBeVisible();
  await noSideScroll(page);
  await axe(page, "wizard 1024");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/#/runs/${full}`);
  await expect(page.locator(".disc").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Policy check, new check" })).toHaveAttribute("aria-current", "page");
  await noSideScroll(page);
  // Slots stack one per row: the four roles share a left edge.
  const xs = await page.locator(".disc").first().locator(".slot").evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().left)));
  expect(new Set(xs).size).toBe(1);
  await page.locator(".disc").first().locator(".disc__slots").scrollIntoViewIfNeeded();
  await shot(page, "390-list");
  await axe(page, "list 390");
  await page.getByRole("button", { name: "Review one at a time" }).click();
  const dialog = page.getByRole("dialog", { name: "Discrepancy 1 of 10" });
  await expect(dialog).toBeVisible();
  // Full screen on a phone (polled: a pointer open settles from scale 0.98 over 200ms).
  await expect.poll(async () => Math.round((await dialog.boundingBox())!.width)).toBe(390);
  await shot(page, "390-review");
  await axe(page, "review 390");
  await page.keyboard.press("Escape");

  await page.goto("/#/policy-check/new");
  await expect(page.getByRole("heading", { name: "New policy check" })).toBeVisible();
  await noSideScroll(page);
  await shot(page, "390-wizard");
  await axe(page, "wizard 390");
});
