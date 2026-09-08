/**
 * Tests for /c/[curriculumId]/l/[lessonId] — the prev/next lesson links in the
 * player header, fed by a client-side fetch of the curriculum's day→lesson map.
 *
 * See page-test-helpers.ts for the shared $lib/api / pipeline mock factories.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/svelte";

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

vi.mock("$lib/api", async () => {
  const { createApiMock } = await import("./page-test-helpers");
  return { api: createApiMock() };
});

vi.mock("$lib/stores/pipeline.svelte", async () => {
  const { createPipelineMock } = await import("./page-test-helpers");
  return { pipelineStore: createPipelineMock() };
});

import { api } from "$lib/api";
import type { DayProgress, TranscriptData } from "$lib/api";
import { listenedStore } from "$lib/stores/listened.svelte";
import { syncStore } from "$lib/stores/sync.svelte";
import { lessonModePref } from "$lib/stores/lessonModePref.svelte";
import { pipelineStore } from "$lib/stores/pipeline.svelte";
import Page from "./+page.svelte";
import { curriculum, lesson, audio, stubViewport } from "./page-test-helpers";

const mockGetProgress = vi.mocked(api.getCurriculumProgress);
const mockGetTranscript = vi.mocked(api.getTranscript);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  stubViewport(false); // desktop default → Read, unless a test overrides
  lessonModePref.set("read"); // reset the singleton's in-memory state
  localStorage.clear(); // ...without leaving the persisted override set() just wrote
  syncStore.notify(null);
  // Reset the shared pipeline mock's status between tests: it's a plain object,
  // not cleared by vi.clearAllMocks(), and a leaked (esp. failed) record would
  // bleed into the ungated regenStatus / follow-effect of an unrelated test.
  (pipelineStore as unknown as { status: unknown }).status = null;
  // Real listenedStore: clear entries + hydration latch so each test starts
  // "never listened" and hydrate()/seedListened() are free to re-fetch.
  listenedStore.reset();
  // When load supplies no transcript the component fetches it on mount. Default
  // to a pending promise so null-transcript renders sit in the loading state
  // without injecting content; tests that care override this.
  mockGetTranscript.mockReturnValue(new Promise<TranscriptData>(() => {}));
});

const d = (day: number, position: number, lesson_id: string): DayProgress => ({
  day,
  position,
  lesson_id,
});

// A track-mode lesson: every section carries its own cue manifest, which is what
// turns on the phase/enunciation model and with it hands-free. The default
// `audio` fixture has no sections at all, so the player would render none of it.
function sectionCue(sectionIndex: number, sectionType: string) {
  return {
    index: 0,
    start_ms: 0,
    end_ms: 800,
    section_index: sectionIndex,
    section_type: sectionType,
    phrase_index: 0,
    role: "speaker",
    language_code: "sl",
    text: "Dober dan",
    ref: { kind: "line" as const, target_index: 0 },
  };
}

const trackAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [
    {
      audio_id: "s0",
      section_index: 0,
      section_type: "key_phrases",
      title: "Key Phrases",
      cues: [sectionCue(0, "key_phrases")],
    },
    {
      audio_id: "s1",
      section_index: 1,
      section_type: "natural_speed",
      title: "Natural Speed",
      cues: [sectionCue(1, "natural_speed")],
    },
    {
      audio_id: "s2",
      section_index: 2,
      section_type: "slow_speed",
      title: "Slow Speed",
      cues: [sectionCue(2, "slow_speed")],
    },
    {
      audio_id: "s3",
      section_index: 3,
      section_type: "translated",
      title: "Translated",
      cues: [sectionCue(3, "translated")],
    },
    {
      audio_id: "s4",
      section_index: 4,
      section_type: "slow_translated",
      title: "Slow Translated",
      cues: [sectionCue(4, "slow_translated")],
    },
  ],
  cues: [sectionCue(1, "natural_speed")],
};

// Records the element the controller builds with `new Audio()`, which is never
// in the document and so cannot be reached by query.
function captureAudio(): { els: HTMLAudioElement[]; restore: () => void } {
  const els: HTMLAudioElement[] = [];
  const Orig = globalThis.Audio;
  class Recording extends Orig {
    constructor(src?: string) {
      super(src);
      els.push(this);
    }
  }
  globalThis.Audio = Recording as unknown as typeof Audio;
  return {
    els,
    restore: () => {
      globalThis.Audio = Orig;
    },
  };
}

describe("hands-free carries on into the next day", () => {
  // Seeded so the player opens ON the last pass of the sequence: ending that
  // track is what completes the run.
  function seedOnLastPass() {
    localStorage.setItem("handsFree", "on");
    localStorage.setItem(
      "lessonPlayerSelection",
      JSON.stringify({ phase: "dialogue", enunciation: "natural", english: "l2_first" }),
    );
  }

  it("navigates to the next day and arms the hand-off baton", async () => {
    seedOnLastPass();
    mockGetProgress.mockResolvedValue([d(1, 1, "lid-1"), d(2, 2, "lid-2")]);
    const cap = captureAudio();
    try {
      render(Page, {
        props: { data: { curriculum, lesson, audio: trackAudio, transcript: null } },
      });
      await waitFor(() => expect(cap.els.length).toBeGreaterThan(0));
      expect(mockGoto).not.toHaveBeenCalled();

      cap.els[0].dispatchEvent(new Event("ended"));

      expect(mockGoto).toHaveBeenCalledWith("/c/cid-1/l/lid-2");
      // Without the baton the next page loads paused and on the saved
      // selection — the run would silently die at the lesson boundary.
      expect(sessionStorage.getItem("handsFreeHandoff")).toBe("1");
    } finally {
      cap.restore();
    }
  });

  it("stops on the LAST day rather than wrapping to day one", async () => {
    seedOnLastPass();
    mockGetProgress.mockResolvedValue([d(1, 1, "lid-1"), d(2, 2, "lid-2")]);
    const cap = captureAudio();
    try {
      render(Page, {
        props: {
          data: { curriculum, lesson: { ...lesson, day: 2 }, audio: trackAudio, transcript: null },
        },
      });
      await waitFor(() => expect(cap.els.length).toBeGreaterThan(0));
      cap.els[0].dispatchEvent(new Event("ended"));
      expect(mockGoto).not.toHaveBeenCalled();
      expect(sessionStorage.getItem("handsFreeHandoff")).toBeNull();
    } finally {
      cap.restore();
    }
  });

  it("hands-free OFF never navigates, however the track ends", async () => {
    localStorage.setItem(
      "lessonPlayerSelection",
      JSON.stringify({ phase: "dialogue", enunciation: "natural", english: "l2_first" }),
    );
    mockGetProgress.mockResolvedValue([d(1, 1, "lid-1"), d(2, 2, "lid-2")]);
    const cap = captureAudio();
    try {
      render(Page, {
        props: { data: { curriculum, lesson, audio: trackAudio, transcript: null } },
      });
      await waitFor(() => expect(cap.els.length).toBeGreaterThan(0));
      cap.els[0].dispatchEvent(new Event("ended"));
      expect(mockGoto).not.toHaveBeenCalled();
    } finally {
      cap.restore();
    }
  });
});

describe("prev/next lesson links in the player header", () => {
  it("renders both neighbours with the correct hrefs", async () => {
    mockGetProgress.mockResolvedValue([d(1, 1, "lid-1"), d(2, 2, "lid-2"), d(3, 3, "lid-3")]);

    const { findByText } = render(Page, {
      props: { data: { curriculum, lesson: { ...lesson, day: 2 }, audio, transcript: null } },
    });

    const prev = await findByText("← Day 1");
    expect(prev.getAttribute("href")).toBe("/c/cid-1/l/lid-1");
    const next = await findByText("Day 3 →");
    expect(next.getAttribute("href")).toBe("/c/cid-1/l/lid-3");
  });

  it("renders only the next link on the first lesson", async () => {
    mockGetProgress.mockResolvedValue([d(1, 1, "lid-1"), d(2, 2, "lid-2")]);

    const { findByText, container } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript: null } },
    });

    const next = await findByText("Day 2 →");
    expect(next.getAttribute("href")).toBe("/c/cid-1/l/lid-2");
    await waitFor(() => {
      const links = container.querySelectorAll(".lesson-nav-link");
      expect(links.length).toBe(1);
      expect(links[0].getAttribute("href")).toBe("/c/cid-1/l/lid-2");
    });
  });

  it("renders only the previous link on the last lesson", async () => {
    mockGetProgress.mockResolvedValue([d(1, 1, "lid-1"), d(2, 2, "lid-2")]);

    const { findByText, container } = render(Page, {
      props: { data: { curriculum, lesson: { ...lesson, day: 2 }, audio, transcript: null } },
    });

    const prev = await findByText("← Day 1");
    expect(prev.getAttribute("href")).toBe("/c/cid-1/l/lid-1");
    await waitFor(() => {
      const links = container.querySelectorAll(".lesson-nav-link");
      expect(links.length).toBe(1);
      expect(links[0].getAttribute("href")).toBe("/c/cid-1/l/lid-1");
    });
  });

  it("orders neighbours by position, not by array order", async () => {
    mockGetProgress.mockResolvedValue([d(3, 3, "lid-3"), d(1, 1, "lid-1"), d(2, 2, "lid-2")]);

    const { findByText } = render(Page, {
      props: { data: { curriculum, lesson: { ...lesson, day: 2 }, audio, transcript: null } },
    });

    const prev = await findByText("← Day 1");
    expect(prev.getAttribute("href")).toBe("/c/cid-1/l/lid-1");
    const next = await findByText("Day 3 →");
    expect(next.getAttribute("href")).toBe("/c/cid-1/l/lid-3");
  });

  it("renders no nav links when the current day is absent from the progress response", async () => {
    mockGetProgress.mockResolvedValue([d(1, 1, "lid-1"), d(3, 3, "lid-3")]);

    const { container } = render(Page, {
      props: { data: { curriculum, lesson: { ...lesson, day: 2 }, audio, transcript: null } },
    });

    await waitFor(() => expect(mockGetProgress).toHaveBeenCalled());
    await waitFor(() => {
      expect(container.querySelector(".lesson-nav-link")).toBeNull();
    });
    // The whole band is gone, not merely empty: an empty <nav> would still cost
    // a grid row and its gap between the breadcrumb and the title.
    expect(container.querySelector(".lesson-nav")).toBeNull();
  });

  it("shows no error and no nav links when getCurriculumProgress rejects", async () => {
    mockGetProgress.mockRejectedValue(new Error("progress fetch failed"));

    const { findByText, queryByText, container } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript: null } },
    });

    // The lesson still renders — the failed side fetch must not blank the page.
    expect(await findByText("Day 1: Coffee")).toBeTruthy();
    await waitFor(() => expect(mockGetProgress).toHaveBeenCalled());
    await waitFor(() => {
      expect(container.querySelector(".lesson-nav-link")).toBeNull();
    });
    expect(container.querySelector(".lesson-nav")).toBeNull();
    expect(queryByText("progress fetch failed")).toBeNull();
  });

  it("keeps the Read/Listen toggle level with the title, below the day pager", async () => {
    mockGetProgress.mockResolvedValue([d(1, 1, "lid-1"), d(2, 2, "lid-2")]);
    const word = {
      lemma: "zdravo",
      active_state: "known",
      progress: 1.0,
      surface: "zdravo",
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
    const masteryTranscript = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [{ role: "A", sentence: "zdravo", words: [word] }],
    };

    const { container, getByRole, findByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript: masteryTranscript } },
    });

    expect(getByRole("button", { name: "Read" })).toBeTruthy();
    expect(getByRole("button", { name: "Listen" })).toBeTruthy();
    await findByText("Day 2 →");

    // Four stacked bands: curriculum link + day pager, title+toggle, stats.
    // jsdom does no layout, so "level with the title" is expressed as sibling
    // order in the header grid — the real geometry is pinned by
    // tests/lesson-header-layout.spec.ts.
    //
    // The full-width bands are wrapped in .header-band by LessonReader. That
    // wrapper is what lets this page span the grid WITHOUT a :global() rule
    // reaching across the component boundary — Svelte cannot verify such a rule
    // statically and reports it as dead CSS.
    const header = container.querySelector(".player-header")!;
    const children = Array.from(header.children);
    const idx = (sel: string) => children.findIndex((el) => el.matches(sel));

    expect(idx(".header-band")).toBe(0);
    expect(children[0].querySelector(".breadcrumb")).toBeTruthy();
    expect(children[0].querySelector(".lesson-nav")).toBeTruthy();
    expect(idx(".player-title-area")).toBe(1);
    expect(idx(".mode-row")).toBe(2);
    expect(children[2].querySelector(".toggle-pill")).toBeTruthy();
    expect(children[3].querySelector(".mastery-line")).toBeTruthy();
    // The breadcrumb moved OUT of the title column to become its own band.
    expect(container.querySelector(".player-title-area .breadcrumb")).toBeNull();
  });
});
