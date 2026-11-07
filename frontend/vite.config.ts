import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The UI calls the backend at http://127.0.0.1:8000 directly (CORS allows this dev origin).
export default defineConfig({
  plugins: [react()],
  server: { host: "localhost", port: 5173, strictPort: true },
  test: { include: ["src/**/*.test.ts"] },
  preview: { host: "localhost", port: 5173, strictPort: true },
});
