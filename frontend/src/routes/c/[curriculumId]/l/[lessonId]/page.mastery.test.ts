/**
 * Tests for /c/[curriculumId]/l/[lessonId] — lesson view: the B1
 * check-your-work / fully-acquired link and the B2 mastery indicator.
 *
 * Split from page.test.ts (item 14, Phase B) — see page-test-helpers.ts for
 * the shared $lib/api / pipeline mock factories and fixtures.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

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
import type { TranscriptData } from "$lib/api";
import { listenedStore } from "$lib/stores/listened.svelte";
import { syncStore } from "$lib/stores/sync.svelte";
import { lessonModePref } from "$lib/stores/lessonModePref.svelte";
import { pipelineStore } from "$lib/stores/pipeline.svelte";
import Page from "./+page.svelte";
import { curriculum, lesson, audio, transcript, stubViewport } from "./page-test-helpers";

const mockMarkAsListened = vi.mocked(api.markAsListened);
const mockGetListens = vi.mocked(api.getListens);

/** Seed the real listenedStore as if `lessonId` had been listened to `count` times. */
async function seedListened(lessonId: string, count: number) {
  mockGetListens.mockResolvedValueOnce({
    lessons: [
      { lesson_id: lessonId, listen_count: count, last_listened_at: "2026-01-01T00:00:00Z" },
    ],
  });
  await listenedStore.hydrate();
}

const mockGetTranscript = vi.mocked(api.getLessonTranscript);
const mockFetchLessonReviewQueue = vi.mocked(api.fetchLessonReviewQueue);

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
  mockMarkAsListened.mockReset();
  mockFetchLessonReviewQueue.mockReset();
  // When load supplies no transcript the component fetches it on mount. Default
  // to a pending promise so null-transcript renders sit in the loading state
  // without injecting content; tests that care override this.
  mockGetTranscript.mockReturnValue(new Promise<TranscriptData>(() => {}));
});

