import { describe, it, expect } from "vitest";
import { masteryColor } from "./mastery";

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
