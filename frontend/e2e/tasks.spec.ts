import { test, expect, type Page, type Request } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Slice 3b T7 Tasks (spec 2.2, 2.3, R3 to R5, D2, D3, D6, D8, O3, O4, O7; TASKS-BRIEF.md) against the dev backend
// (backend/dev_seed.py: an overdue free-standing task and a task linked to run 7's first discrepancy; scripted model).
// Create, edit, done, reopen; search by title and run label; each sort both ways; Open / Done / All and the no-match
// state; delete + Undo (same id), delete committed on toast close and on navigation, failed DELETE restores the row;
// overdue; Create task from a discrepancy card and the deep links both ways; dropped item and removed run (route
// intercept); empty state; axe (every violation) at 1440, 1024 and 390. Every test removes the tasks it made.

const here = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.resolve(here, "../qa-screenshots/tasks");
const API = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

interface TaskLite {
  id: number;
  title: string;
  status: "open" | "done";
  due_date: string | null;
  run_id: number | null;
  discrepancy_item_id: string | null;
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

function isoDay(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

async function allTasks(page: Page): Promise<TaskLite[]> {
  const res = await page.request.get(`${API}/api/tasks`);
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { tasks: TaskLite[] }).tasks;
}

async function createTask(page: Page, body: Record<string, unknown>): Promise<TaskLite> {
  const res = await page.request.post(`${API}/api/tasks`, { data: body });
  expect(res.status(), `POST ${JSON.stringify(body)}`).toBe(201);
  return (await res.json()) as TaskLite;
}

async function patchTask(page: Page, id: number, body: Record<string, unknown>) {
  const res = await page.request.patch(`${API}/api/tasks/${id}`, { data: body });
  expect(res.ok(), `PATCH task ${id}`).toBeTruthy();
}

/** Remove every task except the two the seed made (ids 1 and 2), so tests stay order-independent. */
async function cleanUp(page: Page) {
  for (const t of await allTasks(page)) {
    if (t.id > 2) await page.request.delete(`${API}/api/tasks/${t.id}`);
  }
}

/** The seeded four-role Policy Check run (dev_seed run 7). */
async function fullRun(page: Page): Promise<number> {
  const res = await page.request.get(`${API}/api/runs?limit=100`);
  const runs = ((await res.json()) as { runs: { id: number; workflow_name: string; status: string; documents: { slot?: string }[] }[] }).runs
    .filter((r) => r.workflow_name === "Policy Check")
    .sort((a, b) => a.id - b.id);
  const full = runs.find((r) => r.documents.some((d) => d.slot === "ams") && r.status === "done");
  expect(full, "seeded four-role run (python -m backend.dev_seed seed)").toBeTruthy();
  return full!.id;
}

const row = (page: Page, id: number) => page.locator(`tr[data-task="${id}"]`);
const titles = (page: Page) => page.locator(".tasks-table tbody .task-title");
const filterOpt = (page: Page, name: "Open" | "Done" | "All") => page.locator(".tasks__filter").getByText(name, { exact: true });
const filterRadio = (page: Page, name: "Open" | "Done" | "All") => page.locator(".tasks__filter").getByRole("radio", { name });
const search = (page: Page) => page.getByRole("searchbox", { name: "Search tasks" });
const toast = (page: Page) => page.locator(".toast-region");

/** A hash-only goto keeps the document (and the loaded list); reload so the page reads the server again. */
async function openTasks(page: Page, hash = "#/tasks") {
  await page.goto(`/${hash}`);
  await page.reload();
}

test.afterEach(async ({ page }) => {
  await cleanUp(page);
});

test("rail entry, seeded tasks and overdue; create, edit, mark done, reopen; field errors", async ({ page }) => {
  await page.goto("/");
  const rail = page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Tasks" });
  await expect(rail).toHaveAttribute("href", "#/tasks");
  await rail.click();
  await expect(page).toHaveURL(/#\/tasks$/);
  await expect(page).toHaveTitle("Tasks");
  await expect(rail).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveText("Tasks");
  await expect(page.getByRole("heading", { level: 1, name: "Tasks" })).toBeVisible();
  await expect(filterRadio(page, "Open")).toBeChecked();

  // Seed: task 1 is past due and open (Overdue, in the warning token); task 2 links run 7's first discrepancy.
  const overdue = row(page, 1).locator(".task-overdue");
  await expect(overdue).toHaveText("Overdue");
  await expect(overdue).toHaveCSS("color", "rgb(147, 55, 13)");
  const run7 = await fullRun(page);
  await expect(row(page, 2).getByRole("link", { name: `Policy check, run ${run7}` })).toHaveAttribute("href", `#/runs/${run7}`);
  const chip = row(page, 2).locator(".task-chip");
  await expect(chip).toHaveText(/^Coverage C .* · Open$/);
  await expect(chip).toHaveAttribute("href", `#/runs/${run7}?discrepancy=disc-coverage-c-limit`);
  await shot(page, "1440-page");
  await axe(page, "page 1440");

  // New task: empty title is caught in the dialog; a 400 invalid_task lands under its field.
  await page.getByRole("button", { name: "New task" }).click();
  const dialog = page.getByRole("dialog", { name: "New task" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Title")).toBeFocused();
  await dialog.getByRole("button", { name: "Create task" }).click();
  await expect(dialog.getByText("Enter a title.")).toBeVisible();
  await expect(dialog.getByLabel("Title")).toBeFocused();
  await dialog.getByLabel("Title").fill("E2E call the carrier about the roof");
  await dialog.getByLabel(/^Due date/).fill(isoDay(4));
  await page.route("**/api/tasks", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({
          status: 400,
          contentType: "application/json",
          body: JSON.stringify({ error: { code: "invalid_task", message: "The due date must be a real date.", field: "due_date" } }),
        })
      : route.fallback(),
  );
  await dialog.getByRole("button", { name: "Create task" }).click();
  await expect(dialog.getByText("The due date must be a real date.")).toBeVisible();
  await expect(dialog.getByLabel(/^Due date/)).toBeFocused();
  await expect(dialog.getByLabel(/^Due date/)).toHaveAttribute("aria-invalid", "true");
  await shot(page, "1440-dialog-error");
  await axe(page, "dialog with error 1440");
  await page.unroute("**/api/tasks");

  // A Compare Quotes run offers no discrepancy picker.
  const compare = (await (await page.request.get(`${API}/api/runs?limit=100`)).json()).runs.find(
    (r: { workflow_name: string }) => r.workflow_name === "Compare Quotes",
  ) as { id: number };
  await dialog.getByLabel(/^Run/).selectOption(String(compare.id));
  await expect(dialog.getByLabel(/^Discrepancy/)).toHaveCount(0);
  const [post] = await Promise.all([
    page.waitForRequest((r) => r.method() === "POST" && /\/api\/tasks$/.test(r.url())),
    dialog.getByRole("button", { name: "Create task" }).click(),
  ]);
  expect(post.postDataJSON()).toEqual({ title: "E2E call the carrier about the roof", due_date: isoDay(4), run_id: compare.id });
  await expect(dialog).toHaveCount(0);
  await expect(toast(page)).toContainText("Task created.");
  const created = (await allTasks(page)).find((t) => t.title === "E2E call the carrier about the roof")!;
  await expect(row(page, created.id).getByRole("link", { name: `Compare Quotes, run ${compare.id}` })).toBeVisible();
  await expect(row(page, created.id).locator(".task-overdue")).toHaveCount(0);

  // Edit: the title button opens the same dialog; only the changed field is sent.
  await row(page, created.id).locator(".task-title").click();
  const edit = page.getByRole("dialog", { name: "Edit task" });
  await expect(edit.getByLabel("Title")).toHaveValue("E2E call the carrier about the roof");
  await edit.getByLabel("Title").fill("E2E call the carrier about the roof and the deck");
  const [patch] = await Promise.all([
    page.waitForRequest((r) => r.method() === "PATCH" && new RegExp(`/api/tasks/${created.id}$`).test(r.url())),
    edit.getByRole("button", { name: "Save changes" }).click(),
  ]);
  expect(patch.postDataJSON()).toEqual({ title: "E2E call the carrier about the roof and the deck" });
  await expect(row(page, created.id).locator(".task-title")).toHaveText("E2E call the carrier about the roof and the deck");
  await expect(row(page, created.id).locator(".task-title")).toBeFocused();

  // Mark done: optimistic, PATCH {status}; the row stays in the Open view until the filter changes.
  const box = row(page, created.id).getByRole("checkbox");
  const [done] = await Promise.all([
    page.waitForRequest((r) => r.method() === "PATCH" && new RegExp(`/api/tasks/${created.id}$`).test(r.url())),
    box.check(),
  ]);
  expect(done.postDataJSON()).toEqual({ status: "done" });
  await expect(row(page, created.id)).toHaveClass(/is-done/);
  await expect(page.locator(".tasks__count")).toHaveText("2 open, 1 done");
  await filterOpt(page, "Done").click();
  await expect(titles(page)).toHaveText(["E2E call the carrier about the roof and the deck"]);
  await filterOpt(page, "Open").click();
  await expect(row(page, created.id)).toHaveCount(0);
  // Reopen from the Done view.
  await filterOpt(page, "Done").click();
  await row(page, created.id).getByRole("checkbox").uncheck();
  await expect.poll(async () => (await allTasks(page)).find((t) => t.id === created.id)?.status).toBe("open");
  await filterOpt(page, "Open").click();
  await expect(row(page, created.id)).toBeVisible();

  // Overdue is for open tasks only: a past-due task that is done shows no mark.
  const past = await createTask(page, { title: "E2E past due but done", due_date: isoDay(-2) });
  await patchTask(page, past.id, { status: "done" });
  await page.reload();
  await filterOpt(page, "All").click();
  await expect(row(page, past.id).locator(".task-overdue")).toHaveCount(0);
  await expect(row(page, 1).locator(".task-overdue")).toBeVisible();

  // A failed status PATCH rolls the checkbox back with an error toast.
  await page.route(`**/api/tasks/${created.id}`, (route) =>
    route.request().method() === "PATCH"
      ? route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "internal_error", message: "Server error." } }) })
      : route.fallback(),
  );
  // click(), not check(): check() re-reads the box after the click and throws if the rollback already landed
  // (the fulfilled 500 can beat it), which made this test fail about 1 run in 10. The rollback is asserted below.
  await row(page, created.id).getByRole("checkbox").click();
  await expect(toast(page)).toContainText("Couldn't save: Server error.");
  await expect(row(page, created.id).getByRole("checkbox")).not.toBeChecked();
  await page.unroute(`**/api/tasks/${created.id}`);
});

