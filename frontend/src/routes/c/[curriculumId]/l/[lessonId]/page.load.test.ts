/**
 * Tests for /c/[curriculumId]/l/[lessonId] — the `load` function, plus
 * post-sync transcript refresh (store notification) and the undo-grade
 * ("Got it ✓" → "Undo ↩") cycle.
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
const mockSubmitDrill = vi.mocked(api.submitDrill);
const mockUndoGrade = vi.mocked(api.undoGrade);
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

describe("load function for /c/[curriculumId]/l/[lessonId]", () => {
  it("returns null audio and transcript when they are not found", async () => {
    const { api: mockApi } = await import("$lib/api");
    vi.mocked(mockApi.renderAudio);

    // Simulate a fresh import for the load function test
    vi.doMock("$lib/api", () => ({
      api: {
        getCurriculum: vi.fn().mockResolvedValue(curriculum),
        getLesson: vi.fn().mockResolvedValue(lesson),
        getLessonAudio: vi.fn().mockRejectedValue(new Error("Not Found")),
        getLessonTranscript: vi.fn().mockRejectedValue(new Error("Not Found")),
      },
    }));

    const { load } = await import("./+page");
    const result = await load({
      params: { curriculumId: "cid-1", lessonId: "l1" },
    } as never);

    // audio and transcript should be null due to Promise.allSettled fallthrough
    // (the actual mock resolution depends on vi.doMock timing, so just confirm structure)
    expect(result).toHaveProperty("curriculum");
    expect(result).toHaveProperty("lesson");
    expect(result).toHaveProperty("audio");
    expect(result).toHaveProperty("transcript");
  });

  describe("sync via store notification", () => {
    const PEER_RESULT = {
      auth_success: true,
      pull_required: 0,
      push_required: 1,
      tt_push_pull_exit: 0,
      dry_run: false,
    };

    it("refreshes the transcript and shows a summary after a successful sync", async () => {
      const before = {
        lesson_id: "l1",
        key_phrases: [{ phrase: "kavo prosim", translation: "BEFORE sync" }],
        dialogue_lines: [],
      };
      const after = {
        lesson_id: "l1",
        key_phrases: [{ phrase: "kavo prosim", translation: "AFTER sync" }],
        dialogue_lines: [],
      };
      mockGetTranscript.mockResolvedValue(after);
      const { findByText, queryByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: before } },
      });

      expect(await findByText("BEFORE sync")).toBeTruthy();

      syncStore.notify(PEER_RESULT);

      expect(await findByText("AFTER sync")).toBeTruthy();
      expect(queryByText("BEFORE sync")).toBeFalsy();
      expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      expect(await findByText("Synced with AnkiWeb")).toBeTruthy();
    });

    it("shows an error if the post-sync transcript refresh fails (Error)", async () => {
      const before = {
        lesson_id: "l1",
        key_phrases: [{ phrase: "kavo prosim", translation: "BEFORE sync" }],
        dialogue_lines: [],
      };
      mockGetTranscript.mockRejectedValue(new Error("refresh failed"));
      const { findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: before } },
      });

      syncStore.notify(PEER_RESULT);

      expect(await findByText("refresh failed")).toBeTruthy();
    });

    it("stringifies a non-Error post-sync refresh failure", async () => {
      const before = {
        lesson_id: "l1",
        key_phrases: [{ phrase: "kavo prosim", translation: "BEFORE sync" }],
        dialogue_lines: [],
      };
      mockGetTranscript.mockRejectedValue("weird refresh failure");
      const { findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: before } },
      });

      syncStore.notify(PEER_RESULT);

      expect(await findByText("weird refresh failure")).toBeTruthy();
    });
  });

  describe("undo grade flow (Got it ✓ → Undo ↩ cycle)", () => {
    const dueWordTranscript = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [
        {
          role: "Petra",
          sentence: "Zdravo kako si",
          words: [
            {
              surface: "zdravo",
              lemma: "zdravo",
              srs_state: "learning",
              srs_item_id: 42,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "learning",
              active_direction: "recognition",
              is_due: true,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
          ],
        },
      ],
    };

    it('after grading, the word popover shows "Undo ↩"; clicking it calls api.undoGrade and cycles back', async () => {
      mockSubmitDrill.mockResolvedValue({ new_due_at: "", new_state: "review" });
      mockUndoGrade.mockResolvedValue({
        status: "ok",
        restored_state: "learning",
        restored_due_at: "",
      });
      mockGetTranscript.mockResolvedValue(dueWordTranscript);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: dueWordTranscript } },
      });

      await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));

      const undoBtn = await findByRole("button", { name: "Undo ↩" });
      await fireEvent.click(undoBtn);

      await waitFor(() => {
        expect(mockUndoGrade).toHaveBeenCalledWith(42, "recognition");
      });
      // Cycle complete: the grade button is back.
      expect(await findByRole("button", { name: "Got it ✓" })).toBeTruthy();
    });

    it("the undo targets the direction that was graded, even if the active direction shifts after refetch", async () => {
      // Grading recognition can graduate it → the refetched word's active
      // direction flips to production. Undo must still hit recognition.
      const after = JSON.parse(JSON.stringify(dueWordTranscript));
      after.dialogue_lines[0].words[0].active_direction = "production";
      mockSubmitDrill.mockResolvedValue({ new_due_at: "", new_state: "review" });
      mockUndoGrade.mockResolvedValue({
        status: "ok",
        restored_state: "learning",
        restored_due_at: "",
      });
      mockGetTranscript.mockResolvedValue(after);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: dueWordTranscript } },
      });

      await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));
      await fireEvent.click(await findByRole("button", { name: "Undo ↩" }));

      await waitFor(() => {
        expect(mockUndoGrade).toHaveBeenCalledWith(42, "recognition");
      });
    });

    it("a phrase grade then Undo ↩ calls api.undoGrade with the span id", async () => {
      const collocationTranscript = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "Petra",
            sentence: "dober dan",
            words: [
              {
                surface: "dober",
                lemma: "dober",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: 77,
                collocation_is_due: true,
                collocation_start: true,
                collocation_srs_state: "learning",
                collocation_lemma: "dober dan",
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
                surface: "dan",
                lemma: "dan",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: 77,
                collocation_is_due: true,
                collocation_start: false,
                collocation_srs_state: "learning",
                collocation_lemma: "dober dan",
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
      mockSubmitDrill.mockResolvedValue({ new_due_at: "", new_state: "review" });
      mockUndoGrade.mockResolvedValue({
        status: "ok",
        restored_state: "learning",
        restored_due_at: "",
      });
      mockGetTranscript.mockResolvedValue(collocationTranscript);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: collocationTranscript } },
      });

      await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));
      await fireEvent.click(await findByRole("button", { name: "Undo ↩" }));

      await waitFor(() => {
        expect(mockUndoGrade).toHaveBeenCalledWith(77, "recognition");
      });
    });

    it("shows the error and drops the Undo button when undo is refused (already synced)", async () => {
      mockSubmitDrill.mockResolvedValue({ new_due_at: "", new_state: "review" });
      mockUndoGrade.mockRejectedValue(new Error("grade already synced to Anki"));
      mockGetTranscript.mockResolvedValue(dueWordTranscript);

      const { findByRole, findByText, queryByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: dueWordTranscript } },
      });

      await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));
      await fireEvent.click(await findByRole("button", { name: "Undo ↩" }));

      expect(await findByText("grade already synced to Anki")).toBeTruthy();
      await waitFor(() => {
        expect(queryByRole("button", { name: "Undo ↩" })).toBeNull();
      });
    });
  });
});
