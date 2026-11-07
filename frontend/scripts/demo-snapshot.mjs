// Records the static demo: every GET response the UI makes for the seeded data, into public/demo/.
//
//   node scripts/demo-snapshot.mjs              (or: npm run demo:snapshot)
//
// Starts a freshly seeded dev backend (scripted model, extraction cache off, scratch data folder) on 8060 and Vite
// on 5220, drives the real UI with Playwright (every screen, every seeded run, its tabs and views) and remembers
// each GET path it asked for. It then fetches those paths again straight from the backend (so binaries and bodies
// are exact) and adds direct fetches for what a crawl cannot reach (kept originals, every sheet page, the claims
// of every loss review). Names come from src/api/demoKey.ts, the same code the app uses to read them back.
//
// Env: DEMO_PYTHON (python with the backend's packages; default backend/.venv in this checkout or the main one),
//      DEMO_REUSE=1 (use servers already running on 8060/5220 instead of seeding and starting them).
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { demoFile } from "../src/api/demoKey.ts";
import { exercise } from "./demo-exercise.mjs";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO = path.resolve(FRONTEND, "..");
const OUT = path.join(FRONTEND, "public", "demo");
const API_PORT = 8060;
const UI_PORT = 5220;
const API = `http://127.0.0.1:${API_PORT}`;
const UI = `http://localhost:${UI_PORT}`;
const REUSE = process.env.DEMO_REUSE === "1";

const log = (...a) => console.log("[demo-snapshot]", ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findPython() {
  if (process.env.DEMO_PYTHON) return process.env.DEMO_PYTHON;
  const rel = process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"];
  const here = path.join(REPO, "backend", ".venv", ...rel);
  if (existsSync(here)) return here;
  // A git worktree: the main checkout is three folders up from .claude/worktrees/<name>.
  const main = path.join(REPO, "..", "..", "..", "backend", ".venv", ...rel);
  if (existsSync(main)) return main;
  throw new Error("No backend python found. Set DEMO_PYTHON to a python with the backend requirements (frontend/README.md).");
}

const children = [];
function start(cmd, args, opts) {
  const child = spawn(cmd, args, { stdio: "ignore", windowsHide: true, ...opts });
  children.push(child);
  return child;
}
function stopAll() {
  for (const c of children) {
    if (c.exitCode != null || !c.pid) continue;
    if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(c.pid), "/T", "/F"], { stdio: "ignore" });
    else c.kill("SIGTERM");
  }
}
process.on("exit", stopAll);

async function waitFor(url, label, ms = 90_000) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      /* not up yet */
    }
    await sleep(500);
  }
  throw new Error(`${label} did not come up at ${url}`);
}

const getJson = async (p) => (await fetch(`${API}${p}`)).json();

// ------------------------------------------------------------------------------------------ servers

async function startServers() {
  if (REUSE) {
    log("DEMO_REUSE=1: using the servers already running");
    return;
  }
  const python = findPython();
  const data = mkdtempSync(path.join(tmpdir(), "demo-snap-"));
  const env = {
    ...process.env,
    BROKERDOCS_DATA_DIR: data,
    BROKERDOCS_PORT: String(API_PORT),
    BROKERDOCS_UI_ORIGIN: UI,
    BROKERDOCS_DEV_DELAY: "0",
    BROKERDOCS_EXTRACTION_CACHE: "off",
    OPENAI_API_KEY: "",
    VITE_DEMO: "",
  };
  log("seeding", data);
  const seeded = spawnSync(python, ["-m", "backend.dev_seed", "seed"], { cwd: REPO, env, stdio: "inherit" });
  if (seeded.status !== 0) throw new Error("seed failed");
  start(python, ["-m", "backend.dev_seed", "serve"], { cwd: REPO, env });
  start(process.execPath, [path.join(FRONTEND, "node_modules", "vite", "bin", "vite.js"), "--port", String(UI_PORT), "--strictPort"], {
    cwd: FRONTEND,
    env: { ...env, VITE_API_BASE: API },
  });
  await waitFor(`${API}/api/health`, "backend");
  await waitFor(UI, "vite");
}

// ------------------------------------------------------------------------------------------ crawl

/** Paths seen: GET /api/... exactly as the UI wrote them (query included). */
const seen = new Set();
/** The subset the UI itself asked for. A non-2xx answer to one of these is part of the product (a failed run has no
 * report: 404) and is recorded with its status; a non-2xx answer to a direct fetch is simply not recorded. */
const asked = new Set();

async function visit(page, hash, opts) {
  await page.goto(`${UI}/#${hash}`);
  await page.reload(); // a hash-only goto does not reload; the reload makes every screen start from its own load
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(600);
  await exercise(page, opts);
}

async function crawl(runs) {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  // The crawl must not change the seed: every write and the SSE stream are refused at the network.
  await context.route(`${API}/**`, (route) => {
    const req = route.request();
    if (req.method() !== "GET" || req.url().includes("/events")) return route.abort();
    return route.continue();
  });
  const page = await context.newPage();
  page.on("response", (res) => {
    const req = res.request();
    if (req.method() !== "GET" || !res.url().startsWith(API) || res.url().includes("/events")) return;
    asked.add(res.url().slice(API.length));
    seen.add(res.url().slice(API.length));
  });
  page.on("pageerror", (e) => log("page error:", e.message));

  const clients = [...new Set(runs.map((r) => r.client_name).filter(Boolean))];
  const screens = [
    "/", "/?line=commercial", "/personal-lines", "/activity", "/activity?line=commercial", "/activity?line=personal",
    "/tasks", "/certificates", "/analytics", "/integrations", "/proposals/new", "/policy-check/new", "/policy-check/new?line=personal",
    "/compare/new", "/compare/new?line=personal", "/certificates/new", "/analytics/new",
    ...clients.flatMap((c) => [`/certificates/new?client=${encodeURIComponent(c)}`, `/analytics/new?client=${encodeURIComponent(c)}`]),
  ];
  for (const s of screens) {
    log("screen", s);
    await visit(page, s, { cap: 25 });
  }
  for (const r of runs) {
    log("run", r.id, r.workflow_name);
    await visit(page, `/runs/${r.id}`);
  }
  await browser.close();
}

