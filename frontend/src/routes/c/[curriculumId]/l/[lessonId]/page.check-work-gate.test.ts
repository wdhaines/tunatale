/**
 * Pins the one-shot-per-listen GATE on the lesson page: the "Check your work"
 * link is shown iff the lesson-scoped queue is non-empty AND the server reports
 * has_unreviewed_listen === true. The core regression this guards: a non-empty
 * queue with has_unreviewed_listen === false must NOT show the link (that is the
 * "it comes back day after day" bug).
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
import { listenedStore } from "$lib/stores/listened.svelte";
import { syncStore } from "$lib/stores/sync.svelte";
import { lessonModePref } from "$lib/stores/lessonModePref.svelte";
import { pipelineStore } from "$lib/stores/pipeline.svelte";
import Page from "./+page.svelte";
import { curriculum, lesson, audio, transcript, stubViewport } from "./page-test-helpers";

const mockGetListens = vi.mocked(api.getListens);
const mockGetTranscript = vi.mocked(api.getLessonTranscript);
const mockFetchLessonReviewQueue = vi.mocked(api.fetchLessonReviewQueue);

async function seedListened(lessonId: string, count: number) {
  mockGetListens.mockResolvedValueOnce({
    lessons: [
      { lesson_id: lessonId, listen_count: count, last_listened_at: "2026-01-01T00:00:00Z" },
    ],
  });
  await listenedStore.hydrate();
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  stubViewport(false);
  lessonModePref.set("read");
  localStorage.clear();
  syncStore.notify(null);
  (pipelineStore as unknown as { status: unknown }).status = null;
  listenedStore.reset();
  mockFetchLessonReviewQueue.mockReset();
  mockGetTranscript.mockReturnValue(new Promise<TranscriptData>(() => {}));
});

describe("check-your-work link — one-shot-per-listen gate", () => {
  it("HIDES the link when the queue is non-empty but has_unreviewed_listen is false", async () => {
    await seedListened("l1", 3);
    mockFetchLessonReviewQueue.mockResolvedValue({
      queue: [{ id: 1 } as never, { id: 2 } as never],
      has_unreviewed_listen: false,
    });

    const { queryByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });

    // Give the queue $effect time to resolve, then assert the link never appears.
    await new Promise((r) => setTimeout(r, 0));
    await waitFor(() => {
      expect(queryByText(/Check your work/)).toBeNull();
    });
  });

  it("SHOWS the link when the queue is non-empty and has_unreviewed_listen is true", async () => {
    await seedListened("l1", 3);
    mockFetchLessonReviewQueue.mockResolvedValue({
      queue: [{ id: 1 } as never, { id: 2 } as never],
      has_unreviewed_listen: true,
    });

    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });

    await waitFor(() => {
      expect(getByText(/Check your work/).textContent).toContain("2 words");
    });
  });
});
