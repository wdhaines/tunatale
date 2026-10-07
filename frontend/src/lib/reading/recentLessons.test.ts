/**
 * Which lessons home shows, and which curriculum it shows them for
 * (bd tunatale-e6fq, stage 2).
 *
 * The user agreed the rule from screenshots on 2026-10-07: the first unlistened
 * lesson on top, marked as what comes next, then the lessons before it, newest
 * first. With several curricula ONE of them leads: the one listened to most
 * recently. Both rules are app-computed, so they are pinned here without a DOM.
 */
import { describe, it, expect } from "vitest";
import { leadCurriculum, recentLessons, type LessonDay } from "./recentLessons";

const days = (n: number): LessonDay[] =>
  Array.from({ length: n }, (_, i) => ({ day: i + 1, position: i + 1, lesson_id: `L${i + 1}` }));

const listened =
  (...ids: string[]) =>
  (id: string) =>
    ids.includes(id);

const firstN = (n: number) => listened(...days(n).map((d) => d.lesson_id));

describe("recentLessons", () => {
  it("leads with the first unlistened lesson, then the two before it, newest first", () => {
    const rows = recentLessons(days(10), firstN(7));
    expect(rows.map((r) => r.position)).toEqual([8, 7, 6]);
    expect(rows.map((r) => r.next)).toEqual([true, false, false]);
    expect(rows.map((r) => r.listened)).toEqual([false, true, true]);
  });

  it("shows a single row for a curriculum nobody has started", () => {
    const rows = recentLessons(days(10), listened());
    expect(rows.map((r) => r.position)).toEqual([1]);
    expect(rows[0].next).toBe(true);
  });

  it("shows two rows after the first listen, never padding with later lessons", () => {
    const rows = recentLessons(days(10), firstN(1));
    expect(rows.map((r) => r.position)).toEqual([2, 1]);
  });

  it("shows the last three, none marked next, when everything is listened", () => {
    const rows = recentLessons(days(10), firstN(10));
    expect(rows.map((r) => r.position)).toEqual([10, 9, 8]);
    expect(rows.some((r) => r.next)).toBe(false);
    expect(rows.every((r) => r.listened)).toBe(true);
  });

  it("keeps the FIRST gap as what comes next, whatever was listened after it", () => {
    // Day 4 was listened out of order. Day 3 is still the next thing to do, and
    // day 4 is not "before it", so it is not on the list.
    const rows = recentLessons(days(10), listened("L1", "L2", "L4"));
    expect(rows.map((r) => r.position)).toEqual([3, 2, 1]);
    expect(rows.map((r) => r.next)).toEqual([true, false, false]);
  });

  it("walks by position, not by day key or by the order it was handed", () => {
    // Day 2 was deleted, so keys run 1, 3, 4 while positions run 1, 2, 3; and
    // the list arrives shuffled.
    const shuffled: LessonDay[] = [
      { day: 4, position: 3, lesson_id: "L4" },
      { day: 1, position: 1, lesson_id: "L1" },
      { day: 3, position: 2, lesson_id: "L3" },
    ];
    const before = shuffled.map((d) => d.lesson_id);

    const rows = recentLessons(shuffled, listened("L1"));

    expect(rows.map((r) => r.lesson_id)).toEqual(["L3", "L1"]);
    expect(rows.map((r) => r.day)).toEqual([3, 1]);
    expect(rows.map((r) => r.position)).toEqual([2, 1]);
    // The caller's array is not sorted in place.
    expect(shuffled.map((d) => d.lesson_id)).toEqual(before);
  });

  it("returns nothing for a curriculum with no lessons yet", () => {
    expect(recentLessons([], listened())).toEqual([]);
  });

  it("honours a different limit, and a list shorter than it", () => {
    expect(recentLessons(days(10), firstN(7), 5).map((r) => r.position)).toEqual([8, 7, 6, 5, 4]);
    expect(recentLessons(days(2), firstN(2), 5).map((r) => r.position)).toEqual([2, 1]);
  });
});

describe("leadCurriculum", () => {
  const a = { id: "a", topic: "A" };
  const b = { id: "b", topic: "B" };
  const daysById = {
    a: [{ day: 1, position: 1, lesson_id: "a1" }],
    b: [
      { day: 1, position: 1, lesson_id: "b1" },
      { day: 2, position: 2, lesson_id: "b2" },
    ],
  };
  const at =
    (stamps: Record<string, string>) =>
    (id: string): string | null =>
      stamps[id] ?? null;

  it("is the curriculum listened to most recently, wherever it sits in the list", () => {
    const lead = leadCurriculum(
      [a, b],
      daysById,
      at({ a1: "2026-10-01T10:00:00.000Z", b2: "2026-10-05T10:00:00.000Z" }),
    );
    expect(lead).toBe(b);
  });

  it("compares instants, not strings", () => {
    // The server writes `+00:00` offsets with microseconds and the browser
    // writes `Z` with milliseconds, and an offset can put the later INSTANT
    // under the earlier calendar date. As strings a1 sorts last; as instants
    // b1 is half an hour later.
    const lead = leadCurriculum(
      [a, b],
      daysById,
      at({ a1: "2026-10-07T01:00:00.000000+02:00", b1: "2026-10-06T23:30:00.000Z" }),
    );
    expect(lead).toBe(b);
  });

  it("falls back to the first curriculum when nothing has been listened to", () => {
    expect(leadCurriculum([a, b], daysById, at({}))).toBe(a);
  });

  it("keeps the earlier curriculum on a tie", () => {
    const same = "2026-10-05T10:00:00.000Z";
    expect(leadCurriculum([a, b], daysById, at({ a1: same, b1: same }))).toBe(a);
  });

  it("treats an unreadable timestamp as never listened", () => {
    const lead = leadCurriculum(
      [a, b],
      daysById,
      at({ a1: "not a date", b1: "2026-10-05T10:00:00.000Z" }),
    );
    expect(lead).toBe(b);
  });

  it("survives a curriculum whose progress never loaded", () => {
    // A failed progress fetch leaves no entry at all for that curriculum.
    expect(leadCurriculum([a, b], { b: daysById.b }, at({}))).toBe(a);
    expect(leadCurriculum([a, b], { b: daysById.b }, at({ b1: "2026-10-05T10:00:00.000Z" }))).toBe(
      b,
    );
  });

  it("is null when there are no curricula", () => {
    expect(leadCurriculum([], {}, at({}))).toBeNull();
  });
});
