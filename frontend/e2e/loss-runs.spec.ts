import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Slice 5a T8, loss reviews (direction A "Register and ledger" with B's per-file footing, LOSS-RUNS-BRIEF.md) against
// the dev backend (backend/dev_seed.py run 12: the eight lr_main files, synthetic, scripted model, no tokens).
// Analytics page and rail, queue row hook, Activity, the review page (totals footing, checks one card per printed
// line incl. the conflict and the scanned rows, quick filters with counts, a check's rows with its footing, source
// chips, By file), every broker action with Undo, the wizard (PDF + CSV upload, sheet mapping, read), phone status.
// axe (every violation) at 1440, 1024 and 390. Needs a fresh seed: actions are undone, but the wizard adds a review.

const here = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.resolve(here, "../../tests/fixtures/loss_runs");
const SHOTS = path.resolve(here, "../qa-screenshots/loss-runs");
const API = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const CLIENT = "Harrow Lane Bakery Group LLC";

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
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(0);
}

/** The seeded review (run 12): the synthetic lr_main review for Harrow Lane Bakery Group LLC. */
async function seeded(page: Page): Promise<number> {
  const res = await page.request.get(`${API}/api/loss-runs`);
  const reviews = ((await res.json()) as { reviews: { run_id: number; client: string | null; files: number }[] }).reviews;
  const r = reviews.filter((x) => x.client === CLIENT && x.files === 8).at(-1);
  expect(r, "seeded loss review (reseed: python -m backend.dev_seed seed)").toBeTruthy();
  return r!.run_id;
}

const toResolve = (page: Page) => page.locator(".lr-ledger .lr-resolve > li");
const totalsRow = (page: Page) => page.locator(".lr-footing");

test("Analytics: rail item is live and the seeded review is listed with Review losses", async ({ page }) => {
  await page.goto("/#/analytics");
  const rail = page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: /Analytics/ });
  await expect(rail).toHaveAttribute("href", "#/analytics");
  await expect(rail).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { level: 1, name: "Loss reviews" })).toBeVisible();
  const row = page.locator(".queue-row").filter({ hasText: CLIENT });
  await expect(row.first()).toContainText("8 files, valued as of 09/30/2025");
  await expect(row.first()).toContainText("3 checks to resolve");
  await expect(row.first().getByRole("link", { name: "Review losses" })).toBeVisible();
  await shot(page, "1440-01-analytics");
  await axe(page, "analytics 1440");
  for (const [w, h] of [[1024, 768], [390, 844]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await noSideScroll(page);
    await shot(page, `${w}-01-analytics`);
    await axe(page, `analytics ${w}`);
  }
});

