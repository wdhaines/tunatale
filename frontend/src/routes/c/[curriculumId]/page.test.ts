/**
 * Tests for /c/[curriculumId] — curriculum view with day picker + pipeline.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import { tick } from "svelte";

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

vi.mock("$lib/api", () => ({
  api: {
    getLessonByDay: vi.fn(),
    getCurriculumProgress: vi.fn().mockResolvedValue([]),
    retryPipelineDay: vi.fn(),
    getStoryPrompt: vi.fn().mockResolvedValue({ system_prompt: "sys", user_prompt: "usr" }),
    importStory: vi.fn(),
  },
}));

vi.mock("$lib/stores/listened.svelte", () => ({
  listenedStore: { has: vi.fn().mockReturnValue(false) },
}));

vi.mock("$lib/stores/pipeline.svelte", () => ({
  pipelineStore: { start: vi.fn(), stop: vi.fn(), status: null, error: "" },
}));

vi.mock("$lib/stores/llmActivity.svelte", () => ({
  llmActivityStore: {
    events: [],
    currentLine: "",
    latestSeq: 0,
    refresh: vi.fn(),
    reset: vi.fn(),
  },
}));

vi.mock("$lib/stores/rateLimit.svelte", () => ({
  rateLimitStore: {
    status: null,
    probeError: "",
    refresh: vi.fn(),
    probe: vi.fn(),
    set: vi.fn(),
    ensureFresh: vi.fn(),
  },
}));

vi.mock("$lib/components/RateLimitWidget.svelte", () => ({
  default: (_props: unknown) => "<span>LLM —</span>",
}));

import { api } from "$lib/api";
import { pipelineStore } from "$lib/stores/pipeline.svelte";
import Page from "./+page.svelte";

const mockGetLessonByDay = vi.mocked(api.getLessonByDay);
const mockRetryPipelineDay = vi.mocked(api.retryPipelineDay);
const mockPipelineStoreStart = vi.mocked(pipelineStore.start);
const mockPipelineStoreStop = vi.mocked(pipelineStore.stop);

const day = (n: number) => ({
  day: n,
  title: `Title ${n}`,
  focus: `focus ${n}`,
  collocations: ["kava"],
  learning_objective: `obj ${n}`,
  story_guidance: "",
});

const curriculum = {
  id: "cid-1",
  topic: "Coffee",
  language_code: "sl",
  cefr_level: "A2",
  days: [day(1), day(2), day(3)],
  proposed: null,
};

const manualCurriculum = {
  ...curriculum,
  generation_mode: "manual" as const,
};

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(pipelineStore, "status", { value: null, configurable: true });
});

describe("/c/[curriculumId] page", () => {
  it("renders curriculum topic and day buttons", () => {
    const { getByText } = render(Page, { props: { data: { curriculum } } });
    expect(getByText("Coffee")).toBeTruthy();
    expect(getByText(/Day 1/)).toBeTruthy();
    expect(getByText(/Day 3/)).toBeTruthy();
  });

  it("shows the committed day count and a link to the planner chat", () => {
    const { getByText, getByRole } = render(Page, { props: { data: { curriculum } } });
    expect(getByText(/3 days/)).toBeTruthy();
    const planLink = getByRole("link", { name: /plan next days/i }) as HTMLAnchorElement;
    expect(planLink.getAttribute("href")).toBe("/c/cid-1/plan");
  });

  it("starts pipeline on mount", async () => {
    render(Page, { props: { data: { curriculum } } });
    await waitFor(() => {
      expect(mockPipelineStoreStart).toHaveBeenCalledWith("cid-1");
    });
  });

  it("ensures rate-limit store is fresh on mount (widget lives here now)", async () => {
    const { rateLimitStore } = await import("$lib/stores/rateLimit.svelte");
    render(Page, { props: { data: { curriculum } } });
    await waitFor(() => {
      expect(vi.mocked(rateLimitStore.ensureFresh)).toHaveBeenCalled();
    });
  });

  it("stops pipeline on destroy", () => {
    const { unmount } = render(Page, { props: { data: { curriculum } } });
    unmount();
    expect(mockPipelineStoreStop).toHaveBeenCalled();
  });

  it("navigates to lesson when pipeline says ready", async () => {
    // Set pipeline status to ready for day 1
    Object.defineProperty(pipelineStore, "status", {
      value: {
        active: false,
        days: [
          {
            day: 1,
            state: "ready",
            lesson_id: "l1",
            has_audio: true,
            error: null,
            retryable: false,
            detail: null,
          },
        ],
      },
      configurable: true,
    });

    const { getByText } = render(Page, { props: { data: { curriculum } } });
    await fireEvent.click(getByText(/Day 1 ·/));
    await waitFor(() => {
      expect(mockGoto).toHaveBeenCalledWith("/c/cid-1/l/l1");
    });
    expect(mockGetLessonByDay).not.toHaveBeenCalled();
  });

  it("calls retry when pipeline says failed", async () => {
    Object.defineProperty(pipelineStore, "status", {
      value: {
        active: true,
        days: [
          {
            day: 2,
            state: "failed",
            lesson_id: null,
            has_audio: false,
            error: "LLM error",
            retryable: true,
            detail: null,
          },
        ],
      },
      configurable: true,
    });
    mockRetryPipelineDay.mockResolvedValue({ status: "queued" });

    const { getByText } = render(Page, { props: { data: { curriculum } } });
    await fireEvent.click(getByText(/Day 2 ·/));
    await waitFor(() => {
      expect(mockRetryPipelineDay).toHaveBeenCalledWith("cid-1", 2);
    });
    // Restarts polling so the retried state shows immediately (a failed
    // pipeline is idle → 10s cadence otherwise): once on mount + once here.
    await waitFor(() => {
      expect(mockPipelineStoreStart).toHaveBeenCalledTimes(2);
    });
  });

  it("no-op for queued/generating/rendering pipeline days", async () => {
    Object.defineProperty(pipelineStore, "status", {
      value: {
        active: true,
        days: [
          {
            day: 3,
            state: "generating",
            lesson_id: null,
            has_audio: false,
            error: null,
            retryable: true,
            detail: "generating story",
          },
        ],
      },
      configurable: true,
    });

    mockGetLessonByDay.mockRejectedValue(new Error("Not Found"));
    const { getByText } = render(Page, { props: { data: { curriculum } } });
    await fireEvent.click(getByText(/Day 3 ·/));
    // Falls through to the cached-lesson check (none here) — no navigation, no retry
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockGoto).not.toHaveBeenCalled();
    expect(mockRetryPipelineDay).not.toHaveBeenCalled();
  });

  it("an in-flight render day with an existing lesson still navigates to it", async () => {
    // Regression: a queued render-only job (lesson exists, audio pending) used
    // to hard no-op the click, locking the user out of a readable lesson.
    Object.defineProperty(pipelineStore, "status", {
      value: {
        active: true,
        days: [
          {
            day: 2,
            state: "queued",
            lesson_id: null,
            has_audio: false,
            error: null,
            retryable: null,
            detail: null,
          },
        ],
      },
      configurable: true,
    });
    const lesson = {
      id: "l2",
      day: 2,
      title: "Day 2",
      language_code: "sl",
      sections: [],
      key_phrases: [],
    };
    mockGetLessonByDay.mockResolvedValue(lesson);

    const { getByText } = render(Page, { props: { data: { curriculum } } });
    await fireEvent.click(getByText(/Day 2 ·/));
    await waitFor(() => {
      expect(mockGetLessonByDay).toHaveBeenCalledWith("cid-1", 2);
      expect(mockGoto).toHaveBeenCalledWith("/c/cid-1/l/l2");
    });
  });

  it("falls back to getLessonByDay when no pipeline state exists", async () => {
    const lesson = {
      id: "l1",
      day: 1,
      title: "Day 1",
      language_code: "sl",
      sections: [],
      key_phrases: [],
    };
    mockGetLessonByDay.mockResolvedValue(lesson);

    const { getByText } = render(Page, { props: { data: { curriculum } } });
    await fireEvent.click(getByText(/Day 1 ·/));
    await waitFor(() => {
      expect(mockGetLessonByDay).toHaveBeenCalledWith("cid-1", 1);
      expect(mockGoto).toHaveBeenCalledWith("/c/cid-1/l/l1");
    });
  });

  it("silently ignores when fallback getLessonByDay also fails (pipeline will handle)", async () => {
    mockGetLessonByDay.mockRejectedValue(new Error("Not Found"));

    const { getByText } = render(Page, { props: { data: { curriculum } } });
    await fireEvent.click(getByText(/Day 1 ·/));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockGoto).not.toHaveBeenCalled();
  });

  it("shows error when retry fails", async () => {
    Object.defineProperty(pipelineStore, "status", {
      value: {
        active: true,
        days: [
          {
            day: 1,
            state: "failed",
            lesson_id: null,
            has_audio: false,
            error: "LLM error",
            retryable: true,
            detail: null,
          },
        ],
      },
      configurable: true,
    });
    mockRetryPipelineDay.mockRejectedValue(new Error("409 Conflict"));

    const { getByText, findByText } = render(Page, { props: { data: { curriculum } } });
    await fireEvent.click(getByText(/Day 1 ·/));
    expect(await findByText("409 Conflict")).toBeTruthy();
  });

  it("loads and maps getCurriculumProgress into progress state", async () => {
    const { api: mockApi } = await import("$lib/api");
    vi.mocked(mockApi.getCurriculumProgress).mockResolvedValueOnce([{ day: 1, lesson_id: "l1" }]);

    render(Page, { props: { data: { curriculum } } });
    await waitFor(() => {
      expect(mockApi.getCurriculumProgress).toHaveBeenCalledWith("cid-1");
    });
  });

  it("silently ignores getCurriculumProgress failure", async () => {
    const { api: mockApi } = await import("$lib/api");
    vi.mocked(mockApi.getCurriculumProgress).mockRejectedValueOnce(new Error("Network error"));

    const { getByText } = render(Page, { props: { data: { curriculum } } });
    await waitFor(() => expect(getByText("Coffee")).toBeTruthy());
  });

  it("renders the LLM activity current line when events exist", async () => {
    const { llmActivityStore } = await import("$lib/stores/llmActivity.svelte");
    Object.defineProperty(llmActivityStore, "events", {
      value: [
        {
          seq: 1,
          kind: "pipeline",
          timestamp: 1,
          curriculum_id: "cid-1",
          day: 1,
          state: "ready",
          message: "done",
        },
      ],
      configurable: true,
    });
    Object.defineProperty(llmActivityStore, "currentLine", {
      value: "current: day 1 ready",
      configurable: true,
    });
    try {
      const { getByText } = render(Page, { props: { data: { curriculum } } });
      expect(getByText("current: day 1 ready")).toBeTruthy();
    } finally {
      Object.defineProperty(llmActivityStore, "events", { value: [], configurable: true });
      Object.defineProperty(llmActivityStore, "currentLine", { value: "", configurable: true });
    }
  });

  it("manual-mode: selecting a lesson-less day opens the story panel", async () => {
    mockGetLessonByDay.mockRejectedValue(new Error("Not Found"));

    const { getByText, container } = render(Page, {
      props: { data: { curriculum: manualCurriculum } },
    });
    const btn = getByText(/Day 1 ·/);
    await fireEvent.click(btn);
    await waitFor(() => {
      expect(getByText("Copy story prompt")).toBeTruthy();
    });
    expect(container.querySelector("textarea")).toBeTruthy();
  });

  it("manual-mode: completing an import navigates to the new lesson", async () => {
    mockGetLessonByDay.mockRejectedValue(new Error("Not Found"));
    vi.mocked(api.importStory).mockResolvedValue({
      id: "l-new",
      title: "T",
      sections: [],
      warnings: [],
    });

    const { getByText, container } = render(Page, {
      props: { data: { curriculum: manualCurriculum } },
    });
    const btn = getByText(/Day 1 ·/);
    await fireEvent.click(btn);
    await waitFor(() => {
      expect(getByText("Copy story prompt")).toBeTruthy();
    });
    const textarea = container.querySelector("textarea")!;
    await fireEvent.input(textarea, { target: { value: '{"title":"X"}' } });
    const importBtn = container.querySelector('[data-testid="import-btn"]')!;
    await fireEvent.click(importBtn);
    await waitFor(() => {
      expect(mockGoto).toHaveBeenCalledWith("/c/cid-1/l/l-new");
    });
  });

  it("auto-mode: selecting a lesson-less day renders NO story panel", async () => {
    mockGetLessonByDay.mockRejectedValue(new Error("Not Found"));

    const { getByText, container } = render(Page, {
      props: { data: { curriculum } },
    });
    await fireEvent.click(getByText(/Day 1 ·/));
    await tick();
    expect(container.querySelector("textarea")).toBeNull();
  });
});

describe("load function for /c/[curriculumId]", () => {
  it("throws 404 when curriculum is not found", async () => {
    vi.doMock("$lib/api", () => ({
      api: {
        getCurriculum: vi.fn().mockRejectedValue(new Error("Not Found")),
      },
    }));
    const { load } = await import("./+page");
    const { api: mockApi } = await import("$lib/api");
    vi.mocked(mockApi.getCurriculum).mockRejectedValue(new Error("Not Found"));
    await expect(load({ params: { curriculumId: "nonexistent" } } as never)).rejects.toBeDefined();
  });
});
