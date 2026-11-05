import { test, expect, type Page, type Route } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Work queue UI + Activity (docs/specs/2026-11-02-work-queue-design.md section 2; ledger D5, D6, D9, D11, M1, M5;
// owner feedback 2026-11-03: Retry proposal; QUEUE-BRIEF.md) against the dev backend (backend/dev_seed.py):
// run 7 personal Needs review, run 8 personal Needs attention, run 4 commercial Needs attention with "Proposal didn't finish"
// (run 9 is its failed proposal), runs 1/2/3/5 Needs attention (run 1 unnamed), run 6 Ready.
// Groups on both pages; Review deep link; Retry, Retry proposal and Dismiss leave the group; Mark done + Undo,
// Dismiss + Undo; Build proposal preselects documents and client and Undo restores the old draft; an In progress row
// moves on finish (polling, route-intercepted) and polling stops; all-empty state; the client field on the start
// screens and in the run header; Activity filter, cap note and links; axe (every violation) at 1440, 1024 and 390.
// Every test leaves the seeded runs as it found them.

const here = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.resolve(here, "../qa-screenshots/queue");
const QUOTES = path.resolve(here, "../../tests/fixtures/quotes");
const API = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

type Group = "in_progress" | "needs_review" | "ready" | "needs_attention" | "done_recently";
const GROUPS: Group[] = ["in_progress", "needs_review", "ready", "needs_attention", "done_recently"];
type Queue = { line: string; truncated: boolean } & Record<Group, { run_id: number; [k: string]: unknown }[]>;

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

async function apiQueue(page: Page, line: "commercial" | "personal"): Promise<Queue> {
  const res = await page.request.get(`${API}/api/queue?line=${line}`);
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as Queue;
}

async function patchRun(page: Page, id: number, data: Record<string, unknown>) {
  const res = await page.request.patch(`${API}/api/runs/${id}`, { data });
  expect(res.ok(), `PATCH run ${id} ${JSON.stringify(data)}`).toBeTruthy();
}

const group = (page: Page, g: Group) => page.locator(`section[data-group="${g}"]`);
const row = (page: Page, id: number) => page.locator(`li[data-run-id="${id}"]`);
const inGroup = (page: Page, g: Group, id: number) => group(page, g).locator(`li[data-run-id="${id}"]`);
const toast = (page: Page) => page.locator(".toast-region");

async function menu(page: Page, id: number, item: string) {
  await row(page, id).getByRole("button", { name: /^Actions for / }).click();
  await page.getByRole("menuitem", { name: item }).click();
}

/** Serve GET /api/queue through `edit` (applied to the real answer); returns a counter of queue requests. */
async function editQueue(page: Page, edit: (q: Queue) => Queue) {
  const seen = { n: 0 };
  await page.route(/\/api\/queue\?line=/, async (route: Route) => {
    seen.n += 1;
    const res = await route.fetch();
    route.fulfill({ response: res, json: edit((await res.json()) as Queue) });
  });
  return seen;
}

function moveTo(q: Queue, id: number, to: Group, patch: Record<string, unknown> = {}): Queue {
  let found: Queue[Group][number] | undefined;
  const next = { ...q };
  for (const g of GROUPS) {
    found = found ?? q[g].find((i) => i.run_id === id);
    next[g] = q[g].filter((i) => i.run_id !== id);
  }
  if (found) next[to] = [{ ...found, ...patch }, ...next[to]];
  return next;
}

// The seed contract this suite relies on (python -m backend.dev_seed seed). Runs left in other states by earlier
// suites (re-tags, Mark done) are put back first.
test.beforeEach(async ({ page }) => {
  for (const id of [7, 8]) await patchRun(page, id, { line: "personal", queue_state: "open" });
  for (const id of [1, 2, 3, 4, 5, 6]) await patchRun(page, id, { line: "commercial", queue_state: "open" });
});

test("both pages show the seeded runs in the right groups, named by client", async ({ page }) => {
  await page.goto("/#/personal-lines");
  await expect(inGroup(page, "needs_review", 7)).toBeVisible();
  await expect(inGroup(page, "needs_attention", 8)).toBeVisible();
  // The count is the server's (other suites add personal checks of their own).
  const personal = await apiQueue(page, "personal");
  const g = group(page, "needs_review");
  await expect(g.getByRole("heading", { level: 2 })).toHaveText(new RegExp(`Needs review\\s*${personal.needs_review.length}$`));
  await expect(g.locator("li[data-run-id]")).toHaveCount(personal.needs_review.length);
  await expect(row(page, 7)).toContainText("Avery and Jordan Pellham");
  await expect(row(page, 7)).toContainText("Policy Check · Renewal check");
  await expect(row(page, 7)).toContainText(/\d+ open: \d+ critical/);
  await expect(row(page, 7).getByRole("link", { name: "Review Avery and Jordan Pellham" })).toBeVisible();
  await expect(row(page, 8)).toContainText("1 document not read");
  await expect(row(page, 8).getByRole("button", { name: /^Retry/ })).toBeVisible();
  // Commercial runs never show on Personal lines; Done recently starts collapsed when present.
  for (const id of [1, 2, 3, 4, 5, 6]) await expect(row(page, id)).toHaveCount(0);
  await expect(page.getByRole("link", { name: "See everything in Activity" })).toHaveAttribute("href", "#/activity?line=personal");
  await shot(page, "1440-personal");
  await axe(page, "personal 1440");

  await page.goto("/#/");
  await expect(page.getByRole("heading", { level: 1, name: "Compare Quotes" })).toBeVisible();
  await expect(inGroup(page, "ready", 6)).toBeVisible();
  await expect(inGroup(page, "ready", 4)).toHaveCount(0);
  await expect(row(page, 6).getByRole("button", { name: /^Build proposal/ })).toBeVisible();
  // A comparison whose proposal did not finish needs attention (D3 amended) and offers Retry proposal,
  // not a fresh Build proposal.
  await expect(row(page, 4)).toContainText("Proposal didn't finish");
  await expect(row(page, 4).getByRole("button", { name: /^Retry proposal/ })).toBeVisible();
  await expect(row(page, 4).getByRole("button", { name: /^Build proposal/ })).toHaveCount(0);
  for (const id of [1, 2, 3, 4, 5]) await expect(inGroup(page, "needs_attention", id)).toBeVisible();
  await expect(row(page, 1)).toContainText("Unnamed client");
  await expect(row(page, 5)).toContainText("name from the documents");
  // Proposal runs live under Proposals and Activity, never in the queue.
  await expect(row(page, 9)).toHaveCount(0);
  for (const id of [7, 8]) await expect(row(page, id)).toHaveCount(0);
  // Order on the page follows the spec: Ready before Needs attention.
  const order = await page.locator("section[data-group]").evaluateAll((els) => els.map((e) => e.getAttribute("data-group")));
  expect(order.indexOf("ready")).toBeLessThan(order.indexOf("needs_attention"));
  await shot(page, "1440-workflows");
  await axe(page, "workflows 1440");
});

test("Review opens the run at its first open discrepancy", async ({ page }) => {
  const list = (await (await page.request.get(`${API}/api/runs/7/discrepancies`)).json()) as { items: { id: string; status: string }[] };
  const first = list.items.find((i) => i.status === "open")!;
  await page.goto("/#/personal-lines");
  await row(page, 7).getByRole("link", { name: /^Review/ }).click();
  await expect(page).toHaveURL(new RegExp(`#/runs/7\\?discrepancy=${first.id}$`));
  await expect(page.locator(".disc").first()).toBeVisible();
  await expect(page.locator(":focus")).toBeVisible();
});

test("Mark done + Undo and Dismiss + Undo; Done recently opens and offers Move back to queue", async ({ page }) => {
  await page.goto("/#/");
  await expect(inGroup(page, "ready", 6)).toBeVisible();

  // Mark done: no confirm, the row leaves Ready at once, the toast offers Undo.
  const [patch] = await Promise.all([
    page.waitForRequest((r) => r.method() === "PATCH" && /\/api\/runs\/6$/.test(r.url())),
    menu(page, 6, "Mark done"),
  ]);
  expect(patch.postDataJSON()).toEqual({ queue_state: "done" });
  await expect(inGroup(page, "ready", 6)).toHaveCount(0);
  await expect(toast(page)).toContainText("Marked done.");
  const toggle = group(page, "done_recently").getByRole("button", { name: /Done recently/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(inGroup(page, "done_recently", 6)).toContainText("Marked done");
  await shot(page, "1440-done-recently");
  const [undo] = await Promise.all([
    page.waitForRequest((r) => r.method() === "PATCH" && /\/api\/runs\/6$/.test(r.url())),
    toast(page).getByRole("button", { name: "Undo" }).click(),
  ]);
  expect(undo.postDataJSON()).toEqual({ queue_state: "open" });
  await expect(inGroup(page, "ready", 6)).toBeVisible();
  await expect(toast(page)).toContainText("Back in the queue.");

  // Dismiss + Undo (Ctrl+Z is the toast's keyboard path).
  await menu(page, 6, "Dismiss");
  await expect(inGroup(page, "ready", 6)).toHaveCount(0);
  await expect(toast(page)).toContainText("Dismissed.");
  expect((await (await page.request.get(`${API}/api/runs/6`)).json()).queue_state).toBe("dismissed");
  await page.keyboard.press("Control+z");
  await expect(inGroup(page, "ready", 6)).toBeVisible();
  expect((await (await page.request.get(`${API}/api/runs/6`)).json()).queue_state).toBe("open");

  // Done recently's own way back.
  await menu(page, 6, "Dismiss");
  await expect(inGroup(page, "ready", 6)).toHaveCount(0);
  const toggle2 = group(page, "done_recently").getByRole("button", { name: /Done recently/ });
  if ((await toggle2.getAttribute("aria-expanded")) === "false") await toggle2.click();
  await menu(page, 6, "Move back to queue");
  await expect(inGroup(page, "ready", 6)).toBeVisible();
});

test("In progress rows have no Mark done or Dismiss (M1)", async ({ page }) => {
  await editQueue(page, (q) => moveTo(q, 5, "in_progress", { status: "running", retryable: false }));
  await page.goto("/#/");
  await expect(inGroup(page, "in_progress", 5)).toBeVisible();
  await expect(inGroup(page, "in_progress", 5)).toContainText("Reading documents");
  await row(page, 5).getByRole("button", { name: /^Actions for / }).click();
  await expect(page.getByRole("menuitem", { name: "Mark done" })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: "Dismiss" })).toHaveCount(0);
  // Queue navigation (2026-11-03): "Open run" says what it opens now.
  await expect(page.getByRole("menuitem", { name: "Open comparison" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Move to Personal lines" })).toBeVisible();
  // The menu opens over the rows below, not clipped by the live row (its sweep line clips only itself).
  const hit = await page.getByRole("menuitem", { name: "Move to Personal lines" }).evaluate((el) => {
    const b = el.getBoundingClientRect();
    return document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)?.closest("[role=menuitem]") === el;
  });
  expect(hit, "menu item is on top").toBe(true);
  await shot(page, "1440-in-progress-menu");
  await page.keyboard.press("Escape");
});

test("Retry leaves Needs attention; the In progress row moves on finish (polling) and polling stops", async ({ page }) => {
  // The retry itself is intercepted (the seeded run stays as it is); the queue answer shows run 5 running until
  // `finished`, as the server would while the retry runs.
  let active = false;
  let finished = false;
  await page.route(/\/api\/runs\/5\/retry$/, async (route) => {
    active = true;
    const run = await (await page.request.get(`${API}/api/runs/5`)).json();
    route.fulfill({ status: 202, json: { run: { ...run, status: "queued", active: true, retryable: false } } });
  });
  const seen = await editQueue(page, (q) => (active && !finished ? moveTo(q, 5, "in_progress", { status: "running", retryable: false }) : q));

  await page.goto("/#/");
  await expect(inGroup(page, "needs_attention", 5)).toBeVisible();
  await row(page, 5).getByRole("button", { name: /^Retry/ }).click();
  await expect(inGroup(page, "in_progress", 5)).toBeVisible();
  await expect(inGroup(page, "needs_attention", 5)).toHaveCount(0);
  await shot(page, "1440-in-progress");
  // D5: polls every 3 s while something runs.
  const before = seen.n;
  await expect.poll(() => seen.n, { timeout: 8000 }).toBeGreaterThan(before);
  finished = true;
  await expect(inGroup(page, "needs_attention", 5)).toBeVisible({ timeout: 8000 });
  await expect(group(page, "in_progress")).toHaveCount(0);
  await expect(page.locator(".queue > [role=status]")).toContainText("moved to Needs attention");
  const after = seen.n;
  await page.waitForTimeout(4000);
  expect(seen.n, "polling stops once nothing runs").toBe(after);
});

test("Retry proposal reruns the failed proposal and moves the comparison out of Needs attention", async ({ page }) => {
  let retried = false;
  await page.route(/\/api\/runs\/9\/retry$/, async (route) => {
    retried = true;
    const run = await (await page.request.get(`${API}/api/runs/9`)).json();
    route.fulfill({ status: 202, json: { run: { ...run, status: "queued", active: true, retryable: false } } });
  });
  await editQueue(page, (q) => (retried ? moveTo(q, 4, "in_progress", { summary: "Building proposal", reasons: null }) : q));
  await page.goto("/#/");
  await expect(row(page, 4).getByRole("button", { name: /^Retry proposal/ })).toBeVisible();
  // The overflow keeps the other ways forward.
  await row(page, 4).getByRole("button", { name: /^Actions for / }).click();
  await expect(page.getByRole("menuitem", { name: "Open failed proposal" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Start new proposal" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Mark done" })).toBeVisible();
  await page.keyboard.press("Escape");

  const [post] = await Promise.all([
    page.waitForRequest((r) => r.method() === "POST" && /\/api\/runs\/9\/retry$/.test(r.url())),
    row(page, 4).getByRole("button", { name: /^Retry proposal/ }).click(),
  ]);
  expect(post.url()).toMatch(/\/api\/runs\/9\/retry$/);
  await expect(inGroup(page, "needs_attention", 4)).toHaveCount(0);
  await expect(inGroup(page, "in_progress", 4)).toContainText("Building proposal");
});

test("Build proposal preselects the documents and client; Undo restores the old draft; Generate sends the source", async ({ page }) => {
  const run6 = (await (await page.request.get(`${API}/api/runs/6`)).json()) as { documents: { document_id: number; position: number }[]; client_name: string };
  await page.goto("/#/");
  await page.evaluate(() =>
    sessionStorage.setItem("brokerdocs.proposal-draft.v1", JSON.stringify({ client_name: "Old Draft Co", templateSlug: "standard", step: 0, reached: 0 })),
  );
  await row(page, 6).getByRole("button", { name: /^Build proposal/ }).click();
  await expect(page).toHaveURL(/#\/proposals\/new$/);
  await expect(page.getByText(`From the comparison for ${run6.client_name} (run 6).`)).toBeVisible();
  await expect(toast(page)).toContainText("Replaced your unfinished proposal draft.");
  const draft = await page.evaluate(() => JSON.parse(sessionStorage.getItem("brokerdocs.proposal-draft.v1")!));
  expect(draft.docs.map((d: { id: number }) => d.id)).toEqual([...run6.documents].sort((a, b) => a.position - b.position).map((d) => d.document_id));
  expect(draft).toMatchObject({ client_name: run6.client_name, source_run_id: 6, line: "commercial" });
  await shot(page, "1440-build-proposal");

  await toast(page).getByRole("button", { name: "Undo" }).click();
  await expect(page.getByText(/From the comparison for/)).toHaveCount(0);
  expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem("brokerdocs.proposal-draft.v1")!).client_name)).toBe("Old Draft Co");

  // Again, then generate: POST /api/runs carries source_run_id and the comparison's line (M5). The answer is
  // intercepted so no proposal run is made from the seed.
  await page.evaluate(() => sessionStorage.clear());
  await page.goto("/#/");
  await row(page, 6).getByRole("button", { name: /^Build proposal/ }).click();
  await expect(toast(page)).not.toContainText("Replaced");
  await page.getByRole("radio", { name: /Standard/ }).check();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByLabel(/Client name/)).toHaveValue(run6.client_name);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.route(/\/api\/runs$/, (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 400, json: { error: { code: "invalid_run", message: "Intercepted by the test." } } })
      : route.fallback(),
  );
  const [gen] = await Promise.all([
    page.waitForRequest((r) => r.method() === "POST" && /\/api\/runs$/.test(r.url())),
    page.getByRole("button", { name: "Generate proposal" }).click(),
  ]);
  expect(gen.postDataJSON()).toMatchObject({ workflow: "proposal-builder", source_run_id: 6, line: "commercial" });
  await page.evaluate(() => sessionStorage.clear());
});

