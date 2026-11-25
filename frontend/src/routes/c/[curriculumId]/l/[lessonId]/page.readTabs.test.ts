/**
 * The lesson's Read side as one tab bar (tunatale-cs3p slice 3, the owner's
 * Option A of 2026-11-24). Oracle rows from
 * .beads-tasks/briefs/design-curriculum-page-2026-11.md: R1 (which side and tab
 * a lesson opens on), R2 (a dialogue lesson's tabs, each selecting its part),
 * R3 (a practice lesson has the switch and an Outline of its drills). R4 (word
 * tap on a text tab) is the page.interactions suite, run on the Dialogue tab.
 *
 * Unlike the older page tests, this file does NOT call openLessonsOnDialogue:
 * the first-open rule is what it tests.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/svelte";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));

vi.mock("#lib/api.js", async () => {
  const { createApiMock } = await import("./page-test-helpers");
  return { api: createApiMock() };
});

vi.mock("#lib/stores/pipeline.svelte.js", async () => {
  const { createPipelineMock } = await import("./page-test-helpers");
  return { pipelineStore: createPipelineMock() };
});

import type { Cue, LessonAudio, TranscriptData } from "#lib/api.js";
import { api } from "#lib/api.js";
import { captionBlurPref } from "#lib/stores/captionBlurPref.svelte.js";
import { handsFreePref } from "#lib/stores/handsFreePref.svelte.js";
import { lessonModePref } from "#lib/stores/lessonModePref.svelte.js";
import { lessonSidePref } from "#lib/stores/lessonSidePref.svelte.js";
import { listenedStore } from "#lib/stores/listened.svelte.js";
import { syncStore } from "#lib/stores/sync.svelte.js";
import Page from "./+page.svelte";
import { curriculum, lesson, transcript, stubViewport } from "./page-test-helpers";

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  lessonSidePref.reset();
  stubViewport(true); // a phone: the old global default would have been Listen
  lessonModePref.set("listen");
  localStorage.clear();
  lessonSidePref.reset();
  captionBlurPref.set(true);
  syncStore.notify(null);
  listenedStore.reset?.();
  vi.mocked(api.getCurriculumProgress).mockResolvedValue([]);
});

// The shape of a live Wolff practice lesson's ONE drill recording (l01p1,
// 2026-11-25): the narrator's title, then per drill one instruction, a model,
// and prompt/answer pairs, each pair sharing a drill ref.
let n = 0;
function cue(role: string, text: string, target: number | null, language_code = "ceb"): Cue {
  const i = n++;
  return {
    index: i,
    start_ms: i * 2000,
    end_ms: i * 2000 + 1500,
    section_index: 0,
    section_type: "affix_drill",
    phrase_index: i,
    role,
    language_code,
    text,
    ref: target === null ? { kind: "narration" } : { kind: "drill", target_index: target },
  };
}

const drillCues = [
  cue("narrator", "Drill", null, "en"),
  cue("narrator", "Change the sentence.", 0, "en"),
  cue("model", "Nia siya dinhi.", 0),
  cue("prompt", "si Huwan", 1),
  cue("answer", "Nia si Huwan dinhi.", 1),
  cue("prompt", "sila", 2),
  cue("answer", "Nia sila dinhi.", 2),
  cue("narrator", "Change the sentence.", 3, "en"),
  cue("model", "Ibilin ko kining akong amigo dinhi.", 3),
  cue("prompt", "kana", 4),
  cue("answer", "Ibilin ko kanang akong amigo dinhi.", 4),
];

const practice = {
  ...lesson,
  id: "l01p1",
  title: "Who Is Here (Wolff 1, practice 1)",
  language_code: "ceb",
  sections: [{ type: "affix_drill", phrases: [] }],
  key_phrases: [],
  gloss_entry_count: 0,
};

const practiceAudio = {
  audio_id: "pa",
  lesson_id: "l01p1",
  sections: [
    {
      audio_id: "pd",
      section_index: 0,
      section_type: "affix_drill",
      title: "Drill",
      cues: drillCues,
    },
  ],
  cues: drillCues,
};

function word(surface: string) {
  return {
    lemma: surface.toLowerCase(),
    active_state: "known",
    progress: 1.0,
    surface,
    srs_state: "known",
    srs_item_id: 1,
    translation: null,
    collocation_span_id: null,
    collocation_start: false,
    collocation_srs_state: null,
    collocation_lemma: null,
    collocation_translation: null,
    card_type: "vocab",
    active_direction: null,
    is_due: false,
    inflectable: false,
    inflection_feature: null,
    known_marked: false,
    recognition_state: "known",
    recognition_is_due: false,
  };
}

const storyTranscript = {
  ...transcript,
  dialogue_lines: [
    { role: "A", sentence: "Maayong buntag.", words: [word("Maayong")] },
    { role: "B", sentence: "Kumusta ka?", words: [word("Kumusta")] },
  ],
} as unknown as TranscriptData;

// A story lesson's tracks: per-section cues AND the full-track manifest, which
// together are LessonPlayer's track mode.
const kpCue = {
  ...drillCues[2],
  section_type: "key_phrases",
  ref: { kind: "key_phrase" as const, target_index: 0 },
};
const lineCue = {
  ...drillCues[2],
  section_index: 1,
  section_type: "natural_speed",
  ref: { kind: "line" as const, target_index: 0 },
};
const storyAudio = {
  audio_id: "sa",
  lesson_id: "l1",
  sections: [
    {
      audio_id: "k",
      section_index: 0,
      section_type: "key_phrases",
      title: "Key Phrases",
      cues: [kpCue],
    },
    {
      audio_id: "d",
      section_index: 1,
      section_type: "natural_speed",
      title: "Natural Speed",
      cues: [lineCue],
    },
  ],
  cues: [kpCue, lineCue],
};

function renderLesson(
  lessonData = lesson,
  audio: unknown = null,
  tx: TranscriptData | null = storyTranscript,
) {
  return render(Page, {
    props: {
      data: { curriculum, lesson: lessonData, audio: audio as LessonAudio | null, transcript: tx },
    },
  });
}

const tabNames = (getAllByRole: (r: string) => HTMLElement[]) =>
  getAllByRole("tab").map((t) => t.textContent);

describe("R1: the side and tab a lesson opens on", () => {
  it("opens a never-opened lesson on Read, Outline, even on a phone", () => {
    const { container, getByRole } = renderLesson();
    expect(container.querySelector(".player-header .toggle-pill .active")?.textContent).toBe(
      "Read",
    );
    expect(getByRole("tab", { name: "Outline" }).getAttribute("aria-selected")).toBe("true");
  });

  it("reopens a lesson on Listen after Listen was chosen there, and another lesson on Outline", async () => {
    const first = renderLesson();
    await fireEvent.click(first.getByRole("button", { name: "Listen" }));
    first.unmount();

    const again = renderLesson();
    expect(again.container.querySelector(".toggle-pill .active")?.textContent).toBe("Listen");
    expect(again.queryByRole("tablist")).toBeNull();
    again.unmount();

    const other = renderLesson({ ...lesson, id: "l9" });
    expect(other.getByRole("tab", { name: "Outline" }).getAttribute("aria-selected")).toBe("true");
  });

  it("reopens on the Read tab last used there", async () => {
    const first = renderLesson();
    await fireEvent.click(first.getByRole("tab", { name: "Dialogue" }));
    first.unmount();
    const again = renderLesson();
    expect(again.getByRole("tab", { name: "Dialogue" }).getAttribute("aria-selected")).toBe("true");
    expect(again.getByText("Kumusta")).toBeTruthy();
  });

  it("lands a hands-free hand-off on Listen, where the run goes on, until the learner picks Read", async () => {
    lessonSidePref.set("l1", { mode: "read", tab: "dialogue" });
    handsFreePref.armHandoff();
    const { container, getByRole } = renderLesson(lesson, storyAudio);
    expect(container.querySelector(".toggle-pill .active")?.textContent).toBe("Listen");
    expect(handsFreePref.handoffArmed()).toBe(false); // the player used the baton up

    await fireEvent.click(getByRole("button", { name: "Read" }));
    expect(container.querySelector(".toggle-pill .active")?.textContent).toBe("Read");
    expect(getByRole("tab", { name: "Dialogue" }).getAttribute("aria-selected")).toBe("true");
  });
});

describe("R2: a dialogue lesson's tabs", () => {
  it("are Outline, Key phrases, Dialogue, then Grade, a link to the grade page", () => {
    const { getAllByRole, getByRole } = renderLesson();
    expect(tabNames(getAllByRole)).toEqual(["Outline", "Key phrases", "Dialogue"]);
    const grade = getByRole("tablist").querySelector("a")!;
    expect(grade.textContent).toBe("Grade");
    expect(grade.getAttribute("href")).toMatch(/^\/grade/);
  });

  it("each show their own part of the transcript", async () => {
    const { getByRole, getByText, queryByText } = renderLesson();
    await fireEvent.click(getByRole("tab", { name: "Key phrases" }));
    expect(getByText("a coffee please")).toBeTruthy();
    expect(queryByText("Kumusta")).toBeNull();
    await fireEvent.click(getByRole("tab", { name: "Dialogue" }));
    expect(getByText("Kumusta")).toBeTruthy();
    expect(queryByText("a coffee please")).toBeNull();
  });

  it("has an Outline that counts each part and opens it", async () => {
    const { getByRole, getByText } = renderLesson();
    expect(getByText("1 key phrase")).toBeTruthy();
    expect(getByText("2 lines")).toBeTruthy();
    await fireEvent.click(getByRole("button", { name: /^Dialogue/ }));
    expect(getByRole("tab", { name: "Dialogue" }).getAttribute("aria-selected")).toBe("true");
  });

  it("says nothing of the dialogue's length while the transcript loads", () => {
    vi.mocked(api.getTranscript).mockReturnValue(new Promise(() => {}));
    const { queryByText, getByRole } = renderLesson(lesson, null, null);
    expect(getByRole("button", { name: /^Dialogue/ }).textContent?.trim()).toBe("Dialogue");
    expect(getByRole("button", { name: /^Key phrases/ }).textContent?.trim()).toBe("Key phrases");
    expect(queryByText("2 lines")).toBeNull();
  });

  it("moves the header's word counts into the Outline on Read only", async () => {
    const { container, getByRole } = renderLesson();
    expect(container.querySelector(".player-header .mastery-line")).toBeNull();
    expect(container.querySelector("[role=tabpanel] .mastery-line")).toBeTruthy();
    await fireEvent.click(getByRole("button", { name: "Listen" }));
    expect(container.querySelector(".player-header .mastery-line")).toBeTruthy();
  });
});

describe("R3: a practice lesson", () => {
  it("has the Read / Listen switch and one tab per drill", () => {
    const { getByRole, getAllByRole } = renderLesson(practice, practiceAudio, null);
    expect(getByRole("button", { name: "Read" })).toBeTruthy();
    expect(getByRole("button", { name: "Listen" })).toBeTruthy();
    expect(tabNames(getAllByRole)).toEqual(["Outline", "Drill 1", "Drill 2"]);
  });

  it("has an Outline listing each drill's model, instruction and step count", () => {
    const { getByRole } = renderLesson(practice, practiceAudio, null);
    const first = getByRole("button", { name: /^Drill 1/ });
    expect(first.textContent).toContain("Drill 1 · 2 steps");
    expect(first.textContent).toContain("Nia siya dinhi.");
    expect(first.textContent).toContain("Change the sentence.");
    expect(getByRole("button", { name: /^Drill 2/ }).textContent).toContain("Drill 2 · 1 step");
  });

  it("opens a drill's tab from the Outline, with every step written out and its answer blurred", async () => {
    const { getByRole, container, getByText } = renderLesson(practice, practiceAudio, null);
    await fireEvent.click(getByRole("button", { name: /^Drill 2/ }));
    expect(getByRole("tab", { name: "Drill 2" }).getAttribute("aria-selected")).toBe("true");
    expect(getByText("kana")).toBeTruthy();
    const answer = getByRole("button", { name: "Show the answer" });
    expect(answer.textContent).toBe("Ibilin ko kanang akong amigo dinhi.");
    expect(container.querySelector(".steps")?.textContent).not.toContain("si Huwan");
  });

  it("keeps Listen exactly as it was: no tabs, the full player and its phase-less drill", async () => {
    const { getByRole, queryByRole, container } = renderLesson(practice, practiceAudio, null);
    await fireEvent.click(getByRole("button", { name: "Listen" }));
    expect(queryByRole("tablist")).toBeNull();
    expect(container.querySelector(".player.compact")).toBeNull();
    await waitFor(() => expect(container.querySelector(".player")).toBeTruthy());
  });
});

describe("Read drops the player's own Key Phrases / Dialogue row; Listen keeps it", () => {
  it("on Read the tabs choose the part, and the row is gone", async () => {
    const { container, getByRole } = renderLesson(lesson, storyAudio);
    expect(container.querySelector(".phase-row")).toBeNull();
    await fireEvent.click(getByRole("tab", { name: "Key phrases" }));
    await fireEvent.click(getByRole("tab", { name: "Dialogue" }));
    await fireEvent.click(getByRole("button", { name: "Listen" }));
    expect(container.querySelector(".phase-row")).toBeTruthy();
  });
});
