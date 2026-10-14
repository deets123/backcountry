import { describe, expect, it } from "vitest";
import { nextSheet } from "./sheet";

describe("sheet", () => {
  it("cycles collapsed -> half -> full -> collapsed on tap and follows drags", () => {
    expect(nextSheet("collapsed", "tap")).toBe("half");
    expect(nextSheet("half", "tap")).toBe("full");
    expect(nextSheet("full", "tap")).toBe("collapsed");
    expect(nextSheet("half", "up")).toBe("full");
    expect(nextSheet("full", "down")).toBe("half");
    expect(nextSheet("half", "down")).toBe("collapsed");
    expect(nextSheet("collapsed", "down")).toBe("collapsed");
  });
});
