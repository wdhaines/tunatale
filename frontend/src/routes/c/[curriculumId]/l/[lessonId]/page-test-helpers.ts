/**
 * Shared setup for the /c/[curriculumId]/l/[lessonId] page test split
 * (page.interactions.test.ts, page.mastery.test.ts, page.phrase.test.ts,
 * page.tools.test.ts, page.load.test.ts).
 *
 * NOTE: `vi.mock()` calls cannot live here — Vitest hoists them to the top of
 * the file that calls them, so each split file declares its own
 * `vi.mock("#lib/api.js", …)` / `vi.mock("$app/navigation", …)` /
 * `vi.mock("#lib/stores/pipeline.svelte.js", …)` and wires them to the factories
 * below via an async factory (`await import("./page-test-helpers")`), which
 * sidesteps the hoisting restriction because the import happens when the
 * factory runs, not at hoist time.
 *
 * This file is `src/routes/**` and is intentionally NOT covered by the
 * coverage-gate include list (`src/lib/**\/*.ts` + `**\/*.svelte`).
 */
import { vi } from "vitest";
import { FIRST_OPEN, lessonSidePref, type LessonSide } from "#lib/stores/lessonSidePref.svelte.js";

/** Fresh `#lib/api.js` mock surface — the full method set `+page.svelte` may call. */
export function createApiMock() {
  return {
    getLessonAudio: vi.fn(),
    renderAudio: vi.fn(),
    // Pending by default: the tools card prices a re-render on mount, and a
    // resolved estimate would repaint pages whose tests are about something else.
    estimateLessonRerender: vi.fn(() => new Promise(() => {})),
    rerenderLesson: vi.fn(),
    getTranscript: vi.fn(),
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
    reglossLesson: vi.fn(),
    deleteCurriculumDay: vi.fn(),
    getRateLimit: vi.fn().mockResolvedValue(null),
    // Default RESOLVES to [] (not pending) so every other page test file keeps
    // passing untouched: the lesson page fetches the day→lesson map on mount,
    // and empty progress means no prev/next links render. vi.clearAllMocks()
    // clears only call history, so the default implementation survives.
    getCurriculumProgress: vi.fn().mockResolvedValue([]),
    probeRateLimit: vi.fn().mockResolvedValue(null),
    ignoreLemma: vi.fn(),
    unignoreLemma: vi.fn(),
    getStorySource: vi.fn(),
    importStory: vi.fn(),
    audioUrl: vi.fn((id: string) => `/api/audio/${id}`),
    audioZipUrl: vi.fn((lessonId: string) => `/api/audio/lesson/${lessonId}/zip`),
    fetchLessonReviewQueue: vi.fn(),
    markLessonReviewed: vi.fn(),
    // Real listenedStore's boundary — the store itself is unmocked.
    getListens: vi.fn(),
    markAsListened: vi.fn(),
    importListens: vi.fn(),
    getLanguages: vi.fn(),
    getListenPreview: vi.fn(),
    commitPending: vi.fn(),
  };
}

/** Fresh `pipelineStore` mock — a plain object, not cleared by `vi.clearAllMocks()`. */
export function createPipelineMock() {
  return { status: null as unknown, start: vi.fn(), stop: vi.fn() };
}

/** Stub window.matchMedia (jsdom doesn't implement it). `mobile` drives the
 * lesson-mode viewport default; the page calls it on mount via lessonModePref.init(). */
export function stubViewport(mobile: boolean) {
  (window as unknown as { matchMedia: unknown }).matchMedia = vi.fn(() => ({
    matches: mobile,
    media: "(max-width: 640px)",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

export const curriculum = {
  id: "cid-1",
  topic: "Coffee",
  language_code: "sl",
  cefr_level: "A2",
  days: [
    {
      day: 1,
      position: 1,
      title: "Title 1",
      focus: "f",
      collocations: ["kava"],
      learning_objective: "o",
      story_guidance: "",
      kind: "thematic",
      pattern: "",
      group: "",
      group_title: "",
    },
  ],
  proposed: null,
};

export const lesson = {
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

export const audio = { audio_id: "a1", lesson_id: "l1", sections: [] };

export const transcript = {
  lesson_id: "l1",
  key_phrases: [{ phrase: "kavo prosim", translation: "a coffee please" }],
  dialogue_lines: [],
};

// Captured at import, before any test spies on it: re-spying in each beforeEach
// must wrap the real store, never the previous spy.
const realSideGet = lessonSidePref.get;

/**
 * Open every lesson on Read's Dialogue tab, as a learner who last used that tab
 * on it, unless a test has stored another side (tunatale-cs3p slice 3).
 *
 * A lesson's FIRST open is Read on the Outline tab (R1). These page tests are
 * about what Read does with the transcript (word taps, phrases, navigation),
 * not about which tab opens first, so they start where the transcript is. The
 * first-open rule itself is pinned by page.readTabs.test.ts and LessonReader's
 * own tests, which do not call this. Stored choices still win, so a test that
 * switches side or tab sees its switch.
 */
export function openLessonsOnDialogue() {
  openLessonsAt({ mode: "read", tab: "dialogue" });
}

/** Open every lesson not yet given a side on *side* (see openLessonsOnDialogue). */
export function openLessonsAt(side: LessonSide) {
  // The store is a singleton: a side one test chose must not open the next.
  localStorage.removeItem("lessonSide");
  lessonSidePref.reset();
  vi.spyOn(lessonSidePref, "get").mockImplementation((id: string) => {
    const stored = realSideGet(id);
    return stored === FIRST_OPEN ? side : stored;
  });
}
