import { describe, expect, it } from "vitest";
import type { TranscriptData } from "#lib/api.js";
import { makeGradePlanCard, makeWordToken } from "../../test/factories";
import {
  daysUntil,
  DEFAULT_RANGE,
  dueLabel,
  gradeHref,
  safeBack,
  gradeKey,
  parseRange,
  sayLine,
  serializeRange,
  stepRange,
} from "./grade";

describe("stepRange", () => {
  it("moves one day at a time inside 0..30", () => {
    expect(stepRange(3, 1)).toBe(4);
    expect(stepRange(3, -1)).toBe(2);
    expect(stepRange(0, -1)).toBe(0);
    expect(stepRange(30, 1)).toBe(30);
  });

  it("steps down from all to two weeks, and stays at all going up", () => {
    expect(stepRange(null, -1)).toBe(14);
    expect(stepRange(null, 1)).toBeNull();
  });
});

describe("parseRange / serializeRange", () => {
  it("round-trips a day count and all", () => {
    for (const r of [0, 3, 7, 21, null]) expect(parseRange(serializeRange(r))).toBe(r);
    expect(serializeRange(null)).toBe("all");
    expect(serializeRange(5)).toBe("5");
  });

  it("falls back to three days on nothing stored or garbage, and caps a large count", () => {
    expect(DEFAULT_RANGE).toBe(3);
    expect(parseRange(null)).toBe(3);
    expect(parseRange("")).toBe(3);
    expect(parseRange("-2")).toBe(3);
    expect(parseRange("soon")).toBe(3);
    expect(parseRange("999")).toBe(30);
  });
});

describe("daysUntil / dueLabel", () => {
  const now = new Date(2026, 10, 14, 21, 30); // local 14 Nov, evening

  it("counts calendar days in the viewer's zone, reading a bare ISO time as UTC", () => {
    const at = (d: Date) => d.toISOString().replace("Z", "");
    expect(daysUntil(at(new Date(2026, 10, 14, 6, 0)), now)).toBe(0);
    expect(daysUntil(at(new Date(2026, 10, 16, 23, 0)), now)).toBe(2);
    expect(daysUntil(at(new Date(2026, 10, 12, 1, 0)), now)).toBe(-2);
    expect(daysUntil(new Date(2026, 10, 15, 8, 0).toISOString(), now)).toBe(1);
  });

  it("reads an explicit offset as given", () => {
    const due = new Date(2026, 10, 17, 12, 0);
    const offset = due.toISOString().replace("Z", "+00:00");
    expect(daysUntil(offset, now)).toBe(3);
  });

  it("labels late, today and ahead", () => {
    expect(dueLabel(-2)).toBe("2d late");
    expect(dueLabel(0)).toBe("today");
    expect(dueLabel(4)).toBe("in 4d");
  });
});

describe("gradeKey", () => {
  it("keys a row by card and direction", () => {
    expect(gradeKey({ item_id: 9, direction: "production" })).toBe("9:production");
  });
});

describe("sayLine", () => {
  const transcript: TranscriptData = {
    lesson_id: "l1",
    key_phrases: [],
    dialogue_lines: [
      { role: "female-1", words: [makeWordToken({ surface: "Maayong", srs_item_id: 3 })] },
      {
        role: "male-1",
        words: [
          makeWordToken({ surface: "Nia", srs_item_id: 4 }),
          makeWordToken({ surface: "mi", srs_item_id: 5, collocation_span_id: 20 }),
          makeWordToken({
            surface: "dinhi",
            srs_item_id: 6,
            collocation_span_id: 20,
            suffix_punct: ".",
          }),
          makeWordToken({ surface: "Oo", prefix_punct: "¡", srs_item_id: 7 }),
        ],
      },
    ],
  };

  it("hides a word card in place in the first line that carries it", () => {
    const line = sayLine(makeGradePlanCard({ item_id: 4, gloss: "is here" }), transcript);
    expect(line).toEqual({
      role: "male-1",
      before: "",
      hidden: "Nia",
      after: " mi dinhi. ¡Oo",
      english: "is here",
      prompt: [],
    });
  });

  it("hides a phrase card's whole span", () => {
    const line = sayLine(makeGradePlanCard({ item_id: 20, kind: "phrase" }), transcript);
    expect([line.before, line.hidden, line.after]).toEqual(["Nia ", "mi dinhi.", " ¡Oo"]);
  });

  it("falls back to the card's own text when no line carries it, or there is no transcript", () => {
    const card = makeGradePlanCard({ item_id: 99, text: "bir", gloss: "beer" });
    for (const t of [transcript, null]) {
      expect(sayLine(card, t)).toEqual({
        role: null,
        before: "",
        hidden: "bir",
        after: "",
        english: "beer",
        prompt: [],
      });
    }
  });

  it("splits a sentence card around its blank, and hides the whole line when it has none", () => {
    const blank = makeGradePlanCard({
      kind: "sentence",
      text: "Nia mi dinhi.",
      gloss: "We are here.",
      blank_start: 4,
      blank_end: 6,
    });
    expect([
      sayLine(blank, null).before,
      sayLine(blank, null).hidden,
      sayLine(blank, null).after,
    ]).toEqual(["Nia ", "mi", " dinhi."]);
    const whole = makeGradePlanCard({ kind: "sentence", text: "Nia man." });
    expect([
      sayLine(whole, null).before,
      sayLine(whole, null).hidden,
      sayLine(whole, null).after,
    ]).toEqual(["", "Nia man.", ""]);
  });

  it("puts a drill's worked example and new cue above its hidden answer", () => {
    const drill = {
      kind: "transformation" as const,
      step: 2,
      of: 5,
      cue: "Moadto ko.",
      answer: "Miadto ko.",
      english: "I went.",
      before: "Mikaon ko.",
      before_cue: "Mokaon ko.",
      answer_audio_url: null,
      before_audio_url: null,
      before_cue_audio_url: null,
      cue_audio_url: null,
    };
    const line = sayLine(
      makeGradePlanCard({ kind: "drill", direction: "production", drill }),
      null,
    );
    expect(line.prompt).toEqual(["Mokaon ko.", "Mikaon ko.", "Moadto ko."]);
    expect(line.hidden).toBe("Miadto ko.");
    expect(line.english).toBe("I went.");

    const sub = sayLine(
      makeGradePlanCard({
        kind: "drill",
        drill: { ...drill, kind: "substitution", before_cue: null },
      }),
      null,
    );
    expect(sub.prompt).toEqual(["Mikaon ko.", "Moadto ko."]);
  });
});

describe("gradeHref / safeBack", () => {
  it("builds the Grade page's link with where to come back to and the lesson's name", () => {
    expect(gradeHref("l1", "/c/c1/l/l1", "Wolff 1 & 2")).toBe(
      "/grade/l1?back=%2Fc%2Fc1%2Fl%2Fl1&title=Wolff+1+%26+2",
    );
  });

  it("accepts only a path on this site as the way back", () => {
    expect(safeBack("/c/c1/l/l1")).toBe("/c/c1/l/l1");
    expect(safeBack(null)).toBe("/");
    expect(safeBack("https://evil.example")).toBe("/");
    expect(safeBack("//evil.example")).toBe("/");
    expect(safeBack("/\\evil.example")).toBe("/");
  });
});
