import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // During local development the frontend runs on port 5173 and the backend
    // on 8000. This forwards /api calls to the backend so the browser sees a
    // single origin, exactly as it will in production.
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
