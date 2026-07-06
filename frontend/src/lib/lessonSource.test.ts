import { describe, it, expect } from "vitest";
import { formatSource, buildClaudePrompt } from "./lessonSource";

const SAMPLE_SOURCE = {
  title: "Kavarna",
  key_phrases: [{ phrase: "ena kava", translation: "one coffee" }],
  scenes: [
    {
      label: "At the café",
      lines: [
        { speaker: "barista", text: "Dober dan", translation: "Good day" },
        { speaker: "customer", text: "Ena kava prosim", translation: "One coffee please" },
      ],
    },
  ],
  dialogue_glosses: [{ word: "kava", translation: "coffee" }],
  morphology_focus: ["noun:acc:sg"],
};

describe("formatSource", () => {
  it("returns pretty-printed JSON", () => {
    const result = formatSource(SAMPLE_SOURCE);
    const parsed = JSON.parse(result);
    expect(parsed).toEqual(SAMPLE_SOURCE);
    // Must have indentation (not single-line)
    expect(result).toContain("\n  ");
  });

  it("handles an empty object", () => {
    const result = formatSource({});
    expect(result).toBe("{}");
  });

  it("handles null gracefully", () => {
    const result = formatSource(null as never);
    expect(result).toBe("null");
  });

  it("handles undefined gracefully", () => {
    // JSON.stringify(undefined) returns the JS value undefined
    expect(formatSource(undefined as never)).toBeUndefined();
  });
});

describe("buildClaudePrompt", () => {
  it("includes instructions, schema reminder, JSON, and call to action", () => {
    const prompt = buildClaudePrompt(SAMPLE_SOURCE);
    expect(prompt).toContain("edit this story");
    expect(prompt).toContain("speaker");
    expect(prompt).toContain("text");
    expect(prompt).toContain("translation");
    expect(prompt).toContain("ena kava");
    expect(prompt).toContain("Paste the edited JSON");
    expect(prompt).toContain("Import");
  });

  it("mentions the markdown code block with JSON", () => {
    const prompt = buildClaudePrompt(SAMPLE_SOURCE);
    // Should have JSON in a code block
    expect(prompt).toContain("```json");
    expect(prompt).toContain("```");
  });

  it("says to preserve the structure", () => {
    const prompt = buildClaudePrompt(SAMPLE_SOURCE);
    expect(prompt).toMatch(/preserve|keep the|structure/i);
  });

  it("handles null source without crashing", () => {
    const prompt = buildClaudePrompt(null as never);
    expect(prompt).toBeTruthy();
    expect(typeof prompt).toBe("string");
  });
});
