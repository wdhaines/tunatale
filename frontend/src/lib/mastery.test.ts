import { describe, it, expect } from "vitest";
import { masteryColor, lessonMastery } from "./mastery";
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
      {
        lemma: "kava",
        active_state: "known",
        progress: 1.0,
        recognition_state: "known",
        recognition_is_due: false,
      },
      {
        lemma: "kava",
        active_state: "unknown",
        progress: null,
        recognition_state: null,
        recognition_is_due: false,
      },
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
      {
        lemma: "a",
        active_state: "known",
        progress: 1.0,
        recognition_state: "known",
        recognition_is_due: false,
      },
      { lemma: "b", active_state: "ignored", progress: null },
      {
        lemma: "c",
        active_state: "unknown",
        progress: null,
        recognition_state: null,
        recognition_is_due: false,
      },
    ]);
    const result = lessonMastery(t)!;
    // Only a and c count (b is ignored). a=1.0, c=0 → 1.0/2 = 0.5
    expect(result.pct).toBe(0.5);
    expect(result.counts.known).toBe(1);
    expect(result.counts.new).toBe(1); // "c" is unknown → recognition_state null → new bucket
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
      {
        lemma: "a",
        active_state: "new",
        progress: null,
        recognition_state: "new",
        recognition_is_due: false,
      },
      {
        lemma: "b",
        active_state: "learning",
        progress: 0.2,
        recognition_state: "learning",
        recognition_is_due: true,
      },
      {
        lemma: "c",
        active_state: "relearning",
        progress: 0.15,
        recognition_state: "relearning",
        recognition_is_due: true,
      },
      {
        lemma: "d",
        active_state: "review",
        progress: 0.7,
        recognition_state: "review",
        recognition_is_due: false,
      },
      {
        lemma: "e",
        active_state: "known",
        progress: 1.0,
        recognition_state: "known",
        recognition_is_due: false,
      },
      {
        lemma: "f",
        active_state: "unknown",
        progress: null,
        recognition_state: null,
        recognition_is_due: false,
      },
      { lemma: "g", active_state: "ignored", progress: null },
      {
        lemma: "h",
        active_state: "suspended",
        progress: null,
        recognition_state: "suspended",
        recognition_is_due: false,
      },
    ]);
    const result = lessonMastery(t)!;
    expect(result.counts).toEqual({ new: 2, learning: 2, due: 0, review: 1, known: 1 });
    // unknown and ignored and suspended not in breakdown
  });

  it("unknown/ignored/suspended are not in the breakdown counts", () => {
    const t = makeTranscript([
      {
        lemma: "a",
        active_state: "unknown",
        progress: null,
        recognition_state: null,
        recognition_is_due: false,
      },
      { lemma: "b", active_state: "ignored", progress: null },
      {
        lemma: "c",
        active_state: "suspended",
        progress: null,
        recognition_state: "suspended",
        recognition_is_due: false,
      },
    ]);
    const result = lessonMastery(t)!;
    // "a" is unknown (no card) → new bucket; ignored and suspended excluded
    expect(result.counts).toEqual({ new: 1, learning: 0, due: 0, review: 0, known: 0 });
  });

  describe("recognition-based bucketing", () => {
    it("unknown → new bucket", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "unknown",
          progress: null,
          recognition_state: null,
          recognition_is_due: false,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.new).toBe(1);
      expect(result.lemmas?.new).toEqual(["a"]);
    });

    it("recognition_state 'new' → new bucket", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "new",
          progress: 0,
          recognition_state: "new",
          recognition_is_due: false,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.new).toBe(1);
      expect(result.lemmas?.new).toEqual(["a"]);
    });

    it("recognition_state 'learning' → learning bucket", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "learning",
          progress: 0.3,
          recognition_state: "learning",
          recognition_is_due: true,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.learning).toBe(1);
      expect(result.lemmas?.learning).toEqual(["a"]);
    });

    it("recognition_state 'relearning' → learning bucket", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "learning",
          progress: 0.15,
          recognition_state: "relearning",
          recognition_is_due: true,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.learning).toBe(1);
      expect(result.lemmas?.learning).toEqual(["a"]);
    });

    it("recognition_state 'review' + recognition_is_due true → due bucket", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "review",
          progress: 0.8,
          recognition_state: "review",
          recognition_is_due: true,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.due).toBe(1);
      expect(result.lemmas?.due).toEqual(["a"]);
    });

    it("recognition_state 'review' + recognition_is_due false → review bucket", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "review",
          progress: 0.8,
          recognition_state: "review",
          recognition_is_due: false,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.review).toBe(1);
      expect(result.lemmas?.review).toEqual(["a"]);
    });

    it("recognition_state 'known' → known bucket", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "known",
          progress: 1.0,
          recognition_state: "known",
          recognition_is_due: false,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.known).toBe(1);
      expect(result.lemmas?.known).toEqual(["a"]);
    });

    it("tracked word with recognition_state null (cloze) → excluded", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "learning",
          progress: 0.3,
          recognition_state: null,
          recognition_is_due: false,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts).toEqual({ new: 0, learning: 0, due: 0, review: 0, known: 0 });
      // Cloze word with null recognition_state is excluded from all lemma lists
      expect(result.lemmas!.new).toHaveLength(0);
      expect(result.lemmas!.learning).toHaveLength(0);
      expect(result.lemmas!.review).toHaveLength(0);
      expect(result.lemmas!.known).toHaveLength(0);
    });

    it("guardrail: active_state 'new' + recognition_state 'review' → review, NOT new", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "new",
          progress: 0.8,
          active_direction: "production",
          recognition_state: "review",
          recognition_is_due: false,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.new).toBe(0);
      expect(result.counts.review).toBe(1);
      expect(result.lemmas?.review).toEqual(["a"]);
    });

    it("counts match lemma list lengths", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "unknown",
          progress: null,
          recognition_state: null,
          recognition_is_due: false,
        },
        {
          lemma: "b",
          active_state: "learning",
          progress: 0.3,
          recognition_state: "learning",
          recognition_is_due: true,
        },
        {
          lemma: "c",
          active_state: "review",
          progress: 0.8,
          recognition_state: "review",
          recognition_is_due: false,
        },
        {
          lemma: "d",
          active_state: "known",
          progress: 1.0,
          recognition_state: "known",
          recognition_is_due: false,
        },
        {
          lemma: "e",
          active_state: "new",
          progress: 0,
          recognition_state: "new",
          recognition_is_due: false,
        },
        {
          lemma: "f",
          active_state: "review",
          progress: 0.7,
          recognition_state: "review",
          recognition_is_due: true,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.new).toBe(result.lemmas!.new.length);
      expect(result.counts.learning).toBe(result.lemmas!.learning.length);
      expect(result.counts.due).toBe(result.lemmas!.due.length);
      expect(result.counts.review).toBe(result.lemmas!.review.length);
      expect(result.counts.known).toBe(result.lemmas!.known.length);
    });

    it("pct is unchanged by recognition bucketing (same inputs, same weights)", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "unknown",
          progress: null,
          recognition_state: null,
          recognition_is_due: false,
        },
        {
          lemma: "b",
          active_state: "known",
          progress: 1.0,
          recognition_state: "known",
          recognition_is_due: false,
        },
      ]);
      const result = lessonMastery(t)!;
      // 0 + 1.0 = 1.0 / 2 = 0.5
      expect(result.pct).toBe(0.5);
    });

    it("dedupes by lemma and skips ignored, first-occurrence order", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "learning",
          progress: 0.3,
          recognition_state: "learning",
          recognition_is_due: true,
        },
        {
          lemma: "a",
          active_state: "known",
          progress: 1.0,
          recognition_state: "known",
          recognition_is_due: false,
        },
        { lemma: "b", active_state: "ignored", progress: null },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.learning).toBe(1);
      expect(result.lemmas?.learning).toEqual(["a"]);
      expect(result.counts.known).toBe(0);
    });
  });

  describe("well-known words count as known", () => {
    // A card scheduled past the listen horizon is one the preview has stopped
    // asking about. Counting it as "review" made the metrics disagree with what
    // the listen flow actually treats as finished — and since SRSState.KNOWN
    // does not survive a sync, the known bucket read 0 forever.
    it("buckets a well-known review word as known, not review", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "review",
          progress: 0.9,
          recognition_state: "review",
          recognition_is_due: false,
          well_known: true,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.known).toBe(1);
      expect(result.counts.review).toBe(0);
      expect(result.lemmas?.known).toEqual(["a"]);
    });

    it("leaves the percentage alone — bucketing changes, scoring does not", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "review",
          progress: 0.6,
          recognition_state: "review",
          recognition_is_due: false,
          well_known: true,
        },
      ]);
      // Still 0.6 (its progress), NOT the flat 1.0 an active_state="known" word
      // scores. Deliberate: the display label moved, the mastery math did not.
      expect(lessonMastery(t)!.pct).toBeCloseTo(0.6);
    });

    it("a due word is never well-known, so dueness still wins", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "review",
          progress: 0.9,
          recognition_state: "review",
          recognition_is_due: true,
          well_known: false,
        },
      ]);
      const result = lessonMastery(t)!;
      expect(result.counts.due).toBe(1);
      expect(result.counts.known).toBe(0);
    });
  });

  describe("sides (understand / produce)", () => {
    it("computes per-side pct and bands from the oracle fixture", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "review",
          progress: 0.8,
          understand_progress: 0.8,
          produce_progress: 0.0,
          understand_band: "months",
          produce_band: "none",
        },
        {
          lemma: "b",
          active_state: "review",
          progress: 0.4,
          understand_progress: 0.4,
          produce_progress: 0.2,
          understand_band: "weeks",
          produce_band: "days",
        },
        {
          lemma: "c",
          active_state: "unknown",
          progress: null,
          understand_progress: null,
          produce_progress: null,
          understand_band: null,
          produce_band: null,
        },
        {
          lemma: "d",
          active_state: "ignored",
          progress: 0.9,
          understand_progress: 0.9,
          produce_progress: 0.9,
          understand_band: "solid",
          produce_band: "solid",
        },
        {
          lemma: "a",
          active_state: "review",
          progress: 0.1,
          understand_progress: 0.1,
          produce_progress: 0.9,
          understand_band: "days",
          produce_band: "days",
        },
        {
          lemma: "e",
          active_state: "suspended",
          progress: null,
          understand_progress: null,
          produce_progress: 0.6,
          understand_band: "suspended",
          produce_band: "months",
        },
      ]);
      const result = lessonMastery(t)!;

      // understand: a=.8, b=.4, c=0 (unknown→0); e left out (null) → .4
      expect(result.sides.understand.pct).toBeCloseTo(0.4, 6);
      // produce: a=0, b=.2, c=0, e=.6 → .8/4 = .2
      expect(result.sides.produce.pct).toBeCloseTo(0.2, 6);

      expect(result.sides.understand.bands).toMatchObject({
        months: 1,
        weeks: 1,
        none: 1,
        suspended: 1,
      });
      expect(result.sides.produce.bands).toMatchObject({
        none: 2,
        days: 1,
        months: 1,
      });
    });

    it("existing pct/counts unchanged by sides computation", () => {
      const t = makeTranscript([
        {
          lemma: "a",
          active_state: "learning",
          progress: 0.3,
          recognition_state: "learning",
          recognition_is_due: true,
        },
        {
          lemma: "b",
          active_state: "review",
          progress: 0.8,
          recognition_state: "review",
          recognition_is_due: false,
        },
        {
          lemma: "c",
          active_state: "relearning",
          progress: 0.15,
          recognition_state: "relearning",
          recognition_is_due: true,
        },
      ]);
      const result = lessonMastery(t)!;
      // (0.3 + 0.8 + 0.15) / 3 ≈ 0.417
      expect(result.pct).toBeCloseTo(0.417, 2);
      expect(result.counts).toEqual({ new: 0, learning: 2, due: 0, review: 1, known: 0 });
    });
  });
});