test("search by title and run label; each sort both ways; Open / Done / All; no-match state", async ({ page }) => {
  const alpha = await createTask(page, { title: "E2E Alpha renewal letter", due_date: isoDay(5) });
  const bravo = await createTask(page, { title: "E2E Bravo loss runs", due_date: isoDay(1) });
  const charlie = await createTask(page, { title: "E2E Charlie binder" });
  await openTasks(page);
  await expect(row(page, alpha.id)).toBeVisible();

  // Search: title (case-insensitive, live) and run label.
  await search(page).fill("e2e");
  await expect(titles(page)).toHaveCount(3);
  await search(page).fill("BRAVO");
  await expect(titles(page)).toHaveText(["E2E Bravo loss runs"]);
  const run7 = await fullRun(page);
  await search(page).fill(`run ${run7}`);
  await expect(titles(page)).toHaveText([/^Follow up: Coverage C/]);
  await search(page).fill(`Policy Check · Run ${run7}`);
  await expect(titles(page)).toHaveText([/^Follow up: Coverage C/]);

  await search(page).fill("E2E");
  const header = (name: string) => page.locator(".tasks-table thead th", { has: page.getByRole("button", { name, exact: true }) });
  // Default: due date ascending, no date last.
  await expect(header("Due date")).toHaveAttribute("aria-sort", "ascending");
  await expect(titles(page)).toHaveText(["E2E Bravo loss runs", "E2E Alpha renewal letter", "E2E Charlie binder"]);
  await page.getByRole("button", { name: "Due date", exact: true }).click();
  await expect(header("Due date")).toHaveAttribute("aria-sort", "descending");
  await expect(titles(page)).toHaveText(["E2E Alpha renewal letter", "E2E Bravo loss runs", "E2E Charlie binder"]);
  // Title: A to Z, then Z to A. Only the active header carries aria-sort.
  await page.getByRole("button", { name: "Title", exact: true }).click();
  await expect(header("Title")).toHaveAttribute("aria-sort", "ascending");
  await expect(header("Due date")).not.toHaveAttribute("aria-sort", /.*/);
  await expect(titles(page)).toHaveText(["E2E Alpha renewal letter", "E2E Bravo loss runs", "E2E Charlie binder"]);
  await page.getByRole("button", { name: "Title", exact: true }).click();
  await expect(header("Title")).toHaveAttribute("aria-sort", "descending");
  await expect(titles(page)).toHaveText(["E2E Charlie binder", "E2E Bravo loss runs", "E2E Alpha renewal letter"]);
  // Created: newest first, then oldest first.
  await page.getByRole("button", { name: "Created", exact: true }).click();
  await expect(header("Created")).toHaveAttribute("aria-sort", "descending");
  await expect(titles(page)).toHaveText(["E2E Charlie binder", "E2E Bravo loss runs", "E2E Alpha renewal letter"]);
  await page.getByRole("button", { name: "Created", exact: true }).click();
  await expect(header("Created")).toHaveAttribute("aria-sort", "ascending");
  await expect(titles(page)).toHaveText(["E2E Alpha renewal letter", "E2E Bravo loss runs", "E2E Charlie binder"]);
  await shot(page, "1440-sorted");

  // Filter: Open (default), Done, All.
  await patchTask(page, bravo.id, { status: "done" });
  await page.reload();
  await search(page).fill("E2E");
  await expect(filterRadio(page, "Open")).toBeChecked();
  await expect(titles(page)).toHaveText(["E2E Alpha renewal letter", "E2E Charlie binder"]);
  await filterOpt(page, "Done").click();
  await expect(titles(page)).toHaveText(["E2E Bravo loss runs"]);
  await filterOpt(page, "All").click();
  await expect(titles(page)).toHaveCount(3);
  // Keyboard: the filter is one radio group (arrow keys move).
  await filterRadio(page, "All").focus();
  await page.keyboard.press("ArrowLeft");
  await expect(filterRadio(page, "Done")).toBeChecked();

  // No match: says so, and one action clears the search and shows everything.
  await search(page).fill("nothing has this title");
  await expect(page.getByRole("heading", { name: "No tasks match this search and filter." })).toBeVisible();
  await shot(page, "1440-no-match");
  await axe(page, "no match 1440");
  await page.getByRole("button", { name: "Show all tasks" }).click();
  await expect(search(page)).toHaveValue("");
  await expect(filterRadio(page, "All")).toBeChecked();
  await expect(row(page, charlie.id)).toBeVisible();
  await expect(row(page, 1)).toBeVisible();
});

