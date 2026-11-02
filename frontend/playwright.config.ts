import { defineConfig } from "@playwright/test";

// The journey needs the dev backend running first (see README.md):
//   python -m backend.dev_seed seed && python -m backend.dev_seed serve
// E2E_UI_PORT and VITE_API_BASE let a second stack run beside the default one (5173 / 8000).
const uiPort = Number(process.env.E2E_UI_PORT ?? 5173);
const baseURL = `http://localhost:${uiPort}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL,
    viewport: { width: 1440, height: 900 },
    acceptDownloads: true,
    trace: "retain-on-failure",
  },
  webServer: {
    command: uiPort === 5173 ? "npm run dev" : `npx vite --port ${uiPort} --strictPort`,
    url: baseURL,
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
