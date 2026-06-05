import { describe, it, expect } from "vitest";
import { masteryColor, masteryBackgroundColor } from "./mastery";

describe("masteryColor", () => {
  it("progress 0 returns red (hue 0)", () => {
    const result = masteryColor(0);
    expect(result).toMatch(/hsl\(0, 70%, 50%\)/);
  });

  it("progress 0.5 returns yellow (hue 60)", () => {
    const result = masteryColor(0.5);
    expect(result).toMatch(/hsl\(60, 70%, 46%\)/);
  });

  it("progress 1 returns green (hue 120)", () => {
    const result = masteryColor(1);
    expect(result).toMatch(/hsl\(120, 70%, 42%\)/);
  });

  it("clamps negative values to 0 (hue 0)", () => {
    const result = masteryColor(-0.2);
    expect(result).toMatch(/hsl\(0, 70%, 50%\)/);
  });

  it("clamps values > 1 to 1 (hue 120)", () => {
    const result = masteryColor(1.5);
    expect(result).toMatch(/hsl\(120, 70%, 42%\)/);
  });
});

describe("masteryBackgroundColor", () => {
  it("progress 0 returns a translucent red tint (hue 0)", () => {
    expect(masteryBackgroundColor(0)).toBe("hsla(0, 70%, 45%, 0.15)");
  });

  it("progress 1 returns a translucent green tint (hue 120)", () => {
    expect(masteryBackgroundColor(1)).toBe("hsla(120, 70%, 45%, 0.15)");
  });

  it("clamps out-of-range values", () => {
    expect(masteryBackgroundColor(-0.2)).toBe("hsla(0, 70%, 45%, 0.15)");
    expect(masteryBackgroundColor(1.5)).toBe("hsla(120, 70%, 45%, 0.15)");
  });
});
