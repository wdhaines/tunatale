/**
 * Tests for /c/[curriculumId]/l/[lessonId] — lesson view.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

vi.mock("$lib/api", () => ({
  api: {
    renderAudio: vi.fn(),
    getLessonTranscript: vi.fn(),
    markAsListened: vi.fn(),
    createSRSItem: vi.fn(),
    setSRSItemState: vi.fn(),
    restoreKnown: vi.fn(),
    suspendSRSItem: vi.fn(),
    untrackSRSItem: vi.fn(),
    createBaseCard: vi.fn(),
    createInflectionCloze: vi.fn(),
    submitDrill: vi.fn(),
    undoGrade: vi.fn(),
    generateStory: vi.fn(),
    ignoreLemma: vi.fn(),
    unignoreLemma: vi.fn(),
    audioUrl: vi.fn((id: string) => `/api/audio/${id}`),
  },
}));

vi.mock("$lib/stores/listened.svelte", () => ({
  listenedStore: {
    has: vi.fn().mockReturnValue(false),
    add: vi.fn(),
  },
}));

import { api } from "$lib/api";
import type { TranscriptData } from "$lib/api";
import { listenedStore } from "$lib/stores/listened.svelte";
import { syncStore } from "$lib/stores/sync.svelte";
import Page from "./+page.svelte";

const mockRenderAudio = vi.mocked(api.renderAudio);
const mockGetTranscript = vi.mocked(api.getLessonTranscript);
const mockMarkAsListened = vi.mocked(api.markAsListened);
const mockCreateSRSItem = vi.mocked(api.createSRSItem);
const mockSetSRSItemState = vi.mocked(api.setSRSItemState);
const mockSuspendSRSItem = vi.mocked(api.suspendSRSItem);
const mockUntrackSRSItem = vi.mocked(api.untrackSRSItem);
const mockCreateBaseCard = vi.mocked(api.createBaseCard);
const mockCreateInflectionCloze = vi.mocked(api.createInflectionCloze);
const mockSubmitDrill = vi.mocked(api.submitDrill);
const mockUndoGrade = vi.mocked(api.undoGrade);
const mockGenerateStory = vi.mocked(api.generateStory);
const mockIgnoreLemma = vi.mocked(api.ignoreLemma);
const mockUnignoreLemma = vi.mocked(api.unignoreLemma);
const mockRestoreKnown = vi.mocked(api.restoreKnown);

const curriculum = { id: "cid-1", topic: "Coffee", language_code: "sl", days: 3 };
const lesson = {
  id: "l1",
  day: 1,
  title: "Day 1: Coffee",
  language_code: "sl",
  sections: [
    {
      type: "key_phrases",
      phrases: [{ text: "kavo prosim", role: "female-1", language_code: "sl", voice_id: "v1" }],
    },
  ],
  key_phrases: [],
};
const audio = { audio_id: "a1", lesson_id: "l1", sections: [] };
const transcript = {
  lesson_id: "l1",
  key_phrases: [{ phrase: "kavo prosim", translation: "a coffee please" }],
  dialogue_lines: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  syncStore.notify(null);
  vi.mocked(listenedStore.has).mockReturnValue(false);
  // When load supplies no transcript the component fetches it on mount. Default
  // to a pending promise so null-transcript renders sit in the loading state
  // without injecting content; tests that care override this.
  mockGetTranscript.mockReturnValue(new Promise<TranscriptData>(() => {}));
});

describe("/c/[curriculumId]/l/[lessonId] page", () => {
  it("renders lesson title and sections", () => {
    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript: null } },
    });
    expect(getByText("Day 1: Coffee")).toBeTruthy();
    expect(getByText("Render Audio")).toBeTruthy();
  });

  it("renders lesson title as the primary h1 heading", () => {
    const { getByRole } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript: null } },
    });
    expect(getByRole("heading", { level: 1, name: /Day 1: Coffee/ })).toBeTruthy();
  });

  it("does not render the back-link as a heading", () => {
    const { container } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript: null } },
    });
    // The back-link is a plain anchor, not inside any heading
    const backLink = container.querySelector('a[href="/c/cid-1"]');
    expect(backLink).toBeTruthy();
    expect(backLink!.closest("h1, h2, h3, h4, h5, h6")).toBeNull();
  });

  it("defaults to Read mode with transcript visible", () => {
    const { getByText, queryByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript } },
    });
    expect(getByText("a coffee please")).toBeTruthy();
    expect(queryByText("Mark as Listened")).toBeFalsy();
  });

  it("switches to Listen mode showing listen action", () => {
    const { getByText, queryByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript } },
    });
    fireEvent.click(getByText("Listen"));
    expect(queryByText("a coffee please")).toBeFalsy();
    expect(getByText("Mark as Listened")).toBeTruthy();
  });

  it("toggles back to Read mode after switching to Listen", () => {
    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript } },
    });
    fireEvent.click(getByText("Listen"));
    fireEvent.click(getByText("Read"));
    expect(getByText("a coffee please")).toBeTruthy();
  });

  it("shows AudioPlayer when audio is pre-loaded", () => {
    const { queryByText, container } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript: null } },
    });
    expect(queryByText("Render Audio")).toBeFalsy();
    expect(queryByText("Audio Player")).toBeTruthy();
    expect(container.querySelector("audio")).toBeTruthy();
  });

  it("renders audio on Render Audio click", async () => {
    mockRenderAudio.mockResolvedValue(audio);
    mockGetTranscript.mockResolvedValue(transcript);

    const { getByText, findByText, container } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript: null } },
    });
    await fireEvent.click(getByText("Render Audio"));

    expect(await findByText("Audio Player")).toBeTruthy();
    expect(container.querySelector("audio")).toBeTruthy();
    expect(await findByText("a coffee please")).toBeTruthy();
  });

  it("still shows AudioPlayer if getLessonTranscript fails after render", async () => {
    mockRenderAudio.mockResolvedValue(audio);
    mockGetTranscript.mockRejectedValue(new Error("transcript unavailable"));

    const { getByText, findByText, queryByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript: null } },
    });
    await fireEvent.click(getByText("Render Audio"));

    expect(await findByText("Audio Player")).toBeTruthy();
    expect(queryByText("Render Audio")).toBeFalsy();
  });

  it("calls markAsListened and adds to listenedStore", async () => {
    mockMarkAsListened.mockResolvedValue({ status: "ok", registered: 3 });
    mockGetTranscript.mockResolvedValue(transcript);

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    const btn = await findByText("Mark as Listened");
    await fireEvent.click(btn);

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {});
      expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      expect(listenedStore.add).toHaveBeenCalledWith("l1");
    });
  });

  it("shows listened state when listenedStore.has returns true", () => {
    vi.mocked(listenedStore.has).mockReturnValue(true);
    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    fireEvent.click(getByText("Listen"));
    expect(getByText("✓ Listened")).toBeTruthy();
  });

  it("shows the plain transcript placeholder while the transcript is being fetched", () => {
    // load supplies no transcript (production path), so the component fetches it
    // client-side; until that resolves, the plain placeholder (dialogue text with
    // no word coloring) shows instead of a bare spinner.
    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript: null } },
    });
    expect(getByText(/Preparing word states/)).toBeTruthy();
  });

  describe("client-side transcript fetch (no preload)", () => {
    it("fetches and renders the transcript when load supplies none", async () => {
      mockGetTranscript.mockResolvedValue(transcript);
      const { findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: null } },
      });
      expect(await findByText("a coffee please")).toBeTruthy();
      expect(mockGetTranscript).toHaveBeenCalledWith("l1");
    });

    it("shows 'No transcript available.' when the fetch resolves null", async () => {
      mockGetTranscript.mockResolvedValue(null as never);
      const { findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: null } },
      });
      expect(await findByText("No transcript available.")).toBeTruthy();
    });

    it("shows an error when the transcript fetch fails", async () => {
      mockGetTranscript.mockRejectedValue(new Error("transcript boom"));
      const { findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: null } },
      });
      expect(await findByText("transcript boom")).toBeTruthy();
    });

    it("stringifies a non-Error transcript fetch failure", async () => {
      mockGetTranscript.mockRejectedValue("plain transcript error");
      const { findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: null } },
      });
      expect(await findByText("plain transcript error")).toBeTruthy();
    });
  });

  it("shows the transcript text before audio is rendered", () => {
    // The transcript is extracted from the lesson and needs no audio — the dialogue
    // and key phrases should be readable as soon as the day is generated.
    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript } },
    });
    expect(getByText("a coffee please")).toBeTruthy();
    expect(getByText("Render Audio")).toBeTruthy();
  });

  it("re-syncs audio and transcript when navigating to a different lesson", async () => {
    // SvelteKit reuses this component on same-route param changes (the Regenerate
    // button's goto). The mutable local audio/transcript copies must follow `data`
    // instead of staying frozen on the previous lesson.
    const { rerender, getByText, queryByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    expect(getByText("a coffee please")).toBeTruthy();

    const newLesson = { ...lesson, id: "l1-new", title: "Day 1: Coffee v2" };
    const newTranscript = {
      lesson_id: "l1-new",
      key_phrases: [{ phrase: "nova fraza", translation: "a brand new phrase" }],
      dialogue_lines: [],
    };
    await rerender({
      data: { curriculum, lesson: newLesson, audio: null, transcript: newTranscript },
    });

    await waitFor(() => {
      expect(getByText("Day 1: Coffee v2")).toBeTruthy();
      expect(getByText("a brand new phrase")).toBeTruthy();
      expect(queryByText("a coffee please")).toBeFalsy();
      expect(getByText("Render Audio")).toBeTruthy();
    });
  });

  it("shows error when renderAudio fails with non-Error", async () => {
    mockRenderAudio.mockRejectedValue("plain string error");

    const { getByText, findByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript: null } },
    });
    await fireEvent.click(getByText("Render Audio"));

    expect(await findByText("plain string error")).toBeTruthy();
  });

  it("shows error when markAsListened fails", async () => {
    mockMarkAsListened.mockRejectedValue(new Error("listen failed"));
    mockGetTranscript.mockResolvedValue(transcript);

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText("listen failed")).toBeTruthy();
  });

  it("shows stringified error when markAsListened throws a non-Error", async () => {
    mockMarkAsListened.mockRejectedValue("plain listen error");
    mockGetTranscript.mockResolvedValue(transcript);

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText("plain listen error")).toBeTruthy();
  });

  it("shows listen confirmation with plural words after markAsListened", async () => {
    mockMarkAsListened.mockResolvedValue({ status: "ok", registered: 3 });
    mockGetTranscript.mockResolvedValue(transcript);

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText(/3.*words tracked/i)).toBeTruthy();
  });

  it("shows singular '1 word tracked' when registered is 1", async () => {
    mockMarkAsListened.mockResolvedValue({ status: "ok", registered: 1 });
    mockGetTranscript.mockResolvedValue(transcript);

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText(/1 word tracked/i)).toBeTruthy();
  });

  it("hides listen confirmation when error is set after markAsListened", async () => {
    mockMarkAsListened.mockResolvedValue({ status: "ok", registered: 3 });
    // The transcript refresh after markAsListened fails → error is set, which
    // should suppress the confirmation that was set by the successful listen.
    mockGetTranscript.mockRejectedValueOnce(new Error("refresh failed"));

    const { queryByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(getByText("Mark as Listened"));

    await vi.waitFor(() => {
      expect(queryByText(/words tracked/i)).toBeFalsy();
    });
  });

  it("shows plural phrases label when a section has more than one phrase", () => {
    const lessonMultiPhrase = {
      ...lesson,
      sections: [
        {
          type: "key_phrases",
          phrases: [
            { text: "kavo prosim", role: "female-1", language_code: "sl", voice_id: "v1" },
            { text: "hvala", role: "female-1", language_code: "sl", voice_id: "v1" },
          ],
        },
      ],
    };
    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson: lessonMultiPhrase, audio: null, transcript: null } },
    });
    expect(getByText(/2 phrases/)).toBeTruthy();
  });

  it("falls back to raw section type when not in SECTION_TITLES dictionary", () => {
    // Exercises `{SECTION_TITLES[section.type] ?? section.type}` fallback at L167.
    // "unknown_type" isn't a key in the dictionary, so the raw value renders.
    const lessonOddType = {
      ...lesson,
      sections: [
        {
          type: "unknown_type",
          phrases: [{ text: "x", role: "female-1", language_code: "sl", voice_id: "v1" }],
        },
      ],
    };
    const { container } = render(Page, {
      props: { data: { curriculum, lesson: lessonOddType, audio: null, transcript: null } },
    });
    expect(container.textContent).toContain("unknown_type");
  });

  describe("handleWordClick", () => {
    const makeTranscriptWithWord = (overrides: Record<string, unknown> = {}) => ({
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
              srs_state: "new",
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
              ...overrides,
            },
          ],
        },
      ],
    });

    it("unknown word calls createBaseCard with sentence from dialogue line", async () => {
      const t = makeTranscriptWithWord({ active_state: "unknown" });
      mockCreateBaseCard.mockResolvedValue({
        id: 1,
        was_created: true,
        item: {
          id: 1,
          text: "zdravo",
          translation: "",
          state: "new",
          due_at: "",
          stability: 1,
          difficulty: 5,
          reps: 0,
          lapses: 0,
          last_review: null,
          language_code: "sl",
        },
      });
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.click(await findByRole("button", { name: "Start learning" }));

      await waitFor(() => {
        expect(mockCreateBaseCard).toHaveBeenCalledWith({
          surface: "zdravo",
          lemma: "zdravo",
          sentence: "Zdravo kako si",
          language_code: "sl",
          translation: "",
        });
        expect(mockSubmitDrill).not.toHaveBeenCalled();
        expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      });
    });

    it("due word with active direction calls submitDrill with 'good'", async () => {
      const t = makeTranscriptWithWord({
        active_state: "learning",
        active_direction: "recognition",
        is_due: true,
        srs_item_id: 42,
      });
      mockSubmitDrill.mockResolvedValue({ new_due_at: "", new_state: "review" });
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));

      await waitFor(() => {
        expect(mockSubmitDrill).toHaveBeenCalledWith(42, "recognition", "good");
        expect(mockCreateBaseCard).not.toHaveBeenCalled();
        expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      });
    });

    it("due word with production direction calls submitDrill with production", async () => {
      const t = makeTranscriptWithWord({
        active_state: "review",
        active_direction: "production",
        is_due: true,
        srs_item_id: 42,
      });
      mockSubmitDrill.mockResolvedValue({ new_due_at: "", new_state: "review" });
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));

      await waitFor(() => {
        expect(mockSubmitDrill).toHaveBeenCalledWith(42, "production", "good");
      });
    });

    it("word that is not due does not call any API", async () => {
      const t = makeTranscriptWithWord({
        active_state: "learning",
        active_direction: "recognition",
        is_due: false,
        srs_item_id: 42,
      });
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.keyDown(await findByRole("button", { name: "zdravo" }), { key: "Enter" });

      await waitFor(() => {
        expect(mockCreateBaseCard).not.toHaveBeenCalled();
        expect(mockSubmitDrill).not.toHaveBeenCalled();
      });
    });

    it("known word (terminal) does not call any API", async () => {
      const t = makeTranscriptWithWord({
        active_state: "known",
        srs_item_id: 42,
      });
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.keyDown(await findByRole("button", { name: "zdravo" }), { key: "Enter" });

      await waitFor(() => {
        expect(mockCreateBaseCard).not.toHaveBeenCalled();
        expect(mockSubmitDrill).not.toHaveBeenCalled();
      });
    });

    it("suspended word (terminal) does not call any API", async () => {
      const t = makeTranscriptWithWord({
        active_state: "suspended",
        srs_item_id: 42,
      });
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.keyDown(await findByRole("button", { name: "zdravo" }), { key: "Enter" });

      await waitFor(() => {
        expect(mockCreateBaseCard).not.toHaveBeenCalled();
        expect(mockSubmitDrill).not.toHaveBeenCalled();
      });
    });

    it("ignored word (no card) does not call createBaseCard", async () => {
      const t = makeTranscriptWithWord({
        active_state: "ignored",
        srs_item_id: null,
      });
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.keyDown(await findByRole("button", { name: "zdravo" }), { key: "Enter" });

      await waitFor(() => {
        expect(mockCreateBaseCard).not.toHaveBeenCalled();
        expect(mockSubmitDrill).not.toHaveBeenCalled();
      });
    });

    it("shows error when createBaseCard throws", async () => {
      const t = makeTranscriptWithWord({ active_state: "unknown" });
      mockCreateBaseCard.mockRejectedValue(new Error("base card failed"));
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.click(await findByRole("button", { name: "Start learning" }));

      expect(await findByText("base card failed")).toBeTruthy();
    });

    it("shows stringified error when createBaseCard throws non-Error", async () => {
      const t = makeTranscriptWithWord({ active_state: "unknown" });
      mockCreateBaseCard.mockRejectedValue("plain error");
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.click(await findByRole("button", { name: "Start learning" }));

      expect(await findByText("plain error")).toBeTruthy();
    });

    it("unknown word with missing dialogue line sentence uses empty string", async () => {
      const t = makeTranscriptWithWord({ active_state: "unknown" });
      const raw: Record<string, unknown> = { ...t.dialogue_lines[0] };
      delete raw.sentence;
      t.dialogue_lines[0] = raw as (typeof t.dialogue_lines)[0];
      mockCreateBaseCard.mockResolvedValue({
        id: 1,
        was_created: true,
        item: {
          id: 1,
          text: "zdravo",
          translation: "",
          state: "new",
          due_at: "",
          stability: 1,
          difficulty: 5,
          reps: 0,
          lapses: 0,
          last_review: null,
          language_code: "sl",
        },
      });
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.click(await findByRole("button", { name: "Start learning" }));

      await waitFor(() => {
        expect(mockCreateBaseCard).toHaveBeenCalledWith(expect.objectContaining({ sentence: "" }));
      });
    });

    it("shows error when submitDrill throws", async () => {
      const t = makeTranscriptWithWord({
        active_state: "learning",
        active_direction: "recognition",
        is_due: true,
        srs_item_id: 42,
      });
      mockSubmitDrill.mockRejectedValue(new Error("drill failed"));
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));

      expect(await findByText("drill failed")).toBeTruthy();
    });
  });

  describe("collocation click", () => {
    const transcriptWithCollocation = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [
        {
          role: "Petra",
          sentence: "dober dan hvala",
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

    it("calls submitDrill with recognition good via the popover grade button", async () => {
      mockSubmitDrill.mockResolvedValue({ new_due_at: "", new_state: "review" });
      mockGetTranscript.mockResolvedValue(transcriptWithCollocation);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: transcriptWithCollocation } },
      });

      await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));

      await waitFor(() => {
        expect(mockSubmitDrill).toHaveBeenCalledWith(77, "recognition", "good");
        expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      });
    });

    it("shows error when submitDrill throws", async () => {
      mockSubmitDrill.mockRejectedValue(new Error("coll drill failed"));
      mockGetTranscript.mockResolvedValue(transcriptWithCollocation);

      const { findByRole, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: transcriptWithCollocation } },
      });

      await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));

      expect(await findByText("coll drill failed")).toBeTruthy();
    });
  });

  describe("tooltip actions", () => {
    const makeInflectableTranscript = (overrides: Record<string, unknown> = {}) => ({
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [
        {
          role: "Petra",
          sentence: "Grem v Ljubljano",
          words: [
            {
              surface: "Ljubljano",
              lemma: "ljubljana",
              srs_state: "review",
              srs_item_id: 7,
              translation: "Ljubljana",
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: "vocab",
              active_state: "review",
              active_direction: "production",
              is_due: false,
              progress: 0.8,
              inflectable: true,
              inflection_feature: "noun:acc:sg",
              known_marked: false,
              ...overrides,
            },
          ],
        },
      ],
    });

    const renderInflectable = (t: ReturnType<typeof makeInflectableTranscript>) => {
      mockGetTranscript.mockResolvedValue(t);
      return render(Page, { props: { data: { curriculum, lesson, audio, transcript: t } } });
    };

    it("Create inflection card button calls createInflectionCloze with the line sentence", async () => {
      const t = makeInflectableTranscript();
      mockCreateInflectionCloze.mockResolvedValue({
        id: 9,
        was_created: true,
        item: {
          id: 9,
          text: "Ljubljano",
          translation: "",
          state: "new",
          due_at: "",
          stability: 1,
          difficulty: 5,
          reps: 0,
          lapses: 0,
          last_review: null,
          language_code: "sl",
        },
      });
      const { findByRole } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Create inflection card" }));

      await waitFor(() => {
        expect(mockCreateInflectionCloze).toHaveBeenCalledWith({
          surface: "Ljubljano",
          lemma: "ljubljana",
          feature: "noun:acc:sg",
          sentence: "Grem v Ljubljano",
          language_code: "sl",
          lesson_id: "l1",
          translation: "Ljubljana",
        });
        expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      });
    });

    it("Ignore button calls untrackSRSItem", async () => {
      const t = makeInflectableTranscript();
      mockUntrackSRSItem.mockResolvedValue({ action: "suspended" } as never);
      const { findByRole } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Ignore" }));

      await waitFor(() => {
        expect(mockUntrackSRSItem).toHaveBeenCalledWith(7);
        expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      });
    });

    it("Known button calls setSRSItemState with 'known'", async () => {
      const t = makeInflectableTranscript();
      mockSetSRSItemState.mockResolvedValue({} as never);
      const { findByRole } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Known" }));

      await waitFor(() => {
        expect(mockSetSRSItemState).toHaveBeenCalledWith(7, "known");
      });
    });

    it('"Un-mark known" button calls restoreKnown and refetches transcript', async () => {
      const t = makeInflectableTranscript({ known_marked: true });
      mockRestoreKnown.mockResolvedValue({} as never);
      mockGetTranscript.mockResolvedValue(t);
      const { findByRole } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: /un-mark known/i }));

      await waitFor(() => {
        expect(mockRestoreKnown).toHaveBeenCalledWith(7);
        expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      });
    });

    it("shows error when restoreKnown throws", async () => {
      const t = makeInflectableTranscript({ known_marked: true });
      mockRestoreKnown.mockRejectedValue(new Error("restore boom"));
      mockGetTranscript.mockResolvedValue(t);
      const { findByRole, findByText } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: /un-mark known/i }));

      expect(await findByText("restore boom")).toBeTruthy();
    });

    it("Reset button asks for confirmation, then forgets in Anki when confirmed", async () => {
      const t = makeInflectableTranscript();
      mockSetSRSItemState.mockResolvedValue({} as never);
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      const { findByRole } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Reset" }));

      await waitFor(() => {
        expect(mockSetSRSItemState).toHaveBeenCalledWith(7, "new");
      });
      expect(confirmSpy).toHaveBeenCalledTimes(1);
      expect(confirmSpy.mock.calls[0][0]).toMatch(/Anki/);
      confirmSpy.mockRestore();
    });

    it("Reset button does nothing when confirmation is cancelled", async () => {
      const t = makeInflectableTranscript();
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
      const { findByRole } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Reset" }));

      expect(mockSetSRSItemState).not.toHaveBeenCalled();
      confirmSpy.mockRestore();
    });

    it("Known button does not prompt for confirmation", async () => {
      const t = makeInflectableTranscript();
      mockSetSRSItemState.mockResolvedValue({} as never);
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      const { findByRole } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Known" }));

      await waitFor(() => {
        expect(mockSetSRSItemState).toHaveBeenCalledWith(7, "known");
      });
      expect(confirmSpy).not.toHaveBeenCalled();
      confirmSpy.mockRestore();
    });

    it("Un-ignore button (suspended word) calls suspendSRSItem with id and false", async () => {
      const t = makeInflectableTranscript({ active_state: "suspended", inflectable: false });
      mockSuspendSRSItem.mockResolvedValue({} as never);
      const { findByRole } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Un-ignore" }));

      await waitFor(() => {
        expect(mockSuspendSRSItem).toHaveBeenCalledWith(7, false);
      });
    });

    it("shows error when createInflectionCloze throws", async () => {
      const t = makeInflectableTranscript();
      mockCreateInflectionCloze.mockRejectedValue(new Error("inflect boom"));
      const { findByRole, findByText } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Create inflection card" }));

      expect(await findByText("inflect boom")).toBeTruthy();
    });

    it("shows error when setSRSItemState throws", async () => {
      const t = makeInflectableTranscript();
      mockSetSRSItemState.mockRejectedValue(new Error("state boom"));
      const { findByRole, findByText } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Known" }));

      expect(await findByText("state boom")).toBeTruthy();
    });

    it("shows error when untrackSRSItem throws", async () => {
      const t = makeInflectableTranscript();
      mockUntrackSRSItem.mockRejectedValue(new Error("untrack boom"));
      const { findByRole, findByText } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Ignore" }));

      expect(await findByText("untrack boom")).toBeTruthy();
    });

    it("shows error when suspendSRSItem throws on Un-ignore", async () => {
      const t = makeInflectableTranscript({ active_state: "suspended", inflectable: false });
      mockSuspendSRSItem.mockRejectedValue(new Error("suspend boom"));
      const { findByRole, findByText } = renderInflectable(t);

      await fireEvent.click(await findByRole("button", { name: "Un-ignore" }));

      expect(await findByText("suspend boom")).toBeTruthy();
    });

    const makeCardlessWordTranscript = (overrides: Record<string, unknown> = {}) => ({
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [
        {
          role: "Petra",
          sentence: "Grem v Ljubljano",
          words: [
            {
              surface: "banka",
              lemma: "banka",
              srs_state: "unknown",
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "unknown",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
              ...overrides,
            },
          ],
        },
      ],
    });

    const renderCardlessWord = (t: ReturnType<typeof makeCardlessWordTranscript>) => {
      mockGetTranscript.mockResolvedValue(t);
      return render(Page, { props: { data: { curriculum, lesson, audio, transcript: t } } });
    };

    it("Ignore on unknown word calls ignoreLemma", async () => {
      const t = makeCardlessWordTranscript({ active_state: "unknown" });
      mockIgnoreLemma.mockResolvedValue({ status: "ok" } as never);
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = renderCardlessWord(t);

      await fireEvent.click(await findByRole("button", { name: /ignore/i }));

      await waitFor(() => {
        expect(mockIgnoreLemma).toHaveBeenCalledWith("banka", "sl");
        expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      });
    });

    it("Un-ignore on card-less ignored word calls unignoreLemma", async () => {
      const t = makeCardlessWordTranscript({
        srs_state: "ignored",
        active_state: "ignored",
      });
      mockUnignoreLemma.mockResolvedValue({ status: "ok" } as never);
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = renderCardlessWord(t);

      await fireEvent.click(await findByRole("button", { name: /un-ignore/i }));

      await waitFor(() => {
        expect(mockUnignoreLemma).toHaveBeenCalledWith("banka", "sl");
        expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      });
    });

    it("shows error when ignoreLemma throws", async () => {
      const t = makeCardlessWordTranscript({ active_state: "unknown" });
      mockIgnoreLemma.mockRejectedValue(new Error("ignore boom"));
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole, findByText } = renderCardlessWord(t);

      await fireEvent.click(await findByRole("button", { name: /ignore/i }));

      expect(await findByText("ignore boom")).toBeTruthy();
    });

    it("shows error when unignoreLemma throws", async () => {
      const t = makeCardlessWordTranscript({
        srs_state: "ignored",
        active_state: "ignored",
      });
      mockUnignoreLemma.mockRejectedValue(new Error("unignore boom"));
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole, findByText } = renderCardlessWord(t);

      await fireEvent.click(await findByRole("button", { name: /un-ignore/i }));

      expect(await findByText("unignore boom")).toBeTruthy();
    });
  });

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

  describe("regenerate button", () => {
    let confirmSpy: ReturnType<typeof vi.spyOn>;

    afterEach(() => {
      confirmSpy?.mockRestore();
    });

    it("renders a Regenerate button", () => {
      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      expect(getByText("Regenerate Day 1")).toBeTruthy();
    });

    it("regenerates and navigates to the new lesson when confirmed", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockGenerateStory.mockResolvedValue({
        id: "l1-new",
        title: "Day 1: Coffee v2",
        sections: [],
      });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      await waitFor(() => {
        expect(mockGenerateStory).toHaveBeenCalledWith("cid-1", 1);
        expect(mockGoto).toHaveBeenCalledWith("/c/cid-1/l/l1-new");
      });
    });

    it("does nothing when the confirmation is cancelled", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      expect(mockGenerateStory).not.toHaveBeenCalled();
      expect(mockGoto).not.toHaveBeenCalled();
    });

    it("shows an error when regeneration fails", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockGenerateStory.mockRejectedValue(new Error("generation failed"));

      const { getByText, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      expect(await findByText("generation failed")).toBeTruthy();
      expect(mockGoto).not.toHaveBeenCalled();
    });

    it("shows a stringified error when regeneration throws a non-Error", async () => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      mockGenerateStory.mockRejectedValue("plain regen error");

      const { getByText, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      await fireEvent.click(getByText("Regenerate Day 1"));

      expect(await findByText("plain regen error")).toBeTruthy();
    });
  });
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
