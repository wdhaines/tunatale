/**
 * Tests for /c/[curriculumId]/l/[lessonId] — lesson view: the collapsed
 * lesson-tools panel (audio render/zip, pipeline integration, source panel,
 * regenerate button + status line).
 *
 * Split from page.test.ts (item 14, Phase B) — see page-test-helpers.ts for
 * the shared $lib/api / pipeline mock factories and fixtures.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
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
const mockGetLessonAudio = vi.mocked(api.getLessonAudio);
const mockGetTranscript = vi.mocked(api.getLessonTranscript);
const mockRegenerateDay = vi.mocked(api.regenerateDay);
const mockGetStorySource = vi.mocked(api.getStorySource);
const mockImportStory = vi.mocked(api.importStory);
const mockFetchLessonReviewQueue = vi.mocked(api.fetchLessonReviewQueue);
const mockDeleteCurriculumDay = vi.mocked(api.deleteCurriculumDay);

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
  describe("lesson tools (collapsed rare actions)", () => {
    const audioWithSections = {
      audio_id: "a1",
      lesson_id: "l1",
      sections: [
        { audio_id: "s1", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
        {
          audio_id: "s2",
          section_index: 1,
          section_type: "natural_speed",
          title: "Natural Speed",
        },
      ],
    };

    it("tucks Regenerate and Downloads into a closed details", () => {
      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio: audioWithSections, transcript } },
      });
      const tools = container.querySelector<HTMLDetailsElement>("details.tools-card");
      expect(tools).toBeTruthy();
      expect(tools!.open).toBe(false);
      expect(tools!.textContent).toContain("Regenerate Day 1");
      expect(tools!.textContent).toContain("Download All Sections");
      expect(tools!.textContent).toContain("Key Phrases");
      expect(tools!.textContent).toContain("Natural Speed");
    });

    it("download links point at the zip and per-section audio endpoints", () => {
      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio: audioWithSections, transcript } },
      });
      const all = container.querySelector<HTMLAnchorElement>(".download-all-btn")!;
      expect(all.getAttribute("href")).toBe("/api/audio/lesson/l1/zip");
      const sections = container.querySelectorAll<HTMLAnchorElement>(".section-dl-btn");
      expect(sections.length).toBe(2);
      expect(sections[0].getAttribute("href")).toBe("/api/audio/s1");
    });

    it("offers no download links when the lesson has no audio", () => {
      const { container, queryByText } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });
      expect(container.querySelector("details.tools-card")).toBeTruthy();
      expect(queryByText("Download All Sections")).toBeFalsy();
    });

    it("shows a help toggle that reveals the regen explanation on click", async () => {
      const { container, getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });
      const toggle = container.querySelector<HTMLButtonElement>(".help-toggle")!;
      expect(toggle).toBeTruthy();
      expect(toggle.getAttribute("aria-label")).toBe("What does regenerate do?");
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
      expect(container.querySelector(".help-panel")).toBeFalsy();

      await fireEvent.click(toggle);
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
      expect(getByText(/Regenerating rewrites/)).toBeTruthy();
    });
  });

  describe("pipeline integration", () => {
    it("starts pipeline on mount with curriculum id", () => {
      render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });
      expect(pipelineStore.start).toHaveBeenCalledWith("cid-1");
    });

    it("stops pipeline via effect cleanup on unmount", () => {
      const { unmount } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });
      unmount();
      expect(pipelineStore.stop).toHaveBeenCalled();
    });

    it("shows pipeline state badge when this day is in the pipeline", () => {
      (pipelineStore as any).status = {
        active: true,
        days: [
          {
            day: 1,
            state: "rendering",
            lesson_id: null,
            has_audio: false,
            error: null,
            retryable: null,
            detail: null,
          },
        ],
      };
      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });
      expect(container.querySelector(".pipeline-state")?.textContent).toContain("rendering");
    });

    it("does not show pipeline badge when pipeline is inactive", () => {
      (pipelineStore as any).status = { active: false, days: [] };
      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });
      expect(container.querySelector(".pipeline-state")).toBeFalsy();
    });

    it("fetches audio when pipeline day is ready and audio is null", async () => {
      mockGetLessonAudio.mockResolvedValue(audio);
      (pipelineStore as any).status = {
        active: true,
        days: [
          {
            day: 1,
            state: "ready",
            has_audio: true,
            lesson_id: null,
            error: null,
            retryable: null,
            detail: null,
          },
        ],
      };
      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });
      await waitFor(() => {
        expect(mockGetLessonAudio).toHaveBeenCalledWith("l1");
      });
      await waitFor(() => {
        expect(container.querySelector(".player")).toBeTruthy();
      });
    });

    it("does not refetch audio on repeated ready polls", async () => {
      mockGetLessonAudio.mockResolvedValue(audio);
      (pipelineStore as any).status = {
        active: true,
        days: [
          {
            day: 1,
            state: "ready",
            has_audio: true,
            lesson_id: null,
            error: null,
            retryable: null,
            detail: null,
          },
        ],
      };
      render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });
      await waitFor(() => {
        expect(mockGetLessonAudio).toHaveBeenCalledTimes(1);
      });
      // After the fetch resolves, audio is set. The effect could re-run because
      // audio is a tracked dependency — must not call getLessonAudio again.
      await waitFor(() => {
        expect(mockGetLessonAudio).toHaveBeenCalledTimes(1);
      });
    });

    it("does not fetch audio when a different day is in the pipeline", () => {
      (pipelineStore as any).status = {
        active: true,
        days: [
          {
            day: 2,
            state: "ready",
            has_audio: true,
            lesson_id: null,
            error: null,
            retryable: null,
            detail: null,
          },
        ],
      };
      render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });
      expect(mockGetLessonAudio).not.toHaveBeenCalled();
    });

    it("surfaces error when getLessonAudio fails", async () => {
      mockGetLessonAudio.mockRejectedValue(new Error("audio fetch failed"));
      (pipelineStore as any).status = {
        active: true,
        days: [
          {
            day: 1,
            state: "ready",
            has_audio: true,
            lesson_id: null,
            error: null,
            retryable: null,
            detail: null,
          },
        ],
      };
      const { findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });
      expect(await findByText("audio fetch failed")).toBeTruthy();
      expect(mockGetLessonAudio).toHaveBeenCalledTimes(1);
    });
  });

  describe("lesson source panel", () => {
    it("renders a collapsed LessonSourcePanel inside the tools card", () => {
      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      const toolsCard = container.querySelector("details.tools-card");
      expect(toolsCard).toBeTruthy();
      const sourcePanel = toolsCard!.querySelector<HTMLDetailsElement>(
        "details.lesson-source-panel",
      );
      expect(sourcePanel).toBeTruthy();
      expect(sourcePanel!.textContent).toContain("Edit Source");
      expect(sourcePanel!.open).toBe(false);
    });

    it("imports story through LessonSourcePanel and navigates to the new lesson", async () => {
      mockGetStorySource.mockResolvedValue({
        curriculum_id: "cid-1",
        day: 1,
        story: { title: "Kavarna" },
      });
      mockImportStory.mockResolvedValue({
        id: "new-l1",
        title: "Day 1 v2",
        sections: [],
        warnings: [],
      });

      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });

      const sourceSummary = container.querySelector(
        "details.lesson-source-panel summary",
      ) as HTMLElement;
      await fireEvent.click(sourceSummary);

      await waitFor(() => {
        expect(container.querySelector('[data-testid="copy-json"]')).toBeTruthy();
      });

      const textarea = container.querySelector(
        "details.lesson-source-panel textarea",
      ) as HTMLElement;
      await fireEvent.input(textarea, {
        target: { value: JSON.stringify({ title: "Kavarna v2" }) },
      });

      const importBtn = container.querySelector('[data-testid="import-btn"]') as HTMLElement;
      await fireEvent.click(importBtn);

      await waitFor(() => {
        expect(mockGoto).toHaveBeenCalledWith("/c/cid-1/l/new-l1");
      });
    });
  });

  describe("delete day (Lesson tools)", () => {
    it("renders a Delete day N button", () => {
      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      expect(getByText("Delete day 1")).toBeTruthy();
    });

    it("requires a second click to confirm before deleting", async () => {
      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      const btn = getByText("Delete day 1");
      await fireEvent.click(btn);
      expect(getByText("Confirm delete")).toBeTruthy();
      expect(mockDeleteCurriculumDay).not.toHaveBeenCalled();
    });

    it("deletes the day and navigates to the curriculum on the second click", async () => {
      mockDeleteCurriculumDay.mockResolvedValue({ deleted_day: 1, days: 0 });
      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Delete day 1"));
      await fireEvent.click(getByText("Confirm delete"));

      await waitFor(() => {
        expect(mockDeleteCurriculumDay).toHaveBeenCalledWith("cid-1", 1);
        expect(mockGoto).toHaveBeenCalledWith("/c/cid-1");
      });
    });

    it("resets the confirm state on blur without deleting", async () => {
      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      const btn = getByText("Delete day 1");
      await fireEvent.click(btn);
      expect(getByText("Confirm delete")).toBeTruthy();

      await fireEvent.blur(getByText("Confirm delete"));
      expect(getByText("Delete day 1")).toBeTruthy();
      expect(mockDeleteCurriculumDay).not.toHaveBeenCalled();
    });

    it("shows an error and does not navigate when deletion fails", async () => {
      mockDeleteCurriculumDay.mockRejectedValue(new Error("delete failed"));
      const { getByText, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Delete day 1"));
      await fireEvent.click(getByText("Confirm delete"));

      expect(await findByText("delete failed")).toBeTruthy();
      expect(mockGoto).not.toHaveBeenCalled();
    });
  });

  describe("regenerate button", () => {
    let confirmSpy: ReturnType<typeof vi.spyOn>;

    afterEach(() => {
      confirmSpy?.mockRestore();
    });

    /** Build a one-day pipeline status for day 1 with the given overrides. */
    function dayStatus(overrides: Record<string, unknown>) {
      return {
        active: true,
        days: [
          {
            day: 1,
            state: "generating",
            lesson_id: null,
            has_audio: false,
            error: null,
            retryable: null,
            detail: null,
            ...overrides,
          },
        ],
      };
    }

    it("renders a Regenerate button", () => {
      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      expect(getByText("Regenerate Day 1")).toBeTruthy();
    });

    it("routes regeneration through the pipeline and navigates once the new lesson is ready", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockRegenerateDay.mockResolvedValue({ status: "queued" });
      (pipelineStore as any).status = dayStatus({
        state: "ready",
        lesson_id: "l1-new",
        has_audio: true,
      });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      await waitFor(() => {
        expect(mockRegenerateDay).toHaveBeenCalledWith("cid-1", 1, "WIDER");
        expect(mockGoto).toHaveBeenCalledWith("/c/cid-1/l/l1-new");
      });
    });

    it("does nothing when the confirmation is cancelled", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      expect(mockRegenerateDay).not.toHaveBeenCalled();
      expect(mockGoto).not.toHaveBeenCalled();
    });

    it("does not navigate while the day is still generating", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockRegenerateDay.mockResolvedValue({ status: "queued" });
      (pipelineStore as any).status = dayStatus({ state: "generating", lesson_id: null });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      await waitFor(() => expect(mockRegenerateDay).toHaveBeenCalled());
      expect(mockGoto).not.toHaveBeenCalled();
    });

    it("does not navigate when the ready lesson id equals the current lesson", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockRegenerateDay.mockResolvedValue({ status: "queued" });
      (pipelineStore as any).status = dayStatus({
        state: "ready",
        lesson_id: "l1",
        has_audio: true,
      });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      await waitFor(() => expect(mockRegenerateDay).toHaveBeenCalled());
      expect(mockGoto).not.toHaveBeenCalled();
    });

    it("does not navigate when the ready record has no lesson id", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockRegenerateDay.mockResolvedValue({ status: "queued" });
      (pipelineStore as any).status = dayStatus({
        state: "ready",
        lesson_id: null,
        has_audio: true,
      });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      await waitFor(() => expect(mockRegenerateDay).toHaveBeenCalled());
      expect(mockGoto).not.toHaveBeenCalled();
    });

    it("does not navigate when there is no pipeline record for the day", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockRegenerateDay.mockResolvedValue({ status: "queued" });
      (pipelineStore as any).status = { active: true, days: [] };

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      await waitFor(() => expect(mockRegenerateDay).toHaveBeenCalled());
      expect(mockGoto).not.toHaveBeenCalled();
    });

    it("shows an error and re-enables the button when the regenerate request fails", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockRegenerateDay.mockRejectedValue(new Error("regenerate failed"));

      const { getByText, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      expect(await findByText("regenerate failed")).toBeTruthy();
      expect(mockGoto).not.toHaveBeenCalled();
      // Flag cleared → button back to its idle label.
      expect(getByText("Regenerate Day 1")).toBeTruthy();
    });

    it("shows a stringified error when the regenerate request throws a non-Error", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockRegenerateDay.mockRejectedValue("plain regen error");

      const { getByText, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      expect(await findByText("plain regen error")).toBeTruthy();
    });

    it("clears the regenerating flag when the day fails", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockRegenerateDay.mockResolvedValue({ status: "queued" });
      (pipelineStore as any).status = dayStatus({
        active: false,
        state: "failed",
        error: "Groq returned 429 Too Many Requests (retry after 37s)",
        retryable: true,
      });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      // Follow-effect resets the flag on failure → button re-enabled, no nav.
      await waitFor(() => expect(mockRegenerateDay).toHaveBeenCalled());
      expect(mockGoto).not.toHaveBeenCalled();
      expect(getByText("Regenerate Day 1")).toBeTruthy();
    });
  });

  describe("regenerate status line", () => {
    let confirmSpy: ReturnType<typeof vi.spyOn>;

    afterEach(() => {
      confirmSpy?.mockRestore();
    });

    function dayStatus(overrides: Record<string, unknown>) {
      return {
        active: true,
        days: [
          {
            day: 1,
            state: "generating",
            lesson_id: null,
            has_audio: false,
            error: null,
            retryable: null,
            detail: null,
            ...overrides,
          },
        ],
      };
    }

    it("shows a colored state pill and the rate-limit detail while regenerating", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockRegenerateDay.mockResolvedValue({ status: "queued" });
      (pipelineStore as any).status = dayStatus({
        state: "rendering",
        detail: "waiting 37s for rate-limit window (attempt 2/4)",
      });

      const { getByText, getByTestId, findByTestId } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      const status = await findByTestId("regen-status");
      // State renders as a styled pill (not bare text), message alongside it.
      const pill = status.querySelector(".pipeline-state");
      expect(pill?.textContent).toBe("rendering");
      expect(pill?.classList.contains("state-rendering")).toBe(true);
      expect(getByTestId("regen-detail").textContent).toContain(
        "waiting 37s for rate-limit window",
      );
    });

    it("shows the state pill with no detail line when there is no detail while regenerating", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockRegenerateDay.mockResolvedValue({ status: "queued" });
      (pipelineStore as any).status = dayStatus({ state: "generating", detail: null });

      const { getByText, findByTestId, queryByTestId } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      const status = await findByTestId("regen-status");
      expect(status.querySelector(".pipeline-state")?.textContent).toBe("generating");
      expect(queryByTestId("regen-detail")).toBeNull();
    });

    it("shows a failed pill and the sticky error text when the day has failed", () => {
      (pipelineStore as any).status = dayStatus({
        state: "failed",
        error: "Groq returned HTTP 401",
      });

      const { getByTestId } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      const pill = getByTestId("regen-status").querySelector(".pipeline-state");
      expect(pill?.textContent).toBe("failed");
      expect(pill?.classList.contains("state-failed")).toBe(true);
      expect(getByTestId("regen-detail").textContent).toBe(
        "Last regeneration failed: Groq returned HTTP 401",
      );
    });

    it("shows a generic failure message when a failed day carries no error", () => {
      (pipelineStore as any).status = dayStatus({ state: "failed", error: null });

      const { getByTestId } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      expect(getByTestId("regen-detail").textContent).toBe(
        "Last regeneration failed: Regeneration failed",
      );
    });

    it("shows no status line for a healthy day when not regenerating", () => {
      (pipelineStore as any).status = dayStatus({
        state: "ready",
        lesson_id: "l1",
        has_audio: true,
      });

      const { queryByTestId } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      expect(queryByTestId("regen-status")).toBeNull();
    });
  });
});
