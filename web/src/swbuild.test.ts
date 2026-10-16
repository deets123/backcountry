import { describe, expect, it } from "vitest";
import template from "../sw/sw.js?raw";
import { shellList, swSource, versionOf } from "./swbuild";

describe("service worker build", () => {
  const T = "const VERSION = \"__VERSION__\"; const SHELL = __SHELL__;";
  it("lists the app shell: page, built assets, public files; no source maps or the worker itself", () => {
    expect(shellList(["assets/index-1.js", "assets/index-1.js.map", "assets/index-1.css", "sw.js"]))
      .toEqual(["/", "/assets/index-1.css", "/assets/index-1.js", "/icon.svg", "/index.html", "/manifest.webmanifest"]);
  });
  it("fills in the shell and a version that changes when the files change", () => {
    const a = swSource(T, ["assets/a.js"]), b = swSource(T, ["assets/b.js"]);
    expect(a).toContain('"/assets/a.js"'); expect(a).not.toContain("__SHELL__"); expect(a).toMatch(/VERSION = "[0-9a-f]{8}"/);
    expect(a).not.toBe(b); expect(swSource(T, ["assets/a.js"])).toBe(a);
    expect(versionOf(["/x"])).toMatch(/^[0-9a-f]{8}$/);
  });
  it("refuses a template without its placeholders", () => {
    expect(() => swSource("nothing here", [])).toThrow(/__VERSION__/);
  });
  it("fills in the real template's code, not just its comments", () => {
    const out = swSource(template, ["assets/index-1.js"]);
    expect(out).not.toContain("__SHELL__"); expect(out).not.toContain("__VERSION__");
    expect(out).toMatch(/const SHELL = \[.*"\/assets\/index-1\.js".*\];/);
  });
});
