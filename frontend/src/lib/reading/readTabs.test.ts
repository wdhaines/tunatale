import { describe, expect, it, vi } from "vitest";
import type { Cue, LessonAudio, ReadableLesson } from "#lib/api.js";
import type { PlaybackController } from "#lib/playback/playbackController.svelte.js";
import { lessonDrills, readTabs, seekForTab, tabOrOutline } from "./readTabs.js";

// The Read side's tab bar (tunatale-cs3p slice 3, the owner's Option A of
// 2026-11-24): Outline, one tab per part of the lesson, then Grade. A practice
// lesson's drills share ONE recording, so a drill tab is a cue range in it.

let next = 0;
function cue(
  role: string,
  text: string,
  language_code = "ceb",
  target = 0,
  section_type = "affix_drill",
): Cue {
  const i = next++;
  return {
    index: i,
    start_ms: i * 1000,
    end_ms: i * 1000 + 900,
    section_index: 0,
    section_type,
    phrase_index: i,
    role,
    language_code,
    text,
    ref:
      role === "narrator" && target === -1
        ? { kind: "narration" }
        : { kind: "drill", target_index: target },
  } as Cue;
}

function practiceAudio(): LessonAudio {
  const cues = [
    cue("narrator", "Drill", "en", -1),
    cue("narrator", "Change the sentence.", "en", 0),
    cue("model", "Nia siya dinhi.", "ceb", 0),
    cue("prompt", "si Huwan", "ceb", 1),
    cue("answer", "Nia si Huwan dinhi.", "ceb", 1),
    cue("narrator", "Change the sentence.", "en", 2),
    cue("model", "Ibilin ko kining akong amigo dinhi.", "ceb", 2),
    cue("prompt", "kana", "ceb", 3),
    cue("answer", "Ibilin ko kanang akong amigo dinhi.", "ceb", 3),
  ];
  return {
    audio_id: "a1",
    lesson_id: "l01p1",
    sections: [
      { audio_id: "s0", section_index: 0, section_type: "affix_drill", title: "Drill", cues },
    ],
    cues,
  };
}

function storyAudio(): LessonAudio {
  const line = (t: string) => cue("line", t, "ceb", 0, "natural_speed");
  return {
    audio_id: "a2",
    lesson_id: "l01a",
    sections: [
      {
        audio_id: "k",
        section_index: 0,
        section_type: "key_phrases",
        title: "Key Phrases",
        cues: [line("Maayong buntag.")],
      },
      {
        audio_id: "n",
        section_index: 1,
        section_type: "natural_speed",
        title: "Natural",
        cues: [line("Kumusta ka?")],
      },
    ],
    cues: [line("Kumusta ka?")],
  };
}

const story = {
  id: "l01a",
  language_code: "ceb",
  sections: [{ type: "key_phrases" }, { type: "natural_speed" }],
  key_phrases: [{ phrase: "Maayong buntag.", translation: "Good morning." }],
  drill: null,
} as unknown as ReadableLesson;

const practice = {
  id: "l01p1",
  language_code: "ceb",
  sections: [{ type: "affix_drill" }],
  key_phrases: [],
  drill: null,
} as unknown as ReadableLesson;

describe("readTabs", () => {
  it("gives a dialogue lesson Outline, Key phrases, Dialogue, then Grade (R2)", () => {
    const tabs = readTabs(story, [], "/grade/l01a");
    expect(tabs.map((t) => t.id)).toEqual(["outline", "key_phrases", "dialogue", "grade"]);
    expect(tabs.at(-1)).toMatchObject({ kind: "grade", href: "/grade/l01a" });
  });

  it("adds the closing drill of a story lesson as one Drill tab", () => {
    const withDrill = { ...story, drill: { roots: [] } } as unknown as ReadableLesson;
    expect(readTabs(withDrill, [], null).map((t) => t.id)).toEqual([
      "outline",
      "key_phrases",
      "dialogue",
      "drill",
    ]);
  });

  it("gives a practice lesson one tab per drill, numbered (R3)", () => {
    const drills = lessonDrills(practiceAudio());
    const tabs = readTabs(practice, drills, "/grade/l01p1");
    expect(tabs.map((t) => [t.id, t.label])).toEqual([
      ["outline", "Outline"],
      ["drill-1", "Drill 1"],
      ["drill-2", "Drill 2"],
      ["grade", "Grade"],
    ]);
  });

  it("has no Grade tab where there is no grade page", () => {
    expect(readTabs(story, [], null).map((t) => t.id)).not.toContain("grade");
  });

  it("leaves out Key phrases on a story lesson without any", () => {
    const bare = {
      ...story,
      key_phrases: [],
      sections: [{ type: "natural_speed" }],
    } as unknown as ReadableLesson;
    expect(readTabs(bare, [], null).map((t) => t.id)).toEqual(["outline", "dialogue"]);
  });

  it("finds Key phrases from the lesson's sections when its list is empty", () => {
    const listless = { ...story, key_phrases: [] } as unknown as ReadableLesson;
    expect(readTabs(listless, [], null).map((t) => t.id)).toContain("key_phrases");
  });
});

