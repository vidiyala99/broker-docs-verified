import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Slice 3b T6 Personal lines (spec 2.1, D4, D5, D9, O5; PERSONAL-LINES-BRIEF.md) against the dev backend
// (backend/dev_seed.py: Policy Check runs 7 and 8 are tagged personal, scripted model, no tokens).
// Personal list vs Workflows list, the two entry actions with Personal preselected (and kept on reload), starting a
// personal run from each, re-tag from the run header (moves between the lists, rollback on failure), empty state,
// axe (every violation) at 1440, 1024 and 390. Every test leaves runs 7 and 8 personal again.

const here = path.dirname(fileURLToPath(import.meta.url));
const QUOTES = path.resolve(here, "../../tests/fixtures/quotes");
const POLICY = path.resolve(here, "../../tests/fixtures/policy");
const SHOTS = path.resolve(here, "../qa-screenshots/personal-lines");
const API = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

interface RunLite {
  id: number;
  workflow_name: string;
  line: "commercial" | "personal";
}

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

async function runsOf(page: Page, line?: "personal" | "commercial"): Promise<RunLite[]> {
  const res = await page.request.get(`${API}/api/runs?limit=100${line ? `&line=${line}` : ""}`);
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { runs: RunLite[] }).runs;
}

async function setLine(page: Page, id: number, line: "personal" | "commercial") {
  const res = await page.request.patch(`${API}/api/runs/${id}`, { data: { line } });
  expect(res.ok(), `PATCH run ${id}`).toBeTruthy();
}

// Work queue lane (D7 allowed edit): Personal lines and the Compare screen show the work queue instead of the run
// list (spec W1, W2), so "the page lists run N" now means the queue has a row for run N. The raw list is Activity.
const runLink = (page: Page, id: number) => page.getByRole("main").locator(`li[data-run-id="${id}"]`);
const crumbs = (page: Page) => page.getByRole("navigation", { name: "Breadcrumb" });
const railPersonal = (page: Page) => page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Personal lines" });

test.beforeEach(async ({ page }) => {
  // Seed contract: runs 7 and 8 are the personal Policy Check runs.
  await setLine(page, 7, "personal");
  await setLine(page, 8, "personal");
});

test("Personal lines lists runs 7 and 8 and no Compare runs; Workflows lists neither", async ({ page }) => {
  await page.goto("/");
  await railPersonal(page).click();
  await expect(page).toHaveURL(/#\/personal-lines$/);
  await expect(page).toHaveTitle("Personal lines");
  await expect(railPersonal(page)).toHaveAttribute("aria-current", "page");
  await expect(crumbs(page)).toHaveText("Personal lines");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 1, name: "Personal lines" })).toBeVisible();
  await expect(main.getByText("Homeowners, auto and other household policies.")).toBeVisible();
  // Slice 6 (lane B, allowed edit): Compare opens its own upload page.
  await expect(main.getByRole("link", { name: "Compare homeowners quotes" })).toHaveAttribute("href", "#/compare/new?line=personal");
  await expect(main.getByRole("link", { name: "Check a renewal" })).toHaveAttribute("href", "#/policy-check/new?line=personal");

  // Work queue lane (D7 allowed edit): rows are named by client, not "Policy check, run N"; the count is the
  // personal queue's items rather than every personal run.
  await expect(runLink(page, 7)).toContainText("Avery and Jordan Pellham");
  await expect(runLink(page, 8)).toContainText("Jordan Pellham (lake house)");
  // Exactly the personal queue items, none of them a seeded Compare run.
  const q = (await (await page.request.get(`${API}/api/queue?line=personal`)).json()) as Record<string, unknown[]>;
  const items = ["in_progress", "needs_review", "ready", "needs_attention", "done_recently"].reduce((n, g) => n + q[g]!.length, 0);
  await expect(main.locator("li[data-run-id]")).toHaveCount(items);
  const compare = (await runsOf(page)).filter((r) => r.workflow_name === "Compare Quotes" && r.line === "commercial");
  expect(compare.length).toBeGreaterThan(0);
  for (const r of compare) await expect(runLink(page, r.id)).toHaveCount(0);
  await shot(page, "1440-page");
  await axe(page, "page 1440");

  // Workflows (rail) = commercial only (D9).
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Workflows" }).click();
  await expect(page).toHaveURL(/#\/$/);
  await expect(main.locator("li[data-run-id]").first()).toBeVisible(); // D7 edit: queue rows, see runLink
  await expect(runLink(page, 7)).toHaveCount(0);
  await expect(runLink(page, 8)).toHaveCount(0);
  await expect(runLink(page, compare[0]!.id)).toBeVisible();
});

