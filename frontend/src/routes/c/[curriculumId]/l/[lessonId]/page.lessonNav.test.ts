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
const mockGetTranscript = vi.mocked(api.getLessonTranscript);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
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

    // Four stacked bands: curriculum link, day pager, title+toggle, stats.
    // jsdom does no layout, so "level with the title" is expressed as "the
    // toggle is the title area's immediate next sibling in the header grid" —
    // the real geometry is pinned by tests/lesson-header-layout.spec.ts.
    const header = container.querySelector(".player-header")!;
    const children = Array.from(header.children);
    const idx = (sel: string) => children.findIndex((el) => el.matches(sel));

    expect(idx(".breadcrumb")).toBe(0);
    expect(idx(".lesson-nav")).toBe(1);
    expect(idx(".player-title-area")).toBe(2);
    expect(idx(".mode-row")).toBe(3);
    expect(idx(".mastery-line")).toBe(4);
    expect(children[3].querySelector(".toggle-pill")).toBeTruthy();
    // The breadcrumb moved OUT of the title column to become its own band.
    expect(container.querySelector(".player-title-area .breadcrumb")).toBeNull();
  });
});