// ------------------------------------------------------------------------------------------ direct fetches

async function extras(runs) {
  const add = (p) => seen.add(p);
  for (const p of [
    "/api/workspace", "/api/documents", "/api/templates", "/api/tasks", "/api/loss-runs", "/api/certificates/queue",
    "/api/queue?line=commercial", "/api/queue?line=personal", "/api/runs?limit=100", "/api/runs?limit=20",
    "/api/runs?limit=20&line=commercial", "/api/runs?limit=20&line=personal", "/api/runs?limit=100&line=personal",
  ]) add(p);
  for (const t of (await getJson("/api/templates")).templates ?? []) add(`/api/templates/${encodeURIComponent(t.slug)}`);
  for (const d of (await getJson("/api/documents")).documents ?? []) {
    add(`/api/documents/${d.id}/file`);
    if (d.source_format && d.source_format !== "pdf") add(`/api/documents/${d.id}/original`); // a PDF's original is its file
    if (d.kind === "spreadsheet") {
      add(`/api/documents/${d.id}/sheets`);
      for (let i = 1; i <= (d.page_count ?? 0); i++) add(`/api/documents/${d.id}/sheet-pages/${i}`);
    }
  }
  for (const r of runs) {
    for (const p of ["", "/artifact", "/messages", "/suggestions", "/discrepancies"]) add(`/api/runs/${r.id}${p}`);
    if (/loss/i.test(r.workflow_name ?? "")) {
      add(`/api/loss-runs/${r.id}`);
      add(`/api/loss-runs/${r.id}/claims?dir=desc&limit=100&offset=0&sort=claim_incurred`);
    }
    if (/certificate/i.test(r.workflow_name ?? "")) {
      add(`/api/certificates/${r.id}`);
      for (const i of (await getJson(`/api/certificates/${r.id}`)).issues ?? []) add(`/api/certificates/${r.id}/issues/${i.version}/pdf`);
    }
  }
  for (const c of new Set(runs.map((r) => r.client_name).filter(Boolean))) {
    add(`/api/certificates/holders?client=${encodeURIComponent(c)}`);
    add(`/api/certificates/documents?client=${encodeURIComponent(c)}`);
  }
}

// ------------------------------------------------------------------------------------------ write

/**
 * Generated PDFs carry the product name in their plain-text Info dictionary: the fixtures' generator ("broker-docs-verified
 * quote_generator v1") and the certificate export's author ("BrokerDocs (local)"). publish_scrub.py only renames the
 * first inside binaries, so the snapshot swaps both for same-length neutral text (offsets stay valid). The name check
 * (demo-name-check.mjs) fails the build if any other copy is left, compressed or not.
 */
function renameGenerator(buf) {
  const out = Buffer.from(buf);
  const swaps = [
    ["broker-docs-verified", "brokerdocs-demo"],
    ["BrokerDocs", "BrokerDoc"],
    ["BROKERDOCS", "BROKERDOC"],
    ["brokerdocs", "brokerdoc"],
  ];
  for (const [a, b] of swaps) {
    const from = Buffer.from(a);
    const to = Buffer.from(b);
    for (let i = out.indexOf(from); i >= 0; i = out.indexOf(from, i + from.length)) to.copy(out, i);
  }
  return out;
}

async function write() {
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });
  const files = {};
  const missing = [];
  const owner = new Map();
  for (const p of [...seen].sort()) {
    const res = await fetch(`${API}${p}`);
    const name = demoFile("GET", p);
    if (owner.has(name)) continue; // the same request written in another parameter order
    if (!res.ok) {
      if (!asked.has(p)) {
        missing.push({ path: p, status: res.status });
        continue;
      }
      const err = await res.json().catch(() => ({}));
      const body = Buffer.from(JSON.stringify({ __demo_status: res.status, ...err }));
      owner.set(name, p);
      writeFileSync(path.join(OUT, name), body);
      files[name] = { path: p, bytes: body.length, type: "application/json", recorded_status: res.status };
      continue;
    }
    owner.set(name, p);
    let body = Buffer.from(await res.arrayBuffer());
    if (body.subarray(0, 5).toString("latin1") === "%PDF-") body = renameGenerator(body);
    writeFileSync(path.join(OUT, name), body);
    files[name] = { path: p, bytes: body.length, type: res.headers.get("content-type") ?? "" };
  }
  const manifest = {
    note: "Recorded GET responses of the seeded demo. Names: src/api/demoKey.ts. Regenerate: npm run demo:snapshot.",
    files,
    missing,
  };
  writeFileSync(path.join(OUT, "manifest.json"), JSON.stringify(manifest, null, 1) + "\n");
  log(`wrote ${Object.keys(files).length} files, ${missing.length} not recorded (non-2xx)`);
  for (const m of missing) log("  not recorded:", m.status, m.path);
  return readdirSync(OUT).length;
}

try {
  await startServers();
  const runs = (await getJson("/api/runs?limit=100")).runs;
  log(`${runs.length} seeded runs`);
  await crawl(runs);
  await extras(runs);
  await write();
} finally {
  stopAll();
}
