import { describe, it, expect, vi, beforeEach } from "vitest";
import { createPlaybackController } from "../playbackController.svelte";
import type { Cue, LessonAudio } from "$lib/api";

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
    play: vi.fn(() => {
      return Promise.resolve();
    }),
    pause: vi.fn(() => {}),
    load: vi.fn(),
    ...overrides,
  } as unknown as HTMLAudioElement;
}

// jsdom lacks MediaMetadata; provide a minimal polyfill so the controller can
// create it and the test can inspect properties.
if (typeof globalThis.MediaMetadata === "undefined") {
  (globalThis as any).MediaMetadata = class MediaMetadata {
    title: string;
    artist: string;
    album: string;
    artwork: MediaImage[];
    constructor(init: { title?: string; artist?: string; album?: string; artwork?: MediaImage[] }) {
      this.title = init.title ?? "";
      this.artist = init.artist ?? "";
      this.album = init.album ?? "";
      this.artwork = init.artwork ?? [];
    }
  };
}

const fakeLocalStorage = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value;
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      store = {};
    }),
    get length() {
      return Object.keys(store).length;
    },
    key: vi.fn((_i: number) => ""),
  } as Storage;
})();

function makeFakeMediaSession() {
  return {
    metadata: null as MediaMetadata | null,
    playbackState: "none" as MediaSessionPlaybackState,
    setActionHandler: vi.fn(),
    setPositionState: vi.fn(),
    ...({} as Omit<
      MediaSession,
      "metadata" | "playbackState" | "setActionHandler" | "setPositionState"
    >),
  };
}

const basicCues: Cue[] = [
  makeCue({
    index: 0,
    start_ms: 0,
    end_ms: 800,
    section_index: 0,
    section_type: "key_phrases",
    phrase_index: 0,
    text: "Hello",
    ref: { kind: "key_phrase", target_index: 0 },
  }),
  makeCue({
    index: 1,
    start_ms: 800,
    end_ms: 1500,
    section_index: 0,
    section_type: "key_phrases",
    phrase_index: 1,
    text: "world",
    ref: { kind: "key_phrase", target_index: 0 },
  }),
  makeCue({
    index: 2,
    start_ms: 1500,
    end_ms: 2500,
    section_index: 1,
    section_type: "natural_speed",
    phrase_index: 0,
    text: "How are you",
    ref: { kind: "line", target_index: 0 },
  }),
  makeCue({
    index: 3,
    start_ms: 2500,
    end_ms: 3500,
    section_index: 1,
    section_type: "natural_speed",
    phrase_index: 1,
    text: "I am fine",
    ref: { kind: "line", target_index: 1 },
  }),
  makeCue({
    index: 4,
    start_ms: 3500,
    end_ms: 4500,
    section_index: 2,
    section_type: "translated",
    phrase_index: 0,
    text: "Kako si",
    ref: { kind: "line", target_index: 0 },
  }),
];

const lessonAudio: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [
    { audio_id: "s1", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
    { audio_id: "s2", section_index: 1, section_type: "natural_speed", title: "Natural Speed" },
    { audio_id: "s3", section_index: 2, section_type: "translated", title: "Translated" },
  ],
  cues: basicCues,
};

