import { untrack } from "svelte";
import type { Cue, LessonAudio } from "$lib/api";

export interface PlaybackController {
  readonly currentCue: Cue | null;
  readonly currentSectionIndex: number | null;
  readonly currentSectionTitle: string;
  readonly playing: boolean;
  readonly currentTime: number;
  readonly duration: number;
  readonly playbackRate: number;
  readonly sentenceSkip: boolean;

  play(): void;
  pause(): void;
  togglePlay(): void;
  seekBy(delta: number): void;
  seekTo(time: number): void;
  seekToCue(cue: Cue): void;
  nextSection(): void;
  prevSection(): void;
  nextCue(): void;
  prevCue(): void;
  repeatCue(): void;
  setRate(rate: number): void;
  setSentenceSkip(v: boolean): void;
  destroy(): void;
}

interface Deps {
  createAudio?: () => HTMLAudioElement;
  mediaSession?: MediaSession;
  storage?: Storage;
  lessonId: string;
  lessonTitle?: string;
  audioUrl: string;
  audio: LessonAudio;
}

function getRefGroupKey(cue: Cue): string {
  if (!cue.ref) return `raw-${cue.index}`;
  return `${cue.ref.kind}-${cue.ref.target_index}`;
}

function buildRefGroups(cues: Cue[]): number[][] {
  if (cues.length === 0) return [];
  const groups: number[][] = [];
  let current: number[] = [cues[0].index];
  let currentKey = getRefGroupKey(cues[0]);

  for (let i = 1; i < cues.length; i++) {
    const key = getRefGroupKey(cues[i]);
    if (key === currentKey) {
      current.push(cues[i].index);
    } else {
      groups.push(current);
      current = [cues[i].index];
      currentKey = key;
    }
  }
  groups.push(current);
  return groups;
}

function findCueByIndex(cues: Cue[], index: number): Cue | undefined {
  return cues.find((c) => c.index === index);
}

function findGroupStart(
  groups: number[][],
  currentGroupIdx: number,
  direction: "next" | "prev",
): number | null {
  if (direction === "next" && currentGroupIdx < groups.length - 1) {
    const nextGroup = groups[currentGroupIdx + 1];
    return nextGroup[0];
  }
  if (direction === "prev" && currentGroupIdx > 0) {
    const prevGroup = groups[currentGroupIdx - 1];
    return prevGroup[0];
  }
  return null;
}

