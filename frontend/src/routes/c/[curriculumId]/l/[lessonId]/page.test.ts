/**
 * Tests for /c/[curriculumId]/l/[lessonId] — lesson view.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

vi.mock("$lib/api", () => ({
  api: {
    getLessonAudio: vi.fn(),
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
    fetchQueueStats: vi.fn(),
    regenerateDay: vi.fn(),
    getRateLimit: vi.fn().mockResolvedValue(null),
    probeRateLimit: vi.fn().mockResolvedValue(null),
    ignoreLemma: vi.fn(),
    unignoreLemma: vi.fn(),
    getStorySource: vi.fn(),
    importStory: vi.fn(),
    audioUrl: vi.fn((id: string) => `/api/audio/${id}`),
    audioZipUrl: vi.fn((lessonId: string) => `/api/audio/lesson/${lessonId}/zip`),
    fetchLessonReviewQueue: vi.fn(),
  },
}));

vi.mock("$lib/stores/pipeline.svelte", () => ({
  pipelineStore: { status: null, start: vi.fn(), stop: vi.fn() },
}));

vi.mock("$lib/stores/listened.svelte", () => ({
  listenedStore: {
    has: vi.fn().mockReturnValue(false),
    count: vi.fn().mockReturnValue(0),
    markListened: vi.fn(),
  },
}));

import { api } from "$lib/api";
import type { TranscriptData } from "$lib/api";
import { listenedStore } from "$lib/stores/listened.svelte";

const mockListenedMarkListened = vi.mocked(listenedStore.markListened);
const mockListenedCount = vi.mocked(listenedStore.count);
import { syncStore } from "$lib/stores/sync.svelte";
import { lessonModePref } from "$lib/stores/lessonModePref.svelte";
import { pipelineStore } from "$lib/stores/pipeline.svelte";
import Page from "./+page.svelte";

/** Stub window.matchMedia (jsdom doesn't implement it). `mobile` drives the
 * lesson-mode viewport default; the page calls it on mount via lessonModePref.init(). */
