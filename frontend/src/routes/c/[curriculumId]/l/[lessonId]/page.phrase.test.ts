/**
 * Tests for /c/[curriculumId]/l/[lessonId] — lesson view: handleCreatePhrase
 * (creating a collocation SRS item from a phrase selection).
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
import { curriculum, lesson, audio, stubViewport } from "./page-test-helpers";

const mockMarkAsListened = vi.mocked(api.markAsListened);
const mockGetTranscript = vi.mocked(api.getLessonTranscript);
const mockCreateSRSItem = vi.mocked(api.createSRSItem);
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
  describe("handleCreatePhrase", () => {
    const transcriptWithMultiWord = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [
        {
          role: "Petra",
          words: [
            {
              surface: "centru",
              lemma: "centru",
              srs_state: "new" as const,
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
            {
              surface: "mesta",
              lemma: "mesto",
              srs_state: "new" as const,
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
          ],
        },
      ],
    };

    it("calls createSRSItem and then getLessonTranscript on success", async () => {
      const createdItem = {
        id: 55,
        text: "centru mesta",
        translation: "",
        state: "new" as const,
        due_at: "2026-04-15",
        stability: 1.0,
        difficulty: 5.0,
        reps: 0,
        lapses: 0,
        last_review: null,
        language_code: "sl",
      };
      mockCreateSRSItem.mockResolvedValue(createdItem);
      mockGetTranscript.mockResolvedValue(transcriptWithMultiWord);

      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: transcriptWithMultiWord } },
      });

      // Trigger phrase creation via drag
      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      const createBtn = container.querySelector(
        ".phrase-confirm-bar button.confirm-create",
      ) as HTMLElement;
      await fireEvent.click(createBtn);

      await waitFor(() => {
        expect(mockCreateSRSItem).toHaveBeenCalledWith({
          text: "centru mesta",
          language_code: "sl",
          word_count: 2,
          translation: "",
          source_sentence: expect.any(String),
          source_lesson_id: expect.any(String),
          source_line_index: 0,
        });
        expect(mockGetTranscript).toHaveBeenCalled();
      });
    });

    it("forwards source_line_index from the selected line", async () => {
      const transcriptTwoLines = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "Petra",
            words: [
              {
                surface: "prva",
                lemma: "prva",
                srs_state: "new" as const,
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
          {
            role: "Petra",
            words: [
              {
                surface: "centru",
                lemma: "centru",
                srs_state: "new" as const,
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
              {
                surface: "mesta",
                lemma: "mesto",
                srs_state: "new" as const,
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const createdItem = {
        id: 56,
        text: "centru mesta",
        translation: "",
        state: "new" as const,
        due_at: "2026-04-15",
        stability: 1.0,
        difficulty: 5.0,
        reps: 0,
        lapses: 0,
        last_review: null,
        language_code: "sl",
      };
      mockCreateSRSItem.mockResolvedValue(createdItem);
      mockGetTranscript.mockResolvedValue(transcriptTwoLines);

      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: transcriptTwoLines } },
      });

      // Drag-select on line index 1 (the second dialogue line)
      const centruSpan = container.querySelector(
        '[data-line-index="1"][data-word-index="0"]',
      ) as HTMLElement;
      const mestaSpan = container.querySelector(
        '[data-line-index="1"][data-word-index="1"]',
      ) as HTMLElement;

      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      await fireEvent.click(
        container.querySelector(".phrase-confirm-bar button.confirm-create") as HTMLElement,
      );

      await waitFor(() => {
        expect(mockCreateSRSItem).toHaveBeenCalledWith(
          expect.objectContaining({ source_line_index: 1 }),
        );
      });
    });

    it("sets error when createSRSItem throws an Error", async () => {
      mockCreateSRSItem.mockRejectedValue(new Error("phrase create failed"));
      mockGetTranscript.mockResolvedValue(transcriptWithMultiWord);

      const { container, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: transcriptWithMultiWord } },
      });

      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      await fireEvent.click(
        container.querySelector(".phrase-confirm-bar button.confirm-create") as HTMLElement,
      );

      expect(await findByText("phrase create failed")).toBeTruthy();
    });

    it("sets error to String(e) when createSRSItem throws a non-Error", async () => {
      mockCreateSRSItem.mockRejectedValue("plain phrase error");
      mockGetTranscript.mockResolvedValue(transcriptWithMultiWord);

      const { container, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: transcriptWithMultiWord } },
      });

      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      await fireEvent.click(
        container.querySelector(".phrase-confirm-bar button.confirm-create") as HTMLElement,
      );

      expect(await findByText("plain phrase error")).toBeTruthy();
    });
  });
});