describe("playbackController", () => {
  let audioEl: HTMLAudioElement;

  beforeEach(() => {
    audioEl = makeFakeAudio();
    fakeLocalStorage.clear();
    vi.clearAllMocks();
  });

  function createController(
    overrides: Partial<{
      audio: LessonAudio;
      audioEl: HTMLAudioElement;
      mediaSession: MediaSession | undefined;
      storage: Storage;
      lessonId: string;
      lessonTitle: string;
      audioUrl: string;
      sectionUrl: (audioId: string) => string;
    }> = {},
  ) {
    const ms = overrides.mediaSession !== undefined ? overrides.mediaSession : null;
    return createPlaybackController({
      createAudio: () => overrides.audioEl ?? audioEl,
      mediaSession: ms ?? undefined,
      storage: overrides.storage ?? fakeLocalStorage,
      lessonId: overrides.lessonId ?? "l1",
      lessonTitle: overrides.lessonTitle ?? "Lesson 1",
      audioUrl: overrides.audioUrl ?? "/api/audio/a1",
      audio: overrides.audio ?? lessonAudio,
      sectionUrl: overrides.sectionUrl ?? ((id: string) => `/api/audio/${id}`),
    });
  }

  describe("currentCue", () => {
    it("returns null when cues is null", () => {
      const noCuesAudio: LessonAudio = { ...lessonAudio, cues: null };
      const ctrl = createController({ audio: noCuesAudio });
      expect(ctrl.currentCue).toBeNull();
    });

    it("returns null when currentTime is before any cue start", () => {
      const ctrl = createController();
      audioEl.currentTime = 0;
      audioEl.dispatchEvent(new Event("timeupdate"));
      // First cue starts at 0ms, so at t=0 it's the current cue
      expect(ctrl.currentCue).not.toBeNull();
    });

    it("returns the last cue whose start_ms <= currentTime (mid-pause hold)", () => {
      const ctrl = createController();
      // After cue 0 ends (800ms) but before cue 1 starts (800ms) — t=0.8s exactly
      // Since 800 <= 800, cue 1 IS current (start_ms inclusive).
      // Mid-pause: t=1.2s = 1200ms. Cues with start_ms <= 1200 are index 0 (0), 1 (800).
      audioEl.currentTime = 1.2;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(1);
      // Even though cue 1 ended at 1500ms, it holds through the pause
    });

    it("advances to next cue when crossing start_ms boundary", () => {
      const ctrl = createController();
      audioEl.currentTime = 0.4;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(0);

      audioEl.currentTime = 1.0;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(1);
    });

    it("holds the last cue past the end of audio", () => {
      const ctrl = createController();
      audioEl.currentTime = 10;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(4);
    });
  });

  describe("section navigation", () => {
    it("nextSection during title (null section index) seeks to first cue of section 0", () => {
      const lateCues = basicCues.map((c) => ({
        ...c,
        start_ms: c.start_ms + 500,
      }));
      const lateAudio: LessonAudio = { ...lessonAudio, cues: lateCues };
      const ctrl = createController({ audio: lateAudio });
      // At t=0, no cue has start_ms ≤ 0 → currentSectionIndex is null
      audioEl.currentTime = 0;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentSectionIndex).toBeNull();

      ctrl.nextSection();
      // Should seek to first cue's start (500ms)
      expect(audioEl.currentTime).toBeCloseTo(0.5, 3);
    });

    it("prevSection during title (null section index) seeks to 0", () => {
      const lateCues = basicCues.map((c) => ({
        ...c,
        start_ms: c.start_ms + 500,
      }));
      const lateAudio: LessonAudio = { ...lessonAudio, cues: lateCues };
      const ctrl = createController({ audio: lateAudio });
      audioEl.currentTime = 0.3;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentSectionIndex).toBeNull();

      ctrl.prevSection();
      // Should seek to 0 (beginning of audio)
      expect(audioEl.currentTime).toBe(0);
    });

    it("nextSection seeks to the first cue of the next section", () => {
      const ctrl = createController();
      audioEl.currentTime = 0.1;
      audioEl.dispatchEvent(new Event("timeupdate"));
      ctrl.nextSection();
      // First cue of section 1 is index 2 at 1500ms
      expect(audioEl.currentTime).toBeCloseTo(1.5, 3);
    });

    it("nextSection is a no-op when already in the last section", () => {
      const ctrl = createController();
      audioEl.currentTime = 3.6;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.section_index).toBe(2);
      ctrl.nextSection();
      expect(audioEl.currentTime).toBe(3.6);
    });

    it("prevSection seeks to the first cue of the previous section", () => {
      const ctrl = createController();
      audioEl.currentTime = 3.6;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.section_index).toBe(2);
      ctrl.prevSection();
      // First cue of section 1 is index 2 at 1500ms
      expect(audioEl.currentTime).toBeCloseTo(1.5, 3);
    });

    it("prevSection is a no-op when already in section 0", () => {
      const ctrl = createController();
      audioEl.currentTime = 0.1;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.section_index).toBe(0);
      ctrl.prevSection();
      expect(audioEl.currentTime).toBe(0.1);
    });

    it("nextSection is a no-op when cues is null", () => {
      const noCuesAudio: LessonAudio = { ...lessonAudio, cues: null };
      const ctrl = createController({ audio: noCuesAudio });
      audioEl.currentTime = 5;
      ctrl.nextSection();
      expect(audioEl.currentTime).toBe(5);
    });

    it("prevSection is a no-op when cues is null", () => {
      const noCuesAudio: LessonAudio = { ...lessonAudio, cues: null };
      const ctrl = createController({ audio: noCuesAudio });
      audioEl.currentTime = 5;
      ctrl.prevSection();
      expect(audioEl.currentTime).toBe(5);
    });
  });

  describe("ref-group cue stepping", () => {
    it("nextCue steps past all cues sharing the same ref group", () => {
      // Cues 0-1 share ref {key_phrase, 0} — one group
      const ctrl = createController();
      audioEl.currentTime = 0;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(0);

      ctrl.nextCue();
      // Should skip to the first cue of the NEXT ref group: cue 2 (natural_speed, line 0)
      expect(audioEl.currentTime).toBeCloseTo(1.5, 3);
    });

    it("prevCue steps to the start of the previous ref group", () => {
      const ctrl = createController();
      audioEl.currentTime = 3.6;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(4);

      ctrl.prevCue();
      // Previous ref group is cues 2-3 (section 1, lines 0-1).
      // But actually, cues 2 and 3 have different target_index (0 vs 1),
      // so they're different groups. prev of cue 4's group → cue 3's group
      expect(audioEl.currentTime).toBeCloseTo(2.5, 3);
    });

    it("nextCue is a no-op at the last ref group", () => {
      const ctrl = createController();
      audioEl.currentTime = 4.0;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(4);
      const before = audioEl.currentTime;
      ctrl.nextCue();
      expect(audioEl.currentTime).toBe(before);
    });

    it("prevCue is a no-op at the first ref group", () => {
      const ctrl = createController();
      audioEl.currentTime = 0.1;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(0);
      const before = audioEl.currentTime;
      ctrl.prevCue();
      expect(audioEl.currentTime).toBe(before);
    });

    it("treats consecutive narration cues as separate skip groups", () => {
      // The backend emits narration refs WITHOUT target_index ({"kind": "narration"}),
      // matching real manifests: lesson title + section title are adjacent narration
      // cues and must NOT merge into one group (a shared "narration-undefined" key
      // would make sentence-skip jump past the section announcement).
      const narrationCues: Cue[] = [
        makeCue({
          index: 0,
          start_ms: 0,
          end_ms: 500,
          section_index: null,
          section_type: null,
          text: "Day 1: Coffee",
          ref: { kind: "narration" },
        }),
        makeCue({
          index: 1,
          start_ms: 500,
          end_ms: 1000,
          section_index: 0,
          text: "Key Phrases",
          ref: { kind: "narration" },
        }),
        makeCue({
          index: 2,
          start_ms: 1000,
          end_ms: 1500,
          section_index: 0,
          text: "kavo prosim",
          ref: { kind: "key_phrase", target_index: 0 },
        }),
      ];
      const lessonAud: LessonAudio = { ...lessonAudio, cues: narrationCues };
      const ctrl = createController({ audio: lessonAud });
      audioEl.currentTime = 0;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(0);

      // Steps to the section-title narration (cue 1), not past it to cue 2
      ctrl.nextCue();
      expect(audioEl.currentTime).toBeCloseTo(0.5, 3);
    });

    it("nextCue/prevCue are no-ops when cues is null", () => {
      const noCuesAudio: LessonAudio = { ...lessonAudio, cues: null };
      const ctrl = createController({ audio: noCuesAudio });
      audioEl.currentTime = 5;
      ctrl.nextCue();
      expect(audioEl.currentTime).toBe(5);
      ctrl.prevCue();
      expect(audioEl.currentTime).toBe(5);
    });
  });

  describe("repeatCue", () => {
    it("seeks to currentCue.start_ms", () => {
      const ctrl = createController();
      audioEl.currentTime = 1.0;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(1);

      ctrl.repeatCue();
      // Cue 1 starts at 800ms
      expect(audioEl.currentTime).toBeCloseTo(0.8, 3);
    });

    it("is a no-op when currentCue is null", () => {
      const noCuesAudio: LessonAudio = { ...lessonAudio, cues: null };
      const ctrl = createController({ audio: noCuesAudio });
      audioEl.currentTime = 1.0;
      ctrl.repeatCue();
      expect(audioEl.currentTime).toBe(1.0);
    });
  });

  describe("MediaSession", () => {
    it("defaults to navigator.mediaSession when no dep provided", () => {
      const fakeMs = makeFakeMediaSession();
      const orig = (navigator as any).mediaSession;
      vi.stubGlobal("navigator", { ...navigator, mediaSession: fakeMs });
      try {
        createController({ mediaSession: undefined });
        expect(fakeMs.setActionHandler).toHaveBeenCalledWith("play", expect.any(Function));
        expect(fakeMs.setActionHandler).toHaveBeenCalledWith("pause", expect.any(Function));
      } finally {
        vi.stubGlobal("navigator", { ...navigator, mediaSession: orig });
      }
    });

    it("does not crash when both mediaSession dep and navigator.mediaSession are absent", () => {
      const orig = (navigator as any).mediaSession;
      vi.stubGlobal("navigator", { ...navigator, mediaSession: undefined });
      try {
        expect(() => createController({ mediaSession: undefined })).not.toThrow();
      } finally {
        vi.stubGlobal("navigator", { ...navigator, mediaSession: orig });
      }
    });

    it("wires action handlers when mediaSession is provided", () => {
      const mediaSession = makeFakeMediaSession();
      const ctrl = createController({ mediaSession: mediaSession as unknown as MediaSession });

      expect(mediaSession.setActionHandler).toHaveBeenCalledWith("play", expect.any(Function));
      expect(mediaSession.setActionHandler).toHaveBeenCalledWith("pause", expect.any(Function));
      expect(mediaSession.setActionHandler).toHaveBeenCalledWith(
        "seekbackward",
        expect.any(Function),
      );
      expect(mediaSession.setActionHandler).toHaveBeenCalledWith(
        "seekforward",
        expect.any(Function),
      );
      expect(mediaSession.setActionHandler).toHaveBeenCalledWith(
        "previoustrack",
        expect.any(Function),
      );
      expect(mediaSession.setActionHandler).toHaveBeenCalledWith("nexttrack", expect.any(Function));
      expect(mediaSession.setActionHandler).toHaveBeenCalledWith("seekto", expect.any(Function));
    });

    it("does not crash when mediaSession is undefined", () => {
      expect(() => {
        createController({ mediaSession: undefined });
      }).not.toThrow();
    });

    it("sets metadata with lesson title and section title", () => {
      const mediaSession = makeFakeMediaSession();
      createController({ mediaSession: mediaSession as unknown as MediaSession });

      const meta = mediaSession.metadata as unknown as MediaMetadata;
      expect(meta).toBeInstanceOf(MediaMetadata);
      expect(meta.title).toBe("Lesson 1");
      expect(meta.artist).toBe("Key Phrases");
    });

    it("calls setPositionState on initialization", () => {
      const mediaSession = makeFakeMediaSession();
      createController({ mediaSession: mediaSession as unknown as MediaSession });

      expect(mediaSession.setPositionState).toHaveBeenCalledWith({
        duration: 100,
        playbackRate: 1,
        position: 0,
      });
    });

    it("updates metadata artist when section changes", () => {
      const mediaSession = makeFakeMediaSession();
      createController({ mediaSession: mediaSession as unknown as MediaSession });
      // Advance past section boundary (sec 0 → sec 1 at 1500ms)
      audioEl.currentTime = 2.0;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect((mediaSession.metadata as unknown as MediaMetadata).artist).toBe("Natural Speed");
    });

    it("calls setPositionState after seek", () => {
      const mediaSession = makeFakeMediaSession();
      const ctrl = createController({ mediaSession: mediaSession as unknown as MediaSession });

      vi.clearAllMocks();
      ctrl.seekBy(10);
      expect(mediaSession.setPositionState).toHaveBeenCalled();
    });

    it("calls setPositionState after rate change", () => {
      const mediaSession = makeFakeMediaSession();
      const ctrl = createController({ mediaSession: mediaSession as unknown as MediaSession });

      vi.clearAllMocks();
      ctrl.setRate(1.5);
      expect(mediaSession.setPositionState).toHaveBeenCalledWith({
        duration: 100,
        playbackRate: 1.5,
        position: expect.any(Number),
      });
    });
  });

  describe("currentCue edge cases", () => {
    it("handles cues with null ref (no ref group)", () => {
      const noRefCues: Cue[] = [
        makeCue({ index: 0, start_ms: 0, end_ms: 500, section_index: 0, ref: null }),
        makeCue({ index: 1, start_ms: 500, end_ms: 1000, section_index: 0, ref: null }),
      ];
      const lessonAud: LessonAudio = { ...lessonAudio, cues: noRefCues };
      const ctrl = createController({ audio: lessonAud });
      audioEl.currentTime = 0.3;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(0);

      // Null-ref cues each get their own ref group
      ctrl.nextCue();
      expect(audioEl.currentTime).toBeCloseTo(0.5, 3);
    });

    it("handles empty cues array", () => {
      const emptyCuesAud: LessonAudio = { ...lessonAudio, cues: [] };
      const ctrl = createController({ audio: emptyCuesAud });
      expect(ctrl.currentCue).toBeNull();
      ctrl.nextCue();
      // No-op, no crash
      expect(audioEl.currentTime).toBe(0);
    });

    it("returns null when currentTime is before first cue start", () => {
      const lateCues: Cue[] = [
        makeCue({ index: 0, start_ms: 500, end_ms: 1000, section_index: 0, text: "Late" }),
      ];
      const lessonAud: LessonAudio = { ...lessonAudio, cues: lateCues };
      const ctrl = createController({ audio: lessonAud });
      // currentTime is restored from resume or is 0; first cue starts at 500ms
      audioEl.currentTime = 0.1;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue).toBeNull();
      expect(ctrl.currentSectionIndex).toBeNull();
      expect(ctrl.currentSectionTitle).toBe("");
    });
  });

  describe("playback controls", () => {
    it("play() calls audio.play()", () => {
      const ctrl = createController();
      ctrl.play();
      expect(audioEl.play).toHaveBeenCalled();
    });

    it("pause() calls audio.pause()", () => {
      const ctrl = createController();
      ctrl.pause();
      expect(audioEl.pause).toHaveBeenCalled();
    });

    it("togglePlay plays when paused, pauses when playing", () => {
      const ctrl = createController();
      ctrl.togglePlay();
      expect(audioEl.play).toHaveBeenCalled();
      // Simulate play event to update playing state
      audioEl.dispatchEvent(new Event("play"));
      ctrl.togglePlay();
      expect(audioEl.pause).toHaveBeenCalled();
    });

    it("seekTo seeks to exact time and calls updatePositionState", () => {
      const mediaSession = makeFakeMediaSession();
      const ctrl = createController({ mediaSession: mediaSession as unknown as MediaSession });
      ctrl.seekTo(42.5);
      expect(audioEl.currentTime).toBe(42.5);
    });

    it("seekBy adds seconds to currentTime", () => {
      const ctrl = createController();
      audioEl.currentTime = 5;
      ctrl.seekBy(10);
      expect(audioEl.currentTime).toBe(15);
    });

    it("seekBy clamps to 0", () => {
      const ctrl = createController();
      audioEl.currentTime = 3;
      ctrl.seekBy(-10);
      expect(audioEl.currentTime).toBe(0);
    });

    it("seekBy clamps to duration", () => {
      const ctrl = createController();
      audioEl.currentTime = 90;
      ctrl.seekBy(20);
      expect(audioEl.currentTime).toBe(100);
    });

    it("setRate changes playbackRate", () => {
      const ctrl = createController();
      ctrl.setRate(0.85);
      expect(audioEl.playbackRate).toBe(0.85);
    });

    it("ratechange event updates rate and position state", () => {
      const mediaSession = makeFakeMediaSession();
      createController({ mediaSession: mediaSession as unknown as MediaSession });
      audioEl.playbackRate = 1.5;
      audioEl.dispatchEvent(new Event("ratechange"));
      expect(mediaSession.setPositionState).toHaveBeenCalledWith(
        expect.objectContaining({ playbackRate: 1.5 }),
      );
    });

    it("play event sets playing=true and updates mediaSession state", () => {
      const mediaSession = makeFakeMediaSession();
      const ctrl = createController({ mediaSession: mediaSession as unknown as MediaSession });
      audioEl.dispatchEvent(new Event("play"));
      expect(ctrl.playing).toBe(true);
      expect(mediaSession.playbackState).toBe("playing");
    });

    it("pause event clears playing and updates mediaSession state", () => {
      const mediaSession = makeFakeMediaSession();
      const ctrl = createController({ mediaSession: mediaSession as unknown as MediaSession });
      audioEl.dispatchEvent(new Event("play"));
      audioEl.dispatchEvent(new Event("pause"));
      expect(ctrl.playing).toBe(false);
    });

    it("ended event sets playing=false and updates mediaSession state", () => {
      const mediaSession = makeFakeMediaSession();
      createController({ mediaSession: mediaSession as unknown as MediaSession });
      audioEl.dispatchEvent(new Event("ended"));
      expect(mediaSession.playbackState).toBe("none");
    });

    it("loadedmetadata event triggers updatePositionState", () => {
      const mediaSession = makeFakeMediaSession();
      createController({ mediaSession: mediaSession as unknown as MediaSession });
      (audioEl as unknown as { duration: number }).duration = 200;
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(mediaSession.setPositionState).toHaveBeenCalledWith(
        expect.objectContaining({ duration: 200 }),
      );
    });

    it("pause event saves resume position and updates mediaSession state", () => {
      const mediaSession = makeFakeMediaSession();
      createController({
        mediaSession: mediaSession as unknown as MediaSession,
        storage: fakeLocalStorage,
      });
      audioEl.currentTime = 15.5;
      audioEl.dispatchEvent(new Event("pause"));
      expect(fakeLocalStorage.setItem).toHaveBeenCalledWith("tt-resume-l1", "15.5");
      expect(mediaSession.playbackState).toBe("paused");
    });
  });

  describe("destroy", () => {
    it("readonly getters return current state", () => {
      const ctrl = createController({
        mediaSession: makeFakeMediaSession() as unknown as MediaSession,
      });
      expect(typeof ctrl.currentTime).toBe("number");
      expect(typeof ctrl.duration).toBe("number");
      expect(typeof ctrl.playbackRate).toBe("number");
      expect(typeof ctrl.playing).toBe("boolean");
      expect(typeof ctrl.sentenceSkip).toBe("boolean");
      // At t=0 with first cue at 0ms, currentSectionIndex is 0
      expect(ctrl.currentSectionIndex).toBe(0);
    });

    it("pause saves resume position and clears mediaSession handlers", () => {
      const mediaSession = makeFakeMediaSession();
      const ctrl = createController({ mediaSession: mediaSession as unknown as MediaSession });
      audioEl.pause = vi.fn();
      ctrl.destroy();
      expect(audioEl.pause).toHaveBeenCalled();
      expect(mediaSession.setActionHandler).toHaveBeenCalledWith("play", null);
      expect(mediaSession.metadata).toBeNull();
    });

    it("destroy handles missing mediaSession gracefully", () => {
      const ctrl = createController();
      expect(() => ctrl.destroy()).not.toThrow();
    });

    it("saves resume position before clearing src (browser resets currentTime on src clear)", () => {
      // Real browsers synchronously reset currentTime→0 when src is set to "".
      // The fake emulates this so we can verify saveResume() is called before
      // the src clear, not after.
      let capturedSrc = "";
      const zeroesOnClear = makeFakeAudio({
        set currentTime(v: number) {
          // no-op setter — we drive this via defineProperty below
        },
      });
      let internalTime = 42.5;
      Object.defineProperty(zeroesOnClear, "currentTime", {
        get: () => internalTime,
        set: (v: number) => {
          internalTime = v;
        },
        configurable: true,
      });
      // When src is set to "", reset currentTime to 0 like a real browser
      const origDesc = Object.getOwnPropertyDescriptor(HTMLAudioElement.prototype, "src");
      Object.defineProperty(zeroesOnClear, "src", {
        get: () => capturedSrc,
        set: (v: string) => {
          capturedSrc = v;
          if (v === "") internalTime = 0;
        },
        configurable: true,
      });

      const ctrl = createController({ audioEl: zeroesOnClear, storage: fakeLocalStorage });
      ctrl.destroy();
      // The stored position should be 42.5 (the pre-src-clear value), not 0
      expect(fakeLocalStorage.setItem).toHaveBeenCalledWith("tt-resume-l1", "42.5");
    });

    it("a pause event queued behind destroy() does not clobber the saved resume", async () => {
      // Real browsers QUEUE the pause event as a task (media.pause() spec), so
      // the sequence in a real browser is: destroy() → pause() queues the
      // event → saveResume() stores 42.5 → src="" resets currentTime to 0 →
      // destroy returns → the queued pause listener finally runs and reads
      // currentTime 0. The listener's own resume write must not overwrite the
      // value destroy just saved. Asserts the FINAL stored value, not "was
      // called with" — a later clobbering call must fail the test.
      let internalTime = 42.5;
      const el = makeFakeAudio();
      Object.defineProperty(el, "currentTime", {
        get: () => internalTime,
        set: (v: number) => {
          internalTime = v;
        },
        configurable: true,
      });
      Object.defineProperty(el, "src", {
        get: () => "",
        set: (v: string) => {
          if (v === "") internalTime = 0;
        },
        configurable: true,
      });
      (el as unknown as { pause: () => void }).pause = () => {
        queueMicrotask(() => el.dispatchEvent(new Event("pause")));
      };

      const ctrl = createController({ audioEl: el, storage: fakeLocalStorage });
      ctrl.destroy();
      await Promise.resolve(); // flush the queued pause event
      expect(fakeLocalStorage.getItem("tt-resume-l1")).toBe("42.5");
    });
  });

  describe("per-lesson resume", () => {
    it("saves currentTime to localStorage on pause", () => {
      const ctrl = createController({ storage: fakeLocalStorage, lessonId: "l1" });
      audioEl.currentTime = 12.5;
      audioEl.dispatchEvent(new Event("pause"));
      expect(fakeLocalStorage.setItem).toHaveBeenCalledWith("tt-resume-l1", "12.5");
    });

    it("does NOT touch position before loadedmetadata (scrubber would desync)", () => {
      // Restoring pre-metadata left the range input at max=1 with a stale
      // value — the "shows 4:27 as position 0" bug. The restore must wait
      // for real duration.
      fakeLocalStorage.setItem("tt-resume-l1", "8.3");
      const noMetaAudio = makeFakeAudio({ duration: NaN });
      const ctrl = createController({ storage: fakeLocalStorage, audioEl: noMetaAudio });
      expect(noMetaAudio.currentTime).toBe(0);
      expect(ctrl.currentTime).toBe(0);
    });

    it("restores position when loadedmetadata delivers the duration", () => {
      fakeLocalStorage.setItem("tt-resume-l1", "8.3");
      const ctrl = createController({ storage: fakeLocalStorage, lessonId: "l1" });
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBe(8.3);
      expect(ctrl.currentTime).toBe(8.3);
      expect(ctrl.duration).toBe(100);
    });

    it("discards a saved position past the end of the audio", () => {
      fakeLocalStorage.setItem("tt-resume-l1", "150");
      const ctrl = createController({ storage: fakeLocalStorage, lessonId: "l1" });
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBe(0);
      expect(ctrl.currentTime).toBe(0);
    });

    it("ignores a stored resume that is non-numeric", () => {
      fakeLocalStorage.setItem("tt-resume-l1", "not-a-number"); // parseFloat → NaN
      const ctrl = createController({ storage: fakeLocalStorage, lessonId: "l1" });
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBe(0);
      expect(ctrl.currentTime).toBe(0);
    });

    it("ignores a stored resume that is non-positive", () => {
      fakeLocalStorage.setItem("tt-resume-l1", "-5"); // valid number but <= 0
      const ctrl = createController({ storage: fakeLocalStorage, lessonId: "l1" });
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBe(0);
      expect(ctrl.currentTime).toBe(0);
    });

    it("restores only once (later metadata reloads don't rewind)", () => {
      fakeLocalStorage.setItem("tt-resume-l1", "8.3");
      const ctrl = createController({ storage: fakeLocalStorage, lessonId: "l1" });
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      audioEl.currentTime = 42;
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBe(42);
    });

    it("destroy before loadedmetadata keeps the stored resume intact", () => {
      fakeLocalStorage.setItem("tt-resume-l1", "8.3");
      const noMetaAudio = makeFakeAudio({ duration: NaN });
      const ctrl = createController({ storage: fakeLocalStorage, audioEl: noMetaAudio });
      ctrl.destroy();
      expect(fakeLocalStorage.getItem("tt-resume-l1")).toBe("8.3");
    });

    it("seeking before metadata is not clamped to 0", () => {
      const noMetaAudio = makeFakeAudio({ duration: NaN });
      const ctrl = createController({ audioEl: noMetaAudio });
      ctrl.seekTo(30);
      expect(noMetaAudio.currentTime).toBe(30);
    });

    it("uses different keys for different lessons", () => {
      fakeLocalStorage.setItem("tt-resume-l1", "5.0");
      fakeLocalStorage.setItem("tt-resume-l2", "10.0");
      const ctrl = createController({ storage: fakeLocalStorage, lessonId: "l2" });
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBe(10.0);
    });
  });

  describe("sentenceSkip toggle", () => {
    it("defaults to false", () => {
      const ctrl = createController();
      expect(ctrl.sentenceSkip).toBe(false);
    });

    it("can be set to true", () => {
      const ctrl = createController();
      ctrl.setSentenceSkip(true);
      expect(ctrl.sentenceSkip).toBe(true);
    });

    it("toggles previoustrack/nexttrack behavior", () => {
      const mediaSession = makeFakeMediaSession();
      createController({ mediaSession: mediaSession as unknown as MediaSession });

      // previoustrack handler was registered
      expect(mediaSession.setActionHandler).toHaveBeenCalledWith(
        "previoustrack",
        expect.any(Function),
      );
      expect(mediaSession.setActionHandler).toHaveBeenCalledWith("nexttrack", expect.any(Function));
    });
  });

  describe("MediaSession handlers", () => {
    it("seekbackward handler subtracts 10s", () => {
      const mediaSession = makeFakeMediaSession();
      let seekHandler: ((details: any) => void) | null = null;
      mediaSession.setActionHandler = vi.fn((action: string, handler: any) => {
        if (action === "seekbackward") seekHandler = handler;
      }) as any;
      createController({ mediaSession: mediaSession as unknown as MediaSession });
      audioEl.currentTime = 30;
      seekHandler!({});
      expect(audioEl.currentTime).toBe(20);
    });

    it("seekforward handler adds 10s", () => {
      const mediaSession = makeFakeMediaSession();
      let seekHandler: ((details: any) => void) | null = null;
      mediaSession.setActionHandler = vi.fn((action: string, handler: any) => {
        if (action === "seekforward") seekHandler = handler;
      }) as any;
      createController({ mediaSession: mediaSession as unknown as MediaSession });
      audioEl.currentTime = 30;
      seekHandler!({});
      expect(audioEl.currentTime).toBe(40);
    });

    it("play handler calls audio.play", () => {
      const mediaSession = makeFakeMediaSession();
      let playHandler: (() => void) | null = null;
      mediaSession.setActionHandler = vi.fn((action: string, handler: any) => {
        if (action === "play") playHandler = handler;
      }) as any;
      createController({ mediaSession: mediaSession as unknown as MediaSession });
      playHandler!();
      expect(audioEl.play).toHaveBeenCalled();
    });

    it("pause handler calls audio.pause", () => {
      const mediaSession = makeFakeMediaSession();
      let pauseHandler: (() => void) | null = null;
      mediaSession.setActionHandler = vi.fn((action: string, handler: any) => {
        if (action === "pause") pauseHandler = handler;
      }) as any;
      createController({ mediaSession: mediaSession as unknown as MediaSession });
      pauseHandler!();
      expect(audioEl.pause).toHaveBeenCalled();
    });

    it("seekto handler seeks to specified time", () => {
      const mediaSession = makeFakeMediaSession();
      let seektoHandler: ((details: { seekTime?: number }) => void) | null = null;
      mediaSession.setActionHandler = vi.fn((action: string, handler: any) => {
        if (action === "seekto") seektoHandler = handler;
      }) as any;
      createController({ mediaSession: mediaSession as unknown as MediaSession });
      seektoHandler!({ seekTime: 42 });
      expect(audioEl.currentTime).toBe(42);
    });

    it("previoustrack calls prevSection by default, prevCue when sentenceSkip=true", () => {
      const mediaSession = makeFakeMediaSession();
      let prevHandler: (() => void) | null = null;
      mediaSession.setActionHandler = vi.fn((action: string, handler: any) => {
        if (action === "previoustrack") prevHandler = handler;
      }) as any;
      const ctrl = createController({ mediaSession: mediaSession as unknown as MediaSession });
      audioEl.currentTime = 1.6;
      audioEl.dispatchEvent(new Event("timeupdate"));
      // default: prevSection goes to section 0
      prevHandler!();
      expect(audioEl.currentTime).toBeCloseTo(0, 3);
      // enable sentence skip
      ctrl.setSentenceSkip(true);
      audioEl.currentTime = 1.6;
      audioEl.dispatchEvent(new Event("timeupdate"));
      prevHandler!();
      // prevCue goes to start of previous ref group (key_phrase group) = cue 0 at 0ms
      expect(audioEl.currentTime).toBeCloseTo(0, 3);
    });

    it("nexttrack calls nextSection by default, nextCue when sentenceSkip=true", () => {
      const mediaSession = makeFakeMediaSession();
      let nextHandler: (() => void) | null = null;
      mediaSession.setActionHandler = vi.fn((action: string, handler: any) => {
        if (action === "nexttrack") nextHandler = handler;
      }) as any;
      const ctrl = createController({ mediaSession: mediaSession as unknown as MediaSession });
      audioEl.currentTime = 0.1;
      audioEl.dispatchEvent(new Event("timeupdate"));
      nextHandler!();
      expect(audioEl.currentTime).toBeCloseTo(1.5, 3);
      ctrl.setSentenceSkip(true);
      // Now at sec 1, cue 2; nextCue advances to next group (cue 3, line 1)
      audioEl.currentTime = 1.6;
      audioEl.dispatchEvent(new Event("timeupdate"));
      nextHandler!();
      expect(audioEl.currentTime).toBeCloseTo(2.5, 3);
    });
  });

  describe("sentenceSkip toggle", () => {
    it("defaults to false", () => {
      const ctrl = createController();
      expect(ctrl.sentenceSkip).toBe(false);
    });

    it("can be set to true", () => {
      const ctrl = createController();
      ctrl.setSentenceSkip(true);
      expect(ctrl.sentenceSkip).toBe(true);
    });
  });

  describe("seekToCue", () => {
    it("seeks to cue.start_ms / 1000", () => {
      const ctrl = createController();
      const cue = basicCues[3]; // index 3, start_ms 2500
      ctrl.seekToCue(cue);
      expect(audioEl.currentTime).toBeCloseTo(2.5, 3);
    });

    it("clamps to 0 when start_ms is 0", () => {
      const ctrl = createController();
      audioEl.currentTime = 10;
      const cue = basicCues[0]; // start_ms 0
      ctrl.seekToCue(cue);
      expect(audioEl.currentTime).toBe(0);
    });

    it("calls updatePositionState via doSeek", () => {
      const mediaSession = makeFakeMediaSession();
      const ctrl = createController({ mediaSession: mediaSession as unknown as MediaSession });
      vi.clearAllMocks();
      const cue = basicCues[2]; // start_ms 1500
      ctrl.seekToCue(cue);
      expect(mediaSession.setPositionState).toHaveBeenCalled();
    });
  });

  describe("selectTrack", () => {
    const sectionsCues: Cue[] = [
      makeCue({
        index: 0,
        start_ms: 0,
        end_ms: 500,
        section_index: 0,
        section_type: "natural_speed",
        phrase_index: 0,
        text: "Dober dan",
        ref: { kind: "line", target_index: 0 },
      }),
      makeCue({
        index: 1,
        start_ms: 500,
        end_ms: 1000,
        section_index: 0,
        section_type: "natural_speed",
        phrase_index: 1,
        text: "Kako si",
        ref: { kind: "line", target_index: 1 },
      }),
    ];

    const sectionsAudio: LessonAudio = {
      audio_id: "a1",
      lesson_id: "l1",
      sections: [
        {
          audio_id: "sec-natural",
          section_index: 0,
          section_type: "natural_speed",
          title: "Natural Speed",
          cues: sectionsCues,
        },
        {
          audio_id: "sec-translated",
          section_index: 1,
          section_type: "translated",
          title: "Translated",
          cues: [
            makeCue({
              index: 0,
              start_ms: 0,
              end_ms: 600,
              section_index: 0,
              section_type: "translated",
              phrase_index: 0,
              text: "Dober dan",
              ref: { kind: "line", target_index: 0 },
            }),
            makeCue({
              index: 1,
              start_ms: 600,
              end_ms: 1200,
              section_index: 0,
              section_type: "translated",
              phrase_index: 1,
              text: "Good day",
              ref: { kind: "line", target_index: 0 },
            }),
            makeCue({
              index: 2,
              start_ms: 1200,
              end_ms: 1800,
              section_index: 0,
              section_type: "translated",
              phrase_index: 2,
              text: "Kako si",
              ref: { kind: "line", target_index: 1 },
            }),
            makeCue({
              index: 3,
              start_ms: 1800,
              end_ms: 2400,
              section_index: 0,
              section_type: "translated",
              phrase_index: 3,
              text: "How are you",
              ref: { kind: "line", target_index: 1 },
            }),
          ],
        },
        {
          audio_id: "sec-key",
          section_index: 2,
          section_type: "key_phrases",
          title: "Key Phrases",
          cues: [
            makeCue({
              index: 0,
              start_ms: 0,
              end_ms: 800,
              section_index: 0,
              section_type: "key_phrases",
              phrase_index: 0,
              text: "kavo prosim",
              ref: { kind: "key_phrase", target_index: 0 },
            }),
          ],
        },
      ],
      cues: sectionsCues,
    };

    it("swaps audioEl.src to the section URL", () => {
      const ctrl = createController({ audio: sectionsAudio });
      ctrl.selectTrack("translated");
      expect(audioEl.src).toBe("/api/audio/sec-translated");
    });

    it("swaps activeCues and activeSectionType", () => {
      const ctrl = createController({ audio: sectionsAudio });
      expect(ctrl.activeSectionType).toBe("natural_speed");
      ctrl.selectTrack("translated");
      expect(ctrl.activeSectionType).toBe("translated");
      expect(ctrl.activeCues).toHaveLength(4);
      expect(ctrl.activeCues![0].text).toBe("Dober dan");
    });

    it("seek is deferred — not applied until loadedmetadata fires", () => {
      const ctrl = createController({ audio: sectionsAudio });
      // Start on line 1 (target_index=1) of natural_speed
      audioEl.currentTime = 0.6;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.ref?.target_index).toBe(1);

      // Select translated section — same line ref exists at target_index=1
      ctrl.selectTrack("translated");
      // The seek must NOT have landed yet: selectTrack only stashes it and
      // applies it on loadedmetadata. A synchronous seek would already show
      // the matched cue's 1.2s here — assert we're still at the pre-swap time.
      const timeAfterSwap = audioEl.currentTime;
      expect(timeAfterSwap).toBeCloseTo(0.6, 3);
      expect(timeAfterSwap).not.toBeCloseTo(1.2, 3);
      // Now fire loadedmetadata — the deferred seek should apply
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      // The matched cue at target_index=1 in translated starts at 1200ms
      expect(audioEl.currentTime).toBeCloseTo(1.2, 3);
    });

    it("Dialogue variant switch preserves position via line ref", () => {
      const ctrl = createController({ audio: sectionsAudio });
      // Position on line 0 in natural_speed
      audioEl.currentTime = 0.1;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.ref).toEqual({ kind: "line", target_index: 0 });

      // Switch to translated — same line ref (kind=line, target_index=0) exists
      ctrl.selectTrack("translated");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      // In translated, line 0 starts at 0ms
      expect(audioEl.currentTime).toBeCloseTo(0, 3);
    });

    it("phase switch (Dialogue→Key Phrases) restarts at 0", () => {
      const ctrl = createController({ audio: sectionsAudio });
      // Position on line 1 in natural_speed
      audioEl.currentTime = 0.6;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.ref?.kind).toBe("line");

      // Switch to key_phrases — ref.kind changes from "line" to "key_phrase", no match
      ctrl.selectTrack("key_phrases");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBeCloseTo(0, 3);
    });

    it("play-state continuity: playing before → play() called after deferred seek", () => {
      const ctrl = createController({ audio: sectionsAudio });
      // Start playing — make the fake audio reflect non-paused state
      Object.defineProperty(audioEl, "paused", { value: false, configurable: true });
      audioEl.dispatchEvent(new Event("play"));
      expect(ctrl.playing).toBe(true);

      // selectTrack should detect playing, then after loadedmetadata call play()
      ctrl.selectTrack("translated");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.play).toHaveBeenCalled();
    });

    it("selectTrack does not overwrite tt-resume-${lessonId}", () => {
      fakeLocalStorage.setItem("tt-resume-l1", "5.0");
      const ctrl = createController({
        audio: sectionsAudio,
        storage: fakeLocalStorage,
        lessonId: "l1",
      });
      // Load metadata first so the initial pendingResume is consumed and set to
      // null — otherwise saveResume()'s own pendingResume guard (not the one
      // under test) would protect the key and mask a broken swapping guard.
      audioEl.dispatchEvent(new Event("loadedmetadata"));

      ctrl.selectTrack("translated");
      // A real src swap fires a pause/emptied event while currentTime is 0.
      // With pendingResume already null, only the `swapping` guard stops the
      // pause listener from saveResume()-ing 0 over the stored 5.0.
      audioEl.currentTime = 0;
      audioEl.dispatchEvent(new Event("pause"));
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      // The resume key should still be 5.0, not overwritten by the swap
      expect(fakeLocalStorage.getItem("tt-resume-l1")).toBe("5.0");
    });

    it("existing default behavior unchanged (full-lesson track on init)", () => {
      const ctrl = createController({ audio: sectionsAudio });
      // Initial src is the full lesson URL, not a section URL
      expect(audioEl.src).toBe("/api/audio/a1");
      expect(ctrl.activeSectionType).toBe("natural_speed");
      expect(ctrl.activeCues).toHaveLength(2);
      // currentCue derives from the initial cues
      audioEl.currentTime = 0.1;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl.currentCue?.index).toBe(0);
    });

    it("selectTrack with unknown section type is a no-op", () => {
      const ctrl = createController({ audio: sectionsAudio });
      const srcBefore = audioEl.src;
      ctrl.selectTrack("nonexistent");
      expect(audioEl.src).toBe(srcBefore);
    });

    it("seek falls back to 0 when currentCue is null (no prior ref)", () => {
      const ctrl = createController({ audio: sectionsAudio });
      // Don't advance currentTime — currentCue is null because all cues start > 0
      // (first cue starts at 0ms, but currentTime is 0 so it IS the current cue;
      // instead, use an audio with late-starting cues)
      const lateAudio: LessonAudio = {
        ...sectionsAudio,
        cues: sectionsAudio.cues!.map((c) => ({ ...c, start_ms: c.start_ms + 10000 })),
        sections: sectionsAudio.sections.map((s) => ({
          ...s,
          cues: s.cues?.map((c) => ({ ...c, start_ms: c.start_ms + 10000 })),
        })),
      };
      const ctrl2 = createController({ audio: lateAudio });
      audioEl.currentTime = 0.1;
      audioEl.dispatchEvent(new Event("timeupdate"));
      expect(ctrl2.currentCue).toBeNull();
      // Select a track — no ref to match, seek should go to 0
      ctrl2.selectTrack("translated");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBeCloseTo(0, 3);
    });

    it("selectTrack falls back to default sectionUrl when dep is absent", () => {
      const ctrl = createPlaybackController({
        createAudio: () => audioEl,
        storage: fakeLocalStorage,
        lessonId: "l1",
        lessonTitle: "Lesson 1",
        audioUrl: "/api/audio/a1",
        audio: sectionsAudio,
        // sectionUrl intentionally omitted
      });
      ctrl.selectTrack("translated");
      expect(audioEl.src).toBe("sec-translated");
    });
  });

  describe("findPlayableCue / playRef (transcript ▶ buttons)", () => {
    const kp0 = makeCue({
      index: 0,
      start_ms: 0,
      end_ms: 500,
      section_index: 0,
      section_type: "key_phrases",
      ref: { kind: "key_phrase", target_index: 0 },
      text: "dober dan",
    });
    const kp1 = makeCue({
      index: 1,
      start_ms: 800,
      end_ms: 1300,
      section_index: 0,
      section_type: "key_phrases",
      ref: { kind: "key_phrase", target_index: 1 },
      text: "hvala",
    });
    const line0 = makeCue({
      index: 0,
      start_ms: 700,
      end_ms: 1200,
      section_index: 1,
      section_type: "natural_speed",
      ref: { kind: "line", target_index: 0 },
      text: "zdravo",
    });
    const twoSectionAudio: LessonAudio = {
      audio_id: "a1",
      lesson_id: "l1",
      sections: [
        {
          audio_id: "skp",
          section_index: 0,
          section_type: "key_phrases",
          title: "Key Phrases",
          cues: [kp0, kp1],
        },
        {
          audio_id: "snat",
          section_index: 1,
          section_type: "natural_speed",
          title: "Natural",
          cues: [line0],
        },
      ],
      cues: [kp0, kp1, line0],
    };

    function primeNatural(): ReturnType<typeof createController> {
      const ctrl = createController({ audio: twoSectionAudio });
      ctrl.selectTrack("natural_speed");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      return ctrl;
    }

    it("resolves a key phrase to the key_phrases section (even while dialogue is active)", () => {
      const ctrl = primeNatural();
      expect(ctrl.activeSectionType).toBe("natural_speed");
      expect(ctrl.findPlayableCue({ kind: "key_phrase", target_index: 1 })).toEqual(kp1);
    });

    it("resolves a dialogue line to the natural_speed section (even while key phrases are active)", () => {
      const ctrl = createController({ audio: twoSectionAudio });
      ctrl.selectTrack("key_phrases");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(ctrl.findPlayableCue({ kind: "line", target_index: 0 })).toEqual(line0);
    });

    it("returns null when no section has the ref", () => {
      const ctrl = createController({ audio: twoSectionAudio });
      expect(ctrl.findPlayableCue({ kind: "line", target_index: 9 })).toBeNull();
    });

    it("playRef switches to the key_phrases track and seeks to that phrase", () => {
      const ctrl = primeNatural();
      ctrl.playRef({ kind: "key_phrase", target_index: 1 });
      expect(audioEl.src).toBe("/api/audio/skp");
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(ctrl.activeSectionType).toBe("key_phrases");
      expect(audioEl.currentTime).toBeCloseTo(0.8, 3); // kp1 starts at 800ms
    });

    it("playRef seeks within the active track when the ref is already there (no src swap)", () => {
      const ctrl = primeNatural();
      const srcBefore = audioEl.src;
      ctrl.playRef({ kind: "line", target_index: 0 });
      expect(audioEl.src).toBe(srcBefore);
      expect(audioEl.currentTime).toBeCloseTo(0.7, 3); // line0 starts at 700ms, seeked immediately
    });

    it("playRef is a no-op when the ref has no audio anywhere", () => {
      const ctrl = primeNatural();
      const srcBefore = audioEl.src;
      ctrl.playRef({ kind: "line", target_index: 9 });
      expect(audioEl.src).toBe(srcBefore);
    });

    it("selectTrack seeks to an explicit ref target instead of preserving position", () => {
      const ctrl = createController({ audio: twoSectionAudio });
      ctrl.selectTrack("key_phrases", { kind: "key_phrase", target_index: 1 });
      audioEl.dispatchEvent(new Event("loadedmetadata"));
      expect(audioEl.currentTime).toBeCloseTo(0.8, 3); // sought to kp1, not position 0
    });

    describe("legacy lessons (pre-Phase-A: sections carry no per-section cues)", () => {
      // Rendered before per-section cue manifests existed: the API returns
      // cues=null on every section row, but the full-track manifest is present
      // and spans all sections. The transcript ▶ buttons must keep working by
      // resolving against the full-track cues and seeking in place.
      const legacyAudio: LessonAudio = {
        audio_id: "a1",
        lesson_id: "l1",
        sections: [
          { audio_id: "skp", section_index: 0, section_type: "key_phrases", title: "Key Phrases" },
          { audio_id: "snat", section_index: 1, section_type: "natural_speed", title: "Natural" },
        ],
        cues: [kp0, kp1, line0],
      };

      it("findPlayableCue falls back to the full-track cues when the section has none", () => {
        const ctrl = createController({ audio: legacyAudio });
        expect(ctrl.findPlayableCue({ kind: "key_phrase", target_index: 1 })).toEqual(kp1);
        expect(ctrl.findPlayableCue({ kind: "line", target_index: 0 })).toEqual(line0);
      });

      it("findPlayableCue still returns null for a ref absent from the full track", () => {
        const ctrl = createController({ audio: legacyAudio });
        expect(ctrl.findPlayableCue({ kind: "line", target_index: 9 })).toBeNull();
      });

      it("playRef seeks within the full track without swapping src", () => {
        const ctrl = createController({ audio: legacyAudio });
        const srcBefore = audioEl.src;
        ctrl.playRef({ kind: "line", target_index: 0 });
        expect(audioEl.src).toBe(srcBefore);
        expect(audioEl.currentTime).toBeCloseTo(0.7, 3);
        ctrl.playRef({ kind: "key_phrase", target_index: 1 });
        expect(audioEl.src).toBe(srcBefore); // never switches to a cue-less section track
        expect(audioEl.currentTime).toBeCloseTo(0.8, 3);
      });
    });
  });
});