test("delete: Undo keeps the id; DELETE waits for the toast to close or navigation; a failed DELETE restores the row", async ({ page }) => {
  const t = await createTask(page, { title: "E2E delete me", due_date: isoDay(2) });
  const deletes: string[] = [];
  page.on("request", (r: Request) => {
    if (r.method() === "DELETE") deletes.push(r.url());
  });
  await openTasks(page);
  const menu = (id: number) => row(page, id).getByRole("button", { name: /^Actions for / });

  // Delete then Undo: no request, same id, the deep link still works.
  await menu(t.id).click();
  await expect(page.getByRole("menu")).toBeVisible();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await expect(row(page, t.id)).toHaveCount(0);
  await expect(toast(page)).toContainText("Task deleted.");
  await shot(page, "1440-deleted-toast");
  await toast(page).getByRole("button", { name: "Undo" }).click();
  await expect(row(page, t.id)).toBeVisible();
  await expect(row(page, t.id).locator(".task-title")).toBeFocused();
  await page.waitForTimeout(300);
  expect(deletes).toEqual([]);
  expect((await allTasks(page)).some((x) => x.id === t.id)).toBe(true);
  await openTasks(page, `#/tasks?task=${t.id}`);
  await expect(row(page, t.id)).toHaveClass(/is-target/);

  // Keyboard path: the menu opens with ArrowDown; Esc closes it and returns focus.
  await menu(t.id).focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "Edit" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "Delete" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
  await expect(menu(t.id)).toBeFocused();

  // Delete and let the toast time out: the DELETE goes then, and only then.
  await menu(t.id).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await page.waitForTimeout(1000);
  expect(deletes).toEqual([]);
  await page.waitForRequest((r) => r.method() === "DELETE" && r.url().endsWith(`/api/tasks/${t.id}`), { timeout: 10_000 });
  await expect.poll(async () => (await allTasks(page)).some((x) => x.id === t.id)).toBe(false);
  await expect(toast(page)).toBeEmpty();

  // Delete then navigate away at once: the DELETE is sent on unmount, and the stale Undo is gone.
  const u = await createTask(page, { title: "E2E delete on navigation" });
  await openTasks(page);
  await menu(u.id).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await Promise.all([
    page.waitForRequest((r) => r.method() === "DELETE" && r.url().endsWith(`/api/tasks/${u.id}`)),
    page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Workflows" }).click(),
  ]);
  await expect(toast(page).getByRole("button", { name: "Undo" })).toHaveCount(0);
  await expect.poll(async () => (await allTasks(page)).some((x) => x.id === u.id)).toBe(false);

  // A failed DELETE: the row comes back with an error toast.
  const v = await createTask(page, { title: "E2E delete fails" });
  await openTasks(page);
  await page.route(`**/api/tasks/${v.id}`, (route) =>
    route.request().method() === "DELETE"
      ? route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "internal_error", message: "Server error." } }) })
      : route.fallback(),
  );
  await menu(v.id).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await expect(row(page, v.id)).toHaveCount(0);
  await toast(page).getByRole("button", { name: "Dismiss" }).click();
  await expect(toast(page)).toContainText("Couldn't delete the task. It is back in the list.");
  await expect(row(page, v.id)).toBeVisible();
  await page.unroute(`**/api/tasks/${v.id}`);
  expect((await allTasks(page)).some((x) => x.id === v.id)).toBe(true);
});

