// Curates the recorded demo: drops seeded runs that only exist to exercise failure paths, so a visitor's first screen
// leads with finished work. One failure (run 3, "Extraction stopped: budget reached") stays as the example.
//
//   node scripts/demo-prune.mjs        (demo-snapshot.mjs runs it after recording; safe to run again)
//
// Works on public/demo only (the seed, the dev server and e2e keep every run). Run ids are not renumbered, so the QA
// script and screenshots still find runs 6, 7, 11, 12 and 13. Removes:
//   - every recorded file of a hidden run (/api/runs/{id}..., /api/loss-runs/{id}..., /api/certificates/{id}...);
//   - hidden runs from every run list ({ runs: [...] }) and every queue group ({ ..., needs_attention: [...] });
//   - documents used only by hidden runs, from document lists and their file/original/sheet recordings.
import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** 1 failed (unreadable PDFs), 2 cancelled, 4 + 9 comparison whose proposal failed, 5 partial with no client name. */
export const HIDDEN_RUNS = [1, 2, 4, 5, 9];

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT = path.join(FRONTEND, "public", "demo");

export function prune(dir = OUT, hidden = HIDDEN_RUNS) {
  const manifestFile = path.join(dir, "manifest.json");
  const manifest = JSON.parse(readFileSync(manifestFile, "utf8"));
  const files = manifest.files;
  const hide = new Set(hidden);
  const read = (name) => JSON.parse(readFileSync(path.join(dir, name), "utf8"));
  const runIdOf = (p) => {
    const m = /^\/api\/(?:runs|loss-runs|certificates)\/(\d+)(?:[/?]|$)/.exec(p);
    return m ? Number(m[1]) : null;
  };

  // Which documents each run uses, from its own recording (GET /api/runs/{id}).
  const docsOf = new Map();
  for (const [name, f] of Object.entries(files)) {
    const m = /^\/api\/runs\/(\d+)$/.exec(f.path);
    if (m && !f.recorded_status) docsOf.set(Number(m[1]), (read(name).documents ?? []).map((d) => d.document_id));
  }
  const kept = new Set([...docsOf].filter(([id]) => !hide.has(id)).flatMap(([, d]) => d));
  const hiddenDocs = new Set([...docsOf].filter(([id]) => hide.has(id)).flatMap(([, d]) => d).filter((d) => !kept.has(d)));

  let removed = 0;
  let edited = 0;
  for (const [name, f] of Object.entries(files)) {
    const runId = runIdOf(f.path);
    const doc = /^\/api\/documents\/(\d+)\//.exec(f.path);
    if ((runId != null && hide.has(runId)) || (doc && hiddenDocs.has(Number(doc[1])))) {
      rmSync(path.join(dir, name), { force: true });
      delete files[name];
      removed++;
      continue;
    }
    if (!name.endsWith(".json") || f.recorded_status) continue;
    const body = read(name);
    let changed = false;
    if (Array.isArray(body.runs)) {
      const before = body.runs.length;
      body.runs = body.runs.filter((r) => !hide.has(r.id));
      changed ||= body.runs.length !== before;
    }
    if (f.path.startsWith("/api/queue")) {
      for (const [k, v] of Object.entries(body)) {
        if (!Array.isArray(v)) continue;
        const next = v.filter((r) => !hide.has(r.run_id) && !hide.has(r.proposal_run_id));
        if (next.length !== v.length) (body[k] = next), (changed = true);
      }
    }
    if (Array.isArray(body.documents) && /^\/api\/documents(\?|$)/.test(f.path)) {
      const before = body.documents.length;
      body.documents = body.documents.filter((d) => !hiddenDocs.has(d.id));
      changed ||= body.documents.length !== before;
    }
    if (changed) {
      const text = Buffer.from(JSON.stringify(body));
      writeFileSync(path.join(dir, name), text);
      f.bytes = text.length;
      edited++;
    }
  }
  manifest.missing = (manifest.missing ?? []).filter((m) => !hide.has(runIdOf(m.path)));
  manifest.hidden_runs = [...hide].sort((a, b) => a - b);
  writeFileSync(manifestFile, JSON.stringify(manifest, null, 1) + "\n");
  return { removed, edited, hiddenDocs: hiddenDocs.size };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (!existsSync(path.join(OUT, "manifest.json"))) {
    console.error("No snapshot in public/demo. Run: npm run demo:snapshot");
    process.exit(1);
  }
  const r = prune();
  console.log(`demo prune: hid runs ${HIDDEN_RUNS.join(", ")}; removed ${r.removed} files, edited ${r.edited}, dropped ${r.hiddenDocs} documents`);
}
