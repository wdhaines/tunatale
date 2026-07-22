/**
 * LOCKED guardrail — Fable-authored, DO NOT EDIT (BP: copy verbatim).
 *
 * Target path: frontend/src/routes/review/page.review-trigger.test.ts
 * (sits beside page.test.ts so ./+page.svelte resolves).
 *
 * Pins the one-shot POST trigger: in LESSON mode, once the scoped queue has
 * ACTUALLY drained (the post-grade refetch returned an empty queue), the page
 * POSTs markLessonReviewed exactly once. It must NOT fire on mount (no grade),
 * NOT when the last card is graded but the queue does not drain (an Again
 * re-queues it; a multi-step learning card stays — both kept by the backend's
 * _classify), NOT on a partial review, and NOT in global (non-lesson) mode.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import ReviewPage from "./+page.svelte";

vi.mock("svelte", () => ({ onMount: vi.fn((fn: () => void) => fn()) }));

const urlParams = vi.hoisted(() => new URLSearchParams());
vi.mock("$app/stores", () => ({
  page: {
    subscribe: vi.fn((cb: (v: unknown) => void) => {
      cb({ url: { searchParams: urlParams } });
      return () => {};
    }),
  },
}));

vi.mock("$lib/api", () => ({
  api: {
    fetchQueueStats: vi.fn(),
    fetchReviewQueue: vi.fn(),
    fetchLessonReviewQueue: vi.fn(),
    submitDrill: vi.fn(),
    markLessonReviewed: vi.fn(),
  },
}));

import { api } from "$lib/api";
import { makeReviewQueueItem } from "../../test/factories";

const mockFetchQueueStats = vi.mocked(api.fetchQueueStats);
const mockFetchReviewQueue = vi.mocked(api.fetchReviewQueue);
const mockFetchLessonReviewQueue = vi.mocked(api.fetchLessonReviewQueue);
const mockMarkLessonReviewed = vi.mocked(api.markLessonReviewed);

const stats = {
  new: 0,
  learning: 0,
  review: 0,
  daily_new_cap: 20,
  cap_source: "default" as const,
  fsrs_source: "default" as const,
};
const item = (id: number) =>
  makeReviewQueueItem({ id, text: `w${id}`, translation: "x", direction: "recognition" });

beforeEach(() => {
  vi.clearAllMocks();
  for (const k of Array.from(urlParams.keys())) urlParams.delete(k);
  mockFetchQueueStats.mockResolvedValue(stats);
  mockMarkLessonReviewed.mockResolvedValue({ ok: true });
});

describe("lesson-scoped review — one-shot markLessonReviewed trigger", () => {
  it("POSTs markLessonReviewed once once the lesson queue actually drains after a grade", async () => {
    urlParams.set("lesson", "lesson-abc");
    mockFetchLessonReviewQueue
      .mockResolvedValueOnce({ queue: [item(1)], has_unreviewed_listen: true })
      .mockResolvedValue({ queue: [], has_unreviewed_listen: true });

    const { findByRole } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));

    await waitFor(() => expect(mockMarkLessonReviewed).toHaveBeenCalledTimes(1));
    expect(mockMarkLessonReviewed).toHaveBeenCalledWith("lesson-abc");
  });

  it("does NOT POST on mount with cards and no grade", async () => {
    urlParams.set("lesson", "lesson-abc");
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item(1)], has_unreviewed_listen: true });

    const { findByText } = render(ReviewPage);
    await findByText("w1");

    expect(mockMarkLessonReviewed).not.toHaveBeenCalled();
  });

  it("does NOT POST on mount when the queue is already empty (no grade)", async () => {
    urlParams.set("lesson", "lesson-abc");
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [], has_unreviewed_listen: true });

    const { findByText } = render(ReviewPage);
    await findByText("← Home");

    expect(mockMarkLessonReviewed).not.toHaveBeenCalled();
  });

  it("does NOT POST when grading the last card does not drain the queue (Again/learning re-queues)", async () => {
    urlParams.set("lesson", "lesson-abc");
    mockFetchLessonReviewQueue
      .mockResolvedValueOnce({ queue: [item(1)], has_unreviewed_listen: true })
      .mockResolvedValue({ queue: [item(2)], has_unreviewed_listen: true });

    const { findByRole, findByText } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Again" }));

    await findByText("w2"); // rate() completed its refetch + re-key
    expect(mockMarkLessonReviewed).not.toHaveBeenCalled();
  });

  it("does NOT POST on a partial review (queue still non-empty after a grade)", async () => {
    urlParams.set("lesson", "lesson-abc");
    mockFetchLessonReviewQueue
      .mockResolvedValueOnce({ queue: [item(1), item(2)], has_unreviewed_listen: true })
      .mockResolvedValue({ queue: [item(2)], has_unreviewed_listen: true });

    const { findByRole, findByText } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));

    await findByText("w2");
    expect(mockMarkLessonReviewed).not.toHaveBeenCalled();
  });

  it("does NOT POST in global (non-lesson) mode when the queue drains", async () => {
    // no ?lesson= param → global mode
    mockFetchReviewQueue
      .mockResolvedValueOnce({ queue: [item(1)] })
      .mockResolvedValue({ queue: [] });

    const { findByRole, queryByRole } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));

    // Good button gone == queue rendered empty == rate() finished its cycle.
    await waitFor(() => expect(queryByRole("button", { name: "Good" })).toBeNull());
    expect(mockMarkLessonReviewed).not.toHaveBeenCalled();
  });
});
