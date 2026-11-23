/**
 * The line on Listen naming what is playing (tunatale-cs3p slice 4, oracle L1/L2).
 *
 * The owner's one approved change to Listen (2026-11-13): a line such as
 * "Drill 1 of 2 · Nia siya dinhi." Everything else stays as it was, so these
 * tests also pin the controls the line sits beside. Cue shapes are copied from
 * the live Wolff practice manifests; the rule itself is unit-tested in
 * `playback/__tests__/sectionLine.test.ts`.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render } from "@testing-library/svelte";
import { tick } from "svelte";

import PillSyncHarness from "../../test/PillSyncHarness.svelte";
import { captionBlurPref } from "#lib/stores/captionBlurPref.svelte.js";
import { playerCollapsedPref } from "#lib/stores/playerCollapsedPref.svelte.js";
import type { Cue, LessonAudio } from "#lib/api.js";
import type { PlaybackController } from "#lib/playback/playbackController.svelte.js";

vi.mock("#lib/api.js", () => ({
  api: {
    audioUrl: vi.fn((id: string) => `/api/audio/${id}`),
    audioZipUrl: vi.fn((lessonId: string) => `/api/audio/lesson/${lessonId}/zip`),
  },
}));

vi.mock("#lib/sw/prefetch.js", () => ({
  maybePrefetchLesson: vi.fn(() => Promise.resolve()),
}));

beforeAll(() => {
  vi.spyOn(HTMLAudioElement.prototype, "play").mockImplementation(function (
    this: HTMLAudioElement,
  ) {
    this.dispatchEvent(new Event("play"));
    return Promise.resolve();
  });
  vi.spyOn(HTMLAudioElement.prototype, "pause").mockImplementation(function (
    this: HTMLAudioElement,
  ) {
    this.dispatchEvent(new Event("pause"));
  });
  Object.defineProperty(HTMLAudioElement.prototype, "currentTime", {
    get() {
      return (this as Record<string, unknown>).__tt_currentTime ?? 0;
    },
    set(v: number) {
      (this as Record<string, unknown>).__tt_currentTime = v;
      this.dispatchEvent(new Event("timeupdate"));
    },
    configurable: true,
  });
});

beforeEach(() => {
  localStorage.clear();
  captionBlurPref.set(true);
  playerCollapsedPref.set(false);
});

function cue(
  i: number,
  sectionIndex: number,
  sectionType: string,
  phraseIndex: number,
  role: string,
  lang: string,
  text: string,
): Cue {
  return {
    index: i,
    start_ms: i * 1000,
    end_ms: i * 1000 + 900,
    section_index: sectionIndex,
    section_type: sectionType,
    phrase_index: phraseIndex,
    role,
    language_code: lang,
    text,
    ref: role === "narrator" ? { kind: "narration" } : { kind: "drill", target_index: 0 },
  };
}

const drillCues: Cue[] = [
  cue(0, 0, "affix_drill", 0, "narrator", "en", "Drill"),
  cue(1, 0, "affix_drill", 1, "narrator", "en", "Change the sentence."),
  cue(2, 0, "affix_drill", 2, "model", "ceb", "Nia siya dinhi."),
  cue(3, 0, "affix_drill", 3, "prompt", "ceb", "ako"),
  cue(4, 0, "affix_drill", 4, "answer", "ceb", "Nia ko dinhi."),
  cue(5, 0, "affix_drill", 5, "narrator", "en", "Answer the question."),
  cue(6, 0, "affix_drill", 6, "prompt", "ceb", "Kinsa ang dinhi?"),
  cue(7, 0, "affix_drill", 7, "answer", "ceb", "Si Inting."),
];

const practiceLesson: LessonAudio = {
  audio_id: "a-practice",
  lesson_id: "l-practice",
  sections: [
    {
      audio_id: "sec-drill",
      section_index: 0,
      section_type: "affix_drill",
      title: "Drill",
      cues: drillCues,
    },
  ],
  cues: drillCues,
};

async function mount(audio: LessonAudio, compact = false) {
  let ctrl!: PlaybackController;
  const result = render(PillSyncHarness, {
    props: { audio, compact, onController: (c: PlaybackController) => (ctrl = c) },
  });
  await tick();
  return { ctrl, ...result };
}

const lineText = (container: HTMLElement) =>
  container.querySelector(".section-line")?.textContent?.trim() ?? null;

async function playAt(ctrl: PlaybackController, seconds: number) {
  ctrl.seekTo(seconds);
  await tick();
}

describe("the section line on Listen", () => {
  it("names each drill of a practice lesson as it plays (L2)", async () => {
    const { ctrl, container } = await mount(practiceLesson);

    await playAt(ctrl, 0.1);
    expect(lineText(container)).toBe("Drill");
    await playAt(ctrl, 3.1);
    expect(lineText(container)).toBe("Drill 1 of 2 · Nia siya dinhi.");
    await playAt(ctrl, 6.1);
    expect(lineText(container)).toBe("Drill 2 of 2 · Answer the question.");
  });

  it("is text, not a control: the buttons are the same names in the same order (L1)", async () => {
    const { ctrl, container } = await mount(practiceLesson);
    await playAt(ctrl, 3.1);

    expect(container.querySelector(".section-line button, .section-line input")).toBeNull();
    const names = [...container.querySelectorAll("button, input")].map(
      (el) => el.getAttribute("aria-label") ?? el.getAttribute("title") ?? el.textContent?.trim(),
    );
    // Measured on this lesson BEFORE the line existed (2026-11-23). The "" is
    // the scrubber, unnamed today; the last is the blurred caption, titled
    // with the cue playing.
    expect(names).toEqual([
      "Rewind 10s",
      "Play",
      "Forward 10s",
      "Restart section",
      "Previous sentence",
      "Repeat current",
      "Next sentence",
      "Next section",
      "",
      "Captions Blurred",
      expect.stringMatching(/^Plays the lesson in order without input/),
      "ako",
    ]);
  });

  it("is not drawn in Read (compact), where the transcript does that job", async () => {
    const { ctrl, container } = await mount(practiceLesson, true);
    await playAt(ctrl, 3.1);

    expect(container.querySelector(".section-line")).toBeNull();
  });
});
