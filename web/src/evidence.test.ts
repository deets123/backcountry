import { describe, expect, it } from "vitest";
import { evidenceHtml, kindLabel } from "./evidence";
import type { RouteFeature } from "./types";

describe("evidence", () => {
  it("labels kinds like the reports do", () => {
    expect(kindLabel("camp_site")).toBe("Campsite"); expect(kindLabel("stream crossing")).toBe("Stream crossing");
    expect(kindLabel("usfs campground")).toBe("USFS campground"); expect(kindLabel("water")).toBe("Lake/pond");
  });
  it("shows tags, edit history, links, and the unknown warning without raw ids", () => {
    const f: RouteFeature = { source: "osm:node/12830535560", kind: "camp_site", name: null, lat: 39.04, lon: -79.38, dist_m: 1609.344 * 21.3,
      off_m: 1.8, tags: { tourism: "camp_site", description: "no water" }, meta: { timestamp: "2025-05-12T15:00:02Z", version: 1 },
      url: "https://www.openstreetmap.org/node/12830535560" };
    const html = evidenceHtml(f);
    expect(html).toContain("<b>Campsite</b>"); expect(html).toContain("mile 21.3"); expect(html).toContain("6 ft off route");
    expect(html).toContain("description=no water"); expect(html).toContain("last edited 2025-05-12, version 1");
    expect(html).toContain("google.com/maps/@39.040000,-79.380000"); expect(html).toContain("openstreetmap.org/node/12830535560/history");
    expect(html).toContain("(for water) presence are unknown"); expect(html).not.toContain("osm:node/");
  });
});