test("Each entry action lands with Personal preselected and keeps it on reload; rail entries mean Commercial", async ({ page }) => {
  const line = (name: "Commercial" | "Personal") => page.getByRole("group", { name: "Line of business" }).getByRole("radio", { name });

  await page.goto("/#/personal-lines");
  await page.getByRole("main").getByRole("link", { name: "Compare homeowners quotes" }).click();
  // Slice 6 (lane B, allowed edit): the entry is the upload page (#/compare/new), which files the run in the book.
  await expect(page).toHaveURL(/#\/compare\/new\?line=personal$/);
  await expect(line("Personal")).toBeChecked();
  await expect(crumbs(page)).toHaveText(/^Personal lines.*Compare Quotes.*New comparison$/);
  await expect(railPersonal(page)).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "New comparison" })).toBeVisible();
  await page.reload();
  await expect(line("Personal")).toBeChecked();
  await shot(page, "1440-compare-personal");
  await axe(page, "compare personal 1440");

  await page.goto("/#/personal-lines");
  await page.getByRole("main").getByRole("link", { name: "Check a renewal" }).click();
  await expect(page).toHaveURL(/#\/policy-check\/new\?line=personal$/);
  await expect(page.getByRole("heading", { name: "New policy check" })).toBeVisible();
  await expect(line("Personal")).toBeChecked();
  await expect(crumbs(page)).toHaveText(/^Personal lines.*New check$/);
  await page.reload();
  await expect(line("Personal")).toBeChecked();
  await expect(page.getByRole("link", { name: "Cancel" })).toHaveAttribute("href", "#/personal-lines");
  await shot(page, "1440-check-personal");
  await axe(page, "check personal 1440");

  // Rail Workflows and rail Policy check default to Commercial.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Policy check, new check" }).click();
  await expect(page).toHaveURL(/#\/policy-check\/new$/);
  await expect(line("Commercial")).toBeChecked();
  // Slice 6: rail Workflows opens the Commercial home; its Compare quotes opens the upload page in Commercial.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Workflows" }).click();
  await page.getByRole("main").getByRole("link", { name: "Compare quotes" }).click();
  await expect(line("Commercial")).toBeChecked();
  // Keyboard: arrow keys move the choice (native radios, one tab stop).
  await line("Commercial").focus();
  await page.keyboard.press("ArrowRight");
  await expect(line("Personal")).toBeChecked();
});

