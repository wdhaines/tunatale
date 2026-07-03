/**
 * Tests for AudioPlayer.svelte.
 */
import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/svelte";
import AudioPlayer from "./AudioPlayer.svelte";
import type { LessonAudio } from "$lib/api";

vi.mock("$lib/api", () => ({
  api: {
    audioUrl: vi.fn((id: string) => `/api/audio/${id}`),
    audioZipUrl: vi.fn((lessonId: string) => `/api/audio/lesson/${lessonId}/zip`),
  },
}));

vi.mock("$lib/sw/prefetch", () => ({
  maybePrefetchLesson: vi.fn().mockResolvedValue(undefined),
}));

import { maybePrefetchLesson } from "$lib/sw/prefetch";

const audioWithNoSections: LessonAudio = { audio_id: "a1", lesson_id: "l1", sections: [] };
const audioWithSections: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [
    { audio_id: "s1", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
    { audio_id: "s2", section_index: 1, section_type: "natural_speed", title: "Natural Speed" },
  ],
};

describe("AudioPlayer", () => {
  it("renders the audio heading", () => {
    const { getByText } = render(AudioPlayer, { props: { audio: audioWithNoSections } });
    expect(getByText("Audio Player")).toBeTruthy();
  });

  it("renders an audio element with the correct src", () => {
    const { container } = render(AudioPlayer, { props: { audio: audioWithNoSections } });
    const audioEl = container.querySelector("audio");
    expect(audioEl).toBeTruthy();
    expect(audioEl!.src).toContain("/api/audio/a1");
  });

  it("does not render download controls when sections is empty", () => {
    const { queryByText } = render(AudioPlayer, { props: { audio: audioWithNoSections } });
    expect(queryByText("Download All Sections")).toBeFalsy();
    expect(queryByText("Individual sections")).toBeFalsy();
  });

  it("renders Download All Sections link when sections are present", () => {
    const { getByText } = render(AudioPlayer, { props: { audio: audioWithSections } });
    expect(getByText("Download All Sections")).toBeTruthy();
  });

  it("Download All Sections link points to the ZIP endpoint", () => {
    const { getByText } = render(AudioPlayer, { props: { audio: audioWithSections } });
    const link = getByText("Download All Sections").closest("a") as HTMLAnchorElement;
    expect(link).toBeTruthy();
    expect(link.href).toContain("/api/audio/lesson/l1/zip");
  });

  it("renders individual section links inside a details element", () => {
    const { container, getByText } = render(AudioPlayer, { props: { audio: audioWithSections } });
    expect(getByText("Key Phrases")).toBeTruthy();
    expect(getByText("Natural Speed")).toBeTruthy();
    const details = container.querySelector("details");
    expect(details).toBeTruthy();
    const links = details!.querySelectorAll("a");
    expect(links.length).toBe(2);
  });

  it("section download links use the correct audioUrl", () => {
    const { container } = render(AudioPlayer, { props: { audio: audioWithSections } });
    const details = container.querySelector("details");
    const links = Array.from(details!.querySelectorAll("a")) as HTMLAnchorElement[];
    expect(links[0].href).toContain("/api/audio/s1");
    expect(links[1].href).toContain("/api/audio/s2");
  });

  it("prefetches again when the audio prop changes (backlog #36)", async () => {
    // SvelteKit reuses the component on same-route param nav; a mount-only
    // prefetch would never cache the next lesson's audio.
    const spy = vi.mocked(maybePrefetchLesson);
    spy.mockClear();
    const { rerender } = render(AudioPlayer, { props: { audio: audioWithNoSections } });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toEqual(["/api/audio/a1"]);
    await rerender({ audio: { audio_id: "a2", lesson_id: "l2", sections: [] } });
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy.mock.calls[1][0]).toEqual(["/api/audio/a2"]);
  });
});
