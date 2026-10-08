/**
 * The player and the affix drill (bd tunatale-ve4p.5).
 *
 * A drill is a third place the player can be, beside Key Phrases and Dialogue.
 * Two lesson shapes carry one:
 *
 *  - a GRAMMAR lesson is only a drill. There is nothing to switch between, so
 *    the phase row is not drawn, and Speed / English have nothing to act on.
 *  - a story lesson that ends with a drill gets a third phase button.
 *
 * In both, being on the drill is never REMEMBERED as where to open: most
 * lessons have no drill, and a stored "drill" would open the next one on its
 * first section instead of where the learner listens. Every test that touches
 * storage therefore seeds a non-default selection; with the defaults "left
 * alone" and "reset" are the same observation.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import { tick } from "svelte";

import PillSyncHarness from "../../test/PillSyncHarness.svelte";
import { captionBlurPref } from "$lib/stores/captionBlurPref.svelte";
import { playerCollapsedPref } from "$lib/stores/playerCollapsedPref.svelte";
import type { Cue, LessonAudio } from "$lib/api";
import type { PlaybackController } from "$lib/playback/playbackController.svelte";

vi.mock("$lib/api", () => ({
  api: {
    audioUrl: vi.fn((id: string) => `/api/audio/${id}`),
    audioZipUrl: vi.fn((lessonId: string) => `/api/audio/lesson/${lessonId}/zip`),
  },
}));

vi.mock("$lib/sw/prefetch", () => ({
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
});

const SEL_KEY = "lessonPlayerSelection";

function cue(sectionIndex: number, sectionType: string, text: string, kind: Cue["ref"]): Cue {
  return {
    index: 0,
    start_ms: 0,
    end_ms: 800,
    section_index: sectionIndex,
    section_type: sectionType,
    phrase_index: 0,
    role: "narrator",
    language_code: "en",
    text,
    ref: kind,
  };
}

function section(index: number, type: string, text: string, ref: Cue["ref"]) {
  return {
    audio_id: `sec-${type}`,
    section_index: index,
    section_type: type,
    title: type,
    cues: [cue(index, type, text, ref)],
  };
}

const drillSection = (index: number) =>
  section(index, "affix_drill", "Say: went along.", { kind: "drill", target_index: 0 });

// As rendered: the full-timeline manifest opens with the lesson TITLE, which
// belongs to no section. That matters here. A manifest opening on the drill
// cue seeds the controller on the drill before the player mounts, so nothing
// changes after mount and nothing is persisted — and "the stored selection is
// left alone" then passes whether or not the player guards it (measured: that
// fixture let the guard be deleted with this file still green).
const titleCue: Cue = {
  index: 0,
  start_ms: 0,
  end_ms: 900,
  section_index: null,
  section_type: null,
  phrase_index: 0,
  role: "narrator",
  language_code: "en",
  text: "Affix drill: mo- / mi-",
  ref: { kind: "narration" },
};

const grammarLesson: LessonAudio = {
  audio_id: "a-drill",
  lesson_id: "l-drill",
  sections: [drillSection(0)],
  cues: [
    titleCue,
    { ...cue(0, "affix_drill", "Say: went along.", { kind: "drill", target_index: 0 }), index: 1 },
  ],
};

const line: Cue["ref"] = { kind: "line", target_index: 0 };
const storyWithDrill: LessonAudio = {
  audio_id: "a-story",
  lesson_id: "l-story",
  sections: [
    section(0, "key_phrases", "Hello", { kind: "key_phrase", target_index: 0 }),
    section(1, "natural_speed", "Naglakaw si Paul.", line),
    section(2, "translated", "Naglakaw si Paul.", line),
    section(3, "slow_speed", "Naglakaw si Paul.", line),
    section(4, "slow_translated", "Naglakaw si Paul.", line),
    drillSection(5),
  ],
  cues: [cue(1, "natural_speed", "Naglakaw si Paul.", line)],
};

async function mount(audio: LessonAudio) {
  let ctrl!: PlaybackController;
  const result = render(PillSyncHarness, {
    props: { audio, onController: (c: PlaybackController) => (ctrl = c) },
  });
  await tick();
  return { ctrl, ...result };
}

const stored = () => JSON.parse(localStorage.getItem(SEL_KEY) ?? "null");
const phaseButtons = (container: HTMLElement) =>
  [...container.querySelectorAll<HTMLButtonElement>(".phase-btn")].map((b) =>
    b.textContent?.trim(),
  );

beforeEach(() => {
  localStorage.clear();
  captionBlurPref.set(true);
  playerCollapsedPref.set(false);
  localStorage.clear();
});

describe("a grammar lesson: the drill is the whole lesson", () => {
  it("plays the drill, whatever phase the learner last listened in", async () => {
    localStorage.setItem(
      SEL_KEY,
      JSON.stringify({ phase: "key_phrases", enunciation: "natural", english: "off" }),
    );

    const { ctrl } = await mount(grammarLesson);

    expect(ctrl.activeSectionType).toBe("affix_drill");
  });

  it("draws no phase row: there is nothing to switch between", async () => {
    const { container } = await mount(grammarLesson);

    expect(container.querySelector(".phase-row")).toBeNull();
  });

  it("offers no Speed or English, and keeps Captions and Hands-free", async () => {
    const { container } = await mount(grammarLesson);

    expect(container.querySelector(".enunciation-btn")).toBeNull();
    expect(container.querySelector(".english-btn")).toBeNull();
    expect(container.querySelector(".caption-blur-btn")).toBeTruthy();
    expect(container.querySelector(".hands-free-toggle")).toBeTruthy();
  });

  it("leaves the remembered selection exactly as it was", async () => {
    const before = { phase: "key_phrases", enunciation: "enunciated_0.9", english: "l2_first" };
    localStorage.setItem(SEL_KEY, JSON.stringify(before));

    await mount(grammarLesson);
    await tick();

    expect(stored()).toEqual(before);
  });
});

describe("a story lesson that ends with a drill", () => {
  it("gets a third phase, after Key Phrases and Dialogue", async () => {
    const { container } = await mount(storyWithDrill);

    expect(phaseButtons(container)).toEqual(["Key Phrases", "Dialogue", "Drill"]);
  });

  it("a lesson with no drill keeps its two phases", async () => {
    const noDrill = { ...storyWithDrill, sections: storyWithDrill.sections.slice(0, 5) };

    const { container } = await mount(noDrill);

    expect(phaseButtons(container)).toEqual(["Key Phrases", "Dialogue"]);
  });

  it("the Drill button plays the drill and lights up", async () => {
    const { container, ctrl, getByRole } = await mount(storyWithDrill);

    await fireEvent.click(getByRole("button", { name: "Drill" }));

    expect(ctrl.activeSectionType).toBe("affix_drill");
    expect(container.querySelector(".phase-btn.active")?.textContent?.trim()).toBe("Drill");
  });

  it("the Drill button follows a drill started from outside the player", async () => {
    const { container, ctrl } = await mount(storyWithDrill);

    ctrl.selectTrack("affix_drill");
    await tick();

    expect(container.querySelector(".phase-btn.active")?.textContent?.trim()).toBe("Drill");
  });

  it("Speed and English are switched off on the drill, and a tap changes nothing", async () => {
    const { container, ctrl, getByRole } = await mount(storyWithDrill);
    await fireEvent.click(getByRole("button", { name: "Drill" }));
    const speed = container.querySelector<HTMLButtonElement>(".enunciation-btn")!;
    const english = container.querySelector<HTMLButtonElement>(".english-btn")!;

    expect([speed.disabled, english.disabled]).toEqual([true, true]);
    await fireEvent.click(speed);
    await fireEvent.click(english);

    expect(speed.textContent).toContain("Natural");
    expect(english.textContent).toContain("Off");
    expect(ctrl.activeSectionType).toBe("affix_drill");
  });

  it("going to the drill does not change where the next lesson opens", async () => {
    const before = { phase: "dialogue", enunciation: "enunciated", english: "l2_first" };
    localStorage.setItem(SEL_KEY, JSON.stringify(before));
    const { getByRole } = await mount(storyWithDrill);

    await fireEvent.click(getByRole("button", { name: "Drill" }));
    await tick();

    expect(stored()).toEqual(before);
  });

  it("coming back from the drill to Dialogue is remembered as usual", async () => {
    localStorage.setItem(
      SEL_KEY,
      JSON.stringify({ phase: "key_phrases", enunciation: "natural", english: "off" }),
    );
    const { ctrl, getByRole } = await mount(storyWithDrill);
    await fireEvent.click(getByRole("button", { name: "Drill" }));

    await fireEvent.click(getByRole("button", { name: "Dialogue" }));
    await tick();

    expect(ctrl.activeSectionType).toBe("natural_speed");
    expect(stored().phase).toBe("dialogue");
  });
});
