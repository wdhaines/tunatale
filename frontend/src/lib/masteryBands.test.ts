import { describe, expect, it } from "vitest";
import { RAIL_FILL_PCT, railStyle, bandLabel, holdsLabel, type MasteryBand } from "./masteryBands";

describe("RAIL_FILL_PCT", () => {
  it("pins the fill length per strength band", () => {
    expect(RAIL_FILL_PCT).toEqual({
      learning: 20,
      days: 40,
      weeks: 60,
      months: 80,
      solid: 100,
    });
  });
});

describe("railStyle", () => {
  it.each([
    ["learning", "20%", "var(--band-learning)"],
    ["days", "40%", "var(--band-days)"],
    ["weeks", "60%", "var(--band-weeks)"],
    ["months", "80%", "var(--band-months)"],
    ["solid", "100%", "var(--band-solid)"],
  ] as const)("fills %s to %s in %s", (band: MasteryBand, width: string, color: string) => {
    const r = railStyle(band);
    expect(r.fillStyle).toContain(`width: ${width};`);
    expect(r.fillStyle).toContain(`background-color: ${color};`);
    expect(r.dashed).toBe(false);
  });

  it("draws an empty, un-dashed track for new", () => {
    expect(railStyle("new")).toEqual({ fillStyle: null, dashed: false });
  });

  it("draws an empty, un-dashed track for suspended", () => {
    expect(railStyle("suspended")).toEqual({ fillStyle: null, dashed: false });
  });

  it("draws a dashed empty track for none (no card)", () => {
    expect(railStyle("none")).toEqual({ fillStyle: null, dashed: true });
  });

  it("draws nothing for null/undefined/unknown bands", () => {
    expect(railStyle(null)).toEqual({ fillStyle: null, dashed: false });
    expect(railStyle(undefined)).toEqual({ fillStyle: null, dashed: false });
    expect(railStyle("bogus")).toEqual({ fillStyle: null, dashed: false });
  });
});

describe("bandLabel", () => {
  it.each([
    ["none", "No card"],
    ["new", "Not started"],
    ["learning", "Learning"],
    ["days", "Days"],
    ["weeks", "Weeks"],
    ["months", "Months"],
    ["solid", "Half a year +"],
    ["suspended", "Suspended"],
  ] as const)("labels %s → %s", (band: MasteryBand, label: string) => {
    expect(bandLabel(band)).toBe(label);
  });
});

describe("holdsLabel", () => {
  it.each([
    [3, "holds ~3 days"],
    [1, "holds ~1 day"],
    [8, "holds ~1 week"],
    [45, "holds ~6 weeks"],
    [100, "holds ~3 months"],
    [500, "holds ~1.4 years"],
    [79251, "marked known"],
    [0.4, "holds < 1 day"],
  ] as const)("pins holdsLabel(%s) → %s", (stability: number, label: string) => {
    expect(holdsLabel(stability)).toBe(label);
  });

  it("returns null for no stability", () => {
    expect(holdsLabel(null)).toBeNull();
  });

  it("rounds fractional day/week boundaries sensibly", () => {
    expect(holdsLabel(1.2)).toBe("holds ~1 day");
    expect(holdsLabel(5.7)).toBe("holds ~6 days");
    expect(holdsLabel(6.9)).toBe("holds ~7 days");
    expect(holdsLabel(7.1)).toBe("holds ~1 week");
    expect(holdsLabel(59)).toBe("holds ~8 weeks");
    expect(holdsLabel(60)).toBe("holds ~2 months");
    expect(holdsLabel(364)).toBe("holds ~12 months");
    expect(holdsLabel(365)).toBe("holds ~1 years");
    expect(holdsLabel(9999)).toBe("holds ~27.4 years");
  });
});
