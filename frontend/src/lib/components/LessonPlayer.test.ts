/**
 * Tests for LessonPlayer.svelte.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import LessonPlayer from "./LessonPlayer.svelte";
import PillSyncHarness from "../../test/PillSyncHarness.svelte";
import { tick } from "svelte";
import { maybePrefetchLesson } from "$lib/sw/prefetch";
import { captionBlurPref } from "$lib/stores/captionBlurPref.svelte";
import type { Cue, LessonAudio } from "$lib/api";
import type { PlaybackController } from "$lib/playback/playbackController.svelte";

beforeAll(() => {
  vi.spyOn(HTMLAudioElement.prototype, "play").mockImplementation(
    function (this: HTMLAudioElement) {
      this.dispatchEvent(new Event("play"));
      return Promise.resolve();
    },
  );
  vi.spyOn(HTMLAudioElement.prototype, "pause").mockImplementation(
    function (this: HTMLAudioElement) {
      this.dispatchEvent(new Event("pause"));
    },
  );
  // Define currentTime so setting it dispatches timeupdate (controllers rely on it).
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

// The player persists its phase/enunciation/English selection to localStorage;
// clear it between tests so a click in one test doesn't seed the next mount.
beforeEach(() => {
  localStorage.clear();
  vi.mocked(maybePrefetchLesson).mockClear();
  captionBlurPref.set(true);
});

vi.mock("$lib/api", () => ({
  api: {
    audioUrl: vi.fn((id: string) => `/api/audio/${id}`),
    audioZipUrl: vi.fn((lessonId: string) => `/api/audio/lesson/${lessonId}/zip`),
  },
}));

vi.mock("$lib/sw/prefetch", () => ({
  maybePrefetchLesson: vi.fn(() => Promise.resolve()),
}));

const audioWithNoSections: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [],
};

const audioWithSections: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [
    { audio_id: "s1", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
    { audio_id: "s2", section_index: 1, section_type: "natural_speed", title: "Natural Speed" },
  ],
};

const audioWithCues: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [
    { audio_id: "s1", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
    { audio_id: "s2", section_index: 1, section_type: "natural_speed", title: "Natural Speed" },
  ],
  cues: [
    {
      index: 0,
      start_ms: 0,
      end_ms: 800,
      section_index: 0,
      section_type: "key_phrases",
      phrase_index: 0,
      role: "narrator",
      language_code: "en",
      text: "Hello world",
      ref: { kind: "key_phrase", target_index: 0 },
    },
  ],
};

const audioWithCuesNull: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [
    { audio_id: "s1", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
  ],
  cues: null,
};

// Post-Phase-A shape: every section row carries its own (rebased) cue manifest.
// The phase/enunciation track model is only active for lessons of this shape.
function sectionCue(sectionIndex: number, sectionType: string, text: string): Cue {
  return {
    index: 0,
    start_ms: 0,
    end_ms: 800,
    section_index: sectionIndex,
    section_type: sectionType,
    phrase_index: 0,
    role: "speaker",
    language_code: "sl",
    text,
    ref: { kind: "line", target_index: 0 },
  };
}

const audioWithAllSections: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [
    {
      audio_id: "s1",
      section_index: 0,
      section_type: "key_phrases",
      title: "Key Phrases",
      cues: [
        {
          index: 0,
          start_ms: 0,
          end_ms: 800,
          section_index: 0,
          section_type: "key_phrases",
          phrase_index: 0,
          role: "narrator",
          language_code: "en",
          text: "Hello world",
          ref: { kind: "key_phrase", target_index: 0 },
        },
      ],
    },
    {
      audio_id: "s2",
      section_index: 1,
      section_type: "natural_speed",
      title: "Natural Speed",
      cues: [sectionCue(1, "natural_speed", "Pozdravljeni")],
    },
    {
      audio_id: "s3",
      section_index: 2,
      section_type: "translated",
      title: "Translated",
      cues: [sectionCue(2, "translated", "Pozdravljeni")],
    },
    {
      audio_id: "s4",
      section_index: 3,
      section_type: "slow_speed",
      title: "Slow Speed",
      cues: [sectionCue(3, "slow_speed", "Pozdravljeni")],
    },
    {
      audio_id: "s5",
      section_index: 4,
      section_type: "slow_translated",
      title: "Slow Translated",
      cues: [sectionCue(4, "slow_translated", "Pozdravljeni")],
    },
  ],
  cues: [
    {
      index: 0,
      start_ms: 0,
      end_ms: 800,
      section_index: 0,
      section_type: "key_phrases",
      phrase_index: 0,
      role: "narrator",
      language_code: "en",
      text: "Hello world",
      ref: { kind: "key_phrase", target_index: 0 },
    },
    {
      index: 1,
      start_ms: 1000,
      end_ms: 2000,
      section_index: 1,
      section_type: "natural_speed",
      phrase_index: 0,
      role: "speaker",
      language_code: "sl",
      text: "Pozdravljeni",
      ref: { kind: "line", target_index: 0 },
    },
  ],
};

// Post-EN-first shape: adds the English-first bilingual sections so the English
// control can cycle through all three states (off → l2_first → en_first).
const audioWithEnFirstSections: LessonAudio = {
  ...audioWithAllSections,
  sections: [
    ...audioWithAllSections.sections,
    {
      audio_id: "s6",
      section_index: 5,
      section_type: "en_translated",
      title: "English Translated",
      cues: [sectionCue(5, "en_translated", "Pozdravljeni")],
    },
    {
      audio_id: "s7",
      section_index: 6,
      section_type: "slow_en_translated",
      title: "Slow English Translated",
      cues: [sectionCue(6, "slow_en_translated", "Pozdravljeni")],
    },
  ],
};

// trackMode but missing natural_speed — exercises the defensive
// !currentUrl guard in computePrefetchUrls.
const audioMissingCurrentSection: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [
    {
      audio_id: "s1",
      section_index: 0,
      section_type: "key_phrases",
      title: "Key Phrases",
      cues: [sectionCue(0, "key_phrases", "Hello")],
    },
    {
      audio_id: "s4",
      section_index: 1,
      section_type: "slow_speed",
      title: "Slow Speed",
      cues: [sectionCue(1, "slow_speed", "Pozdravljeni")],
    },
  ],
  cues: [
    {
      index: 0,
      start_ms: 0,
      end_ms: 800,
      section_index: 0,
      section_type: "key_phrases",
      phrase_index: 0,
      role: "narrator",
      language_code: "en",
      text: "Hello world",
      ref: { kind: "key_phrase", target_index: 0 },
    },
  ],
};

describe("LessonPlayer", () => {
  describe("basic transport", () => {
    it("does not render an audio element (controller owns the only one)", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithNoSections } });
      expect(container.querySelector("audio")).toBeFalsy();
    });

    it("renders transport buttons (play, seek)", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithSections } });
      expect(container.querySelector(".transport-row")).toBeTruthy();
      const buttons = container.querySelectorAll(".ctrl-btn");
      expect(buttons.length).toBeGreaterThanOrEqual(3);
    });

    it("renders play button", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithNoSections } });
      const playBtn = container.querySelector(".play-btn");
      expect(playBtn).toBeTruthy();
    });

    it("renders no section info or current line when cues absent", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCuesNull } });
      expect(container.querySelector(".section-info")).toBeFalsy();
      expect(container.querySelector(".current-line")).toBeFalsy();
    });

    it("renders no section info but renders current line when cues present", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      expect(container.querySelector(".section-info")).toBeFalsy();
      expect(container.querySelector(".current-line")).toBeTruthy();
    });

    it("renders the current line BELOW the controls (sticky-header layout)", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const controlsRow = container.querySelector(".controls-row")!;
      const currentLine = container.querySelector(".current-line")!;
      // current-line after the last control row → subtitle sits nearest the content
      expect(
        controlsRow.compareDocumentPosition(currentLine) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });
  });

  describe("compact mode", () => {
    it("hides only the redundant subtitle line; keeps controls, transport, scrubber", () => {
      // Compact (Read mode) is now identical to Listen EXCEPT it omits the
      // current-line subtitle — the synced transcript is the subtitle there.
      // The phase/enunciation/English controls appear in both modes.
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithAllSections, compact: true },
      });
      expect(container.querySelector(".current-line")).toBeFalsy();
      expect(container.querySelector(".download-section")).toBeFalsy();
      expect(container.querySelector(".phase-row")).toBeTruthy();
      expect(container.querySelector(".transport-row")).toBeTruthy();
      expect(container.querySelector(".scrubber-row")).toBeTruthy();
    });

    it("compact without cues hides cue-driven rows but keeps scrubber", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithCuesNull, compact: true },
      });
      expect(container.querySelector(".section-info")).toBeFalsy();
      expect(container.querySelector(".sentence-row")).toBeFalsy();
      expect(container.querySelector(".scrubber-row")).toBeTruthy();
      expect(container.querySelector(".phase-row")).toBeFalsy();
    });

    it("still renders transport row in compact mode", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithNoSections, compact: true },
      });
      expect(container.querySelector(".transport-row")).toBeTruthy();
      expect(container.querySelector("audio")).toBeFalsy();
    });
  });

  describe("downloads (moved to the lesson page's collapsed tools)", () => {
    it("renders no download UI even when not compact", () => {
      const { container, queryByText } = render(LessonPlayer, {
        props: { audio: audioWithSections },
      });
      expect(container.querySelector(".download-section")).toBeFalsy();
      expect(queryByText("Download All Sections")).toBeFalsy();
    });
  });

  describe("sentence controls", () => {
    it("renders sentence row when cues present and not compact", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      expect(container.querySelector(".sentence-row")).toBeTruthy();
    });

    it("does not render sentence row when cues null", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCuesNull } });
      expect(container.querySelector(".sentence-row")).toBeFalsy();
    });

    it("renders sentence/section toggle showing current mode", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const toggle = container.querySelector(".sentence-skip-toggle");
      expect(toggle).toBeTruthy();
      // Default is Section mode (sentenceSkip = false)
      expect(toggle!.textContent).toContain("Section");
    });

    it("clicking the toggle switches between Sentence and Section modes", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const toggle = container.querySelector<HTMLButtonElement>(".sentence-skip-toggle")!;
      // Default: Section
      expect(toggle.textContent).toContain("Section");
      expect(toggle.textContent).not.toContain("Sentence");
      fireEvent.click(toggle);
      // Now: Sentence
      expect(toggle.textContent).toContain("Sentence");
      fireEvent.click(toggle);
      // Back to Section
      expect(toggle.textContent).toContain("Section");
    });
  });

  describe("scrubber", () => {
    it("renders scrubber when cues present and not compact", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      expect(container.querySelector(".scrubber-row")).toBeTruthy();
      expect(container.querySelector(".scrubber")).toBeTruthy();
    });

    it("renders scrubber even with cues null (not compact)", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCuesNull } });
      expect(container.querySelector(".scrubber-row")).toBeTruthy();
      expect(container.querySelector(".scrubber")).toBeTruthy();
    });
  });

  describe("phase selector", () => {
    it("does not render phase row when cues absent", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCuesNull } });
      expect(container.querySelector(".phase-row")).toBeFalsy();
    });

    it("renders Key Phrases and Dialogue buttons when per-section cues present", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const phaseRow = container.querySelector(".phase-row");
      expect(phaseRow).toBeTruthy();
      const buttons = phaseRow!.querySelectorAll("button");
      expect(buttons.length).toBe(2);
      expect(buttons[0].textContent).toContain("Key Phrases");
      expect(buttons[1].textContent).toContain("Dialogue");
    });

    it("renders the phase row in compact mode too (identical controls)", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithAllSections, compact: true },
      });
      expect(container.querySelector(".phase-row")).toBeTruthy();
    });

    it("legacy lesson (no per-section cues): keeps the full track and hides the phase row", () => {
      // Pre-Phase-A lessons have a full-track manifest but cues=null on every
      // section row. Switching tracks there would strand the player on one
      // section's audio with no cues (dead subtitle + sentence nav), so the
      // phase model must stay off and the legacy full-lesson track must keep
      // playing. Regression: onMount applyTrack() used to fire on hasCues alone.
      const srcSpy = vi.spyOn(HTMLMediaElement.prototype, "src", "set");
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      expect(container.querySelector(".phase-row")).toBeFalsy();
      const srcs = srcSpy.mock.calls.map((c) => c[0]);
      expect(srcs).toContain("/api/audio/a1"); // the full concatenated track
      expect(srcs).not.toContain("/api/audio/s1");
      expect(srcs).not.toContain("/api/audio/s2"); // no silent track switch
      srcSpy.mockRestore();
    });

    it("legacy lesson: subtitle and sentence nav still work off the full-track cues", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      // currentTime 0 → first full-track cue is active; the subtitle must carry
      // real text, not render an empty shell (the pre-fix failure mode).
      expect(container.querySelector(".current-line")!.textContent).toContain("Hello world");
      expect(container.querySelector(".sentence-row")).toBeTruthy();
    });

    it("clicking Key Phrases activates that phase", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const keyPhrasesBtn = container.querySelector<HTMLButtonElement>(".phase-btn:first-child")!;
      expect(keyPhrasesBtn.classList.contains("active")).toBe(false);
      fireEvent.click(keyPhrasesBtn);
      expect(keyPhrasesBtn.classList.contains("active")).toBe(true);
    });

    it("clicking Dialogue activates that phase", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      // Default is 'dialogue' — switch away first, then back
      const keyPhrasesBtn = container.querySelector<HTMLButtonElement>(".phase-btn:first-child")!;
      fireEvent.click(keyPhrasesBtn);
      expect(keyPhrasesBtn.classList.contains("active")).toBe(true);
      const dialogueBtn = container.querySelector<HTMLButtonElement>(".phase-btn:last-child")!;
      expect(dialogueBtn.classList.contains("active")).toBe(false);
      fireEvent.click(dialogueBtn);
      expect(dialogueBtn.classList.contains("active")).toBe(true);
    });
  });

  describe("persisted selection (B6)", () => {
    const KEY = "lessonPlayerSelection";

    it("seeds the persisted phase on mount (no click needed)", () => {
      localStorage.setItem(
        KEY,
        JSON.stringify({ phase: "key_phrases", enunciation: "natural", english: false }),
      );
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const keyPhrasesBtn = container.querySelector<HTMLButtonElement>(".phase-btn:first-child")!;
      expect(keyPhrasesBtn.classList.contains("active")).toBe(true);
    });

    it("seeds a persisted enunciation level on mount", () => {
      localStorage.setItem(
        KEY,
        JSON.stringify({ phase: "dialogue", enunciation: "enunciated_0.8", english: false }),
      );
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      expect(container.querySelector(".enunciation-btn")!.textContent).toContain("0.8");
    });

    it("persists the selection to localStorage on change", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      fireEvent.click(container.querySelector<HTMLButtonElement>(".phase-btn:first-child")!);
      expect(JSON.parse(localStorage.getItem(KEY)!).phase).toBe("key_phrases");
    });

    it("sets the section track src to a real URL, not a bare id", () => {
      // Regression: LessonPlayer must pass sectionUrl to the controller.
      // Default dialogue·natural selects natural_speed (s2) on mount; without
      // the wiring, selectTrack falls back to identity and sets audioEl.src to
      // the bare id "s2" — a broken relative URL that never loads, so play
      // silently does nothing. The prefetch path calls api.audioUrl(s2) either
      // way, so we must observe the actual src the controller assigns.
      const srcSpy = vi.spyOn(HTMLMediaElement.prototype, "src", "set");
      render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const srcs = srcSpy.mock.calls.map((c) => c[0]);
      expect(srcs).toContain("/api/audio/s2");
      expect(srcs).not.toContain("s2");
      srcSpy.mockRestore();
    });

    it("does not seed from storage when cues are absent (legacy full track)", () => {
      localStorage.setItem(
        KEY,
        JSON.stringify({ phase: "key_phrases", enunciation: "natural", english: false }),
      );
      // No cues → no phase controls rendered → nothing seeded/applied.
      const { container } = render(LessonPlayer, { props: { audio: audioWithCuesNull } });
      expect(container.querySelector(".phase-row")).toBeFalsy();
    });
  });

  describe("pills mirror an external track change (transcript ▶)", () => {
    async function renderWithController(audio: LessonAudio) {
      let ctrl: PlaybackController | null = null;
      const result = render(PillSyncHarness, {
        props: {
          audio,
          onController: (c: PlaybackController) => {
            ctrl = c;
          },
        },
      });
      await tick();
      return { ctrl: ctrl as unknown as PlaybackController, ...result };
    }

    it("activates the Key Phrases pill when the track switches to key_phrases externally", async () => {
      const { ctrl, container } = await renderWithController(audioWithAllSections);
      const kpBtn = container.querySelector<HTMLButtonElement>(".phase-btn:first-child")!;
      expect(kpBtn.classList.contains("active")).toBe(false); // default: dialogue

      ctrl.selectTrack("key_phrases"); // as a key-phrase ▶ tap would
      await tick();
      expect(kpBtn.classList.contains("active")).toBe(true);
    });

    it("resets enunciation/English to Natural when the track switches to natural_speed externally", async () => {
      const { ctrl, container } = await renderWithController(audioWithAllSections);
      // Move to an enunciated slow track first via the pill.
      fireEvent.click(container.querySelector<HTMLButtonElement>(".enunciation-btn")!);
      await tick();
      expect(container.querySelector(".enunciation-btn")!.textContent).toContain("Enunciated");

      // An external switch to natural_speed (e.g. tapping a dialogue line ▶ from
      // another phase) must pull the enunciation pill back to Natural.
      ctrl.selectTrack("natural_speed");
      await tick();
      expect(container.querySelector(".enunciation-btn")!.textContent).toContain("Natural");
    });
  });

  describe("enunciation and English controls", () => {
    it("renders enunciation and English controls when all sections present", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      expect(container.querySelector(".enunciation-btn")).toBeTruthy();
      expect(container.querySelector(".english-btn")).toBeTruthy();
    });

    it("renders enunciation and English controls in compact (Read) mode too", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithAllSections, compact: true },
      });
      expect(container.querySelector(".enunciation-btn")).toBeTruthy();
      expect(container.querySelector(".english-btn")).toBeTruthy();
    });

    it("does not render enunciation or English buttons when cues absent", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCuesNull } });
      expect(container.querySelector(".enunciation-btn")).toBeFalsy();
      expect(container.querySelector(".english-btn")).toBeFalsy();
    });

    it("enunciation button shows current label", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const btn = container.querySelector(".enunciation-btn");
      expect(btn!.textContent).toContain("Natural");
    });

    it("english button shows Off by default", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const btn = container.querySelector(".english-btn");
      expect(btn!.textContent).toContain("English");
      expect(btn!.textContent).toContain("Off");
    });

    it("english cycles off → l2-first → en-first → off when en-first audio exists", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithEnFirstSections } });
      const btn = container.querySelector<HTMLButtonElement>(".english-btn")!;
      expect(btn.textContent).toContain("Off");
      fireEvent.click(btn);
      expect(btn.textContent).toContain("After"); // l2_first
      fireEvent.click(btn);
      expect(btn.textContent).toContain("Before"); // en_first
      fireEvent.click(btn);
      expect(btn.textContent).toContain("Off");
    });

    it("english cycle skips en-first when the lesson lacks en-first audio", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const btn = container.querySelector<HTMLButtonElement>(".english-btn")!;
      expect(btn.textContent).toContain("Off");
      fireEvent.click(btn);
      expect(btn.textContent).toContain("After"); // l2_first
      fireEvent.click(btn);
      // No en_first track → cycle wraps straight back to Off.
      expect(btn.textContent).toContain("Off");
      expect(btn.textContent).not.toContain("Before");
    });

    it("selecting en-first switches to the en_translated track", () => {
      const srcSpy = vi.spyOn(HTMLMediaElement.prototype, "src", "set");
      const { container } = render(LessonPlayer, { props: { audio: audioWithEnFirstSections } });
      const btn = container.querySelector<HTMLButtonElement>(".english-btn")!;
      fireEvent.click(btn); // l2_first
      fireEvent.click(btn); // en_first
      const srcs = srcSpy.mock.calls.map((c) => c[0]);
      expect(srcs).toContain("/api/audio/s6"); // en_translated
      srcSpy.mockRestore();
    });

    it("enunciation cycles through 4 states on click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const btn = container.querySelector<HTMLButtonElement>(".enunciation-btn")!;
      // Natural → Enunciated → Enun 0.9× → Enun 0.8× → Natural
      expect(btn.textContent).toContain("Natural");
      fireEvent.click(btn);
      expect(btn.textContent).not.toContain("Natural");
      fireEvent.click(btn);
      expect(btn.textContent).toContain("0.9");
      fireEvent.click(btn);
      expect(btn.textContent).toContain("0.8");
      fireEvent.click(btn);
      expect(btn.textContent).toContain("Natural");
    });

    // Item G: disable in key_phrases phase
    it("enunciation and english are disabled in key_phrases phase", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      // Switch to key_phrases
      const kpBtn = container.querySelector<HTMLButtonElement>(".phase-btn:first-child")!;
      fireEvent.click(kpBtn);
      const enunBtn = container.querySelector<HTMLButtonElement>(".enunciation-btn")!;
      const engBtn = container.querySelector<HTMLButtonElement>(".english-btn")!;
      expect(enunBtn.disabled).toBe(true);
      expect(engBtn.disabled).toBe(true);
    });

    it("enunciation and english are enabled in dialogue phase", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      // Default is dialogue
      const enunBtn = container.querySelector<HTMLButtonElement>(".enunciation-btn")!;
      const engBtn = container.querySelector<HTMLButtonElement>(".english-btn")!;
      expect(enunBtn.disabled).toBe(false);
      expect(engBtn.disabled).toBe(false);
    });

    it("clicking enunciation in key_phrases phase does not change state", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const kpBtn = container.querySelector<HTMLButtonElement>(".phase-btn:first-child")!;
      fireEvent.click(kpBtn);
      const enunBtn = container.querySelector<HTMLButtonElement>(".enunciation-btn")!;
      expect(enunBtn.textContent).toContain("Natural");
      fireEvent.click(enunBtn);
      // Still Natural — disabled, click is a no-op
      expect(enunBtn.textContent).toContain("Natural");
    });

    it("clicking english in key_phrases phase does not change state", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const kpBtn = container.querySelector<HTMLButtonElement>(".phase-btn:first-child")!;
      fireEvent.click(kpBtn);
      const engBtn = container.querySelector<HTMLButtonElement>(".english-btn")!;
      expect(engBtn.textContent).toContain("Off");
      fireEvent.click(engBtn);
      // Still Off — disabled, click is a no-op
      expect(engBtn.textContent).toContain("Off");
    });
  });

  // Item D: section header removed
  describe("section header (Item D)", () => {
    it("does not render .section-info on trackMode+allSections lesson", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      expect(container.querySelector(".section-info")).toBeFalsy();
    });

    it("does not render .section-info on non-trackMode cue-bearing lesson", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      expect(container.querySelector(".section-info")).toBeFalsy();
    });

    it("does not render .section-info in compact mode", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithAllSections, compact: true },
      });
      expect(container.querySelector(".section-info")).toBeFalsy();
    });
  });

  describe("interactions", () => {
    it("fires rewind on button click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>('button[title="Rewind 10s"]')!;
      fireEvent.click(btn);
    });

    it("fires togglePlay on play button click and shows pause SVG", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>(".play-btn")!;
      expect(btn.querySelector("svg")).toBeTruthy();
      fireEvent.click(btn);
      expect(btn.querySelector("svg")).toBeTruthy();
    });

    it("fires forward on button click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>('button[title="Forward 10s"]')!;
      fireEvent.click(btn);
    });

    it("fires restartSection on section-start click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>('button[title="Restart section"]')!;
      expect(btn).toBeTruthy();
      fireEvent.click(btn);
    });

    it("fires prevCue on sentence back click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>('button[title="Previous sentence"]')!;
      expect(btn).toBeTruthy();
      fireEvent.click(btn);
    });

    it("fires repeatCue on repeat click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>('button[title="Repeat current"]')!;
      expect(btn).toBeTruthy();
      fireEvent.click(btn);
    });

    it("fires nextCue on sentence forward click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>('button[title="Next sentence"]')!;
      expect(btn).toBeTruthy();
      fireEvent.click(btn);
    });

    it("toggles sentence skip via the toggle button", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const toggle = container.querySelector<HTMLButtonElement>(".sentence-skip-toggle")!;
      expect(toggle.textContent).toContain("Section");
      fireEvent.click(toggle);
      expect(toggle.textContent).toContain("Sentence");
    });

    it("fires seek on scrubber input", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const scrubber = container.querySelector<HTMLInputElement>(".scrubber")!;
      fireEvent.input(scrubber, { target: { value: "5.0" } });
    });
  });

  describe("bindable controller", () => {
    it("accepts a controller bindable prop without error", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithCues, controller: null },
      });
      expect(container.querySelector(".play-btn")).toBeTruthy();
    });

    it("unmount does not throw (cleanup nulls the controller)", () => {
      const { unmount } = render(LessonPlayer, {
        props: { audio: audioWithCues, controller: null },
      });
      expect(() => unmount()).not.toThrow();
    });
  });

  describe("prefetch URL selection", () => {
    it("trackMode: prefetches current section + enunciation neighbor, not the full track", () => {
      render(LessonPlayer, { props: { audio: audioWithAllSections } });
      // Default: dialogue·natural·englishOff → natural_speed (s2).
      // Enunciation neighbor: enunciated·englishOff → slow_speed (s4).
      expect(vi.mocked(maybePrefetchLesson)).toHaveBeenCalledTimes(1);
      const urls = vi.mocked(maybePrefetchLesson).mock.calls[0][0];
      expect(urls).toContain("/api/audio/s2");
      expect(urls).toContain("/api/audio/s4");
      expect(urls).not.toContain("/api/audio/a1");
    });

    it("legacy mode: prefetches only the full concatenated track", () => {
      render(LessonPlayer, { props: { audio: audioWithCues } });
      expect(vi.mocked(maybePrefetchLesson)).toHaveBeenCalledTimes(1);
      const urls = vi.mocked(maybePrefetchLesson).mock.calls[0][0];
      expect(urls).toEqual(["/api/audio/a1"]);
    });

    it("trackMode with key_phrases: prefetches only key_phrases (no neighbor)", () => {
      localStorage.setItem(
        "lessonPlayerSelection",
        JSON.stringify({ phase: "key_phrases", enunciation: "natural", english: false }),
      );
      render(LessonPlayer, { props: { audio: audioWithAllSections } });
      expect(vi.mocked(maybePrefetchLesson)).toHaveBeenCalledTimes(1);
      const urls = vi.mocked(maybePrefetchLesson).mock.calls[0][0];
      expect(urls).toEqual(["/api/audio/s1"]);
    });

    it("trackMode: falls back to the full track when the resolved section is missing", () => {
      // selectTrack no-ops on a missing section, so the player stays on the
      // full concatenated track — the prefetch must cover what actually plays.
      render(LessonPlayer, { props: { audio: audioMissingCurrentSection } });
      expect(vi.mocked(maybePrefetchLesson)).toHaveBeenCalledTimes(1);
      const urls = vi.mocked(maybePrefetchLesson).mock.calls[0][0];
      expect(urls).toEqual(["/api/audio/a1"]);
    });
  });

  // --- Caption blur (Item A) ---

  const longNarrationText =
    "The sun rose over the quiet village, casting a warm golden light across the cobblestone streets. Birds sang in the ancient oak trees that lined the path to the market square.";

  const audioForBlurToggle: LessonAudio = {
    audio_id: "a1",
    lesson_id: "l1",
    sections: [
      { audio_id: "s1", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
      { audio_id: "s2", section_index: 1, section_type: "natural_speed", title: "Natural Speed" },
    ],
    cues: [
      {
        index: 0,
        start_ms: 0,
        end_ms: 800,
        section_index: 0,
        section_type: "key_phrases",
        phrase_index: 0,
        role: "narrator",
        language_code: "en",
        text: "Hello world",
        ref: { kind: "key_phrase", target_index: 0 },
      },
    ],
  };

  const audioForLongCue: LessonAudio = {
    audio_id: "a1",
    lesson_id: "l1",
    sections: [
      { audio_id: "s1", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
      { audio_id: "s2", section_index: 1, section_type: "natural_speed", title: "Natural Speed" },
    ],
    cues: [
      {
        index: 0,
        start_ms: 0,
        end_ms: 20000,
        section_index: 0,
        section_type: "key_phrases",
        phrase_index: 0,
        role: "narrator",
        language_code: "en",
        text: longNarrationText,
        ref: { kind: "key_phrase", target_index: 0 },
      },
    ],
  };

  const audioForMultiCue: LessonAudio = {
    audio_id: "a1",
    lesson_id: "l1",
    sections: [
      { audio_id: "s1", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
      { audio_id: "s2", section_index: 1, section_type: "natural_speed", title: "Natural Speed" },
    ],
    cues: [
      {
        index: 0,
        start_ms: 0,
        end_ms: 800,
        section_index: 0,
        section_type: "key_phrases",
        phrase_index: 0,
        role: "narrator",
        language_code: "en",
        text: "Hello world",
        ref: { kind: "key_phrase", target_index: 0 },
      },
      {
        index: 1,
        start_ms: 1000,
        end_ms: 2000,
        section_index: 1,
        section_type: "natural_speed",
        phrase_index: 1,
        role: "speaker",
        language_code: "en",
        text: "Goodbye world",
        ref: { kind: "line", target_index: 1 },
      },
    ],
  };

  // Multi-chunk cue for testing Repeat seeks to chunk start (Item B wiring)
  const audioForRepeatChunk: LessonAudio = {
    audio_id: "a1",
    lesson_id: "l1",
    sections: [
      { audio_id: "s1", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
      { audio_id: "s2", section_index: 1, section_type: "natural_speed", title: "Natural Speed" },
    ],
    cues: [
      {
        index: 0,
        start_ms: 0,
        end_ms: 30000,
        section_index: 0,
        section_type: "key_phrases",
        phrase_index: 0,
        role: "narrator",
        language_code: "en",
        text: longNarrationText,
        ref: { kind: "key_phrase", target_index: 0 },
      },
    ],
  };

  describe("caption blur", () => {
    it("setting enabled (default): caption has blur class and is a button; clicking reveals", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const line = container.querySelector(".current-line")!;
      expect(line.classList.contains("blurred")).toBe(true);
      expect(line.tagName).toBe("BUTTON");
      fireEvent.click(line);
      expect(line.classList.contains("revealed")).toBe(true);
    });

    it("keyboard Enter/Space reveals the caption", async () => {
      const { container } = render(LessonPlayer, { props: { audio: audioForMultiCue } });
      const line = container.querySelector(".current-line")!;
      expect(line.classList.contains("blurred")).toBe(true);
      await fireEvent.keyDown(line, { key: "Enter" });
      expect(line.classList.contains("revealed")).toBe(true);
      // Re-blur by advancing to next cue, then test Space
      fireEvent.click(container.querySelector('button[title="Next sentence"]')!);
      await tick();
      await fireEvent.keyDown(line, { key: " " });
      expect(line.classList.contains("revealed")).toBe(true);
    });

    it("keeps the whole line revealed across chunk changes within one cue", async () => {
      // The reveal is scoped to the LINE (cue), not the currently-showing
      // chunk: clicking unblurs every segment of the speaker's line, and it
      // stays unblurred as the audio moves through the line's chunks. Re-blur
      // happens only when a new line starts (covered by the next test).
      let ctrl: PlaybackController | null = null;
      const { container } = render(PillSyncHarness, {
        props: {
          audio: audioForLongCue,
          onController: (c: PlaybackController) => {
            ctrl = c;
          },
        },
      });
      await tick();
      const line = container.querySelector(".current-line")!;
      const firstChunk = line.textContent!;
      fireEvent.click(line);
      await tick();
      expect(line.classList.contains("revealed")).toBe(true);
      // Advance to a later chunk of the SAME cue: the chunk text changes but
      // the line stays revealed (previously it re-blurred on every chunk).
      ctrl!.seekTo(19);
      await tick();
      expect(line.textContent).not.toBe(firstChunk);
      expect(line.classList.contains("revealed")).toBe(true);
    });

    it("re-blurs when advancing to a new cue", async () => {
      const { container } = render(LessonPlayer, { props: { audio: audioForMultiCue } });
      const line = container.querySelector(".current-line")!;
      expect(line.classList.contains("blurred")).toBe(true);
      fireEvent.click(line);
      expect(line.classList.contains("revealed")).toBe(true);
      // Advance to next cue → new cue re-blurs
      fireEvent.click(container.querySelector('button[title="Next sentence"]')!);
      await tick();
      expect(line.classList.contains("blurred")).toBe(true);
      expect(line.classList.contains("revealed")).toBe(false);
    });

    it("setting disabled: no blur class, not a button, text visible", () => {
      captionBlurPref.set(false);
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const line = container.querySelector(".current-line")!;
      expect(line.classList.contains("blurred")).toBe(false);
      expect(line.tagName).not.toBe("BUTTON");
    });

    it("blur toggle renders on a cue-bearing NON-trackMode lesson and clicking flips the pref", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioForBlurToggle } });
      const btn = container.querySelector(".caption-blur-btn");
      expect(btn).toBeTruthy();
      expect(btn!.getAttribute("aria-pressed")).toBe("true");
      fireEvent.click(btn!);
      expect(captionBlurPref.enabled).toBe(false);
      expect(btn!.getAttribute("aria-pressed")).toBe("false");
    });

    it("caption blur toggle does not render in compact (Read) mode", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithAllSections, compact: true },
      });
      expect(container.querySelector(".caption-blur-btn")).toBeFalsy();
    });
  });

  // --- Chunked captions (Item B) ---

  describe("chunked captions", () => {
    it("short single-line cue renders full text unchanged", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      expect(container.querySelector(".current-line")!.textContent).toContain("Hello world");
    });

    it("long narration cue shows first chunk at start, later chunk after seek", async () => {
      let ctrl: PlaybackController | null = null;
      const { container } = render(PillSyncHarness, {
        props: {
          audio: audioForLongCue,
          onController: (c: PlaybackController) => {
            ctrl = c;
          },
        },
      });
      await tick();
      const line = container.querySelector(".current-line")!;
      const initialText = line.textContent!;
      expect(initialText.length).toBeLessThan(longNarrationText.length);
      // Seek to near the end → should show the last chunk
      ctrl!.seekTo(19);
      await tick();
      expect(line.textContent).not.toBe(initialText);
    });
  });

  // --- Item A: blur toggle grouped in controls-row ---

  describe("blur toggle placement (Item A)", () => {
    it("blur toggle is inside .controls-row as sibling of enunciation/english on trackMode", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithAllSections } });
      const controlsRow = container.querySelector(".controls-row")!;
      const blurBtn = controlsRow.querySelector(".caption-blur-btn");
      expect(blurBtn).toBeTruthy();
      expect(controlsRow.querySelector(".enunciation-btn")).toBeTruthy();
      expect(controlsRow.querySelector(".english-btn")).toBeTruthy();
    });

    it("blur toggle renders in .controls-row on NON-trackMode cue-bearing lesson", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioForBlurToggle } });
      const controlsRow = container.querySelector(".controls-row")!;
      expect(controlsRow.querySelector(".caption-blur-btn")).toBeTruthy();
    });
  });

  // --- Item B wiring: Repeat seeks to chunk start ---

  describe("repeat seeks to chunk start (Item B wiring)", () => {
    it("clicking Repeat on a multi-chunk cue seeks to current chunk start, not whole cue start", async () => {
      let ctrl: PlaybackController | null = null;
      const { container } = render(PillSyncHarness, {
        props: {
          audio: audioForRepeatChunk,
          onController: (c: PlaybackController) => {
            ctrl = c;
          },
        },
      });
      await tick();
      // Seek into the middle of the cue so we're on chunk 1 (second chunk)
      ctrl!.seekTo(20);
      await tick();
      const beforeMs = ctrl!.currentTime * 1000;
      // Click repeat — should seek to chunk 1's start (well after 0ms)
      const repeatBtn = container.querySelector<HTMLButtonElement>(
        'button[title="Repeat current"]',
      )!;
      fireEvent.click(repeatBtn);
      await tick();
      const afterMs = ctrl!.currentTime * 1000;
      // The chunk start should be > 0 (not the whole cue start)
      expect(afterMs).toBeGreaterThan(0);
      // And it should be close to where we were (same chunk), not back to 0
      expect(afterMs).toBeGreaterThan(beforeMs * 0.3);
    });

    it("single-chunk cue: Repeat seeks to cue start (same as before)", async () => {
      let ctrl: PlaybackController | null = null;
      const { container } = render(PillSyncHarness, {
        props: {
          audio: audioWithCues,
          onController: (c: PlaybackController) => {
            ctrl = c;
          },
        },
      });
      await tick();
      ctrl!.seekTo(0.5);
      await tick();
      const repeatBtn = container.querySelector<HTMLButtonElement>(
        'button[title="Repeat current"]',
      )!;
      fireEvent.click(repeatBtn);
      await tick();
      expect(ctrl!.currentTime).toBeLessThanOrEqual(0.1);
    });
  });
});