test("all-empty queue: one calm empty state carrying the start actions", async ({ page }) => {
  const empty = (q: Queue): Queue => ({ ...q, in_progress: [], needs_review: [], ready: [], needs_attention: [], done_recently: [] });
  await editQueue(page, empty);
  await page.goto("/#/personal-lines");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "No personal-lines work yet." })).toBeVisible();
  await expect(main.getByRole("link", { name: "Compare homeowners quotes" })).toHaveCount(1);
  await expect(main.getByRole("link", { name: "Check a renewal" })).toHaveCount(1);
  await expect(main.locator("section[data-group]")).toHaveCount(0);
  await shot(page, "1440-empty-personal");
  await axe(page, "empty personal 1440");

  await page.goto("/#/");
  await expect(main.getByRole("heading", { name: "No commercial work yet." })).toBeVisible();
  await expect(main.getByText("Drop quote PDFs to compare")).toBeVisible();
  await expect(main.getByRole("link", { name: "Check a renewal instead" })).toHaveAttribute("href", "#/policy-check/new");
  await shot(page, "1440-empty-workflows");
  await axe(page, "empty workflows 1440");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/personal-lines");
  await noSideScroll(page);
  await axe(page, "empty personal 390");
});

test("an error shows the notice with Retry and stops polling", async ({ page }) => {
  let fail = true;
  let calls = 0;
  await page.route(/\/api\/queue\?line=/, (route) => {
    calls += 1;
    return fail
      ? route.fulfill({ status: 500, json: { error: { code: "internal_error", message: "The queue could not be read." } } })
      : route.fallback();
  });
  await page.goto("/#/personal-lines");
  await expect(page.getByRole("alert")).toContainText("The queue could not be read.");
  const n = calls;
  await page.waitForTimeout(3500);
  expect(calls).toBe(n);
  fail = false;
  await page.getByRole("alert").getByRole("button", { name: "Retry" }).click();
  await expect(inGroup(page, "needs_review", 7)).toBeVisible();
});