test("Queue row and Activity: a loss review is a client row with Review losses; Activity lists the run", async ({ page }) => {
  const runId = await seeded(page);
  await page.goto("/#/");
  const row = page.locator(`.queue-item[data-run-id="${runId}"]`);
  await expect(row.getByRole("link", { name: /Review losses/ })).toHaveAttribute("href", `#/runs/${runId}`);
  await row.getByRole("button", { name: /Actions for/ }).click();
  await expect(page.getByRole("menuitem", { name: "Review losses" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.goto("/#/activity");
  await expect(page.getByText(`Loss review, run ${runId}`)).toBeVisible();
});

test("Review page: totals footing, checks per printed line, quick filters, a check's rows, chips, By file", async ({ page }) => {
  const runId = await seeded(page);
  await page.goto(`/#/runs/${runId}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(`${CLIENT} · Loss review`);
  await expect(page.getByText("Synthetic sample data")).toBeVisible();
  await expect(totalsRow(page)).toContainText("Counted totals");
  await expect(totalsRow(page)).toContainText("Unreconciled");
  await expect(totalsRow(page)).toContainText("$865,897.48"); // lr_main totals plus the seeded confirm of BW-23-111
  await expect(totalsRow(page)).toContainText("56 claims, 33 open, valued as of 09/30/2025");
  // one card per printed line: Driftwood 2025's paid, reserve and incurred are one Off card
  await expect(toResolve(page)).toHaveCount(3);
  await expect(toResolve(page).nth(0)).toContainText("Off by $12,400.00");
  await expect(toResolve(page).nth(0).locator(".lr-recon tbody tr")).toHaveCount(3);
  await expect(toResolve(page).nth(1)).toContainText("Conflicting");
  await expect(toResolve(page).nth(2)).toContainText("Not verified");
  await expect(page.locator(".lr-ledger")).toContainText("Handled row by row");
  await expect(page.getByRole("button", { name: "Mark review done" })).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByText("3 checks to resolve")).toBeVisible();
  await shot(page, "1440-02-review");
  await axe(page, "review 1440");

  // quick filters carry the server's counts; Over $100K is honest about being empty
  await expect(page.getByRole("button", { name: /Open only/ })).toContainText("38");
  await page.getByRole("button", { name: /Over \$100K/ }).click();
  await expect(page.getByText("No claim matches Over $100K.")).toBeVisible();
  await page.getByRole("button", { name: /Over \$100K/ }).click();

  // a check opens the rows it covers with the printed line footed under them
  await toResolve(page).nth(0).getByRole("button", { name: /Show the rows/ }).click();
  await expect(page.getByText("Showing the rows behind")).toBeVisible();
  const foot = page.locator(".lr-tie");
  await expect(foot.filter({ hasText: "Rows above add up to" })).toContainText("$150,063.33");
  await expect(foot.filter({ hasText: "Printed minus rows" })).toContainText("$12,400.00");
  await shot(page, "1440-03-scope");

  // a sheet chip opens its rendered sheet page beside the register
  await page.getByRole("button", { name: /All claims/ }).first().click();
  // the CSV row chip (AG-2307 in the export): named for people, its only "sheet" is the file so it reads "Row 6"
  const csvText = page.locator(".lr-tablewrap .lr-chip .lr-chip__text").filter({ hasText: /^Row 6$/ }).first();
  await expect(csvText).toHaveText("Row 6"); // visible text; the file name is only in the tooltip and screen-reader name
  await csvText.click();
  await expect(page.getByRole("button", { name: "Back to checks" })).toBeVisible();
  await expect(page.locator(".lr-source__name")).toHaveText("Ashgrove 2025 CSV, Row 6");
  await expect(page.locator(".pdf__caption")).toHaveText("Source: Ashgrove 2025 CSV, Row 6");
  await expect(page.locator(".lr-source")).not.toContainText("lr_main");
  // a sheet page opens readable (not Fit width) with the cited row's box scrolled into view
  await expect(page.getByRole("button", { name: /Fit width/ })).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".pdf__frame canvas")).toBeVisible();
  const box = page.locator(".pdf-highlight");
  await expect(box).toBeInViewport();
  expect((await box.boundingBox())!.height).toBeGreaterThan(18); // the row is readable size, not a sliver
  await shot(page, "1440-04-source");
  await page.keyboard.press("Escape");
  await expect(page.locator(".lr-ledger")).toBeVisible();

  // By file: one schedule per file, printed totals footed where they were printed
  await page.getByText("By file", { exact: true }).click();
  await expect(page.locator(".lrB-sched").first()).toBeVisible();
  await expect(page.locator(".lrB-foot").first()).toContainText("Printed:");
  await shot(page, "1440-05-by-file");
  await axe(page, "by file 1440");
  await page.getByText("All claims", { exact: true }).click();

  for (const [w, h] of [[1024, 768], [390, 844]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await noSideScroll(page);
    await shot(page, `${w}-02-review`);
    await axe(page, `review ${w}`);
  }
  // phone is for status: totals and the checks' words, no table and no actions
  await expect(page.locator(".lr-phone")).toContainText("Totals");
  await expect(page.locator(".lr-tablewrap")).toHaveCount(0);
});

test("Broker actions apply at once and every one has Undo", async ({ page }) => {
  const runId = await seeded(page);
  await page.goto(`/#/runs/${runId}`);
  await expect(toResolve(page)).toHaveCount(3);
  const toast = page.locator(".toast-region");

  // add the missed claim the carrier's total includes: the Off line resolves itself, Undo brings it back
  await toResolve(page).nth(0).getByRole("button", { name: "Add a missed claim" }).click();
  await page.getByLabel("Claim number").fill("DS-25-0031");
  await page.getByLabel("Paid").fill("4,150.00");
  await page.getByLabel("Reserve").fill("8,250.00");
  await page.getByLabel("Incurred").fill("12,400.00");
  await page.getByRole("button", { name: "Add claim" }).click();
  await expect(toast).toContainText("DS-25-0031 added to Driftwood 2025.");
  await expect(toResolve(page)).toHaveCount(2);
  await expect(totalsRow(page)).toContainText("$878,297.48");
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(toResolve(page)).toHaveCount(3);
  await expect(totalsRow(page)).toContainText("$865,897.48");

  // accept the gap with a note (one Accept for the line's three totals), then Undo accept
  await toResolve(page).nth(0).getByRole("button", { name: "Accept the gap..." }).click();
  await page.getByRole("button", { name: "Accept", exact: true }).click();
  await expect(page.getByText("Add a note: it is kept with the decision.")).toBeVisible();
  await page.getByLabel("Why the gap is acceptable").fill("Carrier confirmed DS-25-0031 by email; it is on the next run.");
  await page.getByRole("button", { name: "Accept", exact: true }).click();
  await expect(toResolve(page)).toHaveCount(2);
  await expect(page.locator(".lr-ledger")).toContainText("Accepted by you");
  await page.getByRole("button", { name: "Undo accept" }).click();
  await expect(toResolve(page)).toHaveCount(3);

  // pick a file for the conflict, then Undo the pick
  await toResolve(page).nth(1).getByRole("button", { name: "Use Ashgrove 2025 workbook" }).click();
  await expect(toast).toContainText("Claim AG-2307 now uses Ashgrove 2025 workbook.");
  await expect(page.locator(".lr-ledger")).toContainText("Picked by you");
  await page.getByRole("button", { name: "Undo the pick" }).click();
  await expect(toResolve(page)).toHaveCount(3);

  // confirm a scanned row with a reason, then Undo confirm from its row
  await toResolve(page).filter({ hasText: "Not verified" }).getByRole("button", { name: /Show the/ }).click();
  await page.getByRole("button", { name: "Actions for claim FH-24-0012" }).click();
  await page.getByRole("menuitem", { name: "Confirm..." }).click();
  await page.getByLabel("Why the row is right").fill("Matches the carrier's emailed statement.");
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(toast).toContainText("FH-24-0012 confirmed.");
  // a decided row leaves "the rows behind" an unverified check (they list what is still undecided): find it in all claims
  await expect(page.locator(".lr-row").filter({ hasText: "FH-24-0012" })).toHaveCount(0);
  await page.getByRole("button", { name: /All claims/ }).first().click();
  await page.getByPlaceholder("Claim number or claimant").fill("FH-24-00");
  const row = page.locator(".lr-row").filter({ hasText: "FH-24-0012" });
  await expect(row).toContainText("Confirmed by you");
  await shot(page, "1440-06-confirmed");
  await page.getByRole("button", { name: "Actions for claim FH-24-0012" }).click();
  await page.getByRole("menuitem", { name: "Undo confirm" }).click();
  await expect(row).toContainText("Not verified");

  // fix an amount: the edited value shows the value read from the file; Undo the edit
  await page.getByRole("button", { name: "Actions for claim FH-24-0027" }).click();
  await page.getByRole("menuitem", { name: "Fix amounts..." }).click();
  await page.getByLabel("Paid").fill("31,075.00");
  await page.getByRole("button", { name: "Save amounts" }).click();
  const fixed = page.locator(".lr-row").filter({ hasText: "FH-24-0027" });
  await expect(fixed).toContainText("Edited by you");
  await expect(fixed.locator(".lr-was")).toContainText("$31,075.25");
  await page.getByRole("button", { name: "Actions for claim FH-24-0027" }).click();
  await page.getByRole("menuitem", { name: "Undo the edit" }).click();
  await expect(fixed).toContainText("Not verified");
});

test("Wizard: Who, Files (PDF and CSV with its mapped columns), Read, then the review", async ({ page }) => {
  const tag = `e2e${Date.now()}`;
  await page.goto("/#/analytics");
  await page.getByRole("link", { name: "New loss review" }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: "New loss review" })).toBeVisible();
  await page.getByLabel("Client").fill("Quarry Road Foods LLC");
  await axe(page, "wizard who");
  await page.getByRole("button", { name: "Continue" }).click();
  const csv = readFileSync(path.join(FIX, "lr_main_ashgrove_export.csv"));
  const pdf = readFileSync(path.join(FIX, "lr_main_driftwood_2025.pdf"));
  await page.getByLabel("Choose loss run files").setInputFiles([
    { name: `lr_main_ashgrove_export--${tag}.csv`, mimeType: "text/csv", buffer: Buffer.concat([csv, Buffer.from(`\n`)]) },
    { name: `lr_main_driftwood_2025--${tag}.pdf`, mimeType: "application/pdf", buffer: Buffer.concat([pdf, Buffer.from(`\n%${tag}\n`)]) },
  ]);
  await expect(page.locator(".lr-sheets__map").first()).toContainText("→ Claim number");
  await expect(page.locator(".lr-uprow").filter({ hasText: ".pdf" })).toContainText("PDF, 2 pages");
  await shot(page, "1440-07-wizard-files");
  await axe(page, "wizard files");
  await page.getByRole("button", { name: "Read the files" }).click();
  await expect(page).toHaveURL(/#\/runs\/\d+/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Quarry Road Foods LLC · Loss review", { timeout: 60_000 });
  await expect(toResolve(page).first()).toContainText("Off by $12,400.00", { timeout: 90_000 });
});
