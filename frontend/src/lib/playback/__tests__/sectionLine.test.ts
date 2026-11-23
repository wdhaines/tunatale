import { describe, expect, it } from "vitest";
import type { Cue } from "#lib/api.js";
import { sectionLine } from "../sectionLine.js";

// Cue shapes copied from the live manifests (2026-11-23): a story drill section
// opens on the narrator's English title, then each drill on one narrator
// instruction; its prompts are in the lesson's language. A rule-built affix
// drill's prompts are the narrator's English.
let nextIndex = 0;
function cue(
  section_index: number,
  phrase_index: number,
  role: string,
  language_code: string,
  text: string,
  section_type = "affix_drill",
): Cue {
  const i = nextIndex++;
  return {
    index: i,
    start_ms: i * 1000,
    end_ms: i * 1000 + 900,
    section_index,
    section_type,
    phrase_index,
    role,
    language_code,
    text,
    ref: role === "narrator" ? { kind: "narration" } : { kind: "drill", target_index: 0 },
  } as Cue;
}

function storyDrill(section = 0): Cue[] {
  return [
    cue(section, 0, "narrator", "en", "Drill"),
    cue(section, 1, "narrator", "en", "Change the sentence."),
    cue(section, 2, "model", "ceb", "Nia siya dinhi."),
    cue(section, 3, "prompt", "ceb", "ako"),
    cue(section, 4, "answer", "ceb", "Nia ko dinhi."),
    cue(section, 5, "narrator", "en", "Answer the question."),
    cue(section, 6, "prompt", "ceb", "Kinsa ang dinhi?"),
    cue(section, 7, "answer", "ceb", "Si Inting."),
  ];
}

describe("sectionLine", () => {
  it("is empty with no current cue", () => {
    expect(sectionLine(storyDrill(), null, "Drill")).toBe("");
  });

  it("names a non-drill section by its title", () => {
    const cues = [
      cue(1, 0, "narrator", "en", "Natural Speed", "natural_speed"),
      cue(1, 1, "line", "ceb", "Maayong buntag.", "natural_speed"),
    ];
    expect(sectionLine(cues, cues[1], "Natural Speed")).toBe("Natural Speed");
  });

  it("names the section while its title plays, before the first drill", () => {
    const cues = storyDrill();
    expect(sectionLine(cues, cues[0], "Drill")).toBe("Drill");
  });

  it("names a substitution drill by its number and model sentence", () => {
    const cues = storyDrill();
    for (const i of [1, 2, 3, 4]) {
      expect(sectionLine(cues, cues[i], "Drill")).toBe("Drill 1 of 2 · Nia siya dinhi.");
    }
  });

  it("names a drill with no model by its instruction", () => {
    const cues = storyDrill();
    for (const i of [5, 6, 7]) {
      expect(sectionLine(cues, cues[i], "Drill")).toBe("Drill 2 of 2 · Answer the question.");
    }
  });

  it("counts only the current cue's own section in a full manifest", () => {
    const cues = [...storyDrill(0), ...storyDrill(3)];
    const second = cues.slice(8);
    expect(sectionLine(cues, second[6], "Drill")).toBe("Drill 2 of 2 · Answer the question.");
    expect(sectionLine(cues, second[2], "Drill")).toBe("Drill 1 of 2 · Nia siya dinhi.");
  });

  it("leaves a rule-built affix drill as its section title (its prompts are English)", () => {
    const cues = [
      cue(0, 0, "narrator", "en", "Affix Drill"),
      cue(0, 1, "narrator", "en", "Listen and repeat."),
      cue(0, 2, "model", "ceb", "katulog"),
      cue(0, 3, "prompt", "en", "will sleep"),
      cue(0, 4, "answer", "ceb", "matulog"),
    ];
    for (const c of cues) expect(sectionLine(cues, c, "Affix Drill")).toBe("Affix Drill");
  });

  it("falls back to the section title when the controller holds no cues", () => {
    const cues = storyDrill();
    expect(sectionLine(null, cues[3], "Drill")).toBe("Drill");
  });

  it("falls back to the section title when the current cue is not in the list", () => {
    const cues = storyDrill();
    const stray = cue(0, 3, "prompt", "ceb", "ako");
    expect(sectionLine(cues, stray, "Drill")).toBe("Drill");
  });
});
