/**
 * Tests for /c/[curriculumId]/l/[lessonId] — lesson view: client-side
 * transcript fetch, word click, collocation click, tooltip actions, and the
 * general render/listen/mode-switch interaction surface.
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

const mockQueueStatsRefresh = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
vi.mock("$lib/stores/queueStats.svelte", () => ({
  queueStatsStore: {
    get stats() {
      return null;
    },
    set: vi.fn(),
    refresh: mockQueueStatsRefresh,
  },
}));

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

const mockRenderAudio = vi.mocked(api.renderAudio);
const mockGetTranscript = vi.mocked(api.getLessonTranscript);
const mockSetSRSItemState = vi.mocked(api.setSRSItemState);
const mockSuspendSRSItem = vi.mocked(api.suspendSRSItem);
const mockUntrackSRSItem = vi.mocked(api.untrackSRSItem);
const mockCreateBaseCard = vi.mocked(api.createBaseCard);
const mockCreateInflectionCloze = vi.mocked(api.createInflectionCloze);
const mockSubmitDrill = vi.mocked(api.submitDrill);
const mockIgnoreLemma = vi.mocked(api.ignoreLemma);
const mockUnignoreLemma = vi.mocked(api.unignoreLemma);
const mockRestoreKnown = vi.mocked(api.restoreKnown);
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

  it("defaults to Read mode with transcript visible and listen card in both modes", () => {
    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio: null, transcript } },
    });
    expect(getByText("a coffee please")).toBeTruthy();
    expect(getByText("Mark as Listened")).toBeTruthy();
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
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      registered: 3,
      created: 1,
      graded: 2,
      remaining_candidates: 0,
      listen_count: 3,
    });
    mockGetTranscript.mockResolvedValue(transcript);
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [], has_unreviewed_listen: false });

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    const btn = await findByText("Mark as Listened");
    await fireEvent.click(btn);

    await waitFor(() => {
      expect(mockMarkAsListened).toHaveBeenCalledWith("l1", {});
      expect(mockGetTranscript).toHaveBeenCalledWith("l1");
    });
  });

  it("refreshes queueStatsStore after mark-as-listened", async () => {
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      registered: 3,
      created: 1,
      graded: 2,
      remaining_candidates: 0,
      listen_count: 3,
    });
    mockGetTranscript.mockResolvedValue(transcript);
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [], has_unreviewed_listen: false });
    mockQueueStatsRefresh.mockClear();

    const { getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    const btn = await getByText("Mark as Listened");
    await fireEvent.click(btn);

    await waitFor(() => {
      expect(mockQueueStatsRefresh).toHaveBeenCalled();
    });
  });

  it("shows listened state when the lesson was already listened to", async () => {
    await seedListened("l1", 1);
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [], has_unreviewed_listen: false });
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
    mockMarkAsListened.mockRejectedValue(new Error("listen failed"));
    mockGetTranscript.mockResolvedValue(transcript);

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText("listen failed")).toBeTruthy();
  });

  it("shows stringified error when markListened throws a non-Error", async () => {
    mockMarkAsListened.mockRejectedValue("plain listen error");
    mockGetTranscript.mockResolvedValue(transcript);

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText("plain listen error")).toBeTruthy();
  });

  it("shows listen confirmation with created/graded/remaining after markListened", async () => {
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      registered: 3,
      created: 2,
      graded: 1,
      remaining_candidates: 5,
      listen_count: 3,
    });
    mockGetTranscript.mockResolvedValue(transcript);
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [], has_unreviewed_listen: false });

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
    mockMarkAsListened.mockResolvedValue({
      status: "ok",
      registered: 1,
      created: 0,
      graded: 1,
      remaining_candidates: 3,
      listen_count: 2,
    });
    mockGetTranscript.mockResolvedValue(transcript);
    mockFetchLessonReviewQueue.mockResolvedValue({ queue: [], has_unreviewed_listen: false });

    const { findByText, getByText } = render(Page, {
      props: { data: { curriculum, lesson, audio, transcript } },
    });
    await fireEvent.click(getByText("Listen"));
    await fireEvent.click(await findByText("Mark as Listened"));

    expect(await findByText(/listen again to add more/i)).toBeTruthy();
  });

  it("hides listen confirmation when error is set after markListened", async () => {
    mockMarkAsListened.mockResolvedValue({
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
});