test("Create task from a discrepancy card; Task: Open opens it highlighted; the chip brings the card back", async ({ page }) => {
  const run7 = await fullRun(page);
  const item = "disc-coverage-d-limit";
  await page.goto(`/#/runs/${run7}`);
  const card = page.locator(`.disc[data-item="${item}"]`);
  await expect(card).toBeVisible();
  // The seeded task's card already shows its link.
  await expect(page.locator('.disc[data-item="disc-coverage-c-limit"]').getByRole("link", { name: "Task: Open" })).toHaveAttribute(
    "href",
    "#/tasks?task=2",
  );
  const itemTitle = (await card.locator(".disc__title").textContent())!;
  await card.getByRole("button", { name: "Create task" }).click();
  const dialog = page.getByRole("dialog", { name: "New task" });
  await expect(dialog.getByLabel("Title")).toHaveValue(`Follow up: ${itemTitle}`);
  await expect(dialog.getByLabel(/^Run/)).toHaveValue(String(run7));
  await expect(dialog.getByLabel(/^Discrepancy/)).toHaveValue(item);
  await shot(page, "1440-card-dialog");
  await axe(page, "card dialog 1440");
  const [post] = await Promise.all([
    page.waitForRequest((r) => r.method() === "POST" && /\/api\/tasks$/.test(r.url())),
    dialog.getByRole("button", { name: "Create task" }).click(),
  ]);
  expect(post.postDataJSON()).toEqual({ title: `Follow up: ${itemTitle}`, run_id: run7, discrepancy_item_id: item });
  const link = card.getByRole("link", { name: "Task: Open" });
  await expect(link).toBeVisible();
  await expect(link).toBeFocused();
  await expect(card.getByRole("button", { name: "Create task" })).toHaveCount(0);
  const task = (await allTasks(page)).find((t) => t.discrepancy_item_id === item)!;
  await expect(link).toHaveAttribute("href", `#/tasks?task=${task.id}`);
  // Independent states (R5): the card is still open.
  await expect(card.locator(".status-word")).toHaveText("Open");
  await shot(page, "1440-card-task");
  await axe(page, "card with task 1440");

  // Task: Open -> Tasks with that task highlighted and focused.
  await link.click();
  await expect(page).toHaveURL(new RegExp(`#/tasks\\?task=${task.id}$`));
  await expect(row(page, task.id)).toHaveClass(/is-target/);
  await expect(row(page, task.id).locator(".task-title")).toBeFocused();
  const chip = row(page, task.id).locator(".task-chip");
  await expect(chip).toHaveText(`${itemTitle} · Open`);
  await shot(page, "1440-deeplink");

  // The chip -> the run with the card scrolled into view, outlined and focused.
  await chip.click();
  await expect(page).toHaveURL(new RegExp(`#/runs/${run7}\\?discrepancy=${item}$`));
  await expect(card).toHaveClass(/is-target/);
  await expect(card).toBeInViewport();
  await expect(card).toBeFocused();

  // Done elsewhere: the card says Task: Done; a done task deep link switches Tasks to All.
  await patchTask(page, task.id, { status: "done" });
  await page.reload();
  await expect(card.getByRole("link", { name: "Task: Done" })).toBeVisible();
  await card.getByRole("link", { name: "Task: Done" }).click();
  await expect(filterRadio(page, "All")).toBeChecked();
  await expect(row(page, task.id)).toHaveClass(/is-target/);
  await expect(row(page, task.id)).toBeInViewport();

  // An unknown id says so.
  await openTasks(page, "#/tasks?task=999999");
  await expect(page.getByText("That task no longer exists.")).toBeVisible();
});

