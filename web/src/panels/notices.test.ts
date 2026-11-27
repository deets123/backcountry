import { describe, expect, it } from "vitest";
import type { Notice, NoticeKind, ParkNotices } from "../types";
import { chipsHtml, noticeKey, noticesHtml, parkLineHtml } from "./notices";

const n = (kind: NoticeKind, text: string, matched = false, links: string[] = []): Notice => ({
  kind, text, applies_to: matched ? "trail: X" : "park", matched,
  source: { name: "National Park Service", ref: null, as_of: "2026-09-16", checked_at: "2026-11-26T10:04:00Z" }, links,
});

describe("notices", () => {
  it("escapes text and keeps only http links", () => {
    const html = noticesHtml([n("info", "<img src=x onerror=alert(1)> & more", false, ["javascript:x", "https://www.nps.gov/a"])]);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
    expect(html).toContain('href="https://www.nps.gov/a"');
    expect(html).not.toContain("javascript:");
  });
  it("marks new notices and shows attachment and dates", () => {
    const one = n("closure", "Watchman Trail closed", true);
    const html = noticesHtml([one], new Set([noticeKey(one)]));
    expect(html).toContain("new since you saved");
    expect(html).toContain("Attached to: trail: X");
    expect(html).toContain("2026-09-16");
    expect(html).toContain("National Park Service");
  });
  it("renders the unknown line plainly", () => {
    const html = noticesHtml([n("unknown", "Everything else: unknown")]);
    expect(html).toContain("Everything else: unknown");
    expect(html).not.toContain("Attached to");
  });
  it("chips only for matched permit, ticket and closure", () => {
    expect(chipsHtml([n("permit", "p", true), n("closure", "c", false), n("fee", "f", true)])).toBe('<span class="chip chip-permit">permit</span>');
    expect(chipsHtml([n("ticket", "t", true), n("closure", "c", true)])).toBe('<span class="chip chip-ticket">ticket</span><span class="chip chip-closure">closure</span>');
    expect(chipsHtml(undefined)).toBe("");
  });
  it("park line counts closures and says when it is checking", () => {
    const checking: ParkNotices = { configured: true, park: { code: "zion", name: "Zion National Park" }, pending: true, stale: false, error: null, checked_at: null, notices: [] };
    expect(parkLineHtml(checking, false)).toContain("Checking park notices…");
    const p: ParkNotices = { configured: true, park: { code: "zion", name: "Zion National Park" }, pending: false, stale: false, error: null,
      checked_at: "2026-11-26T10:04:00Z", notices: [n("closure", "a"), n("closure", "b"), n("fee", "c")] };
    const html = parkLineHtml(p, false);
    expect(html).toContain("2 closures, 1 more");
    expect(html).toContain("National Park Service");
    expect(html).toContain("<details");
    expect(parkLineHtml(null, false)).toBe("");
    expect(parkLineHtml({ ...p, park: null, pending: false, notices: [] }, false)).toBe("");
    expect(parkLineHtml({ ...p, stale: true, error: "HTTP 429" }, false)).toContain("could not refresh");
  });
});