test("Starting a comparison and a check from Personal lines makes personal runs", async ({ page }) => {
  // Compare Quotes: line sent on POST /api/runs; the new run is personal and listed under Personal lines.
  await page.goto("/#/personal-lines");
  await page.getByRole("main").getByRole("link", { name: "Compare homeowners quotes" }).click();
  await page.locator(".compare-new input[type=file]").setInputFiles([path.join(QUOTES, "carrier_a_clean.pdf"), path.join(QUOTES, "carrier_c_clean.pdf")]);
  await expect(page.locator(".quote-row--ready")).toHaveCount(2);
  const [post] = await Promise.all([
    page.waitForRequest((r) => r.method() === "POST" && /\/api\/runs$/.test(r.url())),
    page.getByRole("button", { name: "Compare 2 quotes" }).click(),
  ]);
  expect(post.postDataJSON()).toMatchObject({ workflow: "compare-quotes", line: "personal" });
  await expect(page).toHaveURL(/#\/runs\/\d+$/);
  const compareId = Number(/#\/runs\/(\d+)/.exec(page.url())![1]);
  expect((await (await page.request.get(`${API}/api/runs/${compareId}`)).json()).line).toBe("personal");
  // Queue navigation: the last crumb names the client once the documents give one (before that, "Run N").
  await expect(crumbs(page)).toHaveText(/^Personal lines.*Compare Quotes.*(Run \d+|Cedar Hollow Hospitality LLC)$/);
  // Owner-approved change (line move B, 2026-11-03): the run header shows the book tab, not Line radios.
  await expect(page.getByRole("group", { name: "Book" })).toContainText("Personal lines");
  await expect(page.locator(".report")).toBeVisible({ timeout: 60_000 });

  // Policy Check: the same, through the wizard (roles guessed from the file names).
  await page.goto("/#/personal-lines");
  await page.getByRole("main").getByRole("link", { name: "Check a renewal" }).click();
  if (await page.getByRole("button", { name: "Discard draft" }).isVisible()) await page.getByRole("button", { name: "Discard draft" }).click();
  await page.locator(".details__group").nth(0).locator("input[type=file]").setInputFiles([
    path.join(POLICY, "pc_homeowners_expiring.pdf"),
    path.join(POLICY, "pc_homeowners_renewal.pdf"),
  ]);
  await expect(page.locator(".details__group").nth(0).locator(".file-row--ready")).toHaveCount(2);
  await page.getByRole("button", { name: "Continue" }).click();
  const [check] = await Promise.all([
    page.waitForRequest((r) => r.method() === "POST" && /\/api\/runs$/.test(r.url())),
    page.getByRole("button", { name: "Run check" }).click(),
  ]);
  expect(check.postDataJSON()).toMatchObject({ workflow: "policy-check", line: "personal" });
  await expect(page).toHaveURL(/#\/runs\/\d+$/);
  const checkId = Number(/#\/runs\/(\d+)/.exec(page.url())![1]);
  expect((await (await page.request.get(`${API}/api/runs/${checkId}`)).json()).line).toBe("personal");

  await page.goto("/#/personal-lines");
  await expect(runLink(page, compareId)).toBeVisible();
  await expect(runLink(page, checkId)).toBeVisible();
  await page.goto("/");
  await expect(runLink(page, compareId)).toHaveCount(0);
});

test("Re-tag from the run header moves the run between the lists; a failed PATCH rolls back", async ({ page }) => {
  await page.goto("/#/runs/7");
  // Owner-approved change (line move B, 2026-11-03): the run header's Line radios became the book tab plus one
  // "Move to ..." button; the toast names the client and the book and offers Undo; commercial crumbs lead with
  // "Commercial". Assertions below follow that; the PATCH body, list moves and rollback are unchanged.
  const book = page.getByRole("group", { name: "Book" });
  const tab = book.locator(".booktab__tab");
  await expect(tab).toHaveText("Personal lines");
  // Queue navigation (deliberate update): a named run's last crumb is its client, not "Run 7".
  await expect(crumbs(page)).toHaveText(/^Personal lines.*Policy check.*Avery and Jordan Pellham$/);
  // The rail names the tool: Policy check stays current for a personal Policy Check run.
  await expect(page.getByRole("link", { name: "Policy check, new check" })).toHaveAttribute("aria-current", "page");
  // Work queue lane (D7 allowed edit): the run view's Activity tab opens Activity (spec section 2).
  await expect(page.getByRole("link", { name: "Activity", exact: true })).toHaveAttribute("href", "#/activity");

  // Personal -> Commercial: PATCH body exactly {"line": "commercial"}, crumbs and toast follow.
  const [patch] = await Promise.all([
    page.waitForRequest((r) => r.method() === "PATCH" && /\/api\/runs\/7$/.test(r.url())),
    book.getByRole("button", { name: "Move to Commercial" }).click(),
  ]);
  expect(patch.postDataJSON()).toEqual({ line: "commercial" });
  await expect(tab).toHaveText("Commercial");
  await expect(page.locator(".toast-region")).toContainText("Avery and Jordan Pellham moved to Commercial.");
  await expect(crumbs(page)).toHaveText(/^Commercial.*Policy check.*Avery and Jordan Pellham$/);
  await shot(page, "1440-retag-commercial");

  await page.goto("/");
  await expect(runLink(page, 7)).toBeVisible();
  await page.goto("/#/personal-lines");
  await expect(runLink(page, 8)).toBeVisible();
  await expect(runLink(page, 7)).toHaveCount(0);

  // And back, by keyboard.
  await page.goto("/#/runs/7");
  const toPersonal = book.getByRole("button", { name: "Move to Personal lines" });
  await toPersonal.focus();
  await page.keyboard.press("Enter");
  await expect(tab).toHaveText("Personal lines");
  await expect(page.locator(".toast-region")).toContainText("Avery and Jordan Pellham moved to Personal lines.");
  // Keyboard stays on the control: the same button now offers the way back.
  await expect(book.getByRole("button", { name: "Move to Commercial" })).toBeFocused();
  await page.goto("/#/personal-lines");
  await expect(runLink(page, 7)).toBeVisible();
  await page.goto("/");
  await expect(runLink(page, 7)).toHaveCount(0);

  // Failure: the server refuses; the control rolls back and the error is shown.
  await page.goto("/#/runs/7");
  await page.route("**/api/runs/7", (route) =>
    route.request().method() === "PATCH"
      ? route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "internal_error", message: "Couldn't save the line. Retry." } }) })
      : route.fallback(),
  );
  await book.getByRole("button", { name: "Move to Commercial" }).click();
  await expect(page.locator(".toast-region")).toContainText("Couldn't save the line. Retry.");
  await expect(tab).toHaveText("Personal lines");
  await expect(crumbs(page)).toHaveText(/^Personal lines/);
  await page.unroute("**/api/runs/7");
  expect((await (await page.request.get(`${API}/api/runs/7`)).json()).line).toBe("personal");
});

