import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createPlaybackController } from "../playbackController.svelte";
import type { Cue, LessonAudio } from "$lib/api";

// Locked oracle for tunatale-9idn — "Refresh loses the listening position".
//
// These tests exist because the existing "per-lesson resume" block in
// playbackController.test.ts cannot see the bug: every test there (a) omits
// selectTrack, so it never exercises the mount path LessonPlayer actually
// takes, and (b) asserts on ctrl.currentTime, which is a reactive COPY that
// doSeek never writes. Both shadows have to be gone for a test here to
// discriminate, so these assert on audioEl.currentTime — the element is what
// actually plays.

function makeCue(overrides: Partial<Cue> & { index: number }): Cue {
  return {
    start_ms: 0,
    end_ms: 1000,
    section_index: 0,
    section_type: "key_phrases",
    phrase_index: 0,
    role: "narrator",
    language_code: "en",
    text: "Hello",
    ref: { kind: "key_phrase", target_index: 0 },
    ...overrides,
  };
}

function makeFakeAudio(overrides: Partial<HTMLAudioElement> = {}): HTMLAudioElement {
  const listeners = new Map<string, Set<EventListener>>();
  return {
    currentTime: 0,
    duration: 100,
    paused: true,
    playbackRate: 1,
    src: "",
    volume: 1,
    addEventListener: vi.fn((type: string, handler: EventListener) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(handler);
    }),
    removeEventListener: vi.fn((type: string, handler: EventListener) => {
      listeners.get(type)?.delete(handler);
    }),
    dispatchEvent: vi.fn((event: Event) => {
      const handlers = listeners.get(event.type);
      if (handlers) for (const h of handlers) h(event);
      return true;
    }),
    play: vi.fn(() => Promise.resolve()),
    pause: vi.fn(() => {}),
    load: vi.fn(),
    ...overrides,
  } as unknown as HTMLAudioElement;
}

function makeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((k: string) => store[k] ?? null),
    setItem: vi.fn((k: string, v: string) => {
      store[k] = v;
    }),
    removeItem: vi.fn((k: string) => {
      delete store[k];
    }),
    clear: vi.fn(() => {
      store = {};
    }),
    get length() {
      return Object.keys(store).length;
    },
    key: vi.fn(() => ""),
  } as unknown as Storage;
}

// Per-section cues, so selectTrack takes its cue-matching path the way
// production does (the fixture in playbackController.test.ts has none).
const kpCues = [makeCue({ index: 0, section_type: "key_phrases", start_ms: 0 })];
const nsCues = [
  makeCue({
    index: 0,
    section_type: "natural_speed",
    section_index: 1,
    start_ms: 0,
    ref: { kind: "line", target_index: 0 },
  }),
];
const trCues = [
  makeCue({
    index: 0,
    section_type: "translated",
    section_index: 2,
    start_ms: 0,
    ref: { kind: "line", target_index: 0 },
  }),
];

const lessonAudio: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [
    {
      audio_id: "s1",
      section_index: 0,
      section_type: "key_phrases",
      title: "Key Phrases",
      cues: kpCues,
    },
    {
      audio_id: "s2",
      section_index: 1,
      section_type: "natural_speed",
      title: "Natural Speed",
      cues: nsCues,
    },
    {
      audio_id: "s3",
      section_index: 2,
      section_type: "translated",
      title: "Translated",
      cues: trCues,
    },
  ],
  cues: kpCues,
};

const KEY = "tt-resume-l1";

