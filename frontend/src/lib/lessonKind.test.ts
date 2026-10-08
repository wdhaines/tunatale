import { describe, it, expect } from "vitest";
import { isDrillOnly } from "./lessonKind";
import type { LessonDrill, ReadableLesson } from "./api";

const drill: LessonDrill = { pattern: "mo-mi", title: "Affix drill: mo- / mi-", roots: [] };
const section = (type: string) => ({ type, phrases: [] });
const lesson = (types: string[], withDrill: LessonDrill | null | undefined): ReadableLesson => ({
  id: "l1",
  language_code: "ceb",
  key_phrases: [],
  sections: types.map(section),
  drill: withDrill,
});

describe("isDrillOnly", () => {
  it("is true of a lesson whose every section is the drill", () => {
    expect(isDrillOnly(lesson(["affix_drill"], drill))).toBe(true);
  });

  it("is false of a story lesson that ends with a drill", () => {
    expect(isDrillOnly(lesson(["key_phrases", "natural_speed", "affix_drill"], drill))).toBe(false);
  });

  it("is false of a lesson with no drill, whether the field is null or absent", () => {
    expect(isDrillOnly(lesson(["natural_speed"], null))).toBe(false);
    expect(isDrillOnly(lesson(["natural_speed"], undefined))).toBe(false);
  });
});
