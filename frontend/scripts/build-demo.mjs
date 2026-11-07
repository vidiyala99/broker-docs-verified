// npm run build:demo: typecheck, build with VITE_DEMO=1 into dist/, then the name check on public/demo and dist.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const bin = (pkg, file) => path.join(FRONTEND, "node_modules", pkg, file);
const run = (args, env = {}) => {
  const r = spawnSync(process.execPath, args, { cwd: FRONTEND, stdio: "inherit", env: { ...process.env, ...env } });
  if (r.status !== 0) process.exit(r.status ?? 1);
};

if (!existsSync(path.join(FRONTEND, "public", "demo", "manifest.json"))) {
  console.error("No snapshot in public/demo. Run: npm run demo:snapshot");
  process.exit(1);
}
run([bin("typescript", "bin/tsc"), "-p", "tsconfig.json", "--noEmit"]);
run([bin("vite", "bin/vite.js"), "build"], { VITE_DEMO: "1" });
run([path.join(FRONTEND, "scripts", "demo-name-check.mjs")]);
