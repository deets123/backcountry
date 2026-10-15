import { readFileSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";
import { swSource } from "./src/swbuild";

const api = process.env.BACKCOUNTRY_API ?? "http://127.0.0.1:8000";

// Emits dist/sw.js from sw/sw.js with the built file list (see src/swbuild.ts).
function serviceWorker(): Plugin {
  return {
    name: "backcountry-sw",
    apply: "build",
    enforce: "post",
    generateBundle(_options, bundle) {
      const template = readFileSync(new URL("./sw/sw.js", import.meta.url), "utf8");
      this.emitFile({ type: "asset", fileName: "sw.js", source: swSource(template, Object.keys(bundle)) });
    },
  };
}

export default defineConfig({
  plugins: [serviceWorker()],
  server: { proxy: { "/api": api }, host: true },
  preview: { proxy: { "/api": api }, host: true },
  build: { outDir: "dist", sourcemap: true },
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});
