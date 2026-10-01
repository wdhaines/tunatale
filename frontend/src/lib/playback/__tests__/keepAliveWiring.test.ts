import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest";
import { createPlaybackController } from "../playbackController.svelte";
import type { KeepAlive } from "../keepAlive";
import type { Cue, LessonAudio } from "$lib/api";
import { readMediaTrace, setMediaTraceEnabled, clearMediaTrace } from "$lib/mediaTrace";

// Locked oracle for tunatale-bibo — hands-free with the screen LOCKED advanced
// to the next section, played a tiny clip and stopped.
//
// The on-device trace (2026-10-01, three for three):
//
//   el:pause  section=key_phrases   t=419.5 swapping=0
//   el:ended  section=key_phrases   t=419.5
//   el:play   section=natural_speed t=0.0
//   el:pause  section=natural_speed t=0.1 swapping=0     <- ~1 s later, uncalled
//
// No call:/action: line precedes the second pause and play:rejected is absent,
// so the browser paused a track that had started. A new `src` destroys the
// element's player; while it was the page's only player the media session went
// inactive across the swap. The keep-alive is a second, silent player that must
// therefore be playing across EVERY swap and stopped at every real stop — which
// is all jsdom can check. Whether Chrome then keeps playing is a question only
// the phone answers.

function makeCue(overrides: Partial<Cue> & { index: number }): Cue {
  return {
    start_ms: 0,
    end_ms: 1000,
    section_index: 0,
    section_type: "natural_speed",
    phrase_index: 0,
    role: "female-1",
    language_code: "no",
    text: "God dag",
    ref: { kind: "line", target_index: 0 },
    ...overrides,
  };
}

function makeFakeAudio(): HTMLAudioElement {
  const listeners = new Map<string, Set<EventListener>>();
  return {
    currentTime: 0,
    duration: 100,
    paused: true,
    ended: false,
    playbackRate: 1,
    src: "",
    volume: 1,
    addEventListener: vi.fn(
      (type: string, handler: EventListener, opts?: AddEventListenerOptions) => {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type)!.add(handler);
        opts?.signal?.addEventListener("abort", () => listeners.get(type)?.delete(handler));
      },
    ),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn((event: Event) => {
      for (const h of listeners.get(event.type) ?? []) h(event);
      return true;
    }),
    play: vi.fn(() => Promise.resolve()),
    pause: vi.fn(() => {}),
    load: vi.fn(),
  } as unknown as HTMLAudioElement;
}

function makeStorage(): Storage {
  const store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
  } as unknown as Storage;
}

function section(index: number, type: string) {
  return {
    audio_id: `sec-${type}`,
    section_index: index,
    section_type: type,
    title: type,
    cues: [makeCue({ index: 0, section_index: index, section_type: type })],
  };
}

// Track mode: every section carries its own cues, as every current lesson does.
const lessonAudio: LessonAudio = {
  audio_id: "a1",
  lesson_id: "l1",
  sections: [section(0, "natural_speed"), section(1, "slow_speed"), section(2, "translated")],
  cues: [makeCue({ index: 0 })],
};

