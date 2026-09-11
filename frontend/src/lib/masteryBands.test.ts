import { describe, expect, it } from "vitest";
import {
  RAIL_FILL_PCT,
  RAIL_TRACK_DASHED,
  RAIL_TRACK_SOLID,
  railStyle,
  railPropsFor,
  bandLabel,
  holdsLabel,
  type MasteryBand,
} from "./masteryBands";

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
    ["learning", "var(--band-learning)", "20%"],
    ["days", "var(--band-days)", "40%"],
    ["weeks", "var(--band-weeks)", "60%"],
    ["months", "var(--band-months)", "80%"],
    ["solid", "var(--band-solid)", "100%"],
  ] as const)("fills %s to %s at %s", (band: MasteryBand, colour: string, pct: string) => {
    expect(railStyle(band)).toEqual({ fill: colour, pct, dashed: false });
  });

  it("draws an empty, un-dashed track for new", () => {
    expect(railStyle("new")).toEqual({ fill: null, pct: null, dashed: false });
  });

  it("draws an empty, un-dashed track for suspended", () => {
    expect(railStyle("suspended")).toEqual({ fill: null, pct: null, dashed: false });
  });

  it("draws a dashed empty track for none (no card)", () => {
    expect(railStyle("none")).toEqual({ fill: null, pct: null, dashed: true });
  });

  it("draws nothing for null/undefined/unknown bands", () => {
    expect(railStyle(null)).toEqual({ fill: null, pct: null, dashed: false });
    expect(railStyle(undefined)).toEqual({ fill: null, pct: null, dashed: false });
    expect(railStyle("bogus")).toEqual({ fill: null, pct: null, dashed: false });
  });
});

describe("railPropsFor", () => {
  it.each([
    ["learning", "var(--band-learning)", "20%"],
    ["days", "var(--band-days)", "40%"],
    ["weeks", "var(--band-weeks)", "60%"],
    ["months", "var(--band-months)", "80%"],
    ["solid", "var(--band-solid)", "100%"],
  ] as const)("encodes a painted %s rail as fill + pct custom props", (band, colour, pct) => {
    const props = railPropsFor({ understand_band: band });
    expect(props).toContain(`--rail-u-fill: ${colour};`);
    expect(props).toContain(`--rail-u-pct: ${pct};`);
    expect(props).toContain(`--rail-u-track: ${RAIL_TRACK_SOLID};`);
  });

  it("returns null when the understand band is missing (untracked word)", () => {
    expect(railPropsFor({})).toBeNull();
    expect(railPropsFor({ understand_band: null, produce_band: null })).toBeNull();
  });

  it("paints an invisible produce rail when the produce band is absent", () => {
    const props = railPropsFor({ understand_band: "days" });
    expect(props).toContain("--rail-p-fill: transparent;");
    expect(props).toContain("--rail-p-pct: 0%;");
    expect(props).toContain("--rail-p-track: linear-gradient(transparent, transparent);");
  });

  it("encodes a dashed track for a no-card (none) band", () => {
    const props = railPropsFor({ understand_band: "none", produce_band: "none" });
    expect(props).toContain(`--rail-u-track: ${RAIL_TRACK_DASHED};`);
    expect(props).toContain(`--rail-p-track: ${RAIL_TRACK_DASHED};`);
    expect(props).toContain("--rail-u-fill: transparent;");
    expect(props).toContain("--rail-p-fill: transparent;");
  });

  it("encodes solid empty tracks for new/suspended (fill 0%)", () => {
    const props = railPropsFor({ understand_band: "new", produce_band: "suspended" });
    expect(props).not.toContain(RAIL_TRACK_DASHED);
    expect(props).toContain(`--rail-u-track: ${RAIL_TRACK_SOLID};`);
    expect(props).toContain(`--rail-p-track: ${RAIL_TRACK_SOLID};`);
    expect(props).toContain("--rail-u-fill: transparent;");
    expect(props).toContain("--rail-p-fill: transparent;");
  });

  it("paints each direction from its own band", () => {
    const props = railPropsFor({ understand_band: "weeks", produce_band: "solid" });
    expect(props).toContain("--rail-u-fill: var(--band-weeks);");
    expect(props).toContain("--rail-u-pct: 60%;");
    expect(props).toContain("--rail-p-fill: var(--band-solid);");
    expect(props).toContain("--rail-p-pct: 100%;");
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
