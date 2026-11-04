import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Slice 4 C11, checked certificates (spec section 6, ledger D1 to D18) against the dev backend (backend/dev_seed.py:
// run 10 issued for a past holder, run 11 a draft with Supported, Not supported, one Overridden claim and the
// description sign-off; scripted model, no tokens). Certificates page and rail, queue row hook, wizard (past-holder
// pick, upload, pick with the "read on" marker), draft (remove, override, confirm, edit sign-off, add document,
// version-checked Issue), issued view (download, new version). axe (every violation) at 1440, 1024 and 390.
// Needs a fresh seed (the cache is on in `serve`: the read-on marker comes from this spec's own first read).

const here = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.resolve(here, "../../tests/fixtures/certificates");
const SHOTS = path.resolve(here, "../qa-screenshots/certificates");
const API = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const CLIENT = "Calloway Build Group LLC";
const PAST_HOLDER = "Larkspur Commons Owners Association";

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

interface RunLite {
  id: number;
  workflow_name: string;
  parameters: Record<string, unknown>;
}

/** The seeded draft (run 11): the certificate for Marlowe Property Group LLC. */
async function seededDraft(page: Page): Promise<number> {
  const res = await page.request.get(`${API}/api/runs?limit=100`);
  const runs = ((await res.json()) as { runs: RunLite[] }).runs;
  const run = runs.find((r) => r.workflow_name === "Certificate of Insurance" && r.parameters.holder_name === "Marlowe Property Group LLC");
  expect(run, "seeded certificate draft (reseed: python -m backend.dev_seed seed)").toBeTruthy();
  return run!.id;
}

const claim = (page: Page, key: string) => page.locator(`li[data-claim="${key}"]`);

test("Certificates page: rail item is live, the seeded draft and issue are queued", async ({ page }) => {
  await page.goto("/#/certificates");
  const rail = page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Certificates" });
  await expect(rail).toHaveAttribute("href", "#/certificates");
  await expect(rail).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { level: 1, name: "Certificates" })).toBeVisible();
  const attention = page.locator('[data-group="needs_attention"]');
  await expect(attention.getByRole("link", { name: CLIENT, exact: true })).toBeVisible();
  await expect(attention).toContainText("4 checks to resolve");
  await expect(attention.getByRole("link", { name: `Resolve checks for ${CLIENT}` })).toBeVisible();
  const done = page.locator('[data-group="done_recently"]');
  await done.getByRole("button", { name: /Done recently/ }).click();
  await expect(done).toContainText("Issued");
  await expect(page.getByRole("link", { name: /New certificate/ })).toHaveAttribute("href", "#/certificates/new");
  await shot(page, "1440-01-page");
  await axe(page, "certificates page 1440");
  for (const [w, h] of [[1024, 768], [390, 844]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await noSideScroll(page);
    await shot(page, `${w}-01-page`);
    await axe(page, `certificates page ${w}`);
  }
  // Activity lists certificate runs like any run; the main queue does not (C8).
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/#/activity");
  await expect(page.getByText(/^Certificate, run \d+$/).first()).toBeVisible();
});

