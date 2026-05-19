/**
 * Tests for the unified /review route.
 *
 * Model: the server (`/review-queue`) is the source of truth. The frontend
 * fetches the queue on mount and after every grade, and always renders
 * `queue[0]`. Sibling burying, deferred-learning ordering (pending vs ready),
 * and newSpread are all the server's job. These tests therefore mock
 * `fetchReviewQueue` per call to model what the server would return at each
 * step of the session.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, screen } from "@testing-library/svelte";
import ReviewPage from "./+page.svelte";

// Mock onMount from svelte - must be before component import
vi.mock("svelte", () => {
  return {
    onMount: vi.fn((fn: () => void) => fn()),
  };
});

vi.mock("$lib/api", () => ({
  api: {
    fetchQueueStats: vi.fn(),
    fetchReviewQueue: vi.fn(),
    submitDrill: vi.fn(),
  },
}));

import { api } from "$lib/api";
const mockFetchQueueStats = vi.mocked(api.fetchQueueStats);
const mockFetchReviewQueue = vi.mocked(api.fetchReviewQueue);
const mockSubmitDrill = vi.mocked(api.submitDrill);
import { makeReviewQueueItem } from "../../test/factories";

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchQueueStats.mockResolvedValue({
    new: 0,
    learning: 0,
    review: 0,
    daily_new_cap: 20,
    cap_source: "default",
    fsrs_source: "default",
  });
  mockFetchReviewQueue.mockResolvedValue({ queue: [] });
  mockSubmitDrill.mockResolvedValue({ new_due_at: "2026-04-25", new_state: "review" });
});

describe("review/+page.svelte", () => {
  it("shows loading state initially", () => {
    mockFetchReviewQueue.mockReturnValue(new Promise(() => {}));
    const { container } = render(ReviewPage);
    expect(container.textContent).toContain("Loading");
  });

  it("shows done state when queue is empty", async () => {
    const { findByText } = render(ReviewPage);
    expect(await findByText(/Done for today/)).toBeTruthy();
  });

  it("renders first queue item", async () => {
    const item = makeReviewQueueItem({
      id: 1,
      text: "okno",
      translation: "window",
      direction: "recognition",
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByText } = render(ReviewPage);
    expect(await findByText("okno")).toBeTruthy();
  });

  it("shows direction badge for current card", async () => {
    const item = makeReviewQueueItem({
      id: 1,
      text: "okno",
      translation: "window",
      direction: "recognition",
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByText } = render(ReviewPage);
    expect(await findByText(/Recognition/i)).toBeTruthy();
  });

  it("calls submitDrill with correct direction and id on rating", async () => {
    const item = makeReviewQueueItem({
      id: 5,
      text: "voda",
      translation: "water",
      direction: "recognition",
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByRole } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(mockSubmitDrill).toHaveBeenCalledWith(5, "recognition", "good", expect.any(Number));
    const timeMs = mockSubmitDrill.mock.calls[0][3];
    expect(timeMs).toBeGreaterThanOrEqual(0);
    expect(timeMs).toBeLessThanOrEqual(60000);
  });

  it("calls submitDrill with production direction for production cards", async () => {
    const item = makeReviewQueueItem({
      id: 7,
      text: "banka",
      translation: "bank",
      direction: "production",
      word_count: 2,
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByRole } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(mockSubmitDrill).toHaveBeenCalledWith(7, "production", "good", expect.any(Number));
  });

  it("advances to whatever the server returns next after rating", async () => {
    const item1 = makeReviewQueueItem({
      id: 1,
      text: "okno",
      translation: "window",
      direction: "recognition",
    });
    const item2 = makeReviewQueueItem({
      id: 3,
      text: "hiša",
      translation: "house",
      direction: "recognition",
    });
    mockFetchReviewQueue
      .mockResolvedValueOnce({ queue: [item1, item2] })
      .mockResolvedValueOnce({ queue: [item2] });
    const { findByRole, findByText } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(await findByText("hiša")).toBeTruthy();
  });

  it("answer is hidden on the next card after rating (no answer leak)", async () => {
    const item1 = makeReviewQueueItem({
      id: 1,
      text: "okno",
      translation: "window",
      direction: "recognition",
    });
    const item2 = makeReviewQueueItem({
      id: 3,
      text: "hiša",
      translation: "house",
      direction: "recognition",
    });
    mockFetchReviewQueue
      .mockResolvedValueOnce({ queue: [item1, item2] })
      .mockResolvedValueOnce({ queue: [item2] });
    const { findByRole, queryByRole } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(await findByRole("button", { name: "Show" })).toBeTruthy();
    expect(queryByRole("button", { name: "Good" })).toBeNull();
  });

  it("shows done when server returns empty queue after rating", async () => {
    const item = makeReviewQueueItem({
      id: 1,
      text: "okno",
      translation: "window",
      direction: "recognition",
    });
    mockFetchReviewQueue
      .mockResolvedValueOnce({ queue: [item] })
      .mockResolvedValueOnce({ queue: [] });
    const { findByRole, findByText } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(await findByText(/Done for today/)).toBeTruthy();
  });

  it("shows error when fetch rejects", async () => {
    mockFetchReviewQueue.mockRejectedValue(new Error("Network error"));
    const { findByText } = render(ReviewPage);
    expect(await findByText("Network error")).toBeTruthy();
  });

  it("shows error and stays on card when submitDrill rejects", async () => {
    const item = makeReviewQueueItem({
      id: 1,
      text: "okno",
      translation: "window",
      direction: "recognition",
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    mockSubmitDrill.mockRejectedValue(new Error("Submit failed"));
    const { findByRole, findByText } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(await findByText("Submit failed")).toBeTruthy();
  });

  it("production word_count=1 with image_url shows img element", async () => {
    const item = makeReviewQueueItem({
      id: 10,
      text: "banka",
      translation: "bank",
      direction: "production",
      word_count: 1,
      image_url: "banka.jpg",
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByRole } = render(ReviewPage);
    await findByRole("button", { name: "Show" });
    expect(screen.queryByRole("img")).not.toBeNull();
  });

  it("production word_count>1 shows L1 translation as prompt", async () => {
    const item = makeReviewQueueItem({
      id: 11,
      text: "dober dan",
      translation: "good day",
      direction: "production",
      word_count: 2,
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByText } = render(ReviewPage);
    expect(await findByText("good day")).toBeTruthy();
  });

  it("production word_count=1 without image_url shows L1 translation as prompt", async () => {
    const item = makeReviewQueueItem({
      id: 12,
      text: "banka",
      translation: "bank",
      direction: "production",
      word_count: 1,
      image_url: null,
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByText } = render(ReviewPage);
    expect(await findByText("bank")).toBeTruthy();
  });

  // ── queue-stats breakdown display (Anki-style widget) ──────────────

  it("shows Anki-style widget with three counts", async () => {
    mockFetchQueueStats.mockResolvedValue({
      new: 7,
      learning: 5,
      review: 10,
      daily_new_cap: 30,
      cap_source: "cache",
      fsrs_source: "cache",
    });
    const { findByText } = render(ReviewPage);
    expect(await findByText("7")).toBeTruthy();
    expect(await findByText("5")).toBeTruthy();
    expect(await findByText("10")).toBeTruthy();
  });

  it("shows source label when cap_source is not anki", async () => {
    mockFetchQueueStats.mockResolvedValue({
      new: 5,
      learning: 2,
      review: 3,
      daily_new_cap: 20,
      cap_source: "default",
      fsrs_source: "default",
    });
    const { findByText } = render(ReviewPage);
    expect(await findByText(/\(default\)/)).toBeTruthy();
  });

  it("does not show source label when cap_source is cache", async () => {
    mockFetchQueueStats.mockResolvedValue({
      new: 5,
      learning: 2,
      review: 3,
      daily_new_cap: 30,
      cap_source: "cache",
      fsrs_source: "cache",
    });
    const { queryByText, findByText } = render(ReviewPage);
    await findByText("5");
    expect(queryByText(/\(cache\)/)).toBeFalsy();
  });

  it("shows source label when cap_source is config", async () => {
    mockFetchQueueStats.mockResolvedValue({
      new: 5,
      learning: 2,
      review: 3,
      daily_new_cap: 20,
      cap_source: "config",
      fsrs_source: "default",
    });
    const { findByText } = render(ReviewPage);
    expect(await findByText(/\(config\)/)).toBeTruthy();
  });

  // ── FSRS source indicator ───────────────────────────────────────────

  it("shows FSRS: defaults when fsrs_source is not cache", async () => {
    mockFetchQueueStats.mockResolvedValue({
      new: 5,
      learning: 2,
      review: 3,
      daily_new_cap: 30,
      cap_source: "cache",
      fsrs_source: "default",
    });
    const { findByText } = render(ReviewPage);
    expect(await findByText(/FSRS: defaults/)).toBeTruthy();
  });

  it("does not show FSRS marker when fsrs_source is cache", async () => {
    mockFetchQueueStats.mockResolvedValue({
      new: 5,
      learning: 2,
      review: 3,
      daily_new_cap: 30,
      cap_source: "cache",
      fsrs_source: "cache",
    });
    const { queryByText, findByText } = render(ReviewPage);
    await findByText("5");
    expect(queryByText(/FSRS:/)).toBeFalsy();
  });

  // ── server-driven sibling burying ──────────────────────────────────────
  // Sibling-bury is the server's responsibility (proactive bury in queue
  // builder + state=buried after sync). The frontend just renders queue[0].

  it("shows whatever the server returns after grade — sibling absent if server buried it", async () => {
    const prasicRec = makeReviewQueueItem({
      id: 202,
      text: "prašič",
      translation: "pig",
      direction: "recognition",
    });
    const vlakRec = makeReviewQueueItem({
      id: 251,
      text: "vlak",
      translation: "train",
      direction: "recognition",
    });
    mockFetchReviewQueue
      .mockResolvedValueOnce({ queue: [prasicRec, vlakRec] })
      .mockResolvedValueOnce({ queue: [vlakRec] }); // server buried prašič production sibling
    const { findByRole, findByText } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(await findByText("vlak")).toBeTruthy();
  });

  // ── deferred learning (server-driven) ─────────────────────────────────

  it("does not surface a learning card the server placed in pending_learning", async () => {
    // Anki parity: when a learning card's due_at is in the future relative to
    // the server's frozen cutoff, the server puts it at the tail of the queue
    // (after reviews/new). The user sees the next eligible card, not the just-
    // graded card.
    const item1 = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
    const item2 = makeReviewQueueItem({ id: 3, text: "hiša", direction: "recognition" });
    const oknoPending = makeReviewQueueItem({
      id: 1,
      text: "okno",
      direction: "recognition",
      state: "learning",
    });
    mockSubmitDrill.mockResolvedValue({
      new_due_at: "2026-04-25",
      new_state: "learning",
      left: 1002,
    });
    mockFetchReviewQueue
      .mockResolvedValueOnce({ queue: [item1, item2] })
      .mockResolvedValueOnce({ queue: [item2, oknoPending] });
    const { findByRole, findByText } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Again" }));
    expect(await findByText("hiša")).toBeTruthy();
  });

  it("idle time alone does not preempt the displayed card (Anki parity)", async () => {
    // Anki freezes current_learning_cutoff between grades, so a card whose
    // timer ticks past-due while the user idles must not preempt the current
    // card. Refactored frontend: refetch only happens on grade events, so
    // idle wall-clock advance just doesn't trigger anything.
    vi.useFakeTimers();
    const t0 = Date.parse("2026-05-04T10:00:00Z");
    vi.setSystemTime(t0);

    const item1 = makeReviewQueueItem({
      id: 1,
      text: "okno",
      translation: "window",
      direction: "recognition",
    });
    const item2 = makeReviewQueueItem({
      id: 3,
      text: "hiša",
      translation: "house",
      direction: "recognition",
    });
    mockFetchReviewQueue
      .mockResolvedValueOnce({ queue: [item1, item2] })
      .mockResolvedValueOnce({ queue: [item2] });
    mockSubmitDrill.mockResolvedValue({
      new_due_at: new Date(t0 + 60_000).toISOString(),
      new_state: "learning",
      left: 1002,
    });
    const { findByRole, findByText, queryByText } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Again" }));
    expect(await findByText("hiša")).toBeTruthy();

    // Wall clock advances; no grade, no refetch.
    vi.advanceTimersByTime(60_000 + 100);

    expect(await findByText("hiša")).toBeTruthy();
    expect(queryByText("okno")).toBeNull();
    vi.useRealTimers();
  });

  it("refetches stats and queue after rating", async () => {
    const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const queueCallsBefore = mockFetchReviewQueue.mock.calls.length;
    const statsCallsBefore = mockFetchQueueStats.mock.calls.length;
    const { findByRole } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(mockFetchReviewQueue.mock.calls.length).toBeGreaterThan(queueCallsBefore);
    expect(mockFetchQueueStats.mock.calls.length).toBeGreaterThan(statsCallsBefore);
  });

  it("mount call passes sessionStart=true; grade refetch does not", async () => {
    // Anki parity: page mount = "deck open", advances the server-side cutoff.
    // Subsequent per-grade refetches must keep the cutoff frozen between grades.
    const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByRole } = render(ReviewPage);
    await findByRole("button", { name: "Show" });
    expect(mockFetchReviewQueue).toHaveBeenNthCalledWith(1, { sessionStart: true });
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(mockFetchReviewQueue).toHaveBeenNthCalledWith(2, { sessionStart: false });
  });

  it("graduated card (server omits it) does not resurface", async () => {
    mockSubmitDrill.mockResolvedValue({ new_due_at: "2026-04-25", new_state: "review" });
    const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
    mockFetchReviewQueue
      .mockResolvedValueOnce({ queue: [item] })
      .mockResolvedValueOnce({ queue: [] });
    const { findByRole, findByText } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(await findByText(/Done for today/)).toBeTruthy();
  });

  it("learning card resurfaces when server promotes it past the cutoff", async () => {
    // Mirrors the svetilka/obraz scenario: after grading the next card,
    // the server's cutoff advances, a previously-pending learning card
    // becomes ready, and the next /review-queue puts it at the head.
    const item1 = makeReviewQueueItem({
      id: 1,
      text: "okno",
      translation: "window",
      direction: "recognition",
    });
    const item2 = makeReviewQueueItem({
      id: 3,
      text: "hiša",
      translation: "house",
      direction: "recognition",
    });
    const oknoLearning = makeReviewQueueItem({
      id: 1,
      text: "okno",
      direction: "recognition",
      state: "learning",
    });
    mockSubmitDrill
      .mockResolvedValueOnce({
        new_due_at: "2026-04-25",
        new_state: "learning",
        left: 1002,
      })
      .mockResolvedValueOnce({ new_due_at: "2026-04-25", new_state: "review" });
    mockFetchReviewQueue
      .mockResolvedValueOnce({ queue: [item1, item2] })
      .mockResolvedValueOnce({ queue: [item2, oknoLearning] }) // okno still pending after first grade
      .mockResolvedValueOnce({ queue: [oknoLearning] }); // cutoff advanced; okno now ready
    const { findByRole, findByText } = render(ReviewPage);

    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Again" }));
    expect(await findByText("hiša")).toBeTruthy();

    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));
    expect(await findByText("okno")).toBeTruthy();
  });

  it("displays state badge with correct text and class", async () => {
    const item = makeReviewQueueItem({
      id: 1,
      text: "okno",
      state: "learning",
      direction: "recognition",
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByText } = render(ReviewPage);
    const badge = await findByText("learning");
    expect(badge).toBeTruthy();
    expect(badge.className).toContain("state-learning");
  });

  // ── tab-visibility refetch ─────────────────────────────────────────────
  // /queue-stats reads Anki's collection.anki2 directly each call, so it stays
  // fresh as the user grades in Anki — but the widget only sees those numbers
  // if the page refetches. Without a visibility hook, switching back to the TT
  // tab after grading in Anki shows the stale mount-time counts.

  it("refetches stats and queue when the tab becomes visible again", async () => {
    const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    render(ReviewPage);
    // Wait for mount fetch to settle.
    await screen.findByText("okno");
    const statsCallsBefore = mockFetchQueueStats.mock.calls.length;
    const queueCallsBefore = mockFetchReviewQueue.mock.calls.length;

    // Simulate tab regaining focus after a stint in Anki.
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    // Let the async refetch settle.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockFetchQueueStats.mock.calls.length).toBeGreaterThan(statsCallsBefore);
    expect(mockFetchReviewQueue.mock.calls.length).toBeGreaterThan(queueCallsBefore);
  });

  it("visibility refetch does not advance learning cutoff (sessionStart=false)", async () => {
    // Mid-session tab refocus is not a "deck open" — must not advance the
    // server's frozen learning cutoff, which would surface past-due learning
    // cards mid-screen and diverge from Anki.
    const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    render(ReviewPage);
    await screen.findByText("okno");

    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    const lastCall = mockFetchReviewQueue.mock.calls.at(-1);
    expect(lastCall).toEqual([{ sessionStart: false }]);
  });

  it("does not refetch when the tab transitions to hidden", async () => {
    const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    render(ReviewPage);
    await screen.findByText("okno");
    const statsCallsBefore = mockFetchQueueStats.mock.calls.length;

    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockFetchQueueStats.mock.calls.length).toBe(statsCallsBefore);
  });
});
