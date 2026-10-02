import { describe, it, expect } from "vitest";
import { formatSessionDate } from "./sessionDate";

/**
 * One formatter for a session date (bd tunatale-e6fq). Home and the session
 * reader each had their own copy, so the two lists of sessions could disagree
 * about the day a session happened on.
 */
describe("formatSessionDate", () => {
  it("writes the day and the month name", () => {
    expect(formatSessionDate("2026-09-02")).toBe("2 September");
  });

  it("handles the last day of the year", () => {
    expect(formatSessionDate("2026-12-31")).toBe("31 December");
  });

  it("handles the first day of the year", () => {
    expect(formatSessionDate("2026-01-01")).toBe("1 January");
  });

  it("reads the date as a calendar date, never as an instant", () => {
    // `new Date("2026-09-01")` is UTC midnight, which is 31 August in every
    // negative-offset zone — and a test run in London cannot see it.
    expect(formatSessionDate("2026-09-01")).toBe("1 September");
  });
});
