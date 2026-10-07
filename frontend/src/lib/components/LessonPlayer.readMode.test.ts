/**
 * What the player does in Read mode (bd tunatale-685k).
 *
 * The user, 2026-10-07: "In Read mode, you can listen to the text being read,
 * but there are not fine-grained speed controls and it does not read out loud
 * in English. Blurred captions are not relevant, nor is mic or hands free."
 * Then, on the two questions that left open: Read is "always natural speed with
 * no spoken English"; Hands-free is "ignored while in Read, setting kept"; and
 * "tie screen wake to hands-free" now that the Mic chip is gone.
 *
 * So Read is a VIEW of the player, not a second set of settings: nothing the
 * learner chose in Listen is overwritten by reading, and all of it applies
 * again on switching back. Every test here seeds a non-default Listen choice
 * for that reason; with the defaults, "Read forces natural" and "Read changes
 * nothing" are the same observation.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import { tick } from "svelte";

import PillSyncHarness from "../../test/PillSyncHarness.svelte";
import { captionBlurPref } from "$lib/stores/captionBlurPref.svelte";
import { playerCollapsedPref } from "$lib/stores/playerCollapsedPref.svelte";
import type { Cue, LessonAudio } from "$lib/api";
import type { PlaybackController } from "$lib/playback/playbackController.svelte";

const { wakeSync } = vi.hoisted(() => ({
  wakeSync: vi.fn((_enabled: boolean) => Promise.resolve()),
}));

vi.mock("$lib/voice/wakeLock", () => ({
  createWakeLock: () => ({
    sync: (enabled: boolean) => wakeSync(enabled),
    release: vi.fn(() => Promise.resolve()),
  }),
}));

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

const SEL_KEY = "lessonPlayerSelection";
const HF_KEY = "handsFree";
const BATON = "handsFreeHandoff";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  captionBlurPref.set(true);
  playerCollapsedPref.set(false);
  localStorage.clear();
  wakeSync.mockClear();
});

function cue(sectionIndex: number, sectionType: string, kind: "key_phrase" | "line"): Cue {
  return {
    index: 0,
    start_ms: 0,
    end_ms: 800,
    section_index: sectionIndex,
    section_type: sectionType,
    phrase_index: 0,
    role: "speaker",
    language_code: "no",
    text: "God morgen",
    ref: { kind, target_index: 0 },
  };
}

const SECTIONS = ["key_phrases", "natural_speed", "translated", "slow_speed", "slow_translated"];

const audio: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: SECTIONS.map((type, i) => ({
    audio_id: `s${i + 1}`,
    section_index: i,
    section_type: type,
    title: type,
    cues: [cue(i, type, type === "key_phrases" ? "key_phrase" : "line")],
  })),
  cues: [cue(0, "key_phrases", "key_phrase")],
};

/** A Listen choice that is NOT the default on either axis. */
const LISTEN_CHOICE = { phase: "dialogue", enunciation: "enunciated", english: "l2_first" };
const seedListenChoice = (over: Record<string, string> = {}) =>
  localStorage.setItem(SEL_KEY, JSON.stringify({ ...LISTEN_CHOICE, ...over }));
const storedSelection = () => JSON.parse(localStorage.getItem(SEL_KEY) ?? "null");

function mount(compact: boolean) {
  let ctrl!: PlaybackController;
  const onController = (c: PlaybackController) => {
    ctrl = c;
  };
  const r = render(PillSyncHarness, { props: { audio, compact, onController } });
  return {
    ...r,
    get ctrl() {
      return ctrl;
    },
    async setCompact(next: boolean) {
      await r.rerender({ audio, compact: next, onController });
      await tick();
    },
  };
}

describe("Read mode: which chips the player shows", () => {
  it("shows no Speed, English or Hands-free chip, and keeps the phase row and transport", () => {
    const { container } = mount(true);
    expect(container.querySelector(".enunciation-btn")).toBeNull();
    expect(container.querySelector(".english-btn")).toBeNull();
    expect(container.querySelector(".hands-free-toggle")).toBeNull();
    expect(container.querySelector(".controls-row")).toBeNull();
    expect(container.querySelector(".phase-row")).toBeTruthy();
    expect(container.querySelector(".play-btn")).toBeTruthy();
  });

  it("Listen keeps Speed, English, Captions and Hands-free, and has no Mic chip", () => {
    const { container } = mount(false);
    expect(container.querySelector(".enunciation-btn")).toBeTruthy();
    expect(container.querySelector(".english-btn")).toBeTruthy();
    expect(container.querySelector(".caption-blur-btn")).toBeTruthy();
    expect(container.querySelector(".hands-free-toggle")).toBeTruthy();
    expect(container.querySelector(".voice-btn")).toBeNull();
    const labels = [...container.querySelectorAll(".controls-row .chip-label")].map((e) =>
      e.textContent!.trim(),
    );
    expect(labels.some((l) => /mic/i.test(l))).toBe(false);
    expect(labels).toHaveLength(4);
  });
});