describe("/c/[curriculumId]/l/[lessonId] page", () => {
  describe("B1 — check-your-work link and fully-acquired state", () => {
    it("shows 'Check your work — review N words' link when listened and N > 0", async () => {
      await seedListened("l1", 3);
      mockFetchLessonReviewQueue.mockResolvedValue({
        queue: [{ id: 1 } as never, { id: 2 } as never],
      });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      fireEvent.click(getByText("Listen"));

      // $effect fetches queue asynchronously — wait for queueCount to update
      await waitFor(() => {
        const link = getByText(/Check your work/);
        expect(link.textContent).toContain("2 words");
        expect(link.getAttribute("href")).toBe("/review?lesson=l1&c=cid-1");
      });
    });

    it("does not show check-your-work when N = 0", async () => {
      await seedListened("l1", 2);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { queryByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });

      await waitFor(() => {
        expect(queryByText(/Check your work/)).toBeNull();
      });
    });

    // C2: reload-state fix — when already listened on reload (no listen this
    // session) and the queue is empty, show the enabled "Mark as Listened"
    // button, not a "review 0 words" link.
    it("C2: already-listened reload with empty queue shows 'Mark as Listened', not check-work link", async () => {
      await seedListened("l1", 1);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { getByText, queryByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      fireEvent.click(getByText("Listen"));

      await waitFor(() => {
        // No check-work link when queue is empty
        expect(queryByText(/Check your work/)).toBeNull();
        // Enabled "Mark as Listened" button (not disabled, no "✓ Listened")
        const btn = getByText("Mark as Listened");
        expect(btn).toBeTruthy();
        expect((btn as HTMLButtonElement).disabled).toBe(false);
      });
    });

    it("fetches review queue on mount when already listened", async () => {
      await seedListened("l1", 1);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });

      await waitFor(() => {
        expect(mockFetchLessonReviewQueue).toHaveBeenCalledWith("l1");
      });
    });

    it("refetches review queue after each listen", async () => {
      mockMarkAsListened.mockResolvedValue({
        status: "ok",
        registered: 1,
        created: 0,
        graded: 1,
        remaining_candidates: 2,
        listen_count: 4,
      });
      mockGetTranscript.mockResolvedValue(transcript);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [{} as never] });

      const { getByText, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      fireEvent.click(getByText("Listen"));
      await fireEvent.click(await findByText("Mark as Listened"));

      await waitFor(() => {
        expect(mockFetchLessonReviewQueue).toHaveBeenCalledWith("l1");
      });
    });

    it("shows '✓ Listened (n×)' when fully acquired (remaining=0 AND N=0)", async () => {
      await seedListened("l1", 5);
      mockMarkAsListened.mockResolvedValue({
        status: "ok",
        registered: 1,
        created: 0,
        graded: 1,
        remaining_candidates: 0,
        listen_count: 5,
      });
      mockGetTranscript.mockResolvedValue(transcript);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { getByText, queryByText, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      fireEvent.click(getByText("Listen"));
      // Need a listen to set listenResult — only then can fullyAcquired be true.
      await fireEvent.click(await findByText("Mark as Listened"));

      await waitFor(() => {
        expect(getByText(/✓ Listened \(5×\)/)).toBeTruthy();
        expect(queryByText(/Mark as Listened/)).toBeNull();
      });
    });

    it("shows 'Mark as Listened' when listened but remaining > 0", async () => {
      await seedListened("l1", 3);
      mockFetchLessonReviewQueue.mockResolvedValue({
        queue: [{} as never],
      });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      fireEvent.click(getByText("Listen"));

      expect(getByText("Mark as Listened")).toBeTruthy();
    });

    it("updates to fully-acquired after listen with remaining=0 and N=0", async () => {
      // Not seeded: real store starts "never listened" (has()=false) and the
      // real markListened() call below drives the has()/count() transition
      // itself — no separate "post-listen" mock state to fake.
      mockMarkAsListened.mockResolvedValue({
        status: "ok",
        registered: 1,
        created: 0,
        graded: 1,
        remaining_candidates: 0,
        listen_count: 3,
      });
      mockGetTranscript.mockResolvedValue(transcript);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { getByText, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      fireEvent.click(getByText("Listen"));
      await fireEvent.click(await findByText("Mark as Listened"));

      await waitFor(() => {
        expect(getByText("✓ Listened (3×)")).toBeTruthy();
      });
    });

    it("singular '1 word' in check-your-work link when N=1", async () => {
      await seedListened("l1", 1);
      mockFetchLessonReviewQueue.mockResolvedValue({
        queue: [{} as never],
      });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      fireEvent.click(getByText("Listen"));

      await waitFor(() => {
        const link = getByText(/Check your work/);
        expect(link.textContent).toContain("1 word");
      });
    });
  });

  describe("B2 — mastery indicator in listen mode", () => {
    it("renders mastery percentage and counts from the transcript", async () => {
      const transcriptWithWords = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "A",
            sentence: "zdravo kava prosim",
            words: [
              {
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
              },
              {
                lemma: "kava",
                active_state: "learning",
                progress: 0.3,
                surface: "kava",
                srs_state: "learning",
                srs_item_id: 2,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: "vocab",
                active_direction: "recognition",
                is_due: true,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
                recognition_state: "learning",
                recognition_is_due: true,
              },
              {
                lemma: "prosim",
                active_state: "unknown",
                progress: null,
                surface: "prosim",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_direction: null,
                is_due: false,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
                recognition_state: null,
                recognition_is_due: false,
              },
            ],
          },
        ],
      };
      mockGetTranscript.mockResolvedValue(transcriptWithWords);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: transcriptWithWords } },
      });
      fireEvent.click(getByText("Listen"));

      // 1 known + 1 learning (0.3) + 1 unknown (0) = 1.3/3 ≈ 43%
      expect(getByText(/43%/)).toBeTruthy();
      expect(getByText(/1 known/)).toBeTruthy();
    });

    it("renders every counts segment when new, learning, review, and known all appear", async () => {
      const word = (
        lemma: string,
        active_state: string,
        progress: number,
        recognition_state: string,
        recognition_is_due = false,
      ) => ({
        lemma,
        active_state,
        progress,
        surface: lemma,
        srs_state: active_state,
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
        recognition_state,
        recognition_is_due,
      });
      const transcriptAllStates = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "A",
            sentence: "ena dva tri štiri",
            words: [
              word("ena", "new", 0, "new"),
              word("dva", "learning", 0.3, "learning"),
              word("tri", "review", 0.8, "review"),
              word("štiri", "known", 1.0, "known"),
            ],
          },
        ],
      };
      mockGetTranscript.mockResolvedValue(transcriptAllStates);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { getByText, container } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: transcriptAllStates } },
      });
      fireEvent.click(getByText("Listen"));

      // (0 + 0.3 + 0.8 + 1.0) / 4 = 0.525 → 53%
      expect(getByText(/53%/)).toBeTruthy();
      const masteryLine = container.querySelector(".mastery-line");
      expect(masteryLine?.textContent).toContain("1 new");
      expect(masteryLine?.textContent).toContain("1 learning");
      expect(masteryLine?.textContent).toContain("1 review");
      expect(masteryLine?.textContent).toContain("1 known");
    });

    it("updates mastery after transcript refetch (post-listen)", async () => {
      const beforeTranscript = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "A",
            sentence: "kava",
            words: [
              {
                lemma: "kava",
                active_state: "unknown",
                progress: null,
                surface: "kava",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_direction: null,
                is_due: false,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const afterTranscript = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "A",
            sentence: "kava",
            words: [
              {
                lemma: "kava",
                active_state: "learning",
                progress: 0.15,
                surface: "kava",
                srs_state: "learning",
                srs_item_id: 1,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: "vocab",
                active_direction: "recognition",
                is_due: false,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      mockGetTranscript.mockResolvedValue(afterTranscript);
      mockMarkAsListened.mockResolvedValue({
        status: "ok",
        registered: 1,
        created: 1,
        graded: 0,
        remaining_candidates: 0,
        listen_count: 1,
      });
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { getByText, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: beforeTranscript } },
      });
      // Switch to listen mode — mastery indicator is only visible there.
      fireEvent.click(getByText("Listen"));
      // Before listen: 0% mastery (unknown)
      expect(getByText(/0%/)).toBeTruthy();

      await fireEvent.click(await findByText("Mark as Listened"));

      // After listen + refetch: 15% mastery (learning, progress 0.15)
      await waitFor(() => {
        expect(getByText(/15%/)).toBeTruthy();
      });
    });
  });

  describe("B4 — per-bucket breakdown tooltip", () => {
    const makeWord = (
      lemma: string,
      recognition_state: string | null,
      opts?: { recognition_is_due?: boolean; active_state?: string },
    ) => ({
      lemma,
      active_state: opts?.active_state ?? "known",
      progress: 1.0,
      surface: lemma,
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
      recognition_state,
      recognition_is_due: opts?.recognition_is_due ?? false,
    });

    it("tooltip lists the bucket's lemmas for a non-zero segment", async () => {
      const tx = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "A",
            sentence: "zdravo kava",
            words: [
              makeWord("zdravo", "known"),
              makeWord("kava", "learning", { active_state: "learning", recognition_is_due: true }),
            ],
          },
        ],
      };
      mockGetTranscript.mockResolvedValue(tx);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: tx } },
      });

      await waitFor(() => {
        const masteryLine = container.querySelector(".mastery-line");
        expect(masteryLine).toBeTruthy();
        const translations = masteryLine!.querySelectorAll(".tt-translation");
        const texts = Array.from(translations).map((t) => t.textContent);
        expect(texts).toContain("kava");
      });
    });

    it("16 lemmas in a bucket shows 15 + '+1 more' in tooltip", async () => {
      const words = Array.from({ length: 16 }, (_, i) => makeWord(`word${i}`, "known"));
      const tx = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [{ role: "A", sentence: words.map((w) => w.surface).join(" "), words }],
      };
      mockGetTranscript.mockResolvedValue(tx);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: tx } },
      });

      await waitFor(() => {
        const masteryLine = container.querySelector(".mastery-line");
        expect(masteryLine).toBeTruthy();
        const translations = masteryLine!.querySelectorAll(".tt-translation");
        const texts = Array.from(translations).map((t) => t.textContent);
        expect(texts.some((t) => t?.includes("+1 more"))).toBe(true);
      });
    });

    it("zero-count segments do not render a tooltip trigger", async () => {
      const tx = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "A",
            sentence: "zdravo",
            words: [makeWord("zdravo", "known")],
          },
        ],
      };
      mockGetTranscript.mockResolvedValue(tx);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: tx } },
      });

      await waitFor(() => {
        const masteryLine = container.querySelector(".mastery-line");
        expect(masteryLine).toBeTruthy();
        const segments = masteryLine!.querySelectorAll(".mastery-segment");
        const texts = Array.from(segments).map((s) => s.textContent);
        expect(texts.every((t) => !t?.includes("new"))).toBe(true);
        expect(texts.every((t) => !t?.includes("learning"))).toBe(true);
        expect(texts.every((t) => !t?.includes("due"))).toBe(true);
        expect(texts.every((t) => !t?.includes("review"))).toBe(true);
        expect(texts.some((t) => t?.includes("known"))).toBe(true);
      });
    });

    it("non-zero segments are keyboard-reachable (focus reveals via :focus-within); zero-count are not", async () => {
      const tx = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "A",
            sentence: "kava",
            words: [makeWord("kava", "learning", { active_state: "learning" })],
          },
        ],
      };
      mockGetTranscript.mockResolvedValue(tx);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: tx } },
      });

      await waitFor(() => {
        const segments = Array.from(
          container.querySelectorAll<HTMLElement>(".mastery-line .mastery-segment"),
        );
        const learning = segments.find((s) => s.textContent?.includes("learning"));
        const known = segments.find((s) => s.textContent?.includes("known"));
        expect(learning).toBeTruthy();
        expect(learning!.getAttribute("tabindex")).toBe("0");
        expect(learning!.getAttribute("role")).toBe("button");
        expect(known).toBeTruthy();
        expect(known!.hasAttribute("tabindex")).toBe(false);
      });

      const segments = Array.from(
        container.querySelectorAll<HTMLElement>(".mastery-line .mastery-segment"),
      );
      const learning = segments.find((s) => s.textContent?.includes("learning"))!;
      const wrap = learning.closest(".tt-wrap")!;
      // Enter activates (forwards a click → Tooltip toggles open); other keys don't.
      await fireEvent.keyDown(learning, { key: "a" });
      expect(wrap.classList.contains("open")).toBe(false);
      await fireEvent.keyDown(learning, { key: "Enter" });
      expect(wrap.classList.contains("open")).toBe(true);
    });
  });

  describe("B3 — mastery line + listen card in read mode", () => {
    const transcriptWithRecFields = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [
        {
          role: "A",
          sentence: "zdravo kava",
          words: [
            {
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
            },
            {
              lemma: "kava",
              active_state: "learning",
              progress: 0.3,
              surface: "kava",
              srs_state: "learning",
              srs_item_id: 2,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: "vocab",
              active_direction: "recognition",
              is_due: true,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
              recognition_state: "learning",
              recognition_is_due: true,
            },
          ],
        },
      ],
    };

    it("mastery line renders in read mode", async () => {
      mockGetTranscript.mockResolvedValue(transcriptWithRecFields);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { getByText } = render(Page, {
        props: {
          data: {
            curriculum,
            lesson: { ...lesson, day: 1 },
            audio: null,
            transcript: transcriptWithRecFields,
          },
        },
      });

      // Desktop defaults to read mode
      await waitFor(() => {
        expect(getByText(/65%/)).toBeTruthy();
      });
    });

    it("'Mark as Listened' renders in read mode and fires on click", async () => {
      mockGetTranscript.mockResolvedValue(transcriptWithRecFields);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });
      mockMarkAsListened.mockResolvedValue({
        status: "ok",
        registered: 1,
        created: 0,
        graded: 1,
        remaining_candidates: 0,
        listen_count: 1,
      });

      const { getByText, findByText } = render(Page, {
        props: {
          data: {
            curriculum,
            lesson: { ...lesson, day: 1 },
            audio: null,
            transcript: transcriptWithRecFields,
          },
        },
      });

      // "Mark as Listened" is visible in read mode
      const btn = getByText("Mark as Listened");
      expect(btn).toBeTruthy();
      expect((btn as HTMLButtonElement).disabled).toBe(false);

      // Click fires handleMarkListened
      await fireEvent.click(btn);

      await waitFor(() => {
        expect(mockMarkAsListened).toHaveBeenCalled();
      });
    });

    it("listen mode behavior unchanged — mastery line still renders", async () => {
      mockGetTranscript.mockResolvedValue(transcriptWithRecFields);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { getByText } = render(Page, {
        props: {
          data: {
            curriculum,
            lesson: { ...lesson, day: 1 },
            audio,
            transcript: transcriptWithRecFields,
          },
        },
      });

      // Switch to listen mode
      fireEvent.click(getByText("Listen"));

      await waitFor(() => {
        expect(getByText(/65%/)).toBeTruthy();
      });
    });
  });
});