test("queue row hook: Issue certificate prefills the client", async ({ page }) => {
  await page.goto("/");
  const row = page.locator(".queue-item").filter({ hasText: "Brightwater Logistics Inc." }).first();
  await row.getByRole("button", { name: /Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Issue certificate" }).click();
  await expect(page).toHaveURL(/#\/certificates\/new\?client=Brightwater/);
  await expect(page.getByRole("heading", { level: 1, name: "New certificate" })).toBeVisible();
  await expect(page.getByLabel("Client")).toHaveValue("Brightwater Logistics Inc.");
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Certificates" })).toHaveAttribute("aria-current", "page");
});

test("wizard: past-holder pick, upload and pick, read, then the read-on marker", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/#/certificates/new");
  await page.getByLabel("Client").fill(CLIENT);
  // C2: the client's past holder is offered; picking it fills the holder fields.
  const past = page.getByRole("radio", { name: new RegExp(PAST_HOLDER) });
  await expect(past).toBeVisible();
  await past.check();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue(PAST_HOLDER);
  await page.getByRole("checkbox", { name: "Additional insured" }).check();
  await shot(page, "1440-02-wizard-who");
  await axe(page, "wizard who");
  // D6: the holder name is required, named at its field.
  await page.getByRole("radio", { name: "New holder" }).check();
  await page.getByRole("button", { name: /Continue/ }).click();
  await expect(page.getByText("Enter the certificate holder's name.")).toBeVisible();
  await past.check();
  await page.getByRole("button", { name: /Continue/ }).click();

  await expect(page.getByRole("heading", { name: /The policies to certify/ })).toBeVisible();
  const mine = page.getByRole("list", { name: `${CLIENT}'s documents` });
  await expect(mine.getByText("cert_contractor_gl.pdf")).toBeVisible();
  await mine.getByRole("checkbox").first().check();
  await page.locator(".cpick input[type=file]").setInputFiles([path.join(FIX, "cert_written_contract_gl.pdf")]);
  await expect(page.getByRole("list", { name: "Uploaded for this certificate" }).getByText("cert_written_contract_gl.pdf")).toBeVisible();
  await shot(page, "1440-03-wizard-policies");
  await axe(page, "wizard policies");
  await page.getByRole("button", { name: "Read and check" }).click();

  // Read: the run page reads, then the draft (paper beside the checks).
  await expect(page).toHaveURL(/#\/runs\/\d+/);
  await expect(page.getByRole("heading", { level: 2, name: "Checks" })).toBeVisible({ timeout: 90_000 });
  await expect(page.locator(".paper")).toContainText(PAST_HOLDER);

  // A second certificate for the client: both documents were read, so they say so and cost no new read (D3).
  await page.goto("/#/certificates/new");
  await page.getByLabel("Client").fill(CLIENT);
  await page.getByRole("radio", { name: new RegExp(PAST_HOLDER) }).check();
  await page.getByRole("button", { name: /Continue/ }).click();
  const row = page.locator(".cpick__row").filter({ hasText: "cert_written_contract_gl.pdf" });
  await expect(row).toContainText(/Read on .*no new read/);
  await row.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Read and check" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Checks" })).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Show policies" }).click();
  await expect(page.locator(".doc-row").filter({ hasText: "cert_written_contract_gl.pdf" })).toContainText(/Read on .*no new read/);
});

