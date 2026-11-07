// Click-through of the built static demo with NO backend: node scripts/demo-qa.mjs   (after npm run build:demo)
//
// Serves dist/ with `vite preview` on 5221 and visits every screen and every seeded run at 1440 and 390 wide, clicking
// the read-only controls on each (tabs, filters, sorts, citation chips, panels). Fails on: a console error, a page
// error, a /demo/ request that is missing (404 or the app shell instead of JSON/PDF), a "couldn't load" state, a
// missing banner. Then checks that a write attempt shows the read-only message. Screenshots: qa-screenshots/demo-static/.
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { exercise } from "./demo-exercise.mjs";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SHOTS = path.join(FRONTEND, "qa-screenshots", "demo-static");
const PORT = 5221;
const BASE = `http://localhost:${PORT}`;
mkdirSync(SHOTS, { recursive: true });

const manifest = JSON.parse(readFileSync(path.join(FRONTEND, "public", "demo", "manifest.json"), "utf8"));
const runs = JSON.parse(readFileSync(path.join(FRONTEND, "public", "demo", Object.entries(manifest.files).find(([, v]) => v.path === "/api/runs?limit=100")[0]), "utf8")).runs;

const server = spawn(process.execPath, [path.join(FRONTEND, "node_modules", "vite", "bin", "vite.js"), "preview", "--port", String(PORT), "--strictPort"], {
  cwd: FRONTEND,
  stdio: "ignore",
  windowsHide: true,
});
const stop = () => (process.platform === "win32" ? spawnSync("taskkill", ["/pid", String(server.pid), "/T", "/F"], { stdio: "ignore" }) : server.kill());
process.on("exit", stop);
for (let i = 0; i < 60; i++) {
  try {
    if ((await fetch(BASE)).ok) break;
  } catch {
    await new Promise((r) => setTimeout(r, 500));
  }
}

const SCREENS = [
  "/", "/personal-lines", "/activity", "/activity?line=commercial", "/activity?line=personal", "/tasks", "/certificates", "/analytics",
  "/integrations", "/proposals/new", "/policy-check/new", "/compare/new", "/certificates/new", "/analytics/new",
];
const BAD_TEXT = /couldn't (load|open|read|reach)|can't reach|request failed|couldn't check the server/i;
const failures = [];
/** API paths the app asked the snapshot for and did not find. Written to demo-extra-paths.json, which the snapshot fetches. */
const gaps = new Set();
let pages = 0;

async function newPage(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: width > 600 ? 900 : 844 } });
  const page = await context.newPage();
  const state = { problems: [] };
  page.on("console", (m) => {
    if (m.type() === "error") state.problems.push(`console error: ${m.text()}`);
    const gap = /^demo snapshot missing: (.+)$/.exec(m.text());
    if (gap) gaps.add(gap[1]);
  });
  page.on("pageerror", (e) => state.problems.push(`page error: ${e.message}`));
  page.on("requestfailed", (r) => state.problems.push(`request failed: ${r.url()}`));
  page.on("response", (r) => {
    const url = r.url();
    if (!url.includes("/demo/")) {
      if (url.includes("127.0.0.1:8000") || url.includes("/api/")) state.problems.push(`call to a backend: ${url}`);
      return;
    }
    const type = r.headers()["content-type"] ?? "";
    if (r.status() >= 400 || (/\.(json|pdf|bin)$/.test(url.split("?")[0]) && type.includes("html"))) state.problems.push(`missing snapshot: ${url} (${r.status()})`);
  });
  return { page, state, context };
}

async function check(page, state, label, width) {
  pages++;
  const banner = await page.locator(".demo-banner").first();
  if (!(await banner.isVisible())) state.problems.push("banner not visible");
  else if (!/Read-only portfolio demo with synthetic sample data\./.test(await banner.innerText())) state.problems.push("banner text wrong");
  const text = await page.locator("body").innerText();
  const bad = BAD_TEXT.exec(text);
  if (bad) state.problems.push(`error state on screen: "${bad[0]}"`);
  for (const p of state.problems.splice(0)) failures.push(`${label} @${width}: ${p}`);
}