describe("Read mode: what plays", () => {
  it("plays natural speed with no English, whatever Listen last chose", async () => {
    seedListenChoice();
    const m = mount(true);
    await tick();
    expect(m.ctrl.activeSectionType).toBe("natural_speed");
  });

  it("leaves Listen's choice in storage exactly as it was", async () => {
    seedListenChoice();
    mount(true);
    await tick();
    expect(storedSelection()).toEqual(LISTEN_CHOICE);
  });

  it("gives Listen its Speed and English back on switching to it", async () => {
    seedListenChoice();
    const m = mount(true);
    await tick();

    await m.setCompact(false);

    expect(m.ctrl.activeSectionType).toBe("slow_translated");
    expect(m.container.querySelector(".english-btn")!.textContent).toContain("After");
    expect(storedSelection()).toEqual(LISTEN_CHOICE);
  });

  it("drops to natural on switching to Read from a slow English track, saving nothing", async () => {
    seedListenChoice();
    const m = mount(false);
    await tick();
    // The control: Listen really is on the slow English track first.
    expect(m.ctrl.activeSectionType).toBe("slow_translated");

    await m.setCompact(true);

    expect(m.ctrl.activeSectionType).toBe("natural_speed");
    expect(storedSelection()).toEqual(LISTEN_CHOICE);
  });

  it("keeps Key Phrases on Key Phrases", async () => {
    seedListenChoice({ phase: "key_phrases" });
    const m = mount(true);
    await tick();
    expect(m.ctrl.activeSectionType).toBe("key_phrases");
  });

  it("a track picked from the transcript moves the phase, never the stored Speed or English", async () => {
    seedListenChoice();
    const m = mount(true);
    await tick();

    // What a key-phrase ▶ tap in the transcript does.
    m.ctrl.selectTrack("key_phrases");
    await tick();

    expect(
      m.container
        .querySelector<HTMLButtonElement>(".phase-btn:first-child")!
        .classList.contains("active"),
    ).toBe(true);
    expect(storedSelection()).toEqual({ ...LISTEN_CHOICE, phase: "key_phrases" });
  });

  it("the phase buttons still switch tracks, at natural speed", async () => {
    seedListenChoice({ phase: "key_phrases" });
    const m = mount(true);
    await tick();

    await fireEvent.click(m.container.querySelector<HTMLButtonElement>(".phase-btn:last-child")!);
    await tick();

    expect(m.ctrl.activeSectionType).toBe("natural_speed");
    expect(storedSelection()).toEqual(LISTEN_CHOICE);
  });
});

describe("Read mode: hands-free is ignored, and kept", () => {
  it("a stored On does not make the controller hands-free, and stays stored", async () => {
    localStorage.setItem(HF_KEY, "on");
    const m = mount(true);
    await tick();
    expect(m.ctrl.handsFree).toBe(false);
    expect(m.ctrl.repeatLesson).toBe(false);
    expect(localStorage.getItem(HF_KEY)).toBe("on");
  });

  it("applies again on switching to Listen, chip and controller both", async () => {
    localStorage.setItem(HF_KEY, "repeat");
    const m = mount(true);
    await tick();

    await m.setCompact(false);

    expect(m.ctrl.handsFree).toBe(true);
    expect(m.ctrl.repeatLesson).toBe(true);
    expect(m.container.querySelector(".hands-free-toggle")!.textContent).toContain("Repeat");
  });

  it("is suspended on switching to Read, without saving Off", async () => {
    localStorage.setItem(HF_KEY, "repeat");
    const m = mount(false);
    await tick();
    // The control: it really was running in Listen.
    expect(m.ctrl.handsFree).toBe(true);
    expect(m.ctrl.repeatLesson).toBe(true);

    await m.setCompact(true);

    expect(m.ctrl.handsFree).toBe(false);
    expect(m.ctrl.repeatLesson).toBe(false);
    expect(localStorage.getItem(HF_KEY)).toBe("repeat");
  });

  it("a hand-off that arrives in Read is used up and starts nothing", async () => {
    localStorage.setItem(HF_KEY, "on");
    sessionStorage.setItem(BATON, "1");
    const m = mount(true);
    await tick();
    expect(m.ctrl.playing).toBe(false);
    // Still consumed, or it would fire on some later, unrelated mount.
    expect(sessionStorage.getItem(BATON)).toBeNull();
  });

  it("the same hand-off in Listen does start playback (the control for the test above)", async () => {
    localStorage.setItem(HF_KEY, "on");
    sessionStorage.setItem(BATON, "1");
    const m = mount(false);
    await tick();
    expect(m.ctrl.playing).toBe(true);
  });
});

describe("the screen wake lock follows hands-free, not a Mic setting", () => {
  const lastWake = () => wakeSync.mock.calls.at(-1)?.[0];

  it("is off in Listen with Hands-free off, on once it is switched on", async () => {
    const m = mount(false);
    await tick();
    expect(lastWake()).toBe(false);

    await fireEvent.click(m.container.querySelector<HTMLButtonElement>(".hands-free-toggle")!);
    await tick();

    expect(lastWake()).toBe(true);
  });

  it("is released in Read and taken again back in Listen", async () => {
    localStorage.setItem(HF_KEY, "on");
    const m = mount(false);
    await tick();
    expect(lastWake()).toBe(true);

    await m.setCompact(true);
    expect(lastWake()).toBe(false);

    await m.setCompact(false);
    expect(lastWake()).toBe(true);
  });

  it("a stored Mic setting no longer keeps the screen awake", async () => {
    localStorage.setItem("voice", "on");
    mount(false);
    await tick();
    expect(lastWake()).toBe(false);
  });
});