// Line move B (owner-approved 2026-11-03, LINE-MOVE-BRIEF.md): the move says where the file went, and Undo puts it back.
test("Move to Commercial then Undo restores the book, the crumb and the server line", async ({ page }) => {
  await page.goto("/#/runs/8");
  const book = page.getByRole("group", { name: "Book" });
  const tab = book.locator(".booktab__tab");
  await expect(tab).toHaveText("Personal lines");
  await expect(crumbs(page)).toHaveText(/^Personal lines.*Policy check/);
  // The button's hit area is 44px tall although it draws 32px (DESIGN.md touch targets).
  const hit = await book.getByRole("button", { name: "Move to Commercial" }).evaluate((b) => {
    const r = b.getBoundingClientRect();
    const at = (y: number) => b.contains(document.elementFromPoint(r.left + r.width / 2, y));
    return { above: at(r.top - 6), below: at(r.bottom + 5) };
  });
  expect(hit).toEqual({ above: true, below: true });

  await book.getByRole("button", { name: "Move to Commercial" }).click();
  await expect(tab).toHaveText("Commercial");
  await expect(crumbs(page)).toHaveText(/^Commercial.*Policy check/);
  await expect.poll(async () => (await (await page.request.get(`${API}/api/runs/8`)).json()).line).toBe("commercial");

  await page.locator(".toast-region").getByRole("button", { name: "Undo" }).click();
  await expect(tab).toHaveText("Personal lines");
  await expect(crumbs(page)).toHaveText(/^Personal lines.*Policy check/);
  await expect(page.locator(".toast-region")).toContainText("moved back to Personal lines.");
  await expect(page.locator(".toast-region").getByRole("button", { name: "Undo" })).toHaveCount(0);
  await expect.poll(async () => (await (await page.request.get(`${API}/api/runs/8`)).json()).line).toBe("personal");

  // Keyboard: Ctrl+Z runs Undo while the toast shows; focus stays on the Move button.
  const move = book.getByRole("button", { name: "Move to Commercial" });
  await move.focus();
  await page.keyboard.press("Enter");
  await expect(tab).toHaveText("Commercial");
  await page.keyboard.press("Control+z");
  await expect(tab).toHaveText("Personal lines");
  await expect(book.getByRole("button", { name: "Move to Commercial" })).toBeFocused();
  await expect.poll(async () => (await (await page.request.get(`${API}/api/runs/8`)).json()).line).toBe("personal");
});