test("Client field: set on Compare and Policy Check start, edited and cleared in the run header", async ({ page }) => {
  // Compare upload panel: the name goes out as client_name (the POST answer is intercepted, nothing is created).
  await page.goto("/#/");
  await page.locator(".upload input[type=file]").setInputFiles([path.join(QUOTES, "carrier_a_clean.pdf")]);
  await expect(page.locator(".file-row--ready")).toHaveCount(1);
  await page.getByLabel(/^Client/).fill("  Harbor   Lights Marina ");
  await page.route(/\/api\/runs$/, (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 400, json: { error: { code: "invalid_run", message: "Intercepted by the test." } } })
      : route.fallback(),
  );
  const [post] = await Promise.all([
    page.waitForRequest((r) => r.method() === "POST" && /\/api\/runs$/.test(r.url())),
    page.getByRole("button", { name: "Compare 1 quote" }).click(),
  ]);
  expect(post.postDataJSON()).toMatchObject({ workflow: "compare-quotes", client_name: "Harbor   Lights Marina" });

  // Policy Check step 1, kept in the saved draft.
  await page.goto("/#/policy-check/new");
  if (await page.getByRole("button", { name: "Discard draft" }).isVisible()) await page.getByRole("button", { name: "Discard draft" }).click();
  await page.getByLabel(/^Client/).fill("Pellham household");
  await expect(page.getByLabel(/^Client/)).toHaveValue("Pellham household");
  await page.unroute(/\/api\/runs$/);

  // Run header: next to Line. Enter saves (PATCH client_name), "" clears, Esc cancels. Queue navigation
  // (2026-11-03, deliberate update): the name moved into the title, the button reads "Change" ("Change client: <name>"
  // to a screen reader), and clearing a typed name falls back to the documents' name, so run 6 shows "Change" with the
  // "name from the documents" tag rather than "Add client" (that is checked on run 1 in queue-nav.spec.ts).
  const before = (await (await page.request.get(`${API}/api/runs/6`)).json()).client_name as string;
  const title = page.locator(".toolbar__title");
  try {
    await page.goto("/#/runs/6");
    const button = page.getByRole("button", { name: `Change client: ${before}` });
    await expect(button).toBeVisible();
    await expect(button).toHaveText("Change");
    await button.click();
    const input = page.getByRole("textbox", { name: "Client" });
    await expect(input).toHaveValue(before);
    await input.fill("Cedar Hollow Hotels");
    const [patch] = await Promise.all([
      page.waitForRequest((r) => r.method() === "PATCH" && /\/api\/runs\/6$/.test(r.url())),
      input.press("Enter"),
    ]);
    expect(patch.postDataJSON()).toEqual({ client_name: "Cedar Hollow Hotels" });
    await expect(toast(page)).toContainText("Client name saved.");
    await expect(page.getByRole("button", { name: "Change client: Cedar Hollow Hotels" })).toBeFocused();
    await expect(title).toContainText("Cedar Hollow Hotels · Compare Quotes");
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveText(/Cedar Hollow Hotels$/);
    await shot(page, "1440-run-header-client");

    await page.getByRole("button", { name: "Change client: Cedar Hollow Hotels" }).click();
    await page.getByRole("textbox", { name: "Client" }).fill("Something else");
    await page.getByRole("textbox", { name: "Client" }).press("Escape");
    await expect(page.getByRole("button", { name: "Change client: Cedar Hollow Hotels" })).toBeVisible();

    await page.getByRole("button", { name: "Change client: Cedar Hollow Hotels" }).click();
    await page.getByRole("textbox", { name: "Client" }).fill("");
    await page.getByRole("textbox", { name: "Client" }).press("Enter");
    await expect(toast(page)).toContainText("Client name cleared.");
    await expect(page.getByRole("button", { name: "Change client: Cedar Hollow Hospitality LLC" })).toBeVisible();
    await expect(title).toContainText("Cedar Hollow Hospitality LLC · Compare Quotes");
    await expect(title).toContainText("name from the documents");
    expect((await (await page.request.get(`${API}/api/runs/6`)).json()).client_name).toBeNull();
  } finally {
    await patchRun(page, 6, { client_name: before });
  }
});

