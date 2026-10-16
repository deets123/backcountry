// Builds the service worker from web/sw/sw.js during `vite build` (see vite.config.ts): fills in the
// app-shell file list and a version derived from it, so a new build installs a new worker.
export const ALWAYS = ["index.html", "icon.svg", "manifest.webmanifest"];

export function shellList(bundleFiles: string[]): string[] {
  const files = [...bundleFiles, ...ALWAYS].filter((f) => !f.endsWith(".map") && f !== "sw.js");
  return [...new Set(["/", ...files.map((f) => `/${f}`)])].sort();
}

export function versionOf(list: string[]): string {
  let h = 0x811c9dc5; // FNV-1a, 32-bit
  for (const ch of list.join("\n")) { h ^= ch.charCodeAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
  return h.toString(16).padStart(8, "0");
}

export function swSource(template: string, bundleFiles: string[]): string {
  if (!template.includes("__VERSION__") || !template.includes("__SHELL__")) throw new Error("sw.js template is missing __VERSION__ or __SHELL__");
  const shell = shellList(bundleFiles);
  return template.replaceAll("__VERSION__", versionOf(shell)).replaceAll("__SHELL__", JSON.stringify(shell));
}
