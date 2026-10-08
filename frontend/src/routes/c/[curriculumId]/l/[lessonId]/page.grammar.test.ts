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
import { render, waitFor } from "@testing-library/svelte";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));

vi.mock("$lib/api", async () => {
  const { createApiMock } = await import("./page-test-helpers");
  return { api: createApiMock() };
});

vi.mock("$lib/stores/pipeline.svelte", async () => {
  const { createPipelineMock } = await import("./page-test-helpers");
  return { pipelineStore: createPipelineMock() };
});

import { api } from "$lib/api";
import type { TranscriptData } from "$lib/api";
import { captionBlurPref } from "$lib/stores/captionBlurPref.svelte";
import { lessonModePref } from "$lib/stores/lessonModePref.svelte";
import { listenedStore } from "$lib/stores/listened.svelte";
import Page from "./+page.svelte";
import { curriculum, lesson, audio, transcript, stubViewport } from "./page-test-helpers";

const drill = {
  pattern: "mo-mi",
  title: "Affix drill: mo- / mi-",
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
  vi.clearAllMocks();
  localStorage.clear();
  stubViewport(false);
  lessonModePref.set("read");
  captionBlurPref.set(true);
  listenedStore.reset();
  vi.mocked(api.getTranscript).mockReturnValue(new Promise<TranscriptData>(() => {}));
});

describe("a grammar lesson", () => {
  it("shows the table of what it drills where the transcript would be", () => {
    const { container, getByText } = renderPage(grammarLesson);

    expect(getByText("will drink")).toBeTruthy();
    expect([...container.querySelectorAll(".drill-root .root")].map((e) => e.textContent)).toEqual([
      "inom",
    ]);
  });

  it("offers no Read / Listen toggle, and leaves the stored mode for the next lesson", () => {
    const { queryByRole } = renderPage(grammarLesson);

    expect(queryByRole("button", { name: /^Read$/ })).toBeNull();
    expect(queryByRole("button", { name: /^Listen$/ })).toBeNull();
    expect(lessonModePref.mode).toBe("read");
  });

  it("is laid out as Listen even though Read is the stored mode", () => {
    const { container } = renderPage(grammarLesson);

    // Listen's player is the full one; Read's is `.compact`, with the reader
    // chips under it.
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

  it("shows no transcript, loading or otherwise", async () => {
    const { container, queryByText } = renderPage(grammarLesson, transcript as TranscriptData);

    await waitFor(() => expect(container.querySelector(".drill-root")).toBeTruthy());
    expect(queryByText("kavo prosim")).toBeNull();
    expect(queryByText("No transcript available.")).toBeNull();
  });
});

describe("a story lesson that ends with a drill", () => {
  it("keeps its mastery line", () => {
    const { container } = renderPage(storyWithDrill);

    expect(container.querySelector(".mastery-placeholder")).toBeTruthy();
  });

  it("is still read like any other lesson: toggle, transcript, no table", () => {
    const { container, getByText, getByRole } = renderPage(
      storyWithDrill,
      transcript as TranscriptData,
    );

    // The control for the grammar-lesson tests above: these two buttons are
    // what "no toggle" there means.
    expect(getByRole("button", { name: /^Read$/ })).toBeTruthy();
    expect(getByRole("button", { name: /^Listen$/ })).toBeTruthy();
    expect(getByText("kavo prosim")).toBeTruthy();
    expect(container.querySelector(".drill-root")).toBeNull();
    expect(container.querySelector(".player.compact")).toBeTruthy();
  });
});
