/**
 * Tests for the unified /review route.
 *
 * Model: the server (`/review-queue`) is the source of truth. The frontend
 * fetches the queue on mount and after every grade, and always renders
 * `queue[0]`. Sibling burying, deferred-learning ordering (pending vs ready),
 * and newSpread are all the server's job. These tests therefore mock
 * `fetchReviewQueue` per call to model what the server would return at each
 * step of the session.
 *
 * Lesson mode (C1): `?lesson=X` in the URL switches from `fetchReviewQueue`
 * to `fetchLessonReviewQueue` for ALL refetches. The lesson mode never calls
 * `fetchReviewQueue` — it is read-only server-side and never advances the
 * learning cutoff.
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

// Mutable URL search params — individual tests set ?lesson= to enter lesson mode.
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
    commitPending: vi.fn(),
    getLesson: vi.fn(),
  },
}));

import { api } from "$lib/api";
import type { LessonDetail, ReviewQueueItem } from "$lib/api";
import { syncStore } from "$lib/stores/sync.svelte";
const mockFetchQueueStats = vi.mocked(api.fetchQueueStats);
const mockFetchReviewQueue = vi.mocked(api.fetchReviewQueue);
const mockFetchLessonReviewQueue = vi.mocked(api.fetchLessonReviewQueue);
const mockSubmitDrill = vi.mocked(api.submitDrill);
const mockCommitPending = vi.mocked(api.commitPending);
const mockMarkLessonReviewed = vi.mocked(api.markLessonReviewed);
const mockGetLesson = vi.mocked(api.getLesson);

// The header only reads `title`; the rest satisfies LessonDetail's shape.
const lessonDetail = (title: string): LessonDetail => ({
  id: "lesson-abc",
  day: 4,
  title,
  language_code: "no",
  sections: [],
  key_phrases: [],
});
import { makeReviewQueueItem } from "../../test/factories";

beforeEach(() => {
  vi.clearAllMocks();
  // Default: global mode (no ?lesson= param).
  const keys = Array.from(urlParams.keys());
  for (const k of keys) urlParams.delete(k);
  syncStore.notify(null);
  mockFetchQueueStats.mockResolvedValue({
    new: 0,
    learning: 0,
    review: 0,
    daily_new_cap: 20,
    cap_source: "default",
    fsrs_source: "default",
  });
  mockFetchReviewQueue.mockResolvedValue({ queue: [] });
  mockFetchLessonReviewQueue.mockResolvedValue({ queue: [], has_unreviewed_listen: false });
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
    expect(mockSubmitDrill).toHaveBeenCalledWith(
      5,
      "recognition",
      "good",
      expect.any(Number),
      false,
    );
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
    expect(mockSubmitDrill).toHaveBeenCalledWith(
      7,
      "production",
      "good",
      expect.any(Number),
      false,
    );
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

  it("keeps the just-graded card in place until the next card loads (no prompt flash)", async () => {
    // Regression: `reviewed` drives the {#key} that resets the DrillCard. If it
    // bumps before the refetch resolves, the card is torn down and rebuilt with
    // the *old* item in its unrevealed state (prompt image jumps back to full
    // size) for the whole network round-trip — a visible flash. The grade must
    // refetch first, then re-key once, so the graded card stays put until the
    // next card is ready.
    const item = makeReviewQueueItem({
      id: 1,
      text: "okno",
      translation: "window",
      direction: "recognition",
    });
    let resolveSecond!: (value: { queue: ReviewQueueItem[] }) => void;
    const pendingSecond = new Promise<{ queue: ReviewQueueItem[] }>((resolve) => {
      resolveSecond = resolve;
    });
    mockFetchReviewQueue
      .mockResolvedValueOnce({ queue: [item] })
      .mockReturnValueOnce(pendingSecond);
    const { findByRole, queryByRole } = render(ReviewPage);
    await fireEvent.click(await findByRole("button", { name: "Show" }));
    await fireEvent.click(await findByRole("button", { name: "Good" }));

    // Refetch is still in flight: the graded card stays revealed (rating buttons
    // visible), not re-keyed back to an unrevealed "Show" prompt.
    expect(await findByRole("button", { name: "Good" })).toBeTruthy();
    expect(queryByRole("button", { name: "Show" })).toBeNull();

    resolveSecond({ queue: [] });
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

  // ── deep-link to the Cards viewer ──────────────────────────────────────
  // An unobtrusive link from the card under review to its entry in the Cards
  // table. Carries the item id (for precise highlight) plus its text as the
  // search term (so the row lands on the first page of the filtered list).

  it("shows a 'Card details' link to the cards viewer for the current item", async () => {
    const item = makeReviewQueueItem({
      id: 5,
      text: "voda",
      translation: "water",
      direction: "recognition",
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByRole } = render(ReviewPage);
    const link = await findByRole("link", { name: /card details/i });
    expect(link.getAttribute("href")).toBe("/cards?focus=5&q=voda");
  });

  it("opens the cards link in a new tab (does not leave the review session)", async () => {
    const item = makeReviewQueueItem({
      id: 5,
      text: "voda",
      translation: "water",
      direction: "recognition",
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByRole } = render(ReviewPage);
    const link = await findByRole("link", { name: /card details/i });
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("url-encodes the search term for multi-word cards", async () => {
    const item = makeReviewQueueItem({
      id: 8,
      text: "dober dan",
      translation: "good day",
      direction: "production",
    });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    const { findByRole } = render(ReviewPage);
    const link = await findByRole("link", { name: /card details/i });
    expect(link.getAttribute("href")).toBe("/cards?focus=8&q=dober%20dan");
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

  // A peer-sync rebuilds the server's frozen queue at sync time. The header badge
  // refetches via the layout's syncStore subscription, but the review *body* must
  // refetch too — otherwise the queue shows pre-sync cards until you navigate away
  // and back (user report: "header updates counts but the body doesn't").
  it("refetches stats and queue when a sync completes", async () => {
    const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    render(ReviewPage);
    await screen.findByText("okno");
    const statsCallsBefore = mockFetchQueueStats.mock.calls.length;
    const queueCallsBefore = mockFetchReviewQueue.mock.calls.length;

    // SyncButton calls syncStore.notify(result) on a successful peer-sync.
    syncStore.notify({ ok: true, pushed: 0, pulled: 1, created: 0, message: "" } as never);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockFetchQueueStats.mock.calls.length).toBeGreaterThan(statsCallsBefore);
    expect(mockFetchReviewQueue.mock.calls.length).toBeGreaterThan(queueCallsBefore);
  });

  it("sync refetch does not advance the learning cutoff (sessionStart=false)", async () => {
    // sync_pull already rebuilt the frozen queue and advanced the cutoff server-side;
    // the body just pulls that result — it must not trigger a second rebuild.
    const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
    render(ReviewPage);
    await screen.findByText("okno");

    syncStore.notify({ ok: true, pushed: 0, pulled: 1, created: 0, message: "" } as never);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockFetchReviewQueue.mock.calls.at(-1)).toEqual([{ sessionStart: false }]);
  });

  // ── C1: lesson mode (?lesson= URL param) ─────────────────────────────────
  // When mounted with ?lesson=X, the review page fetches the lesson-scoped
  // queue via fetchLessonReviewQueue instead of fetchReviewQueue. This is
  // read-only server-side and never advances the learning cutoff.

  describe("C1 — lesson mode", () => {
    beforeEach(() => {
      urlParams.set("lesson", "lesson-abc");
    });

    // ── Header identity (2026-07-27) ──────────────────────────────────
    // The nav already renders QueueStatsWidget for the WHOLE collection. Lesson
    // mode used to render the same widget for a different scope, so identical
    // glyphs meant two different things and the page one carried no label at
    // all. Lesson mode now names itself and links back to the lesson instead.

    it("lesson mode heads the page 'Check your work', not 'Review'", async () => {
      mockGetLesson.mockResolvedValue(lessonDetail("Day 4: Interview with a Neighbor"));
      const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });

      const { findByText, queryByText } = render(ReviewPage);
      await findByText("okno");

      expect(await findByText("Check your work")).toBeTruthy();
      expect(queryByText("Review")).toBeNull();
    });

    it("lesson mode links its title back to the lesson page", async () => {
      mockGetLesson.mockResolvedValue(lessonDetail("Day 4: Interview with a Neighbor"));
      urlParams.set("c", "curriculum-xyz");
      const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });

      const { findByText } = render(ReviewPage);
      const link = await findByText("Day 4: Interview with a Neighbor");

      expect(link.getAttribute("href")).toBe("/c/curriculum-xyz/l/lesson-abc");
    });

    it("lesson mode does not render the queue-stats widget", async () => {
      // The whole point of the split: no "0 + 0 + 105" competing with the nav.
      mockGetLesson.mockResolvedValue(lessonDetail("Day 4: Interview with a Neighbor"));
      const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });

      const { container, findByText } = render(ReviewPage);
      await findByText("okno");

      expect(container.querySelector(".queue-stats")).toBeNull();
    });

    it("a failed lesson-title fetch still renders the header", async () => {
      mockGetLesson.mockRejectedValue(new Error("boom"));
      const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });

      const { findByText } = render(ReviewPage);
      await findByText("okno");

      expect(await findByText("Check your work")).toBeTruthy();
    });

    it("mount with ?lesson=X never calls fetchReviewQueue", async () => {
      const item = makeReviewQueueItem({
        id: 1,
        text: "okno",
        translation: "window",
        direction: "recognition",
      });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });
      render(ReviewPage);
      await screen.findByText("okno");

      expect(mockFetchLessonReviewQueue).toHaveBeenCalledWith("lesson-abc");
      expect(mockFetchReviewQueue).not.toHaveBeenCalled();
    });

    it("after a grade in lesson mode, fetchLessonReviewQueue refetches and fetchReviewQueue still never called", async () => {
      const item = makeReviewQueueItem({
        id: 1,
        text: "okno",
        translation: "window",
        direction: "recognition",
      });
      mockFetchLessonReviewQueue
        .mockResolvedValueOnce({ queue: [item], has_unreviewed_listen: true })
        .mockResolvedValueOnce({ queue: [], has_unreviewed_listen: false });
      const { findByRole } = render(ReviewPage);
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.click(await findByRole("button", { name: "Good" }));

      expect(mockFetchLessonReviewQueue.mock.calls.length).toBeGreaterThanOrEqual(2);
      expect(mockFetchReviewQueue).not.toHaveBeenCalled();
    });

    it("rating in lesson mode goes through submitDrill and refetches the lesson queue", async () => {
      const item = makeReviewQueueItem({
        id: 5,
        text: "voda",
        translation: "water",
        direction: "recognition",
      });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });
      const { findByRole } = render(ReviewPage);
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.click(await findByRole("button", { name: "Good" }));

      expect(mockSubmitDrill).toHaveBeenCalledWith(
        5,
        "recognition",
        "good",
        expect.any(Number),
        true,
      );
      // After grading, the lesson queue was refetched (before reviewed++ incremented the key)
      expect(mockFetchLessonReviewQueue.mock.calls.length).toBeGreaterThanOrEqual(2);
    });

    it("done state shows '← Back to lesson' link when ?c= param is present", async () => {
      urlParams.set("lesson", "lesson-abc");
      urlParams.set("c", "curriculum-xyz");
      const { findByText } = render(ReviewPage);

      const link = await findByText("← Back to lesson");
      expect(link.getAttribute("href")).toBe("/c/curriculum-xyz/l/lesson-abc");
    });

    it("done state shows '← Home' when no ?c= param", async () => {
      // ?lesson is set but no ?c=
      const { findByText } = render(ReviewPage);

      const link = await findByText("← Home");
      expect(link.getAttribute("href")).toBe("/");
    });

    it("pre-fills the card's suggested rating button from a gradeable pending_rating", async () => {
      const item = makeReviewQueueItem({
        id: 40,
        text: "sonce",
        direction: "recognition",
        pending_rating: "easy",
      });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });

      const { findByRole, container } = render(ReviewPage);
      await fireEvent.click(await findByRole("button", { name: "Show" }));

      expect(container.querySelector(".btn-easy")?.classList.contains("suggested")).toBe(true);
      expect(container.querySelector(".btn-good")?.classList.contains("suggested")).toBe(false);
    });

    it("treats a 'skip' pending_rating as no suggestion on the card", async () => {
      const item = makeReviewQueueItem({
        id: 41,
        text: "luna",
        direction: "recognition",
        pending_rating: "skip",
      });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });

      const { findByRole, container } = render(ReviewPage);
      await fireEvent.click(await findByRole("button", { name: "Show" }));

      expect(container.querySelectorAll(".suggested").length).toBe(0);
    });

    it("Sync it button calls commitPending (atomic) and refreshes queue", async () => {
      vi.stubGlobal("confirm", () => true);
      const itemA = makeReviewQueueItem({
        id: 10,
        text: "kava",
        direction: "recognition",
        pending_rating: "good",
      });
      const itemB = makeReviewQueueItem({
        id: 11,
        text: "mleko",
        direction: "production",
        pending_rating: "easy",
      });
      mockFetchLessonReviewQueue
        .mockResolvedValueOnce({ queue: [itemA, itemB], has_unreviewed_listen: true })
        .mockResolvedValueOnce({ queue: [], has_unreviewed_listen: false });
      mockCommitPending.mockResolvedValue({ status: "ok", applied: 2 });

      const { findByText } = render(ReviewPage);

      const syncBtn = await findByText("Accept all");
      expect(syncBtn).toBeTruthy();
      expect(await findByText("2 words pre-graded by your listen")).toBeTruthy();

      await fireEvent.click(syncBtn);

      await vi.waitFor(() => {
        expect(mockCommitPending).toHaveBeenCalledWith("lesson-abc");
      });
    });

    // F2: "Sync it" is the only irreversible action in the flow (writes FSRS
    // state + revlog + dirty_fsrs for every pending card, no undo) — it must
    // be gated behind a confirm(), same idiom as cards/+page.svelte's
    // deleteItem/resetItem/bulkDelete.

    it("Sync it does nothing when the user cancels the confirm dialog", async () => {
      vi.stubGlobal("confirm", () => false);
      const item = makeReviewQueueItem({
        id: 12,
        text: "hvala",
        direction: "recognition",
        pending_rating: "good",
      });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });

      const { findByText } = render(ReviewPage);
      const syncBtn = await findByText("Accept all");
      await fireEvent.click(syncBtn);

      // Give any accidental async work a chance to run before asserting absence.
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(mockCommitPending).not.toHaveBeenCalled();
    });

    it("Sync it confirm message names the pending count", async () => {
      const confirmSpy = vi.fn(() => false);
      vi.stubGlobal("confirm", confirmSpy);
      const itemA = makeReviewQueueItem({
        id: 13,
        text: "sonce",
        direction: "recognition",
        pending_rating: "good",
      });
      const itemB = makeReviewQueueItem({
        id: 14,
        text: "luna",
        direction: "recognition",
        pending_rating: "easy",
      });
      mockFetchLessonReviewQueue.mockResolvedValue({
        queue: [itemA, itemB],
        has_unreviewed_listen: true,
      });

      const { findByText } = render(ReviewPage);
      const syncBtn = await findByText("Accept all");
      await fireEvent.click(syncBtn);

      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("2"));
    });

    it("Sync it commits when the user confirms", async () => {
      vi.stubGlobal("confirm", () => true);
      const item = makeReviewQueueItem({
        id: 15,
        text: "voda",
        direction: "recognition",
        pending_rating: "good",
      });
      mockFetchLessonReviewQueue
        .mockResolvedValueOnce({ queue: [item], has_unreviewed_listen: true })
        .mockResolvedValueOnce({ queue: [], has_unreviewed_listen: false });
      mockCommitPending.mockResolvedValue({ status: "ok", applied: 1 });

      const { findByText } = render(ReviewPage);
      const syncBtn = await findByText("Accept all");
      await fireEvent.click(syncBtn);

      await vi.waitFor(() => {
        expect(mockCommitPending).toHaveBeenCalledWith("lesson-abc");
      });
    });

    // F3: committing drains the queue too (released cards are graded-today
    // and drop out server-side), so the one-shot "lesson reviewed" post must
    // fire from syncPending as well as rate(), or has_unreviewed_listen
    // stays true forever.

    it("marks the lesson reviewed exactly once after Sync it drains the queue", async () => {
      vi.stubGlobal("confirm", () => true);
      const item = makeReviewQueueItem({
        id: 16,
        text: "kruh",
        direction: "recognition",
        pending_rating: "good",
      });
      mockFetchLessonReviewQueue
        .mockResolvedValueOnce({ queue: [item], has_unreviewed_listen: true })
        .mockResolvedValueOnce({ queue: [], has_unreviewed_listen: false });
      mockCommitPending.mockResolvedValue({ status: "ok", applied: 1 });

      const { findByText } = render(ReviewPage);
      const syncBtn = await findByText("Accept all");
      await fireEvent.click(syncBtn);

      await vi.waitFor(() => {
        expect(mockMarkLessonReviewed).toHaveBeenCalledTimes(1);
        expect(mockMarkLessonReviewed).toHaveBeenCalledWith("lesson-abc");
      });
    });

    it("does not mark the lesson reviewed when Sync it leaves cards in the queue", async () => {
      vi.stubGlobal("confirm", () => true);
      const itemA = makeReviewQueueItem({
        id: 17,
        text: "sir",
        direction: "recognition",
        pending_rating: "good",
      });
      const itemB = makeReviewQueueItem({
        id: 18,
        text: "jajce",
        direction: "recognition",
        pending_rating: null,
      });
      mockFetchLessonReviewQueue
        .mockResolvedValueOnce({ queue: [itemA, itemB], has_unreviewed_listen: true })
        .mockResolvedValueOnce({ queue: [itemB], has_unreviewed_listen: true });
      mockCommitPending.mockResolvedValue({ status: "ok", applied: 1 });

      const { findByText } = render(ReviewPage);
      const syncBtn = await findByText("Accept all");
      await fireEvent.click(syncBtn);

      await vi.waitFor(() => {
        expect(mockCommitPending).toHaveBeenCalled();
      });
      expect(mockMarkLessonReviewed).not.toHaveBeenCalled();
    });

    it("Sync it button is hidden when no pending-rated items", async () => {
      const item = makeReviewQueueItem({
        id: 20,
        text: "hvala",
        direction: "recognition",
        pending_rating: null,
      });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });

      const { queryByText } = render(ReviewPage);

      await vi.waitFor(() => {
        expect(queryByText("Sync it")).toBeNull();
      });
    });

    it("Sync it shows error when commitPending fails", async () => {
      vi.stubGlobal("confirm", () => true);
      const item = makeReviewQueueItem({
        id: 30,
        text: "voda",
        direction: "recognition",
        pending_rating: "good",
      });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });
      mockCommitPending.mockRejectedValue(new Error("sync boom"));

      const { findByText } = render(ReviewPage);

      const syncBtn = await findByText("Accept all");
      await fireEvent.click(syncBtn);

      expect(await findByText("sync boom")).toBeTruthy();
    });

    it("Sync it shows stringified non-Error when commitPending rejects", async () => {
      vi.stubGlobal("confirm", () => true);
      const item = makeReviewQueueItem({
        id: 31,
        text: "kruh",
        direction: "production",
        pending_rating: "easy",
      });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [item], has_unreviewed_listen: true });
      mockCommitPending.mockRejectedValue("plain sync error");

      const { findByText } = render(ReviewPage);

      const syncBtn = await findByText("Accept all");
      await fireEvent.click(syncBtn);

      expect(await findByText("plain sync error")).toBeTruthy();
    });

    it("fetchLessonReviewQueue rejecting (e.g. 404) lands in the error state", async () => {
      mockFetchLessonReviewQueue.mockRejectedValue(new Error("Lesson not found"));
      const { findByText } = render(ReviewPage);

      expect(await findByText("Lesson not found")).toBeTruthy();
    });

    // Retired 2026-07-27: "scoped review shows scoped counts in the header
    // widget, not global counts" pinned a lesson-scoped QueueStatsWidget that
    // no longer renders. Lesson mode shows a titled "Check your work" header
    // instead — the widget duplicated the nav badge's exact glyphs for a
    // different scope, and with the queue now equal to the pending set there is
    // no second count to display. Covered by "lesson mode does not render the
    // queue-stats widget" above.
  });

  it("unscoped review shows global counts in the header widget", async () => {
    mockFetchQueueStats.mockResolvedValue({
      new: 3,
      learning: 1,
      review: 7,
      daily_new_cap: 20,
      cap_source: "default",
      fsrs_source: "default",
    });
    const item = makeReviewQueueItem({ id: 1, text: "okno", direction: "recognition" });
    mockFetchReviewQueue.mockResolvedValue({ queue: [item] });

    const { container } = render(ReviewPage);
    await screen.findByText("okno");

    const widget = container.querySelector(".queue-stats");
    expect(widget).not.toBeNull();
    expect(widget!.querySelector(".new")!.textContent).toBe("3");
    expect(widget!.querySelector(".learning")!.textContent).toBe("1");
    expect(widget!.querySelector(".review")!.textContent).toBe("7");
  });
});
