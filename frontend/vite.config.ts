import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The UI calls the backend at http://127.0.0.1:8000 directly (CORS allows this dev origin).
export default defineConfig({
  plugins: [react()],
  server: { host: "localhost", port: 5173, strictPort: true },
  preview: { host: "localhost", port: 5173, strictPort: true },
});