test("Activity: line filter, links in and out, cap note", async ({ page }) => {
  const all = (await (await page.request.get(`${API}/api/runs?limit=100`)).json()).runs as { id: number; line: string }[];
  await page.goto("/#/personal-lines");
  await page.getByRole("link", { name: "See everything in Activity" }).click();
  await expect(page).toHaveURL(/#\/activity\?line=personal$/);
  await expect(page).toHaveTitle("Activity");
  await expect(page.getByRole("heading", { level: 1, name: "Activity" })).toBeVisible();
  const filter = page.getByRole("group", { name: "Show" });
  await expect(filter.getByRole("radio", { name: "Personal" })).toBeChecked();
  const runs = page.getByRole("main").locator("a.run-link");
  await expect(runs).toHaveCount(all.filter((r) => r.line === "personal").length);
  await filter.getByText("Commercial").click();
  await expect(runs).toHaveCount(all.filter((r) => r.line === "commercial").length);
  await expect(page).toHaveURL(/#\/activity\?line=commercial$/);
  await filter.getByText("All", { exact: true }).click();
  await expect(runs).toHaveCount(all.length);
  await expect(page).toHaveURL(/#\/activity$/);
  await expect(page.getByText("Showing the newest 100 runs.")).toHaveCount(0);
  await shot(page, "1440-activity");
  await axe(page, "activity 1440");

  // The run view's Activity tab opens Activity.
  await page.goto("/#/runs/7");
  await expect(page.getByRole("link", { name: "Activity", exact: true })).toHaveAttribute("href", "#/activity");

  // D11: at 100 runs the cap is said out loud.
  const one = (await (await page.request.get(`${API}/api/runs/6`)).json()) as Record<string, unknown>;
  await page.route(/\/api\/runs\?limit=100$/, (route) =>
    route.fulfill({ json: { runs: Array.from({ length: 100 }, (_, i) => ({ ...one, id: 1000 + i })) } }),
  );
  await page.goto("/#/activity");
  await expect(runs).toHaveCount(100);
  await expect(page.getByText("Showing the newest 100 runs.")).toBeVisible();
});

for (const vp of [
  { w: 1440, h: 900 },
  { w: 1024, h: 768 },
  { w: 390, h: 844 },
]) {
  test(`axe 0 at ${vp.w}: both queues (menu open, Done recently open) and Activity`, async ({ page }) => {
    await page.setViewportSize({ width: vp.w, height: vp.h });
    // A Done recently row and an In progress row, so every row kind is checked.
    await editQueue(page, (q) =>
      q.line === "commercial"
        ? moveTo(moveTo(q, 2, "done_recently", { queue_state: "dismissed", queue_changed_at: new Date().toISOString() }), 5, "in_progress", { status: "running", retryable: false })
        : q,
    );
    await page.goto("/#/personal-lines");
    await expect(inGroup(page, "needs_review", 7)).toBeVisible();
    await noSideScroll(page);
    await shot(page, `${vp.w}-personal`);
    await axe(page, `personal ${vp.w}`);

    await page.goto("/#/");
    await expect(inGroup(page, "ready", 6)).toBeVisible();
    await group(page, "done_recently").getByRole("button", { name: /Done recently/ }).click();
    await expect(inGroup(page, "done_recently", 2)).toBeVisible();
    await row(page, 6).getByRole("button", { name: /^Actions for / }).click();
    await expect(page.getByRole("menuitem", { name: "Mark done" })).toBeVisible();
    await noSideScroll(page);
    await shot(page, `${vp.w}-workflows-menu`);
    await axe(page, `workflows ${vp.w}`);
    await page.keyboard.press("Escape");

    await page.goto("/#/activity");
    await expect(page.getByRole("main").locator("a.run-link").first()).toBeVisible();
    await noSideScroll(page);
    await shot(page, `${vp.w}-activity`);
    await axe(page, `activity ${vp.w}`);
  });
}