const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const { page, state, context } = await newPage(browser, width);
    for (const s of SCREENS) {
      await page.goto(`${BASE}/#${s}`);
      await page.reload();
      await page.waitForLoadState("networkidle").catch(() => {});
      await page.waitForTimeout(500);
      await exercise(page, { cap: 25 });
      await check(page, state, s, width);
      if (width === 1440 && ["/", "/activity", "/analytics"].includes(s)) await page.screenshot({ path: path.join(SHOTS, `screen-${s.replace(/\W+/g, "") || "home"}-${width}.png`) });
    }
    for (const r of runs) {
      await page.goto(`${BASE}/#/runs/${r.id}`);
      await page.reload();
      await page.waitForLoadState("networkidle").catch(() => {});
      await page.waitForTimeout(700);
      await exercise(page);
      await check(page, state, `run ${r.id} (${r.workflow_name})`, width);
      if ([6, 7, 11, 12, 13].includes(r.id)) await page.screenshot({ path: path.join(SHOTS, `run-${r.id}-${width}.png`) });
    }
    await context.close();
  }

  // Writes: each shows the read-only message in the app's own error UI and nothing crashes.
  const { page, state, context } = await newPage(browser, 1440);
  const MESSAGE = "This is a read-only demo. Live uploads are coming soon.";
  const expectMessage = async (label) => {
    const ok = await page.getByText(MESSAGE).first().waitFor({ timeout: 4000 }).then(() => true, () => false);
    if (!ok) failures.push(`write "${label}": the read-only message did not appear`);
    await page.screenshot({ path: path.join(SHOTS, `write-${label.replace(/\W+/g, "-")}.png`) });
    for (const p of state.problems.splice(0)) failures.push(`write "${label}": ${p}`);
  };
  await page.goto(`${BASE}/#/runs/6`);
  await page.reload();
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.getByRole("button", { name: "Good report" }).first().click();
  await expectMessage("feedback");
  // Collapsed, the toggle's name includes its summary ("Chat Ask about these quotes"), so open it by state, not name.
  await page.locator('.chat__toggle[aria-expanded="false"]').first().click({ timeout: 2000 }).catch(() => {});
  await page.getByRole("button", { name: "Which quote has the lowest total premium?" }).first().click().catch(() => {});
  await page.getByRole("button", { name: "Send question" }).first().click().catch(() => {});
  await expectMessage("chat question");
  for (const f of ["Export PDF, full report", "Export DOCX, Word document", "Export XLSX, tables only"]) {
    if (!(await page.getByRole("button", { name: f }).isDisabled())) failures.push(`${f} is not disabled`);
  }
  await page.getByRole("button", { name: "Export PDF, full report" }).hover({ force: true });
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(SHOTS, "export-disabled-tooltip.png") });
  await page.goto(`${BASE}/#/compare/new`);
  await page.reload();
  await page.waitForLoadState("networkidle").catch(() => {});
  const input = page.locator('input[type="file"]').first();
  if (await input.count()) {
    await input.setInputFiles({ name: "quote.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n%%EOF\n") });
    await expectMessage("upload");
  } else failures.push("no file input on /compare/new");
  await context.close();
} finally {
  await browser.close();
  stop();
}

if (gaps.size) {
  const file = path.join(FRONTEND, "scripts", "demo-extra-paths.json");
  const known = (() => { try { return JSON.parse(readFileSync(file, "utf8")); } catch { return []; } })();
  writeFileSync(file, JSON.stringify([...new Set([...known, ...gaps])].sort(), null, 1) + "\n");
  console.error(`demo qa: ${gaps.size} missing API path(s) added to scripts/demo-extra-paths.json; run npm run demo:snapshot, build, and QA again`);
}
console.log(`demo qa: ${pages} page views (${SCREENS.length} screens + ${runs.length} runs, at 1440 and 390 wide)`);
if (failures.length) {
  console.error(`demo qa FAILED (${failures.length}):`);
  for (const f of [...new Set(failures)].slice(0, 80)) console.error("  " + f);
  process.exit(1);
}
console.log("demo qa passed: no console errors, no missing snapshots, no error states, banner on every view, writes refused with the message.");
