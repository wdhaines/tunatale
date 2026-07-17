import { describe, it, expect } from "vitest";
import { masteryColor, masteryBackgroundColor, lessonMastery } from "./mastery";
import type { TranscriptData } from "./api";

describe("masteryColor", () => {
  it("progress 0 returns red (hue 0)", () => {
    const result = masteryColor(0);
    expect(result).toMatch(/hsl\(0, 70%, 50%\)/);
  });

  it("progress 0.5 returns yellow (hue 60)", () => {
    const result = masteryColor(0.5);
    expect(result).toMatch(/hsl\(60, 70%, 46%\)/);
  });

  it("progress 1 returns green (hue 120)", () => {
    const result = masteryColor(1);
    expect(result).toMatch(/hsl\(120, 70%, 42%\)/);
  });

  it("clamps negative values to 0 (hue 0)", () => {
    const result = masteryColor(-0.2);
    expect(result).toMatch(/hsl\(0, 70%, 50%\)/);
  });

  it("clamps values > 1 to 1 (hue 120)", () => {
    const result = masteryColor(1.5);
    expect(result).toMatch(/hsl\(120, 70%, 42%\)/);
  });
});

describe("masteryBackgroundColor", () => {
  it("progress 0 returns a translucent red tint (hue 0)", () => {
    expect(masteryBackgroundColor(0)).toBe("hsla(0, 70%, 45%, 0.15)");
  });

  it("progress 1 returns a translucent green tint (hue 120)", () => {
    expect(masteryBackgroundColor(1)).toBe("hsla(120, 70%, 45%, 0.15)");
  });

  it("clamps out-of-range values", () => {
    expect(masteryBackgroundColor(-0.2)).toBe("hsla(0, 70%, 45%, 0.15)");
    expect(masteryBackgroundColor(1.5)).toBe("hsla(120, 70%, 45%, 0.15)");
  });
});

describe("lessonMastery", () => {
  const makeTranscript = (words: Array<Record<string, unknown>>): TranscriptData => ({
    lesson_id: "l1",
    key_phrases: [],
    dialogue_lines: [{ role: "A", sentence: "", words: words as never }],
  });

  it("returns null for an empty transcript", () => {
    expect(lessonMastery({ dialogue_lines: [] })).toBeNull();
  });

  it("returns null for a transcript with no words", () => {
    const t: TranscriptData = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [{ role: "A", sentence: "", words: [] }],
    };
    expect(lessonMastery(t)).toBeNull();
  });

  it("dedupes by lemma, keeping the first occurrence", () => {
    const t = makeTranscript([
      { lemma: "kava", active_state: "known", progress: 1.0 },
      { lemma: "kava", active_state: "unknown", progress: null },
    ]);
    const result = lessonMastery(t)!;
    expect(result.pct).toBe(1.0);
    expect(result.counts.known).toBe(1);
    expect(result.counts.new).toBe(0);
  });

  it("unknown → 0, known → 1.0", () => {
    const t = makeTranscript([
      { lemma: "a", active_state: "unknown", progress: null },
      { lemma: "b", active_state: "known", progress: 1.0 },
    ]);
    const result = lessonMastery(t)!;
    expect(result.pct).toBe(0.5);
  });

  it("ignored words are excluded from numerator and denominator", () => {
    const t = makeTranscript([
      { lemma: "a", active_state: "known", progress: 1.0 },
      { lemma: "b", active_state: "ignored", progress: null },
      { lemma: "c", active_state: "unknown", progress: null },
    ]);
    const result = lessonMastery(t)!;
    // Only a and c count (b is ignored). a=1.0, c=0 → 1.0/2 = 0.5
    expect(result.pct).toBe(0.5);
    expect(result.counts.known).toBe(1);
    expect(result.counts.new).toBe(0);
  });

  it("uses progress ?? 0 for non-terminal non-ignored states", () => {
    const t = makeTranscript([
      { lemma: "a", active_state: "learning", progress: 0.3 },
      { lemma: "b", active_state: "review", progress: 0.8 },
      { lemma: "c", active_state: "relearning", progress: 0.15 },
    ]);
    const result = lessonMastery(t)!;
    // (0.3 + 0.8 + 0.15) / 3 ≈ 0.417
    expect(result.pct).toBeCloseTo(0.417, 2);
  });

  it("treats null progress as 0 for non-terminal states", () => {
    const t = makeTranscript([{ lemma: "a", active_state: "learning", progress: null }]);
    const result = lessonMastery(t)!;
    expect(result.pct).toBe(0);
  });

  it("counts breakdown: relearning folds into learning", () => {
    const t = makeTranscript([
      { lemma: "a", active_state: "new", progress: null },
      { lemma: "b", active_state: "learning", progress: 0.2 },
      { lemma: "c", active_state: "relearning", progress: 0.15 },
      { lemma: "d", active_state: "review", progress: 0.7 },
      { lemma: "e", active_state: "known", progress: 1.0 },
      { lemma: "f", active_state: "unknown", progress: null },
      { lemma: "g", active_state: "ignored", progress: null },
      { lemma: "h", active_state: "suspended", progress: null },
    ]);
    const result = lessonMastery(t)!;
    expect(result.counts).toEqual({ new: 1, learning: 2, review: 1, known: 1 });
    // unknown and ignored and suspended not in breakdown
  });

  it("unknown/ignored/suspended are not in the breakdown counts", () => {
    const t = makeTranscript([
      { lemma: "a", active_state: "unknown", progress: null },
      { lemma: "b", active_state: "ignored", progress: null },
      { lemma: "c", active_state: "suspended", progress: null },
    ]);
    const result = lessonMastery(t)!;
    expect(result.counts).toEqual({ new: 0, learning: 0, review: 0, known: 0 });
  });
});
