import { describe, it, expect } from "vitest";
import { buildScenes, fallbackScenes } from "./transcriptScenes";
import type { DialogueLine, LessonDetail } from "./api";

function word(surface: string) {
  return {
    surface,
    lemma: surface.toLowerCase(),
    srs_state: "new",
    srs_item_id: null,
    translation: null,
    collocation_span_id: null,
    collocation_start: false,
    collocation_srs_state: null,
    collocation_lemma: null,
    collocation_translation: null,
    card_type: null,
    active_state: "new",
    active_direction: null,
    is_due: false,
    progress: null,
    inflectable: false,
    inflection_feature: null,
    known_marked: false,
  };
}

function narrator(text: string) {
  return { text, role: "narrator", language_code: "en", voice_id: "v" };
}

function l2(text: string, role = "female-1") {
  return { text, role, language_code: "sl", voice_id: "v" };
}

function baseLesson(overrides: Partial<LessonDetail> = {}): LessonDetail {
  return {
    id: "l1",
    day: 1,
    title: "t",
    language_code: "sl",
    key_phrases: [],
    sections: [],
    ...overrides,
  };
}

describe("buildScenes", () => {
  it("returns empty when there is no natural_speed section", () => {
    const lesson = baseLesson({ sections: [{ type: "slow_speed", phrases: [] }] });
    expect(buildScenes(lesson, [])).toEqual([]);
  });

  it("groups L2 lines under scene headers", () => {
    const lesson = baseLesson({
      sections: [
        {
          type: "natural_speed",
          phrases: [
            narrator("Natural Speed"),
            narrator("At the Airport"),
            l2("zdravo"),
            narrator("At the Hotel"),
            l2("hvala"),
          ],
        },
      ],
    });
    const dialogueLines: DialogueLine[] = [
      { role: "female-1", words: [word("zdravo")] },
      { role: "female-1", words: [word("hvala")] },
    ];
    const scenes = buildScenes(lesson, dialogueLines);
    expect(scenes).toHaveLength(2);
    expect(scenes[0].title).toBe("At the Airport");
    expect(scenes[0].lines[0].naturalText).toBe("zdravo");
    expect(scenes[1].title).toBe("At the Hotel");
    expect(scenes[1].lines[0].naturalText).toBe("hvala");
  });

  it("attaches slow and translated text when present", () => {
    const lesson = baseLesson({
      sections: [
        {
          type: "natural_speed",
          phrases: [narrator("Natural Speed"), narrator("Scene"), l2("zdravo")],
        },
        {
          type: "slow_speed",
          phrases: [narrator("Slow Speed"), l2("zdra...vo")],
        },
        {
          type: "translated",
          phrases: [narrator("Translated"), l2("zdravo"), narrator("Hello")],
        },
      ],
    });
    const scenes = buildScenes(lesson, [{ role: "female-1", words: [word("zdravo")] }]);
    expect(scenes[0].lines[0].slowText).toBe("zdra...vo");
    expect(scenes[0].lines[0].translatedText).toBe("Hello");
  });

  it("handles an awaiting translation at the end of translated section", () => {
    const lesson = baseLesson({
      sections: [
        {
          type: "natural_speed",
          phrases: [narrator("Natural Speed"), l2("zdravo"), l2("hvala")],
        },
        {
          type: "translated",
          // second L2 line has no translation after it
          phrases: [narrator("Translated"), l2("zdravo"), narrator("Hello"), l2("hvala")],
        },
      ],
    });
    const scenes = buildScenes(lesson, [
      { role: "female-1", words: [word("zdravo")] },
      { role: "female-1", words: [word("hvala")] },
    ]);
    expect(scenes[0].lines[0].translatedText).toBe("Hello");
    expect(scenes[0].lines[1].translatedText).toBe("");
  });

  it("handles two consecutive L2 lines (no translation between) in translated section", () => {
    const lesson = baseLesson({
      sections: [
        {
          type: "natural_speed",
          phrases: [narrator("Natural Speed"), l2("zdravo"), l2("hvala")],
        },
        {
          type: "translated",
          phrases: [narrator("Translated"), l2("zdravo"), l2("hvala"), narrator("Thanks")],
        },
      ],
    });
    const scenes = buildScenes(lesson, [
      { role: "female-1", words: [word("zdravo")] },
      { role: "female-1", words: [word("hvala")] },
    ]);
    expect(scenes[0].lines[0].translatedText).toBe("");
    expect(scenes[0].lines[1].translatedText).toBe("Thanks");
  });

  it("defaults slowText and translatedText to empty when those sections are missing", () => {
    const lesson = baseLesson({
      sections: [
        {
          type: "natural_speed",
          phrases: [narrator("Natural Speed"), l2("zdravo")],
        },
      ],
    });
    const scenes = buildScenes(lesson, [{ role: "female-1", words: [word("zdravo")] }]);
    expect(scenes[0].lines[0].slowText).toBe("");
    expect(scenes[0].lines[0].translatedText).toBe("");
  });

  it("falls back to empty words when dialogue lines are shorter than L2 phrases", () => {
    const lesson = baseLesson({
      sections: [
        {
          type: "natural_speed",
          phrases: [narrator("Natural Speed"), l2("zdravo"), l2("hvala")],
        },
      ],
    });
    const scenes = buildScenes(lesson, [{ role: "female-1", words: [word("zdravo")] }]);
    expect(scenes[0].lines[1].words).toEqual([]);
  });

  it("keeps leading L2 lines in a title-less first scene", () => {
    const lesson = baseLesson({
      sections: [
        {
          type: "natural_speed",
          // First L2 phrase appears before any scene label
          phrases: [narrator("Natural Speed"), l2("zdravo"), narrator("Later Scene"), l2("hvala")],
        },
      ],
    });
    const scenes = buildScenes(lesson, [
      { role: "female-1", words: [word("zdravo")] },
      { role: "female-1", words: [word("hvala")] },
    ]);
    expect(scenes).toHaveLength(2);
    expect(scenes[0].title).toBeNull();
    expect(scenes[0].lines[0].naturalText).toBe("zdravo");
    expect(scenes[1].title).toBe("Later Scene");
  });

  it("emits a trailing scene with a title but no lines", () => {
    const lesson = baseLesson({
      sections: [
        {
          type: "natural_speed",
          phrases: [narrator("Natural Speed"), l2("zdravo"), narrator("Empty Scene")],
        },
      ],
    });
    const scenes = buildScenes(lesson, [{ role: "female-1", words: [word("zdravo")] }]);
    expect(scenes).toHaveLength(2);
    expect(scenes[1].title).toBe("Empty Scene");
    expect(scenes[1].lines).toEqual([]);
  });
});

describe("fallbackScenes", () => {
  it("wraps dialogue lines in a single title-less scene", () => {
    const lines: DialogueLine[] = [
      { role: "a", words: [word("x")] },
      { role: "b", words: [word("y")] },
    ];
    const scenes = fallbackScenes(lines);
    expect(scenes).toHaveLength(1);
    expect(scenes[0].title).toBeNull();
    expect(scenes[0].lines).toHaveLength(2);
    expect(scenes[0].lines[0].role).toBe("a");
    expect(scenes[0].lines[1].transcriptIndex).toBe(1);
  });

  it("returns a single empty scene for empty input", () => {
    const scenes = fallbackScenes([]);
    expect(scenes).toHaveLength(1);
    expect(scenes[0].lines).toEqual([]);
  });
});