export function createPlaybackController(deps: Deps): PlaybackController {
  const audioEl = (deps.createAudio?.() ?? new Audio()) as HTMLAudioElement;
  const storage = deps.storage ?? localStorage;
  const mediaSession =
    deps.mediaSession ?? (typeof navigator !== "undefined" ? navigator.mediaSession : undefined);
  const lessonId = deps.lessonId;
  const cues: Cue[] | null = deps.audio.cues ?? null;
  const refGroups = cues ? buildRefGroups(cues) : [];
  const sectionTitles = deps.audio.sections.reduce(
    (acc, s) => {
      acc[s.section_index] = s.title;
      return acc;
    },
    {} as Record<number, string>,
  );

  let currentTime = $state(0);
  let playing = $state(false);
  let duration = $state(audioEl.duration || 0);
  let rate = $state(1);
  let sentenceSkip = $state(false);
  // Browsers QUEUE the pause event, so destroy()'s own pause() fires the
  // listener AFTER src="" has reset currentTime to 0 — without this flag the
  // listener would overwrite the resume position destroy just saved.
  let destroyed = false;

  let currentCue = $derived.by(() => {
    if (!cues || cues.length === 0) return null;
    const tMs = currentTime * 1000;
    let best: Cue | null = null;
    for (const c of cues) {
      if (c.start_ms <= tMs) best = c;
      else break;
    }
    return best;
  });

  let currentSectionIndex = $derived(currentCue?.section_index ?? null);

  let currentSectionTitle = $derived.by(() => {
    if (currentSectionIndex === null) return "";
    return sectionTitles[currentSectionIndex] ?? "";
  });

  function updatePositionState() {
    if (mediaSession?.setPositionState) {
      try {
        mediaSession.setPositionState({
          duration: audioEl.duration || duration,
          playbackRate: audioEl.playbackRate || rate,
          position: audioEl.currentTime || currentTime,
        });
      } catch {
        // setPositionState can throw if called before metadata is set
      }
    }
  }

  function doSeek(time: number) {
    // Pre-metadata duration is NaN/0 — don't clamp every seek to 0 then.
    const max =
      Number.isFinite(audioEl.duration) && audioEl.duration > 0 ? audioEl.duration : Infinity;
    audioEl.currentTime = Math.max(0, Math.min(time, max));
    updatePositionState();
  }

  // Per-lesson resume: read the saved position now, but APPLY it only when
  // loadedmetadata delivers the real duration. Restoring at init raced the
  // scrubber (max still 1, value clamped) so the thumb showed the resume
  // position as the track start and back-scrubbing clamped to it.
  const savedResume = storage.getItem(`tt-resume-${lessonId}`);
  let pendingResume: number | null = savedResume ? parseFloat(savedResume) : null;
  if (pendingResume !== null && (isNaN(pendingResume) || pendingResume <= 0)) {
    pendingResume = null;
  }

  // Audio event listeners
  audioEl.addEventListener("timeupdate", () => {
    currentTime = audioEl.currentTime;
  });
  audioEl.addEventListener("loadedmetadata", () => {
    duration = audioEl.duration;
    if (pendingResume !== null) {
      if (pendingResume < audioEl.duration) {
        audioEl.currentTime = pendingResume;
        currentTime = pendingResume;
      }
      pendingResume = null;
    }
    updatePositionState();
  });
  audioEl.addEventListener("play", () => {
    playing = true;
    if (mediaSession) mediaSession.playbackState = "playing";
  });
  audioEl.addEventListener("pause", () => {
    if (destroyed) return;
    playing = false;
    saveResume();
    if (mediaSession) mediaSession.playbackState = "paused";
    updatePositionState();
  });
  audioEl.addEventListener("ratechange", () => {
    rate = audioEl.playbackRate;
    updatePositionState();
  });
  audioEl.addEventListener("ended", () => {
    playing = false;
    if (mediaSession) mediaSession.playbackState = "none";
    updatePositionState();
  });

  audioEl.preload = "metadata";
  audioEl.src = deps.audioUrl;

  // MediaSession wiring
  if (mediaSession) {
    const ms = mediaSession;
    try {
      ms.metadata = new MediaMetadata({
        title: deps.lessonTitle || "",
        // Intentional initial-value read (untrack): this seeds the metadata once
        // at init; the timeupdate listener below keeps the artist fresh.
        artist: untrack(() => currentSectionTitle) || "",
      });
    } catch {
      // MediaMetadata not available (jsdom, some browsers)
    }

    // Refresh metadata when section changes
    audioEl.addEventListener("timeupdate", () => {
      const newTitle = currentSectionTitle;
      if (ms.metadata && ms.metadata.artist !== newTitle) {
        try {
          ms.metadata = new MediaMetadata({
            title: deps.lessonTitle,
            artist: newTitle,
          });
        } catch {
          // MediaMetadata not available
        }
      }
    });

    ms.setActionHandler("play", () => {
      audioEl.play();
    });
    ms.setActionHandler("pause", () => {
      audioEl.pause();
    });
    ms.setActionHandler("seekbackward", () => {
      doSeek(audioEl.currentTime - 10);
    });
    ms.setActionHandler("seekforward", () => {
      doSeek(audioEl.currentTime + 10);
    });
    ms.setActionHandler("previoustrack", () => {
      if (sentenceSkip) {
        prevCueAction();
      } else {
        prevSection();
      }
    });
    ms.setActionHandler("nexttrack", () => {
      if (sentenceSkip) {
        nextCueAction();
      } else {
        nextSection();
      }
    });
    ms.setActionHandler("seekto", (details) => {
      if (details.seekTime != null) {
        doSeek(details.seekTime);
      }
    });

    updatePositionState();
  }

  const RESUME_KEY = `tt-resume-${lessonId}`;

  function saveResume() {
    // While a restore is still pending (metadata never arrived), position 0
    // is meaningless — writing it would clobber the real saved spot.
    if (pendingResume !== null) return;
    storage.setItem(RESUME_KEY, String(audioEl.currentTime));
  }

  // --- Section navigation ---

  function nextSection(): void {
    if (!cues) return;
    if (currentSectionIndex === null) {
      const firstCue = cues.find((c) => c.section_index != null);
      if (firstCue) {
        doSeek(firstCue.start_ms / 1000);
      }
      return;
    }
    const firstCueAfter = cues.find(
      (c) => c.section_index != null && c.section_index > currentSectionIndex!,
    );
    if (firstCueAfter) {
      doSeek(firstCueAfter.start_ms / 1000);
    }
  }

  function prevSection(): void {
    if (!cues) return;
    if (currentSectionIndex === null) {
      doSeek(0);
      return;
    }
    if (currentSectionIndex <= 0) return;
    const targetSection = currentSectionIndex - 1;
    const firstCueInTarget = cues.find((c) => c.section_index === targetSection);
    if (firstCueInTarget) {
      doSeek(firstCueInTarget.start_ms / 1000);
    }
  }

  // --- Ref-group cue stepping ---

  function findCurrentGroupIdx(): number {
    const cueIndex = currentCue?.index ?? -1;
    for (let i = 0; i < refGroups.length; i++) {
      if (refGroups[i].includes(cueIndex)) return i;
    }
    return -1;
  }

  function nextCueAction(): void {
    const groupIdx = findCurrentGroupIdx();
    if (groupIdx < 0) return;
    const nextCueIndex = findGroupStart(refGroups, groupIdx, "next");
    if (nextCueIndex == null) return;
    const targetCue = findCueByIndex(cues!, nextCueIndex);
    if (targetCue) {
      doSeek(targetCue.start_ms / 1000);
    }
  }

  function prevCueAction(): void {
    const groupIdx = findCurrentGroupIdx();
    if (groupIdx < 0) return;
    const prevCueIndex = findGroupStart(refGroups, groupIdx, "prev");
    if (prevCueIndex == null) return;
    const targetCue = findCueByIndex(cues!, prevCueIndex);
    if (targetCue) {
      doSeek(targetCue.start_ms / 1000);
    }
  }

  // --- Repeat ---

  function repeatCue(): void {
    const cue = currentCue;
    if (!cue) return;
    doSeek(cue.start_ms / 1000);
  }

  // --- Public API ---

  return {
    get currentCue() {
      return currentCue;
    },
    get currentSectionIndex() {
      return currentSectionIndex;
    },
    get currentSectionTitle() {
      return currentSectionTitle;
    },
    get playing() {
      return playing;
    },
    get currentTime() {
      return currentTime;
    },
    get duration() {
      return duration;
    },
    get playbackRate() {
      return rate;
    },
    get sentenceSkip() {
      return sentenceSkip;
    },

    play() {
      audioEl.play();
    },
    pause() {
      audioEl.pause();
    },
    togglePlay() {
      if (playing) {
        audioEl.pause();
      } else {
        audioEl.play();
      }
    },
    seekBy(delta: number) {
      doSeek(audioEl.currentTime + delta);
    },
    seekTo(time: number) {
      doSeek(time);
    },
    seekToCue(cue: Cue) {
      doSeek(cue.start_ms / 1000);
    },
    nextSection,
    prevSection,
    nextCue: nextCueAction,
    prevCue: prevCueAction,
    repeatCue,
    setRate(newRate: number) {
      audioEl.playbackRate = newRate;
      rate = newRate;
      updatePositionState();
    },
    setSentenceSkip(v: boolean) {
      sentenceSkip = v;
    },
    destroy() {
      destroyed = true;
      audioEl.pause();
      saveResume();
      audioEl.src = "";
      if (mediaSession) {
        mediaSession.setActionHandler("play", null);
        mediaSession.setActionHandler("pause", null);
        mediaSession.setActionHandler("seekbackward", null);
        mediaSession.setActionHandler("seekforward", null);
        mediaSession.setActionHandler("previoustrack", null);
        mediaSession.setActionHandler("nexttrack", null);
        mediaSession.setActionHandler("seekto", null);
        mediaSession.metadata = null;
      }
    },
  };
}
