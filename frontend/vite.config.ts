import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy /api and /health to the backend so the frontend can use same-origin
// relative URLs in dev. Backend runs on :8000, Vite dev server on :5173.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/events": "http://127.0.0.1:8000",
    },
  },
});