test("draft: resolve, add document, version-checked issue, download, new version", async ({ page }) => {
  test.setTimeout(240_000);
  const runId = await seededDraft(page);
  await page.goto(`/#/runs/${runId}`);
  await expect(page.getByRole("heading", { level: 1, name: new RegExp(`${CLIENT} · Certificate`) })).toBeVisible();
  const issue = page.getByRole("button", { name: "Issue certificate" });
  await expect(issue).toHaveAttribute("aria-disabled", "true");
  await expect(page.locator(".issue__count")).toHaveText("4 checks to resolve");
  await expect(page.getByText("Sample wording").first()).toBeVisible();
  // The seeded override shows its reason under Handled by you; Supported is folded.
  await expect(claim(page, "waiver_of_subrogation:A")).toContainText("Lease section 14.3");
  await expect(page.getByRole("button", { name: /Supported by the documents 7/ })).toHaveAttribute("aria-expanded", "false");
  await shot(page, "1440-04-draft");
  await axe(page, "draft 1440");

  // A chip opens the cited page beside the checks; Esc goes back to the certificate.
  await claim(page, "additional_insured:B").locator("button.chip").first().click();
  await expect(page.getByRole("region", { name: /Source: cert_contractor_auto.pdf/ })).toBeVisible();
  await shot(page, "1440-05-source");
  await page.keyboard.press("Escape");
  await expect(page.locator(".paper")).toBeVisible();

  // Add document (D2): the draft reads only the new document, then checks again; decisions carry over.
  await claim(page, "additional_insured:B").getByRole("button", { name: "Add document" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a document" });
  await dialog.locator("input[type=file]").setInputFiles([path.join(FIX, "cert_traps_ai_blanket.pdf")]);
  await expect(dialog.getByText("cert_traps_ai_blanket.pdf")).toBeVisible();
  await axe(page, "add document dialog");
  await dialog.getByRole("button", { name: "Add and read" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Checks" })).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Show policies" }).click();
  await expect(page.locator(".doc-row")).toHaveCount(7);
  await expect(page.locator(".doc-row").filter({ hasText: "cert_traps_ai_blanket.pdf" })).toBeVisible();
  await page.getByRole("button", { name: "Hide panel" }).click();
  await expect(claim(page, "waiver_of_subrogation:A")).toContainText("Lease section 14.3");

  // Remove (D13) takes the wording off the paper; Override needs a reason (D10); the description needs a sign-off (D11).
  const autoRow = page.locator('.pcov__row[data-policy="B"]');
  await expect(autoRow.getByText("Waiver of subrogation")).toBeVisible();
  await claim(page, "waiver_of_subrogation:B").getByRole("button", { name: "Remove from certificate" }).click();
  await expect(autoRow.getByText("Waiver of subrogation")).toHaveCount(0);
  await expect(claim(page, "waiver_of_subrogation:B")).toContainText("Removed");
  await expect(claim(page, "waiver_of_subrogation:B").getByRole("button", { name: "Put back on the certificate" })).toBeVisible();

  await claim(page, "additional_insured:B").getByRole("button", { name: "Override..." }).click();
  await claim(page, "additional_insured:B").getByRole("button", { name: "Override", exact: true }).click();
  await expect(claim(page, "additional_insured:B").getByRole("alert")).toContainText("Type the reason first");
  await claim(page, "additional_insured:B").getByLabel("Reason for the override").fill("Holder accepted the auto AI by email on 11/03.");
  await shot(page, "1440-06-override");
  await claim(page, "additional_insured:B").getByRole("button", { name: "Override", exact: true }).click();
  await expect(claim(page, "additional_insured:B")).toContainText("Overridden");
  await expect(page.locator(".issue__count")).toHaveText("2 checks to resolve");

  await claim(page, "primary_noncontributory:B").getByRole("button", { name: "Remove from certificate" }).click();
  await expect(claim(page, "primary_noncontributory:B")).toContainText("Removed");
  await claim(page, "description").getByRole("button", { name: "Confirm..." }).click();
  await claim(page, "description").getByLabel("Confirm the description as typed").fill("Matches the lease exhibit.");
  await claim(page, "description").getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.locator(".issue__count")).toHaveText("Every check is handled");
  await expect(issue).not.toHaveAttribute("aria-disabled", "true");

  // D12: an edited printed value adds a sign-off row.
  await page.locator('[data-region="f-A-cert:gl_each_occurrence"]').hover();
  await page.getByRole("button", { name: "Edit Each Occurrence" }).click();
  await page.getByLabel("Each Occurrence, new value").fill("$2,000,000");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(claim(page, "edited:A:cert:gl_each_occurrence")).toContainText("Needs sign-off");
  await expect(page.locator(".issue__count")).toHaveText("1 check to resolve");
  await claim(page, "edited:A:cert:gl_each_occurrence").getByRole("button", { name: "Restore the extracted value" }).click();
  await expect(page.locator(".issue__count")).toHaveText("Every check is handled");

  // D5: the draft changes behind the page (another tab's edit); Issue refuses, reloads and explains.
  const view = await (await page.request.get(`${API}/api/certificates/${runId}`)).json();
  const edit = await page.request.patch(`${API}/api/artifacts/${view.artifact_id}/cells`, {
    data: { block_id: "certificate", row_key: "cert:gl_medical_expense", column_key: "A", value: "$15,000", base_version: view.version },
  });
  expect(edit.ok()).toBeTruthy();
  await issue.click();
  await expect(page.getByRole("alert").filter({ hasText: "The draft changed since you opened it" })).toBeVisible();
  await expect(claim(page, "edited:A:cert:gl_medical_expense")).toContainText("Needs sign-off");
  await claim(page, "edited:A:cert:gl_medical_expense").getByRole("button", { name: "Restore the extracted value" }).click();
  await expect(page.locator(".issue__count")).toHaveText("Every check is handled");

  for (const [w, h] of [[1024, 768], [390, 844]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await noSideScroll(page);
    await shot(page, `${w}-04-draft`);
    await axe(page, `draft ${w}`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });

  await issue.click();
  await expect(page.locator(".cert__issued")).toContainText("Issued");
  await expect(page.getByRole("heading", { level: 2, name: "Record" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Override..." })).toHaveCount(0);
  await shot(page, "1440-07-issued");
  await axe(page, "issued 1440");
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download PDF" }).click();
  expect((await download).suggestedFilename()).toMatch(/^certificate-run\d+-v\d+\.pdf$/);

  await page.getByRole("button", { name: "New version" }).click();
  await expect(page.getByText(/Draft, version \d+/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Issue certificate" })).toBeVisible();
  await expect(claim(page, "additional_insured:B")).toContainText("Holder accepted the auto AI");
});