describe("keep-alive across track boundaries (tunatale-bibo)", () => {
  let audioEl: HTMLAudioElement;
  let keepAlive: { [K in keyof KeepAlive]: Mock<() => void> };
  let onHandsFreeEnd: Mock<() => void>;

  beforeEach(() => {
    audioEl = makeFakeAudio();
    keepAlive = {
      start: vi.fn<() => void>(),
      stop: vi.fn<() => void>(),
      destroy: vi.fn<() => void>(),
    };
    onHandsFreeEnd = vi.fn<() => void>();
  });

  afterEach(() => {
    setMediaTraceEnabled(false);
    vi.unstubAllGlobals();
  });

  function mk() {
    return createPlaybackController({
      createAudio: () => audioEl,
      keepAlive,
      mediaSession: undefined,
      storage: makeStorage(),
      lessonId: "l1",
      lessonTitle: "Lesson 1",
      audioUrl: null,
      audio: lessonAudio,
      sectionUrl: (id: string) => `/api/audio/${id}`,
      onHandsFreeEnd,
    });
  }

  // What the browser does when a track plays to its end: pause, then ended,
  // with `ended` already true while the pause handler runs.
  function playToEnd() {
    Object.assign(audioEl, { paused: true, ended: true });
    audioEl.dispatchEvent(new Event("pause"));
    audioEl.dispatchEvent(new Event("ended"));
  }

  function startPlaying() {
    Object.assign(audioEl, { paused: false, ended: false });
    audioEl.dispatchEvent(new Event("play"));
  }

  it("is not started by building a controller", () => {
    mk();
    expect(keepAlive.start).not.toHaveBeenCalled();
  });

  it("starts when the lesson audio starts playing", () => {
    mk();
    startPlaying();
    expect(keepAlive.start).toHaveBeenCalledTimes(1);
    expect(keepAlive.stop).not.toHaveBeenCalled();
  });

  it("stops when the user pauses", () => {
    mk();
    startPlaying();
    Object.assign(audioEl, { paused: true });
    audioEl.dispatchEvent(new Event("pause"));
    expect(keepAlive.stop).toHaveBeenCalledTimes(1);
  });

  it("hands-free: stays running from the end of one pass into the next", () => {
    const ctrl = mk();
    ctrl.setHandsFree(true);
    startPlaying();

    playToEnd();

    // The advance happened (a new src, a play), and at no point across it was
    // the keep-alive stopped — that gap is the bug.
    expect(ctrl.activeSectionType).toBe("slow_speed");
    expect(audioEl.src).toBe("/api/audio/sec-slow_speed");
    expect(audioEl.play).toHaveBeenCalledTimes(1);
    expect(keepAlive.stop).not.toHaveBeenCalled();
  });

  it("hands-free OFF: a track that ends stops it", () => {
    const ctrl = mk();
    startPlaying();
    playToEnd();
    expect(ctrl.activeSectionType).toBe("natural_speed");
    expect(keepAlive.stop).toHaveBeenCalledTimes(1);
  });

  it("hands-free: the end of the LAST pass stops it before the page is told", () => {
    const ctrl = mk();
    ctrl.setHandsFree(true);
    ctrl.selectTrack("translated", null, true);
    audioEl.dispatchEvent(new Event("loadedmetadata"));
    startPlaying();
    onHandsFreeEnd.mockImplementation(() => {
      expect(keepAlive.stop).toHaveBeenCalledTimes(1);
    });

    playToEnd();

    expect(onHandsFreeEnd).toHaveBeenCalledTimes(1);
    expect(keepAlive.stop).toHaveBeenCalledTimes(1);
  });

  it("a pause event during a track swap does not stop it", () => {
    const ctrl = mk();
    startPlaying();
    ctrl.selectTrack("slow_speed");
    Object.assign(audioEl, { paused: true });
    audioEl.dispatchEvent(new Event("pause"));
    expect(keepAlive.stop).not.toHaveBeenCalled();
  });

  it("a refused play() stops it: nothing is playing, so nothing should claim to be", async () => {
    const ctrl = mk();
    vi.mocked(audioEl.play).mockReturnValueOnce(
      Promise.reject(new DOMException("blocked", "NotAllowedError")),
    );
    ctrl.play();
    await Promise.resolve();
    await Promise.resolve();
    expect(keepAlive.stop).toHaveBeenCalledTimes(1);
  });

  it("a play() aborted by a track swap does not stop it: the swap resumes playback", async () => {
    const ctrl = mk();
    startPlaying();
    vi.mocked(audioEl.play).mockReturnValueOnce(
      Promise.reject(new DOMException("interrupted by a new load request", "AbortError")),
    );
    ctrl.play();
    ctrl.selectTrack("slow_speed");
    await Promise.resolve();
    await Promise.resolve();
    expect(keepAlive.stop).not.toHaveBeenCalled();
  });

  it("destroy tears it down", () => {
    const ctrl = mk();
    startPlaying();
    ctrl.destroy();
    expect(keepAlive.destroy).toHaveBeenCalledTimes(1);
  });

  it("with none supplied, the controller builds its own and its events reach the trace", () => {
    clearMediaTrace();
    setMediaTraceEnabled(true);
    // Fail the build on purpose: the cheapest event to provoke, and the one
    // that says the default really is the real keep-alive.
    vi.stubGlobal("URL", {
      createObjectURL: () => {
        throw new Error("no blob urls");
      },
    });
    createPlaybackController({
      createAudio: () => audioEl,
      mediaSession: undefined,
      storage: makeStorage(),
      lessonId: "l1",
      audioUrl: null,
      audio: lessonAudio,
    });

    startPlaying();

    const line = readMediaTrace().find((l) => l.includes("ka:unavailable"));
    expect(line).toBeTruthy();
    expect(line).toContain("err=Error: no blob urls");
    expect(line).toContain("section=natural_speed");
  });
});