test("dropped item, removed run and the empty state (route-intercepted task lists)", async ({ page }) => {
  const run7 = await fullRun(page);
  const now = new Date().toISOString();
  const base = { due_date: null, status: "open", created_at: now, updated_at: now, completed_at: null };
  const extra = [
    { ...base, id: 9001, title: "E2E task whose run was removed", run_id: 4242, discrepancy_item_id: null, run_label: null, run_missing: true, discrepancy: null },
    {
      ...base,
      id: 9002,
      title: "E2E task whose item left the report",
      run_id: run7,
      discrepancy_item_id: "disc-gone",
      run_label: `Policy Check · Run ${run7}`,
      run_missing: false,
      discrepancy: { item_id: "disc-gone", title: "Coverage B (Other Structures) decreased", status: "resolved", in_report: false },
    },
  ];
  await page.route("**/api/tasks", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const res = await route.fetch();
    const body = (await res.json()) as { tasks: unknown[] };
    await route.fulfill({ response: res, json: { tasks: [...extra, ...body.tasks] } });
  });
  await openTasks(page);
  const gone = row(page, 9001).locator(".tasks-table__link");
  await expect(gone).toHaveText("Linked run removed");
  await expect(gone.getByRole("link")).toHaveCount(0);
  const dropped = row(page, 9002).locator(".task-chip");
  await expect(dropped).toHaveText("Coverage B (Other Structures) decreased · No longer in the report");
  await expect(dropped).toHaveAttribute("href", `#/runs/${run7}`);
  await shot(page, "1440-dropped-removed");
  await axe(page, "dropped and removed 1440");
  await page.unroute("**/api/tasks");

  // No tasks at all: the empty state carries the one New task button.
  await page.route("**/api/tasks", (route) => (route.request().method() === "GET" ? route.fulfill({ json: { tasks: [] } }) : route.fallback()));
  await openTasks(page);
  await expect(page.getByRole("heading", { name: "No tasks yet. Create one, or add one from a Policy Check discrepancy." })).toBeVisible();
  await expect(page.getByRole("button", { name: "New task" })).toHaveCount(1);
  await expect(search(page)).toHaveCount(0);
  await shot(page, "1440-empty");
  await axe(page, "empty 1440");
  await page.setViewportSize({ width: 390, height: 844 });
  await noSideScroll(page);
  await shot(page, "390-empty");
  await axe(page, "empty 390");
  await page.unroute("**/api/tasks");
});

