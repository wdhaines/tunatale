/**
 * The study-day convention, stated once (tunatale-ss5q.3 item 2).
 *
 * TT's study day is the LOCAL calendar date with a 04:00 LOCAL rollover
 * (backend rollover.py::anki_today). A day-level due_at is 04:00 UTC of its
 * due date (due_at_rollover_utc), so its UTC date IS the due day. An intraday
 * due_at (learning / relearning) is a real instant, so it belongs to the study
 * day of its LOCAL time.
 *
 * TZ pinned to America/New_York (EDT = UTC-4 in September 2026).
 */
process.env.TZ = "America/New_York";

import { describe, expect, it } from "vitest";
import { dueStudyDay, studyDayOf } from "./studyDay";

const day = (y: number, m: number, d: number) => Date.UTC(y, m - 1, d);

describe("studyDayOf", () => {
  it("is the local date in the daytime", () => {
    expect(studyDayOf(new Date("2026-09-26T13:00:00Z"))).toBe(day(2026, 9, 26));
  });

  it("is still yesterday's study day in the evening after UTC has rolled", () => {
    // 22:30 EDT on 09-25; the UTC date is already 09-26.
    expect(studyDayOf(new Date("2026-09-26T02:30:00Z"))).toBe(day(2026, 9, 25));
  });

  it("is still yesterday's study day before the 04:00 local rollover", () => {
    expect(studyDayOf(new Date("2026-09-26T07:30:00Z"))).toBe(day(2026, 9, 25)); // 03:30 EDT
  });

  it("rolls at 04:00 local", () => {
    expect(studyDayOf(new Date("2026-09-26T08:00:00Z"))).toBe(day(2026, 9, 26)); // 04:00 EDT
  });

  it("rolls back across a month end", () => {
    expect(studyDayOf(new Date("2026-10-01T06:00:00Z"))).toBe(day(2026, 9, 30)); // 02:00 EDT 10-01
  });
});

describe("dueStudyDay", () => {
  it("reads a day-level due_at by its UTC date", () => {
    expect(dueStudyDay(new Date("2026-09-15T04:00:00Z"), false)).toBe(day(2026, 9, 15));
  });

  it("reads an intraday due_at by its local study day", () => {
    expect(dueStudyDay(new Date("2026-09-26T02:30:00Z"), true)).toBe(day(2026, 9, 25));
  });
});
