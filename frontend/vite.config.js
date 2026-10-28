import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built app is served by FastAPI at http://127.0.0.1:8000/app/
// In development (npm run dev) Vite serves it on :5173 and proxies API + WebSocket calls to the backend.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  // Stable file names (no content hashes) so a rebuild overwrites the same files in
  // frontend/dist instead of piling up old bundles. The backend serves them with
  // ETag/Last-Modified, so browsers still pick up new versions.
  worker: {
    format: "es",
    rollupOptions: { output: { entryFileNames: "assets/[name].js", chunkFileNames: "assets/[name].js", assetFileNames: "assets/[name][extname]" } },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 4000,
    sourcemap: false,
    rollupOptions: { output: { entryFileNames: "assets/[name].js", chunkFileNames: "assets/[name].js", assetFileNames: "assets/[name][extname]" } },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.js"],
    testTimeout: 60000,
  },
});
