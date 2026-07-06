import { describe, it, expect } from "vitest";
import { formatCompactNumber } from "./formatCompactNumber";

describe("formatCompactNumber", () => {
  it("returns string for numbers < 1000", () => {
    expect(formatCompactNumber(0)).toBe("0");
    expect(formatCompactNumber(1)).toBe("1");
    expect(formatCompactNumber(999)).toBe("999");
  });

  it("formats 1000 as 1.0k", () => {
    expect(formatCompactNumber(1000)).toBe("1.0k");
  });

  it("formats 1500 as 1.5k", () => {
    expect(formatCompactNumber(1500)).toBe("1.5k");
  });

  it("formats 7927 as 7.9k", () => {
    expect(formatCompactNumber(7927)).toBe("7.9k");
  });

  it("formats 10000 as 10.0k", () => {
    expect(formatCompactNumber(10000)).toBe("10.0k");
  });

  it("formats 8000 as 8.0k", () => {
    expect(formatCompactNumber(8000)).toBe("8.0k");
  });
});
