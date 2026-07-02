/**
 * Tests for LessonPlayer.svelte.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import LessonPlayer from "./LessonPlayer.svelte";
import type { LessonAudio } from "$lib/api";
import type { PlaybackController } from "$lib/playback/playbackController.svelte";

vi.mock("$lib/api", () => ({
  api: {
    audioUrl: vi.fn((id: string) => `/api/audio/${id}`),
    audioZipUrl: vi.fn((lessonId: string) => `/api/audio/lesson/${lessonId}/zip`),
  },
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

describe("LessonPlayer", () => {
  describe("basic transport", () => {
    it("does not render an audio element (controller owns the only one)", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithNoSections } });
      expect(container.querySelector("audio")).toBeFalsy();
    });

    it("renders transport buttons (play, seek, section)", () => {
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

    it("hides section nav buttons when cues are absent", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCuesNull } });
      expect(container.querySelector('button[title="Previous section"]')).toBeFalsy();
      expect(container.querySelector('button[title="Next section"]')).toBeFalsy();
      const buttons = container.querySelectorAll(".ctrl-btn");
      expect(buttons.length).toBe(3);
    });

    it("renders no section info or current line when cues absent", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCuesNull } });
      expect(container.querySelector(".section-info")).toBeFalsy();
      expect(container.querySelector(".current-line")).toBeFalsy();
    });

    it("renders section info and current line when cues present", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      expect(container.querySelector(".section-info")).toBeTruthy();
      expect(container.querySelector(".current-line")).toBeTruthy();
    });
  });

  describe("compact mode", () => {
    it("hides only the current-line subtitle and downloads; keeps all controls", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithCues, compact: true },
      });
      // Subtitle display is the transcript's job in Read mode.
      expect(container.querySelector(".current-line")).toBeFalsy();
      expect(container.querySelector(".download-section")).toBeFalsy();
      // Full control parity with Listen mode.
      expect(container.querySelector(".section-info")).toBeTruthy();
      expect(container.querySelector(".sentence-row")).toBeTruthy();
      expect(container.querySelector(".scrubber-row")).toBeTruthy();
      expect(container.querySelector(".speed-row")).toBeTruthy();
    });

    it("compact without cues hides cue-driven rows but keeps scrubber and speed", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithCuesNull, compact: true },
      });
      expect(container.querySelector(".section-info")).toBeFalsy();
      expect(container.querySelector(".sentence-row")).toBeFalsy();
      expect(container.querySelector(".scrubber-row")).toBeTruthy();
      expect(container.querySelector(".speed-row")).toBeTruthy();
    });

    it("still renders transport row in compact mode", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithNoSections, compact: true },
      });
      expect(container.querySelector(".transport-row")).toBeTruthy();
      expect(container.querySelector("audio")).toBeFalsy();
    });
  });

  describe("downloads", () => {
    it("renders download section when not compact", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithSections } });
      const details = container.querySelector(".download-section");
      expect(details).toBeTruthy();
    });

    it("renders Download All Sections link", () => {
      const { getByText } = render(LessonPlayer, { props: { audio: audioWithSections } });
      expect(getByText("Download All Sections")).toBeTruthy();
    });

    it("renders individual section links inside download section", () => {
      const { getByText } = render(LessonPlayer, { props: { audio: audioWithSections } });
      expect(getByText("Key Phrases")).toBeTruthy();
      expect(getByText("Natural Speed")).toBeTruthy();
    });

    it("no download section when compact", () => {
      const { container } = render(LessonPlayer, {
        props: { audio: audioWithSections, compact: true },
      });
      expect(container.querySelector(".download-section")).toBeFalsy();
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

    it("renders sentence skip toggle", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const toggle = container.querySelector(".sentence-skip-toggle");
      expect(toggle).toBeTruthy();
      const checkbox = toggle!.querySelector("input[type=checkbox]");
      expect(checkbox).toBeTruthy();
    });
  });

  describe("scrubber and speed", () => {
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

    it("renders speed button with rate", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const speedBtn = container.querySelector(".speed-btn");
      expect(speedBtn).toBeTruthy();
      expect(speedBtn!.textContent).toContain("×");
    });

    it("renders speed button even with cues null (not compact)", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCuesNull } });
      const speedBtn = container.querySelector(".speed-btn");
      expect(speedBtn).toBeTruthy();
    });

    it("cycles speed on click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const speedBtn = container.querySelector<HTMLButtonElement>(".speed-btn")!;
      const initialRate = parseFloat(speedBtn.textContent!);
      fireEvent.click(speedBtn);
      const nextRate = parseFloat(speedBtn.textContent!);
      expect(nextRate).not.toBe(initialRate);
    });
  });

  describe("interactions", () => {
    it("fires prevSection on button click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>('button[title="Previous section"]')!;
      fireEvent.click(btn);
    });

    it("fires rewind on button click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>('button[title="Rewind 10s"]')!;
      fireEvent.click(btn);
    });

    it("fires togglePlay on play button click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>(".play-btn")!;
      fireEvent.click(btn);
    });

    it("fires forward on button click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>('button[title="Forward 10s"]')!;
      fireEvent.click(btn);
    });

    it("fires nextSection on button click", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const btn = container.querySelector<HTMLButtonElement>('button[title="Next section"]')!;
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

    it("toggles sentence skip checkbox", () => {
      const { container } = render(LessonPlayer, { props: { audio: audioWithCues } });
      const checkbox = container.querySelector<HTMLInputElement>(
        '.sentence-skip-toggle input[type="checkbox"]',
      )!;
      expect(checkbox.checked).toBe(false);
      fireEvent.click(checkbox);
      expect(checkbox.checked).toBe(true);
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
});
