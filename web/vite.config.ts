import { defineConfig } from "vite";

const api = process.env.BACKCOUNTRY_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  server: { proxy: { "/api": api }, host: true },
  preview: { proxy: { "/api": api }, host: true },
  build: { outDir: "dist", sourcemap: true },
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});