describe("resume position: section identity, page-hide saves, and the mount-order clobber", () => {
  let audioEl: HTMLAudioElement;
  let storage: Storage;

  beforeEach(() => {
    audioEl = makeFakeAudio();
    storage = makeStorage();
    vi.clearAllMocks();
  });

  afterEach(() => {
    Object.defineProperty(document, "visibilityState", {
      value: "visible",
      configurable: true,
    });
  });

  function mk(overrides: Partial<{ audioEl: HTMLAudioElement; lessonId: string }> = {}) {
    return createPlaybackController({
      createAudio: () => overrides.audioEl ?? audioEl,
      mediaSession: undefined,
      storage,
      lessonId: overrides.lessonId ?? "l1",
      lessonTitle: "Lesson 1",
      audioUrl: "/api/audio/a1",
      audio: lessonAudio,
      sectionUrl: (id: string) => `/api/audio/${id}`,
    });
  }

  function saved(): { section: string | null; position: number } {
    const raw = storage.getItem(KEY);
    expect(raw).not.toBeNull();
    return JSON.parse(raw!);
  }

  describe("what gets written", () => {
    it("records the active section alongside the position", () => {
      const ctrl = mk();
      ctrl.selectTrack("translated");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      audioEl.currentTime = 12.5;
      audioEl.dispatchEvent(new Event("pause"));

      expect(saved()).toEqual({ section: "translated", position: 12.5 });
    });

    it("records the section on destroy too", () => {
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      audioEl.currentTime = 42.5;
      ctrl.destroy();

      expect(saved()).toEqual({ section: "natural_speed", position: 42.5 });
    });
  });

  describe("saving without a pause — the refresh case", () => {
    it("saves when the page is hidden (mobile refresh / app switch)", () => {
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      audioEl.currentTime = 30;

      Object.defineProperty(document, "visibilityState", {
        value: "hidden",
        configurable: true,
      });
      document.dispatchEvent(new Event("visibilitychange"));

      expect(saved()).toEqual({ section: "natural_speed", position: 30 });
    });

    it("does not save while the page is merely becoming visible again", () => {
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      audioEl.currentTime = 30;
      (storage.setItem as ReturnType<typeof vi.fn>).mockClear();

      document.dispatchEvent(new Event("visibilitychange")); // visibilityState: "visible"

      expect(storage.setItem).not.toHaveBeenCalled();
    });

    it("saves on pagehide (desktop refresh, back/forward cache)", () => {
      const ctrl = mk();
      ctrl.selectTrack("translated");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      audioEl.currentTime = 7.25;

      window.dispatchEvent(new Event("pagehide"));

      expect(saved()).toEqual({ section: "translated", position: 7.25 });
    });

    it("saves periodically while playing, so a crash loses seconds not minutes", () => {
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      audioEl.currentTime = 2;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(storage.getItem(KEY)).toBeNull(); // under the throttle: nothing yet

      audioEl.currentTime = 6;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(saved()).toEqual({ section: "natural_speed", position: 6 });

      audioEl.currentTime = 8;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(saved().position).toBe(6); // throttled again, measured from the last save

      audioEl.currentTime = 11;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(saved().position).toBe(11);
    });

    it("stops saving once destroyed — the listeners are removed, not just guarded", () => {
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      audioEl.currentTime = 15;
      ctrl.destroy();
      (storage.setItem as ReturnType<typeof vi.fn>).mockClear();

      Object.defineProperty(document, "visibilityState", {
        value: "hidden",
        configurable: true,
      });
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("pagehide"));

      expect(storage.setItem).not.toHaveBeenCalled();
    });
  });

  describe("restoring", () => {
    it("survives the selectTrack that LessonPlayer.onMount always calls", () => {
      // THE REGRESSION. selectTrack sets pendingSeek; loadedmetadata applied
      // the resume and THEN let doSeek(pendingSeek) overwrite the element with
      // 0, while the reactive currentTime kept the resumed value — the
      // scrubber showed the right place and the audio played from the start.
      storage.setItem(KEY, JSON.stringify({ section: "natural_speed", position: 8.3 }));
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      expect(audioEl.currentTime).toBe(8.3);
      expect(ctrl.currentTime).toBe(8.3);
    });

    it("leaves the element and the reactive copy agreeing", () => {
      storage.setItem(KEY, JSON.stringify({ section: "translated", position: 20 }));
      const ctrl = mk();
      ctrl.selectTrack("translated");
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      expect(ctrl.currentTime).toBe(audioEl.currentTime);
    });

    it("a swap that lands on the saved section still resumes when playback was running", () => {
      // pendingSeek carries the swap bookkeeping (clearing `swapping`, resuming
      // playback). Nulling it to let the resume win would strand the player
      // with swapping stuck true, silently killing every later pause-save.
      storage.setItem(KEY, JSON.stringify({ section: "natural_speed", position: 8.3 }));
      const ctrl = mk();
      audioEl.dispatchEvent(new Event("play"));
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      expect(audioEl.currentTime).toBe(8.3);
      expect(audioEl.play).toHaveBeenCalled();

      // `swapping` must have been cleared: a later pause has to still save.
      audioEl.currentTime = 50;
      audioEl.dispatchEvent(new Event("pause"));
      expect(saved().position).toBe(50);
    });

    it("DISCARDS a position saved in a different section rather than misapplying it", () => {
      // Resuming at the right offset in the wrong section looks like it worked,
      // which is worse than starting over.
      storage.setItem(KEY, JSON.stringify({ section: "translated", position: 8.3 }));
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      expect(audioEl.currentTime).toBe(0);
      expect(ctrl.currentTime).toBe(0);
    });

    it("still honours a legacy bare-number value, applying it to whatever loads", () => {
      storage.setItem(KEY, "8.3");
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      expect(audioEl.currentTime).toBe(8.3);
    });

    it("survives a re-render of the lesson: section_type matches, audio_id need not", () => {
      // audio_id is a fresh uuid on every re-render; section_type is stable.
      storage.setItem(
        KEY,
        JSON.stringify({ section: "natural_speed", position: 8.3, audio_id: "STALE-UUID" }),
      );
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      expect(audioEl.currentTime).toBe(8.3);
    });

    it("discards a position past the end of its section", () => {
      storage.setItem(KEY, JSON.stringify({ section: "natural_speed", position: 150 }));
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      expect(audioEl.currentTime).toBe(0);
    });

    it("ignores a malformed stored value", () => {
      storage.setItem(KEY, "{not json");
      const ctrl = mk();
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      expect(audioEl.currentTime).toBe(0);
      expect(ctrl.currentTime).toBe(0);
    });

    it("ignores a stored object whose position is not a positive number", () => {
      storage.setItem(KEY, JSON.stringify({ section: "natural_speed", position: -5 }));
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      expect(audioEl.currentTime).toBe(0);
    });

    it("does not resume a genuine mid-session track swap at the old offset", () => {
      // The resume is consumed once, at the first loadedmetadata. A later
      // user-driven swap must land where selectTrack put it, not at 8.3.
      storage.setItem(KEY, JSON.stringify({ section: "natural_speed", position: 8.3 }));
      const ctrl = mk();
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBe(8.3);

      ctrl.selectTrack("translated");
      audioEl.currentTime = 0;
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBe(0);
    });
  });
});
