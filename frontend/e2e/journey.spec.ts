import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Full user journey (spec "User journey", T10 Verify) against the dev backend
// (backend/dev_seed.py serve, scripted model, no tokens). Screenshots land in qa-screenshots/.

const here = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.resolve(here, "../../tests/fixtures/quotes");
const SHOTS = path.resolve(here, "../qa-screenshots");

async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), animations: "disabled" });
}

async function axe(page: Page, label: string) {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const summary = bad.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(" ")}`);
  console.log(`axe ${label}: ${res.violations.length} violations, ${bad.length} serious/critical`, summary);
  expect(summary, `axe serious/critical on ${label}`).toEqual([]);
}

test("upload, run, review, chip, edit, chat, export", async ({ page }) => {
  // 1. Opens app: rail, drop zone with limits, recent runs (DG1, DG6).
  await page.goto("/");
  await expect(page.getByText("Drop quote PDFs to compare").first()).toBeVisible();
  await expect(page.getByText(/up to 300 pages and 50 MB each, 8 files per run/).first()).toBeVisible();
  // Slices 2 and 3: Proposals and Policy check are live; the rest of the rail still says "coming in a later slice".
  await expect(page.getByRole("link", { name: "Proposals, new proposal" })).toHaveAttribute("href", "#/proposals/new");
  await expect(page.getByRole("link", { name: "Policy check, new check" })).toHaveAttribute("href", "#/policy-check/new");
  // Slice 3b T6 (D7 allowed edit): Personal lines is live now, so its "coming in a later slice" check became this link check.
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Personal lines" })).toHaveAttribute("href", "#/personal-lines");
  // 10 before slice 3b; Integrations (T5), Personal lines (T6) and Tasks (T7) are live now (D7 allowed edits, 9 -> 8 -> 7).
  await expect(page.locator(".rail__item.is-disabled")).toHaveCount(7);
  await shot(page, "1440-01-launch");
  await axe(page, "launch");

  // 2. Drops quote PDFs: per-file rows, inline rejection naming the problem.
  await page.locator(".upload input[type=file]").setInputFiles([
    path.join(FIX, "carrier_a_clean.pdf"),
    path.join(FIX, "carrier_b_scanned.pdf"),
    path.join(FIX, "not_a_pdf.pdf"),
    path.join(FIX, "carrier_c_clean.pdf"),
  ]);
  await expect(page.locator(".file-row--ready")).toHaveCount(3);
  await expect(page.locator(".file-row--rejected")).toContainText("Not a PDF");
  await shot(page, "1440-02-upload");

  // 3. Clicks Run: per-document rows driven by SSE, then the report.
  await page.getByRole("button", { name: "Compare 3 quotes" }).click();
  await expect(page).toHaveURL(/#\/runs\/\d+/);
  await expect(page.locator(".doc-row")).toHaveCount(3);
  await expect(page.locator(".doc-row .spinner").first()).toBeVisible();
  await shot(page, "1440-03-run-progress");
  await expect(page.locator(".report")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".doc-row--done")).toHaveCount(3);

  // 4. Report opens: outline trust counts (DG2).
  await expect(page.locator(".trust__item--verified .trust__count")).not.toHaveText("0");
  await expect(page.locator(".chip--scan").first()).toBeVisible();
  await shot(page, "1440-04-report");
  await axe(page, "report");

  // 5. Clicks a chip: PDF beside the report at the cited page, dashed highlight; Esc exits.
  const chip = page.locator("#section-premium button.chip").first();
  const label = (await chip.getAttribute("data-chip"))!;
  const pageNo = label.slice(1);
  await chip.click();
  await expect(page.locator(".source-pane")).toBeVisible();
  await expect(page.locator(".pdf__page-input")).toHaveValue(pageNo);
  await expect(page.locator(".pdf-highlight")).toBeVisible();
  await shot(page, "1440-05-source-side-by-side");
  await axe(page, "side by side");
  await page.keyboard.press("Escape");
  await expect(page.locator(".source-pane")).toHaveCount(0);
  await expect(chip).toBeFocused();

  // Keyboard: Enter on a focused chip opens the source too (DG10).
  await page.keyboard.press("Enter");
  await expect(page.locator(".source-pane")).toBeVisible();
  await page.keyboard.press("Escape");

  // 6. A scan-read chip: page-level highlight only.
  await page.locator(".chip--scan").first().click();
  await expect(page.locator(".pdf-highlight--page")).toBeVisible();
  await page.keyboard.press("Escape");

  // 7. Edits a cell: Enter opens, Enter saves, "Edited. Undo" toast, stale tag (DG12, DG5).
  const cell = page.locator("#section-premium td").first();
  await cell.focus();
  await page.keyboard.press("Enter");
  const input = page.locator(".cell-edit__input");
  await expect(input).toBeFocused();
  await input.fill("$30,000.00");
  await page.keyboard.press("Enter");
  await expect(page.locator(".toast")).toContainText("Edited.");
  await expect(cell).toContainText("$30,000.00");
  await expect(page.locator(".stale-tag").first()).toBeVisible();
  await shot(page, "1440-06-edited-stale");
  await page.locator(".toast__action").click();
  await expect(page.locator(".toast")).toContainText("Edit undone.");
  await expect(cell).not.toContainText("$30,000.00");

  // Esc cancels an edit.
  await cell.dblclick({ position: { x: 6, y: 6 } });
  await input.fill("nope");
  await page.keyboard.press("Escape");
  await expect(cell).not.toContainText("nope");

  // 8. Asks a follow-up: typing indicator, answer with chips.
  await page.locator(".suggestion").first().click();
  await expect(page.locator(".msg--assistant .chip").first()).toBeVisible({ timeout: 20_000 });
  await shot(page, "1440-07-chat");

  // 9. Exports: XLSX and PDF download; toast notes XLSX holds tables only.
  const [xlsx] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export XLSX, tables only" }).click()]);
  expect(xlsx.suggestedFilename()).toMatch(/\.xlsx$/);
  await expect(page.locator(".toast")).toContainText("tables only");
  const [pdf] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export PDF, full report" }).click()]);
  expect(pdf.suggestedFilename()).toMatch(/\.pdf$/);

  // 10. 1024: left panel becomes a strip, PDF opens as a tab over the report (DG9).
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(page.locator(".left-strip")).toBeVisible();
  await shot(page, "1024-01-report");
  await axe(page, "report 1024");
  await page.locator("#section-premium button.chip").first().click();
  await expect(page.locator(".source-pane")).toBeVisible();
  await expect(page.locator(".report-area")).toHaveCount(0);
  await shot(page, "1024-02-source-tab");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Show run panel" }).click();
  await expect(page.locator(".left-panel--overlay")).toBeVisible();
  await shot(page, "1024-03-panel-open");
});

test("seeded states: failed, cancelled, budget, fallback, partial", async ({ page }) => {
  const api = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
  // Compare Quotes runs only: the seed also holds Policy Check runs (slice 3), one of them partial with 3 documents.
  const runs = ((await (await page.request.get(`${api}/api/runs?limit=50`)).json()).runs as { id: number; status: string; workflow_name: string; documents: { filename: string }[] }[])
    .filter((r) => r.workflow_name === "Compare Quotes");
  const find = (pred: (r: (typeof runs)[number]) => boolean) => runs.find(pred)!;
  const failed = find((r) => r.status === "failed");
  const cancelled = find((r) => r.status === "cancelled");
  const fallback = find((r) => r.documents.some((d) => d.filename.startsWith("compose_fail")));
  const partial = find((r) => r.documents.some((d) => d.filename === "long_120.pdf") && r.status === "partial");
  const budget = find((r) => r.status === "partial" && r.documents.length === 3);

  for (const [name, id, text] of [
    ["failed", failed.id, "No report was written."],
    ["cancelled", cancelled.id, "This run was cancelled"],
    ["budget", budget.id, "token budget ran out"],
    ["fallback", fallback.id, "Retry prose"],
    ["partial", partial.id, "could not be read"],
  ] as const) {
    await page.goto(`/#/runs/${id}`);
    await expect(page.getByText(text).first()).toBeVisible();
    await shot(page, `1440-state-${name}`);
    await axe(page, `state ${name}`);
  }

  // Conflicting values: filter, cell silhouette, chips per value.
  await page.goto(`/#/runs/${partial.id}`);
  await page.getByRole("button", { name: /Conflicting values/ }).first().click();
  const conflict = page.locator('td[data-state="conflicting"]').first();
  await expect(conflict).toContainText("Conflicting values");
  await expect(conflict.locator("button.chip")).toHaveCount(2);
  await shot(page, "1440-state-conflicting");

  // Collapsed outline: warning mark with counts in the label.
  await page.getByRole("button", { name: "Hide outline" }).click();
  const handle = page.locator(".outline-handle");
  await expect(handle).toHaveAttribute("aria-label", /Outline\. .*conflicting/);
  await expect(handle.locator(".outline-handle__mark")).toBeVisible();
  await handle.click();
  await expect(page.locator(".trust__item").first()).toBeFocused();

  // Under 1024: notice, report still readable.
  await page.setViewportSize({ width: 900, height: 760 });
  await expect(page.getByText("Best viewed on a wider screen")).toBeVisible();
  await shot(page, "0900-narrow");
});
