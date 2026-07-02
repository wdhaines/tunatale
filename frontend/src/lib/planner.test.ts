import { describe, it, expect } from "vitest";
import type { ProposedBatch } from "./api";
import { appendTurn, batchRange, clampBatchSize, commitEvent } from "./planner";

const batch = (start: number, count: number): ProposedBatch => ({
  start_day: start,
  days: Array.from({ length: count }, (_, i) => ({
    day: start + i,
    title: `Day ${start + i}`,
    focus: "f",
    collocations: ["a"],
    learning_objective: "o",
    story_guidance: "",
  })),
});

describe("appendTurn", () => {
  it("appends user + planner messages without mutating the input", () => {
    const before = [{ role: "user" as const, content: "hi" }];
    const after = appendTurn(before, "plan 3 days", "Here you go");
    expect(after).toEqual([
      { role: "user", content: "hi" },
      { role: "user", content: "plan 3 days" },
      { role: "planner", content: "Here you go" },
    ]);
    expect(before).toHaveLength(1);
  });
});

describe("commitEvent", () => {
  it("multi-day batch → range message matching the server's event wording", () => {
    expect(commitEvent(batch(3, 2))).toEqual({ role: "event", content: "Committed days 3-4." });
  });

  it("single-day batch → singular message", () => {
    expect(commitEvent(batch(1, 1))).toEqual({ role: "event", content: "Committed day 1." });
  });
});

describe("batchRange", () => {
  it("returns first and last day numbers", () => {
    expect(batchRange(batch(4, 3))).toEqual({ start: 4, end: 6 });
  });

  it("single day → start equals end", () => {
    expect(batchRange(batch(9, 1))).toEqual({ start: 9, end: 9 });
  });
});

describe("clampBatchSize", () => {
  it("passes normal values through", () => {
    expect(clampBatchSize(5)).toBe(5);
  });

  it("clamps below 1 up to 1", () => {
    expect(clampBatchSize(0)).toBe(1);
    expect(clampBatchSize(-3)).toBe(1);
  });

  it("clamps above 14 down to 14", () => {
    expect(clampBatchSize(99)).toBe(14);
  });

  it("floors non-integers and defaults NaN to 5", () => {
    expect(clampBatchSize(3.7)).toBe(3);
    expect(clampBatchSize(Number.NaN)).toBe(5);
  });
});
