import { describe, expect, it } from "vitest";
import { feet, feetFromMeters, miles, signedFeet } from "./format";

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
