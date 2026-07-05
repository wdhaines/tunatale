import { describe, it, expect } from "vitest";
import {
  buildScenes,
  fallbackScenes,
  cueHighlight,
  findSeekCue,
  findKeyPhraseSeekCue,
} from "./transcriptScenes";
import type { Cue, CueRef, DialogueLine, LessonDetail } from "./api";

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

function makeCue(overrides: Partial<Cue> & { index: number }): Cue {
  return {
    start_ms: 0,
    end_ms: 1000,
    section_index: 0,
    section_type: "natural_speed",
    phrase_index: 0,
    role: "narrator",
    language_code: "en",
    text: "test",
    ref: null,
    ...overrides,
  };
}

describe("cueHighlight", () => {
  it("returns ref for a line cue", () => {
    const cue = makeCue({ index: 0, ref: { kind: "line", target_index: 3 } });
    expect(cueHighlight(cue)).toEqual({ kind: "line", target_index: 3 });
  });

  it("returns ref for a key_phrase cue", () => {
    const cue = makeCue({ index: 0, ref: { kind: "key_phrase", target_index: 1 } });
    expect(cueHighlight(cue)).toEqual({ kind: "key_phrase", target_index: 1 });
  });

  it("returns null for a narration cue", () => {
    const cue = makeCue({ index: 0, ref: { kind: "narration", target_index: 0 } });
    expect(cueHighlight(cue)).toBeNull();
  });

  it("returns null when ref is null", () => {
    const cue = makeCue({ index: 0, ref: null });
    expect(cueHighlight(cue)).toBeNull();
  });

  it("returns null when cue is null", () => {
    expect(cueHighlight(null)).toBeNull();
  });
});

describe("findSeekCue", () => {
  const cues: Cue[] = [
    makeCue({
      index: 0,
      start_ms: 0,
      section_index: 0,
      section_type: "natural_speed",
      ref: { kind: "line", target_index: 0 },
    }),
    makeCue({
      index: 1,
      start_ms: 1000,
      section_index: 1,
      section_type: "slow_speed",
      ref: { kind: "line", target_index: 0 },
    }),
    makeCue({
      index: 2,
      start_ms: 2000,
      section_index: 2,
      section_type: "translated",
      ref: { kind: "line", target_index: 0 },
    }),
    makeCue({
      index: 3,
      start_ms: 3000,
      section_index: 1,
      section_type: "slow_speed",
      ref: { kind: "line", target_index: 1 },
    }),
  ];

  it("returns the cue in the matching section when currentSectionIndex is provided", () => {
    const result = findSeekCue(cues, 0, 1);
    expect(result).not.toBeNull();
    expect(result!.index).toBe(1);
    expect(result!.section_type).toBe("slow_speed");
  });

  it("returns the first occurrence when currentSectionIndex is null", () => {
    const result = findSeekCue(cues, 0, null);
    expect(result).not.toBeNull();
    expect(result!.index).toBe(0);
  });

  it("returns the first occurrence when no cue matches the section", () => {
    const result = findSeekCue(cues, 0, 999);
    expect(result).not.toBeNull();
    expect(result!.index).toBe(0);
  });

  it("returns null when no cue matches the lineIndex", () => {
    const result = findSeekCue(cues, 42, null);
    expect(result).toBeNull();
  });

  it("returns the FIRST cue of the line's group within the current section", () => {
    // Translated section: line n has TWO cues refing it — the L2 phrase, then
    // the narrator's English translation. Tap-to-seek must land on the L2
    // phrase (group start), not the translation.
    const translatedCues: Cue[] = [
      makeCue({
        index: 10,
        start_ms: 10_000,
        section_index: 3,
        section_type: "translated",
        language_code: "sl",
        role: "female-1",
        ref: { kind: "line", target_index: 0 },
      }),
      makeCue({
        index: 11,
        start_ms: 12_000,
        section_index: 3,
        section_type: "translated",
        language_code: "en",
        role: "narrator",
        ref: { kind: "line", target_index: 0 },
      }),
    ];
    const result = findSeekCue(translatedCues, 0, 3);
    expect(result).not.toBeNull();
    expect(result!.index).toBe(10);
  });

  it("returns null for empty cues array", () => {
    const result = findSeekCue([], 0, null);
    expect(result).toBeNull();
  });

  it("returns null for lineIndex that never appears", () => {
    const result = findSeekCue(cues, 5, 0);
    expect(result).toBeNull();
  });
});

describe("findKeyPhraseSeekCue", () => {
  const cues: Cue[] = [
    makeCue({ index: 0, start_ms: 0, ref: { kind: "key_phrase", target_index: 0 } }),
    makeCue({ index: 1, start_ms: 500, ref: { kind: "key_phrase", target_index: 0 } }),
    makeCue({ index: 2, start_ms: 1000, ref: { kind: "key_phrase", target_index: 1 } }),
  ];

  it("returns the first cue matching the key_phrase index", () => {
    const result = findKeyPhraseSeekCue(cues, 0);
    expect(result).not.toBeNull();
    expect(result!.index).toBe(0);
  });

  it("returns null when no cue matches", () => {
    const result = findKeyPhraseSeekCue(cues, 5);
    expect(result).toBeNull();
  });

  it("returns null for empty cues array", () => {
    const result = findKeyPhraseSeekCue([], 0);
    expect(result).toBeNull();
  });
});