for (const vp of [
  { w: 1440, h: 900 },
  { w: 1024, h: 768 },
  { w: 390, h: 844 },
]) {
  test(`axe 0 at ${vp.w}: page, dialog, discrepancy card`, async ({ page }) => {
    const run7 = await fullRun(page);
    await createTask(page, { title: "E2E linked to the run only", due_date: isoDay(3), run_id: run7 });
    await page.setViewportSize({ width: vp.w, height: vp.h });
    await openTasks(page);
    await expect(row(page, 2)).toBeVisible();
    await filterOpt(page, "All").click();
    await noSideScroll(page);
    if (vp.w < 700) {
      // Phone: the headers give way to a Sort select (they stay for screen readers).
      await expect(page.getByLabel("Sort by")).toBeVisible();
      await page.getByLabel("Sort by").selectOption("title:asc");
      await expect(titles(page).first()).toHaveText(/^E2E linked/);
    }
    await shot(page, `${vp.w}-page`);
    await axe(page, `page ${vp.w}`);

    await row(page, 2).getByRole("button", { name: /^Actions for / }).click();
    await page.getByRole("menuitem", { name: "Edit" }).click();
    const dialog = page.getByRole("dialog", { name: "Edit task" });
    await expect(dialog.getByLabel(/^Discrepancy/)).toHaveValue("disc-coverage-c-limit");
    await noSideScroll(page);
    await shot(page, `${vp.w}-dialog`);
    await axe(page, `dialog ${vp.w}`);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);

    await page.goto(`/#/runs/${run7}?discrepancy=disc-coverage-c-limit`);
    const card = page.locator('.disc[data-item="disc-coverage-c-limit"]');
    await expect(card).toHaveClass(/is-target/);
    await expect(card.getByRole("link", { name: "Task: Open" })).toBeVisible();
    await expect(card).toBeInViewport();
    await noSideScroll(page);
    await shot(page, `${vp.w}-card`);
    await axe(page, `card ${vp.w}`);
  });
}