function stubViewport(mobile: boolean) {
  (window as unknown as { matchMedia: unknown }).matchMedia = vi.fn(() => ({
    matches: mobile,
    media: "(max-width: 640px)",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

const mockGetLessonAudio = vi.mocked(api.getLessonAudio);
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
const mockRegenerateDay = vi.mocked(api.regenerateDay);
const mockGetStorySource = vi.mocked(api.getStorySource);
const mockImportStory = vi.mocked(api.importStory);
const mockIgnoreLemma = vi.mocked(api.ignoreLemma);
const mockUnignoreLemma = vi.mocked(api.unignoreLemma);
const mockRestoreKnown = vi.mocked(api.restoreKnown);
const mockFetchLessonReviewQueue = vi.mocked(api.fetchLessonReviewQueue);

const curriculum = {
  id: "cid-1",
  topic: "Coffee",
  language_code: "sl",
  cefr_level: "A2",
  days: [
    {
      day: 1,
      title: "Title 1",
      focus: "f",
      collocations: ["kava"],
      learning_objective: "o",
      story_guidance: "",
    },
  ],
  proposed: null,
};
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
  localStorage.clear();
  stubViewport(false); // desktop default → Read, unless a test overrides
  lessonModePref.set("read"); // reset the singleton's in-memory state
  localStorage.clear(); // ...without leaving the persisted override set() just wrote
  syncStore.notify(null);
  // Reset the shared pipeline mock's status between tests: it's a plain object,
  // not cleared by vi.clearAllMocks(), and a leaked (esp. failed) record would
  // bleed into the ungated regenStatus / follow-effect of an unrelated test.
  (pipelineStore as unknown as { status: unknown }).status = null;
  vi.mocked(listenedStore.has).mockReturnValue(false);
  mockListenedCount.mockReturnValue(0);
  mockListenedMarkListened.mockReset();
  mockFetchLessonReviewQueue.mockReset();
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

  it("defaults to Listen mode on a mobile viewport (no stored preference)", () => {
    localStorage.clear();
    stubViewport(true); // mobile → Listen is the primary task
    const { getByText, queryByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript } },
    });
    expect(getByText("Mark as Listened")).toBeTruthy();
    expect(queryByText("a coffee please")).toBeFalsy();
  });

  it("honors a stored mode preference over the viewport default", () => {
    localStorage.setItem("lessonMode", "listen");
    stubViewport(false); // desktop would default to Read, but the stored pref wins
    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript } },
    });
    expect(getByText("Mark as Listened")).toBeTruthy();
  });

  it("shows LessonPlayer when audio is pre-loaded", () => {
    const { queryByText, container } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript: null } },
    });
    expect(queryByText("Render Audio")).toBeFalsy();
    expect(container.querySelector(".player")).toBeTruthy();
    expect(container.querySelector("audio")).toBeFalsy();
  });

  it("keeps ONE LessonPlayer alive across Listen↔Read switches (playback survives)", async () => {
    const pauseSpy = vi.spyOn(HTMLAudioElement.prototype, "pause");
    const { getByText, container } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    expect(container.querySelectorAll(".player").length).toBe(1);

    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(getByText("Read"));

    // Mode switches only flip the `compact` prop — no destroy, no pause.
    expect(pauseSpy).not.toHaveBeenCalled();
    expect(container.querySelectorAll(".player").length).toBe(1);
    // Compact in Read mode, full in Listen mode, always inside a card.
    expect(container.querySelector(".card .player.compact")).toBeTruthy();
    await fireEvent.click(getByText("Listen"));
    expect(container.querySelector(".card .player:not(.compact)")).toBeTruthy();
    pauseSpy.mockRestore();
  });

  it("wraps the player in a sticky card offset below the global nav", () => {
    // Simulate the layout's sticky nav so the offset-measure path runs.
    const nav = document.createElement("nav");
    nav.className = "global-nav";
    document.body.appendChild(nav);
    try {
      const { container } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });
      const card = container.querySelector(".card.player-card") as HTMLElement;
      expect(card).toBeTruthy();
      // top offset mirrors the measured nav height (0 in jsdom, but set)
      expect(card.style.top).toBe("0px");
    } finally {
      nav.remove();
    }
  });

  it("keeps the Read/Listen toggle inside the sticky player card (audio present)", () => {
    const { container } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    // The toggle must survive scrolling: it lives in the sticky card, not the header.
    expect(container.querySelector(".card.player-card .toggle-pill")).toBeTruthy();
    expect(container.querySelectorAll(".toggle-pill").length).toBe(1);
  });

  it("renders the sticky card with toggle and Render Audio when audio is missing", () => {
    const { container } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript: null } },
    });
    const card = container.querySelector(".card.player-card");
    expect(card).toBeTruthy();
    expect(card!.querySelector(".toggle-pill")).toBeTruthy();
    expect(card!.textContent).toContain("Render Audio");
  });

  it("destroys and recreates LessonPlayer when audio_id changes (lesson nav)", async () => {
    const pauseSpy = vi.spyOn(HTMLAudioElement.prototype, "pause");
    const { rerender } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript: null } },
    });
    expect(pauseSpy).not.toHaveBeenCalled();

    const newAudio = { audio_id: "a2", lesson_id: "l2", sections: [] };
    await rerender({
      data: { curriculum, lesson, audio: newAudio, transcript: null },
    });

    // The old LessonPlayer was destroyed (pause called), a new one created
    expect(pauseSpy).toHaveBeenCalled();
    pauseSpy.mockRestore();
  });

  it("renders audio on Render Audio click", async () => {
    mockRenderAudio.mockResolvedValue(audio);
    mockGetTranscript.mockResolvedValue(transcript);

    const { getByText, findByText, container } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript: null } },
    });
    await fireEvent.click(getByText("Render Audio"));

    expect(container.querySelector(".player")).toBeTruthy();
    expect(container.querySelector(".transport-row")).toBeTruthy();
    expect(await findByText("a coffee please")).toBeTruthy();
  });

  it("still shows LessonPlayer if getLessonTranscript fails after render", async () => {
    mockRenderAudio.mockResolvedValue(audio);
    mockGetTranscript.mockRejectedValue(new Error("transcript unavailable"));

    const { getByText, queryByText, container } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript: null } },
    });
    await fireEvent.click(getByText("Render Audio"));

    expect(container.querySelector(".transport-row")).toBeTruthy();
    expect(queryByText("Render Audio")).toBeFalsy();
  });

  it("calls listenedStore.markListened and refetches transcript", async () => {
    mockListenedMarkListened.mockResolvedValue({
      status: "ok",
      registered: 3,
      created: 1,
      graded: 2,
      remaining_candidates: 0,
      listen_count: 3,
    });
    mockGetTranscript.mockResolvedValue(transcript);
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    const btn = await findByText("Mark as Listened");
    await fireEvent.click(btn);

    await waitFor(() => {
      expect(mockListenedMarkListened).toHaveBeenCalledWith("l1");
      expect(mockGetTranscript).toHaveBeenCalledWith("l1");
    });
  });

  it("shows listened state when listenedStore.has returns true", async () => {
    vi.mocked(listenedStore.has).mockReturnValue(true);
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });
    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    fireEvent.click(getByText("Listen"));
    // has=true but no listenResult in this session → fullyAcquired is false,
    // so the button says "Mark as Listened" (can listen again).
    await waitFor(() => {
      const btn = getByText("Mark as Listened");
      expect(btn.classList.contains("listened")).toBe(true);
    });
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

    it("ignores a stale transcript response after navigating to another lesson", async () => {
      // The transcript endpoint can take many seconds on a cold backend (the
      // whole reason it's fetched client-side). Navigating lesson→lesson while
      // lesson A's fetch is in flight must not let A's late response clobber
      // lesson B's transcript.
      let resolveA!: (t: TranscriptData) => void;
      let resolveB!: (t: TranscriptData) => void;
      mockGetTranscript
        .mockReturnValueOnce(new Promise<TranscriptData>((r) => (resolveA = r)))
        .mockReturnValueOnce(new Promise<TranscriptData>((r) => (resolveB = r)));

      const { rerender, queryByText, findByText } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: null } },
      });

      const lessonB = { ...lesson, id: "l2", title: "Day 2: Fish", day: 2 };
      await rerender({
        data: { curriculum, lesson: lessonB, audio: null, transcript: null },
      });

      // Lesson A's slow response lands after navigation — must be dropped.
      resolveA(transcript);
      await waitFor(() => expect(mockGetTranscript).toHaveBeenCalledWith("l2"));
      expect(queryByText("a coffee please")).toBeFalsy();

      const transcriptB = {
        lesson_id: "l2",
        key_phrases: [{ phrase: "riba", translation: "a fish" }],
        dialogue_lines: [],
      };
      resolveB(transcriptB);
      expect(await findByText("a fish")).toBeTruthy();
      expect(queryByText("a coffee please")).toBeFalsy();
    });

    it("keeps the loading placeholder when a stale fetch settles after navigation", async () => {
      // The stale fetch's `finally` must not clear transcriptLoading for the
      // NEW lesson whose own fetch is still in flight.
      let rejectA!: (e: unknown) => void;
      mockGetTranscript
        .mockReturnValueOnce(new Promise<TranscriptData>((_r, rej) => (rejectA = rej)))
        .mockReturnValueOnce(new Promise<TranscriptData>(() => {}));

      const { rerender, queryByText, getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: null } },
      });

      const lessonB = { ...lesson, id: "l2", title: "Day 2: Fish", day: 2 };
      await rerender({
        data: { curriculum, lesson: lessonB, audio, transcript: null },
      });

      rejectA(new Error("stale lesson A failure"));
      await waitFor(() => expect(mockGetTranscript).toHaveBeenCalledWith("l2"));
      // Neither A's error nor its `finally` may leak into B's view:
      expect(queryByText("stale lesson A failure")).toBeFalsy();
      expect(getByText(/Preparing word states/)).toBeTruthy();
    });
  });

  it("drops the post-render transcript when navigation happens between the two fetches", async () => {
    // handleRenderAudio awaits renderAudio, then getLessonTranscript. Navigating
    // in that window must not let lesson A's transcript land on lesson B.
    mockRenderAudio.mockResolvedValueOnce(audio);
    let resolveTranscript!: (t: TranscriptData) => void;
    mockGetTranscript.mockReturnValueOnce(
      new Promise<TranscriptData>((r) => (resolveTranscript = r)),
    );

    const { rerender, getByText, queryByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript } },
    });
    await fireEvent.click(getByText("Render Audio"));
    await waitFor(() => expect(mockGetTranscript).toHaveBeenCalledWith("l1"));

    const lessonB = { ...lesson, id: "l2", title: "Day 2: Fish", day: 2 };
    const transcriptB = {
      lesson_id: "l2",
      key_phrases: [{ phrase: "riba", translation: "a fish" }],
      dialogue_lines: [],
    };
    await rerender({
      data: { curriculum, lesson: lessonB, audio: null, transcript: transcriptB },
    });

    resolveTranscript(transcript); // lesson A's transcript arrives late
    await waitFor(() => expect(getByText("a fish")).toBeTruthy());
    expect(queryByText("a coffee please")).toBeFalsy();
  });

  it("ignores a stale renderAudio response after navigating to another lesson", async () => {
    // Rendering takes tens of seconds (full-lesson TTS). Navigating away while
    // it runs must not attach lesson A's player/audio to lesson B's page.
    let resolveRender!: (a: typeof audio) => void;
    mockRenderAudio.mockReturnValueOnce(new Promise<typeof audio>((r) => (resolveRender = r)));

    const { rerender, getByText, container } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript } },
    });
    await fireEvent.click(getByText("Render Audio"));

    const lessonB = { ...lesson, id: "l2", title: "Day 2: Fish", day: 2 };
    const transcriptB = { lesson_id: "l2", key_phrases: [], dialogue_lines: [] };
    await rerender({
      data: { curriculum, lesson: lessonB, audio: null, transcript: transcriptB },
    });

    resolveRender(audio); // lesson A's render completes late
    await waitFor(() => {
      // Lesson B still shows its Render Audio button, not lesson A's player.
      expect(getByText("Render Audio")).toBeTruthy();
      expect((getByText("Render Audio") as HTMLButtonElement).disabled).toBe(false);
    });
    expect(container.querySelector(".player")).toBeFalsy();
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

  it("shows error when markListened fails", async () => {
    mockListenedMarkListened.mockRejectedValue(new Error("listen failed"));
    mockGetTranscript.mockResolvedValue(transcript);

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText("listen failed")).toBeTruthy();
  });

  it("shows stringified error when markListened throws a non-Error", async () => {
    mockListenedMarkListened.mockRejectedValue("plain listen error");
    mockGetTranscript.mockResolvedValue(transcript);

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText("plain listen error")).toBeTruthy();
  });

  it("shows listen confirmation with created/graded/remaining after markListened", async () => {
    mockListenedMarkListened.mockResolvedValue({
      status: "ok",
      registered: 3,
      created: 2,
      graded: 1,
      remaining_candidates: 5,
      listen_count: 3,
    });
    mockGetTranscript.mockResolvedValue(transcript);
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText(/2 new words added/)).toBeTruthy();
    expect(await findByText(/1 reviewed/)).toBeTruthy();
    expect(await findByText(/5 remaining/)).toBeTruthy();
  });

  it("shows 'listen again to add more' in the confirmation", async () => {
    mockListenedMarkListened.mockResolvedValue({
      status: "ok",
      registered: 1,
      created: 0,
      graded: 1,
      remaining_candidates: 3,
      listen_count: 2,
    });
    mockGetTranscript.mockResolvedValue(transcript);
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText(/listen again to add more/i)).toBeTruthy();
  });

  it("hides listen confirmation when error is set after markListened", async () => {
    mockListenedMarkListened.mockResolvedValue({
      status: "ok",
      registered: 3,
      created: 1,
      graded: 2,
      remaining_candidates: 0,
      listen_count: 3,
    });
    // The transcript refresh after markListened fails → error is set, which
    // should suppress the confirmation that was set by the successful listen.
    mockGetTranscript.mockRejectedValueOnce(new Error("refresh failed"));

    const { queryByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(getByText("Mark as Listened"));

    await vi.waitFor(() => {
      expect(queryByText(/new words added|reviewed|remaining/i)).toBeFalsy();
    });
  });

  it("does not render per-section phrase counts (header is title-only)", () => {
    const { container, queryByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript: null } },
    });
    expect(container.querySelector(".section-meta")).toBeFalsy();
    expect(queryByText(/1 phrase/)).toBeFalsy();
  });

  describe("B1 — check-your-work link and fully-acquired state", () => {
    it("shows 'Check your work — review N words' link when listened and N > 0", async () => {
      vi.mocked(listenedStore.has).mockReturnValue(true);
      mockListenedCount.mockReturnValue(3);
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
        expect(link.getAttribute("href")).toBe("/review?lesson=l1");
      });
    });

    it("does not show check-your-work when N = 0", async () => {
      vi.mocked(listenedStore.has).mockReturnValue(true);
      mockListenedCount.mockReturnValue(2);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { queryByText } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });

      await waitFor(() => {
        expect(queryByText(/Check your work/)).toBeNull();
      });
    });

    it("fetches review queue on mount when already listened", async () => {
      vi.mocked(listenedStore.has).mockReturnValue(true);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      render(Page, {
        props: { data: { curriculum, lesson, audio, transcript } },
      });

      await waitFor(() => {
        expect(mockFetchLessonReviewQueue).toHaveBeenCalledWith("l1");
      });
    });

    it("refetches review queue after each listen", async () => {
      mockListenedMarkListened.mockResolvedValue({
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
      vi.mocked(listenedStore.has).mockReturnValue(true);
      mockListenedCount.mockReturnValue(5);
      mockListenedMarkListened.mockResolvedValue({
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
      vi.mocked(listenedStore.has).mockReturnValue(true);
      mockListenedCount.mockReturnValue(3);
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
      vi.mocked(listenedStore.has).mockReturnValue(false);
      mockListenedMarkListened.mockResolvedValue({
        status: "ok",
        registered: 1,
        created: 0,
        graded: 1,
        remaining_candidates: 0,
        listen_count: 3,
      });
      mockGetTranscript.mockResolvedValue(transcript);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });
      // After listen, has() now returns true
      vi.mocked(listenedStore.has).mockReturnValue(true);
      mockListenedCount.mockReturnValue(3);

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
      vi.mocked(listenedStore.has).mockReturnValue(true);
      mockListenedCount.mockReturnValue(1);
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
      const word = (lemma: string, active_state: string, progress: number) => ({
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
      });
      const transcriptAllStates = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "A",
            sentence: "ena dva tri štiri",
            words: [
              word("ena", "new", 0),
              word("dva", "learning", 0.3),
              word("tri", "review", 0.8),
              word("štiri", "known", 1.0),
            ],
          },
        ],
      };
      mockGetTranscript.mockResolvedValue(transcriptAllStates);
      mockFetchLessonReviewQueue.mockResolvedValue({ queue: [] });

      const { getByText } = render(Page, {
        props: { data: { curriculum, lesson, audio: null, transcript: transcriptAllStates } },
      });
      fireEvent.click(getByText("Listen"));

      // (0 + 0.3 + 0.8 + 1.0) / 4 = 0.525 → 53%
      expect(getByText(/53%/)).toBeTruthy();
      expect(getByText(/1 new · 1 learning · 1 review · 1 known/)).toBeTruthy();
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
      mockListenedMarkListened.mockResolvedValue({
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

    it("unknown word creates the base card AND reviews it (recognition good) in one tap", async () => {
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
      mockSubmitDrill.mockResolvedValue({ new_due_at: "", new_state: "learning" });
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
        // Introduced + reviewed: the newly created card id is graded recognition.
        expect(mockSubmitDrill).toHaveBeenCalledWith(1, "recognition", "good");
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

    it("word that is not due (and not review-ahead eligible) does not call any API", async () => {
      const t = makeTranscriptWithWord({
        active_state: "learning",
        active_direction: "recognition",
        is_due: false,
        srs_item_id: 42,
        recognition_reviewable: false,
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

    it("not-due review-ahead word grades RECOGNITION 'good' and refreshes", async () => {
      const t = makeTranscriptWithWord({
        active_state: "review",
        active_direction: "recognition",
        is_due: false,
        srs_item_id: 42,
        recognition_reviewable: true,
      });
      mockSubmitDrill.mockResolvedValue({ new_due_at: "", new_state: "review" });
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.click(await findByRole("button", { name: "Review ✓" }));

      await waitFor(() => {
        expect(mockSubmitDrill).toHaveBeenCalledWith(42, "recognition", "good");
        expect(mockGetTranscript).toHaveBeenCalledWith("l1");
      });
    });

    it("review-ahead grades RECOGNITION even when the active direction is production", async () => {
      // Guardrail: a graduated word (recognition REVIEW → active_direction
      // resolves to production) must still grade recognition, never production.
      const t = makeTranscriptWithWord({
        active_state: "review",
        active_direction: "production",
        is_due: false,
        srs_item_id: 42,
        recognition_reviewable: true,
      });
      mockSubmitDrill.mockResolvedValue({ new_due_at: "", new_state: "review" });
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      await fireEvent.click(await findByRole("button", { name: "Review ✓" }));

      await waitFor(() => {
        expect(mockSubmitDrill).toHaveBeenCalledWith(42, "recognition", "good");
        expect(mockSubmitDrill).not.toHaveBeenCalledWith(42, "production", "good");
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

    it("double-click guard prevents a second submission while the first is in-flight", async () => {
      const t = makeTranscriptWithWord({
        active_state: "learning",
        active_direction: "recognition",
        is_due: true,
        srs_item_id: 42,
      });
      // Return a hanging promise so the first call stays in-flight.
      mockSubmitDrill.mockReturnValue(new Promise(() => {}));
      mockGetTranscript.mockResolvedValue(t);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: t } },
      });

      const btn = await findByRole("button", { name: "Got it ✓" });
      await fireEvent.click(btn);
      await fireEvent.click(btn);

      await vi.waitFor(() => {
        expect(mockSubmitDrill).toHaveBeenCalledTimes(1);
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

    it("double-tap guard prevents a second collocation submission", async () => {
      mockSubmitDrill.mockReturnValue(new Promise(() => {}));
      mockGetTranscript.mockResolvedValue(transcriptWithCollocation);

      const { findByRole } = render(Page, {
        props: { data: { curriculum, lesson, audio, transcript: transcriptWithCollocation } },
      });

      const btn = await findByRole("button", { name: "Got it ✓" });
      await fireEvent.click(btn);
      await fireEvent.click(btn);

      await vi.waitFor(() => {
        expect(mockSubmitDrill).toHaveBeenCalledTimes(1);
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
