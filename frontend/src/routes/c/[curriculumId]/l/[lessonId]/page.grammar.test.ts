/**
 * The lesson page on a GRAMMAR lesson: a lesson that is only an affix drill
 * (bd tunatale-ve4p.5).
 *
 * Such a lesson has no story, so there is nothing to read: no transcript, and
 * no Read mode. The page is always the Listen layout, the Read/Listen toggle
 * is not offered, and the table of what the drill covers stands where the
 * transcript would. Every test here starts with Read stored as the learner's
 * mode, because under Listen "forced to Listen" and "left alone" look alike.
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

import { api } from "#lib/api.js";
import type { TranscriptData } from "#lib/api.js";
import { captionBlurPref } from "#lib/stores/captionBlurPref.svelte.js";
import { lessonModePref } from "#lib/stores/lessonModePref.svelte.js";
import { listenedStore } from "#lib/stores/listened.svelte.js";
import Page from "./+page.svelte";
import {
  curriculum,
  lesson,
  audio,
  transcript,
  stubViewport,
  openLessonsOnDialogue,
  openLessonsAt,
} from "./page-test-helpers";

const drill = {
  pattern: "mo-mi",
  title: "Affix drill: mo- / mi-",
  labels: ["not begun", "begun"],
  roots: [
    {
      root: "inom",
      english: "drink",
      new: false,
      forms: [
        { form: "moinom", english: "will drink" },
        { form: "miinom", english: "drank" },
      ],
    },
  ],
};

const drillSection = {
  type: "affix_drill",
  phrases: [{ text: "Say: drank.", role: "prompt", language_code: "en", voice_id: "v1" }],
};

const grammarLesson = {
  ...lesson,
  title: "Affix drill: mo- / mi-",
  sections: [drillSection],
  key_phrases: [],
  drill,
};

// A story lesson that ENDS with a drill still has a story to read.
const storyWithDrill = { ...lesson, sections: [...lesson.sections, drillSection], drill };

function renderPage(lessonData: typeof lesson, withTranscript: TranscriptData | null = null) {
  return render(Page, {
    props: { data: { curriculum, lesson: lessonData, audio, transcript: withTranscript } },
  });
}

beforeEach(() => {
  openLessonsOnDialogue();
  vi.clearAllMocks();
  localStorage.clear();
  stubViewport(false);
  lessonModePref.set("read");
  captionBlurPref.set(true);
  listenedStore.reset();
  vi.mocked(api.getTranscript).mockReturnValue(new Promise<TranscriptData>(() => {}));
});

describe("a grammar lesson", () => {
  it("shows the table of what it drills where the transcript would be, on Listen", () => {
    openLessonsAt({ mode: "listen", tab: "outline" });
    const { container, getByText } = renderPage(grammarLesson);

    expect(getByText("will drink")).toBeTruthy();
    expect([...container.querySelectorAll(".drill-root .root")].map((e) => e.textContent)).toEqual([
      "inom",
    ]);
  });

  // A lesson that is only a drill had no Read side until slice 3 of the
  // curriculum page (tunatale-cs3p, R3): it now has the switch, and Read opens
  // on its Outline like any lesson.
  it("offers the Read / Listen switch, and leaves the global mode for the next lesson", () => {
    const { getByRole } = renderPage(grammarLesson);

    expect(getByRole("button", { name: /^Read$/ })).toBeTruthy();
    expect(getByRole("button", { name: /^Listen$/ })).toBeTruthy();
    expect(lessonModePref.mode).toBe("read");
  });

  it("is laid out as Read, on an Outline that lists its drill", () => {
    const { container, getByRole } = renderPage(grammarLesson);

    // Listen's player is the full one; Read's is `.compact`, with the reader
    // chips under it.
    expect(container.querySelector(".player.compact")).toBeTruthy();
    expect(getByRole("tab", { name: "Outline" }).getAttribute("aria-selected")).toBe("true");
    expect(getByRole("button", { name: /^Drill/ })).toBeTruthy();
    expect(container.querySelector(".drill-root")).toBeNull();
  });

  it("is laid out as Listen once Listen is its side", () => {
    openLessonsAt({ mode: "listen", tab: "outline" });
    const { container } = renderPage(grammarLesson);

    expect(container.querySelector(".player.compact")).toBeNull();
    expect(container.querySelector(".player")).toBeTruthy();
    expect(container.querySelector(".reader-chips")).toBeNull();
  });

  it("leaves out the mastery line, which is counted from a transcript", () => {
    // With the transcript still loading, which is when the line draws its
    // placeholder bars whatever the lesson turns out to hold.
    const { container } = renderPage(grammarLesson);

    expect(container.querySelector(".mastery-placeholder")).toBeNull();
    expect(container.querySelector(".mastery-line")).toBeNull();
  });

  it("shows its table on Read's Drill tab and no transcript, loading or otherwise", async () => {
    const { container, queryByText, getByRole } = renderPage(
      grammarLesson,
      transcript as TranscriptData,
    );

    expect(() => getByRole("tab", { name: "Dialogue" })).toThrow();
    await fireEvent.click(getByRole("tab", { name: "Drill" }));
    await waitFor(() => expect(container.querySelector(".drill-root")).toBeTruthy());
    expect(queryByText("kavo prosim")).toBeNull();
    expect(queryByText("No transcript available.")).toBeNull();
  });
});

describe("a story lesson that ends with a drill", () => {
  it("keeps its mastery line, which on Read sits in the Outline tab", () => {
    openLessonsAt({ mode: "read", tab: "outline" });
    const { container } = renderPage(storyWithDrill);

    expect(container.querySelector(".player-header .mastery-placeholder")).toBeNull();
    expect(container.querySelector("[role=tabpanel] .mastery-placeholder")).toBeTruthy();
    expect(container.querySelector(".mastery-placeholder")).toBeTruthy();
  });

  it("is still read like any other lesson, with what its drill covers on a tab after the story", async () => {
    const { container, getByText, getByRole, getAllByRole } = renderPage(
      storyWithDrill,
      transcript as TranscriptData,
    );

    expect(getByRole("button", { name: /^Read$/ })).toBeTruthy();
    expect(getByRole("button", { name: /^Listen$/ })).toBeTruthy();
    expect(container.querySelector(".player.compact")).toBeTruthy();
    // One tab per part, the drill after the story (tunatale-cs3p slice 3, R2).
    // No new grammar lessons are made (the owner, 2026-10-13), so this is the
    // page where a learner can see what a drill covered, and where
    // "not begun / begun" is read.
    expect(getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Outline",
      "Key phrases",
      "Dialogue",
      "Drill",
    ]);
    await fireEvent.click(getByRole("tab", { name: "Key phrases" }));
    expect(getByText("kavo prosim")).toBeTruthy();
    expect(container.querySelector(".drill-root")).toBeNull();
    await fireEvent.click(getByRole("tab", { name: "Drill" }));
    expect(container.querySelector(".drill-root")).toBeTruthy();
    expect(
      [...container.querySelectorAll(".drill-labels .label")].map((e) => e.textContent),
    ).toEqual(["not begun", "begun"]);
  });

  it("shows no table while it is being listened to: that page has nothing to read", () => {
    openLessonsAt({ mode: "listen", tab: "outline" });
    const { container } = renderPage(storyWithDrill, transcript as TranscriptData);

    expect(container.querySelector(".drill-root")).toBeNull();
  });
});
