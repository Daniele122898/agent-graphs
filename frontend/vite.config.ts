import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy /api and /health to the backend so the frontend can use same-origin
// relative URLs in dev. Backend runs on :8000, Vite dev server on :5173.
// Both are overridable (AG_BACKEND / --port) so an isolated verification stack
// can run next to a live dev session — see scripts/verify_ui.py.
const backend = process.env.AG_BACKEND || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/health": backend,
      "/events": backend,
    },
  },
});
