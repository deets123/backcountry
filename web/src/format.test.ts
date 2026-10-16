import { describe, expect, it } from "vitest";
import { feet, feetFromMeters, miles, shortDate, signedFeet } from "./format";

describe("format", () => {
  it("formats miles and feet in user units", () => {
    expect(miles(1609.344 * 7.39)).toBe("7.39 mi");
    expect(feet(2023.4)).toBe("2,023 ft");
    expect(feetFromMeters(1000)).toBe("3,281 ft");
    expect(signedFeet(2023, 2023)).toBe("+2,023 / −2,023 ft");
  });
  it("says unknown instead of NaN", () => {
    expect(miles(null)).toBe("unknown");
    expect(feet(undefined)).toBe("unknown");
    expect(signedFeet(null, 5)).toBe("unknown");
  });
});

describe("shortDate", () => {
  it("formats an ISO time as a short US date, or unknown", () => {
    expect(shortDate("2026-10-14T16:21:38Z")).toBe("Oct 14, 2026");
    expect(shortDate(null)).toBe("unknown");
  });
});
