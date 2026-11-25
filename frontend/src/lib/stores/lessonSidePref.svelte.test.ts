import { beforeEach, describe, expect, it } from "vitest";
import { FIRST_OPEN, lessonSidePref, parseSides, SIDE_LIMIT } from "./lessonSidePref.svelte";

// R1 of the curriculum-page design (tunatale-cs3p slice 3): the first open of a
// lesson is Read on the Outline tab; afterwards the side last used ON THAT LESSON.

beforeEach(() => {
  localStorage.clear();
  lessonSidePref.reset();
});

describe("lessonSidePref", () => {
  it("opens a never-opened lesson on Read, Outline", () => {
    expect(lessonSidePref.get("lesson-a")).toEqual({ mode: "read", tab: "outline" });
    expect(FIRST_OPEN).toEqual({ mode: "read", tab: "outline" });
  });

  it("reopens a lesson on the side last chosen for it", () => {
    lessonSidePref.set("lesson-a", { mode: "listen", tab: "outline" });
    expect(lessonSidePref.get("lesson-a").mode).toBe("listen");
  });

  it("keeps each lesson's side apart", () => {
    lessonSidePref.set("lesson-a", { mode: "listen", tab: "outline" });
    expect(lessonSidePref.get("lesson-b")).toEqual(FIRST_OPEN);
  });

  it("remembers the Read tab too", () => {
    lessonSidePref.set("lesson-a", { mode: "read", tab: "drill-2" });
    expect(lessonSidePref.get("lesson-a")).toEqual({ mode: "read", tab: "drill-2" });
  });

  it("persists across a reload of the store", () => {
    lessonSidePref.set("lesson-a", { mode: "listen", tab: "dialogue" });
    lessonSidePref.reset(); // drops the in-memory copy, as a new page load would
    expect(lessonSidePref.get("lesson-a")).toEqual({ mode: "listen", tab: "dialogue" });
  });

  it("forgets the least recently set lesson past its limit", () => {
    for (let i = 0; i <= SIDE_LIMIT; i++)
      lessonSidePref.set(`l${i}`, { mode: "listen", tab: "outline" });
    expect(lessonSidePref.get("l0")).toEqual(FIRST_OPEN);
    expect(lessonSidePref.get(`l${SIDE_LIMIT}`).mode).toBe("listen");
    // setting a lesson again makes it the newest
    lessonSidePref.set("l1", { mode: "listen", tab: "outline" });
    lessonSidePref.set("extra", { mode: "listen", tab: "outline" });
    expect(lessonSidePref.get("l1").mode).toBe("listen");
    expect(lessonSidePref.get("l2")).toEqual(FIRST_OPEN);
  });
});

describe("parseSides", () => {
  it("reads nothing from nothing or from garbage", () => {
    expect(parseSides(null)).toEqual({});
    expect(parseSides("not json")).toEqual({});
    expect(parseSides("[1,2]")).toEqual({});
    expect(parseSides('"text"')).toEqual({});
  });

  it("drops an entry it cannot trust and keeps the rest", () => {
    const raw = JSON.stringify({
      good: { mode: "listen", tab: "dialogue" },
      badMode: { mode: "sing", tab: "outline" },
      badTab: { mode: "read", tab: 3 },
      notObject: "read",
    });
    expect(parseSides(raw)).toEqual({ good: { mode: "listen", tab: "dialogue" } });
  });
});