test("Empty state when no run is personal", async ({ page }) => {
  const personal = await runsOf(page, "personal");
  for (const r of personal) await setLine(page, r.id, "commercial");
  try {
    await page.goto("/#/personal-lines");
    const main = page.getByRole("main");
    // Work queue lane (D7 allowed edit): the queue's empty state replaces the run list's.
    await expect(main.getByRole("heading", { name: "No personal-lines work yet." })).toBeVisible();
    await expect(main.locator("li[data-run-id]")).toHaveCount(0);
    // The two actions appear once, in the empty state. Slice 6 (lane B, allowed edit, spec 4.2): the comparison starts
    // from the empty state's drop zone, which hands its files to the upload page.
    await expect(main.getByRole("link", { name: "Compare homeowners quotes" })).toHaveCount(0);
    await expect(main.getByText("Drop quotes to compare")).toBeVisible();
    await expect(main.getByRole("link", { name: "Check a renewal" })).toHaveCount(1);
    await expect(main.locator(".queue-empty").getByRole("link", { name: "Check a renewal" })).toBeVisible();
    await shot(page, "1440-empty");
    await axe(page, "empty 1440");
    await page.setViewportSize({ width: 390, height: 844 });
    await noSideScroll(page);
    await shot(page, "390-empty");
    await axe(page, "empty 390");
  } finally {
    for (const r of personal) await setLine(page, r.id, "personal");
  }
});

for (const vp of [
  { w: 1440, h: 900 },
  { w: 1024, h: 768 },
  { w: 390, h: 844 },
]) {
  test(`axe 0 at ${vp.w}: page, run header, entry screens`, async ({ page }) => {
    await page.setViewportSize({ width: vp.w, height: vp.h });
    await page.goto("/#/personal-lines");
    await expect(runLink(page, 7)).toBeVisible();
    await noSideScroll(page);
    await shot(page, `${vp.w}-page`);
    await axe(page, `page ${vp.w}`);

    await page.goto("/#/runs/7");
    await expect(page.locator(".disc").first()).toBeVisible();
    // Owner-approved change (line move B, 2026-11-03): the header shows the book tab and its Move button.
    const book = page.getByRole("group", { name: "Book" });
    await expect(book.locator(".booktab__tab")).toHaveText("Personal lines");
    await book.getByRole("button", { name: "Move to Commercial" }).focus();
    await noSideScroll(page);
    await shot(page, `${vp.w}-run-header`);
    await axe(page, `run header ${vp.w}`);

    await page.goto("/#/policy-check/new?line=personal");
    await expect(page.getByRole("heading", { name: "New policy check" })).toBeVisible();
    await noSideScroll(page);
    await axe(page, `check personal ${vp.w}`);

    await page.goto("/#/compare/new?line=personal"); // slice 6: the Compare entry screen
    await expect(page.getByRole("heading", { name: "New comparison" })).toBeVisible();
    await noSideScroll(page);
    await axe(page, `compare personal ${vp.w}`);
  });
}