describe("lessonDrills", () => {
  it("reads the drills from the one drill recording's cues", () => {
    const drills = lessonDrills(practiceAudio());
    expect(drills.map((d) => [d.label, d.steps.length])).toEqual([
      ["Nia siya dinhi.", 1],
      ["Ibilin ko kining akong amigo dinhi.", 1],
    ]);
  });

  it("is empty with no audio, or no drill recording", () => {
    expect(lessonDrills(null)).toEqual([]);
    expect(lessonDrills(storyAudio())).toEqual([]);
  });
});

describe("tabOrOutline", () => {
  it("keeps a remembered tab the lesson still has, else falls back to Outline", () => {
    const tabs = readTabs(practice, lessonDrills(practiceAudio()), null);
    expect(tabOrOutline(tabs, "drill-2").id).toBe("drill-2");
    expect(tabOrOutline(tabs, "drill-9").id).toBe("outline");
    expect(tabOrOutline(tabs, "grade").id).toBe("outline"); // a link, never a panel
  });
});

function fakeController(active: string | null): PlaybackController {
  return {
    activeSectionType: active,
    selectTrack: vi.fn(),
    seekToCue: vi.fn(),
  } as unknown as PlaybackController;
}

describe("seekForTab", () => {
  it("jumps a drill tab to that drill's opener within the drill recording", () => {
    const audio = practiceAudio();
    const tabs = readTabs(practice, lessonDrills(audio), null);
    const ctrl = fakeController("affix_drill");
    seekForTab(tabs[2], ctrl, audio);
    expect(ctrl.seekToCue).toHaveBeenCalledWith(audio.sections[0].cues![5]);
    expect(ctrl.selectTrack).not.toHaveBeenCalled();
  });

  it("switches to the drill recording at the opener from another track", () => {
    const audio = practiceAudio();
    const tabs = readTabs(practice, lessonDrills(audio), null);
    const ctrl = fakeController(null);
    seekForTab(tabs[1], ctrl, audio);
    expect(ctrl.selectTrack).toHaveBeenCalledWith("affix_drill", {
      kind: "drill",
      target_index: 0,
    });
  });

  it("selects the part's recording for Key phrases and Dialogue (R2)", () => {
    const audio = storyAudio();
    const tabs = readTabs(story, [], null);
    const ctrl = fakeController("natural_speed");
    seekForTab(tabs[1], ctrl, audio);
    expect(ctrl.selectTrack).toHaveBeenCalledWith("key_phrases");
    const onKp = fakeController("key_phrases");
    seekForTab(tabs[2], onKp, audio);
    expect(onKp.selectTrack).toHaveBeenCalledWith("natural_speed");
  });

  it("selects the drill recording for a story lesson's closing drill, once", () => {
    const audio = storyAudio();
    audio.sections.push({
      audio_id: "d",
      section_index: 2,
      section_type: "affix_drill",
      title: "Drill",
      cues: [cue("prompt", "x")],
    });
    const withDrill = { ...story, drill: { roots: [] } } as unknown as ReadableLesson;
    const drillTab = readTabs(withDrill, [], null).at(-1)!;
    const elsewhere = fakeController("natural_speed");
    seekForTab(drillTab, elsewhere, audio);
    expect(elsewhere.selectTrack).toHaveBeenCalledWith("affix_drill");
    const there = fakeController("affix_drill");
    seekForTab(drillTab, there, audio);
    expect(there.selectTrack).not.toHaveBeenCalled();
  });

  it("leaves a dialogue variant already playing alone", () => {
    const audio = storyAudio();
    const ctrl = fakeController("slow_translated");
    seekForTab(readTabs(story, [], null)[2], ctrl, audio);
    expect(ctrl.selectTrack).not.toHaveBeenCalled();
  });

  it("does nothing for Outline, with no player, or on a lesson without per-section cues", () => {
    const audio = storyAudio();
    const ctrl = fakeController("natural_speed");
    seekForTab(readTabs(story, [], null)[0], ctrl, audio);
    seekForTab(readTabs(story, [], null)[1], null, audio);
    const legacy = { ...audio, sections: audio.sections.map((s) => ({ ...s, cues: null })) };
    seekForTab(readTabs(story, [], null)[1], ctrl, legacy);
    // and the full-track manifest is half of the player's test too
    seekForTab(readTabs(story, [], null)[1], ctrl, { ...audio, cues: [] });
    expect(ctrl.selectTrack).not.toHaveBeenCalled();
    expect(ctrl.seekToCue).not.toHaveBeenCalled();
  });
});
