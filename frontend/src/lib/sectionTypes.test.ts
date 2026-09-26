import { describe, it, expect } from "vitest";
import { SECTION_TYPES, isSectionType } from "./sectionTypes";

describe("SECTION_TYPES", () => {
  it("is the section-type token set, spelled once", () => {
    // Pinned exactly: a token is a contract with the audio renderer and the
    // stored lessons that already use it, so an accidental add or drop has to
    // be a deliberate edit here.
    expect([...SECTION_TYPES]).toEqual([
      "key_phrases",
      "natural_speed",
      "translated",
      "en_translated",
      "slow_speed",
      "slow_translated",
      "slow_en_translated",
    ]);
  });

  it("has no duplicates", () => {
    expect(new Set(SECTION_TYPES).size).toBe(SECTION_TYPES.length);
  });
});

describe("isSectionType", () => {
  it("accepts every token", () => {
    for (const token of SECTION_TYPES) {
      expect(isSectionType(token)).toBe(true);
    }
  });

  it("rejects null, an empty string, and an unknown token", () => {
    expect(isSectionType(null)).toBe(false);
    expect(isSectionType("")).toBe(false);
    expect(isSectionType("banana")).toBe(false);
    // Close, and the one a typo would produce.
    expect(isSectionType("slow_en_translate")).toBe(false);
  });
});
