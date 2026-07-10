import { untrack } from "svelte";
import type { Cue, CueRef, LessonAudio, SectionAudio } from "$lib/api";

export interface PlaybackController {
  readonly currentCue: Cue | null;
  readonly currentSectionIndex: number | null;
  readonly currentSectionTitle: string;
  readonly playing: boolean;
  readonly currentTime: number;
  readonly duration: number;
  readonly playbackRate: number;
  readonly sentenceSkip: boolean;
  readonly activeSectionType: string | null;
  readonly activeCues: Cue[] | null;

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
  selectTrack(sectionType: string, seekRef?: CueRef | null): void;
  findPlayableCue(ref: CueRef): Cue | null;
  playRef(ref: CueRef): void;
  setRate(rate: number): void;
  setEnunciationRate(rate: number): void;
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
  sectionUrl?: (audioId: string) => string;
}

function getRefGroupKey(cue: Cue): string {
  // Refs without a target (narration) don't identify a shared entity, so each
  // such cue is its own group — otherwise adjacent-but-distinct narration cues
  // (lesson title + section title) would merge into one sentence-skip stop.
  if (!cue.ref || cue.ref.target_index == null) return `raw-${cue.index}`;
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
  const sectionUrlFn = deps.sectionUrl ?? ((id: string) => id);
  const audioSections = deps.audio.sections;

  // Seed the reactive track state from a plain const (not the $state itself) so
  // the initializers don't trip Svelte's state_referenced_locally warning.
  const initialCues: Cue[] | null = deps.audio.cues ?? null;
  let activeCues: Cue[] | null = $state(initialCues);
  let refGroups: number[][] = $state(initialCues ? buildRefGroups(initialCues) : []);
  let sectionTitles: Record<number, string> = $state(
    audioSections.reduce(
      (acc, s) => {
        acc[s.section_index] = s.title;
        return acc;
      },
      {} as Record<number, string>,
    ),
  );
  let activeSectionType: string | null = $state(
    initialCues && initialCues.length > 0 && initialCues[0].section_type
      ? initialCues[0].section_type
      : null,
  );

  let currentTime = $state(0);
  let playing = $state(false);
  let duration = $state(audioEl.duration || 0);
  let rate = $state(1);
  let enunciationRate = 1;
  let sentenceSkip = $state(false);
  // Browsers QUEUE the pause event, so destroy()'s own pause() fires the
  // listener AFTER src="" has reset currentTime to 0 — without this flag the
  // listener would overwrite the resume position destroy just saved.
  let destroyed = false;
  // Guard against pause/emptied events fired during a src swap in selectTrack.
  let swapping = false;
  let pendingSeek: number | null = null;
  let wasPlayingBeforeSwap = false;

  let currentCue = $derived.by(() => {
    if (!activeCues || activeCues.length === 0) return null;
    const tMs = currentTime * 1000;
    let best: Cue | null = null;
    for (const c of activeCues) {
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

  function applyEnunciationRate() {
    if (enunciationRate === 1) return;
    if (!activeCues || activeCues.length === 0) return;
    const tMs = currentTime * 1000;
    let best: Cue | null = null;
    for (const c of activeCues) {
      if (c.start_ms <= tMs) best = c;
      else break;
    }
    if (!best) return;
    const targetRate = best.language_code === "en" ? 1 : enunciationRate;
    if (audioEl.playbackRate !== targetRate) {
      audioEl.playbackRate = targetRate;
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
    applyEnunciationRate();
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
    if (pendingSeek !== null) {
      doSeek(pendingSeek);
      pendingSeek = null;
      swapping = false;
      if (wasPlayingBeforeSwap) {
        audioEl.play();
        wasPlayingBeforeSwap = false;
      }
    }
    updatePositionState();
  });
  audioEl.addEventListener("play", () => {
    playing = true;
    if (mediaSession) mediaSession.playbackState = "playing";
  });
  audioEl.addEventListener("pause", () => {
    if (destroyed || swapping) return;
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
    if (!activeCues) return;
    if (currentSectionIndex === null) {
      const firstCue = activeCues.find((c) => c.section_index != null);
      if (firstCue) {
        doSeek(firstCue.start_ms / 1000);
      }
      return;
    }
    const firstCueAfter = activeCues.find(
      (c) => c.section_index != null && c.section_index > currentSectionIndex!,
    );
    if (firstCueAfter) {
      doSeek(firstCueAfter.start_ms / 1000);
    }
  }

  function prevSection(): void {
    if (!activeCues) return;
    if (currentSectionIndex === null) {
      doSeek(0);
      return;
    }
    if (currentSectionIndex <= 0) return;
    const targetSection = currentSectionIndex - 1;
    const firstCueInTarget = activeCues.find((c) => c.section_index === targetSection);
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
    const targetCue = findCueByIndex(activeCues!, nextCueIndex);
    if (targetCue) {
      doSeek(targetCue.start_ms / 1000);
    }
  }

  function prevCueAction(): void {
    const groupIdx = findCurrentGroupIdx();
    if (groupIdx < 0) return;
    const prevCueIndex = findGroupStart(refGroups, groupIdx, "prev");
    if (prevCueIndex == null) return;
    const targetCue = findCueByIndex(activeCues!, prevCueIndex);
    if (targetCue) {
      doSeek(targetCue.start_ms / 1000);
    }
  }

  // --- Track selection (B2) ---

  function selectTrack(sectionType: string, seekRef: CueRef | null = null): void {
    const section = audioSections.find((s) => s.section_type === sectionType);
    if (!section) return;

    // Where to land in the new track: an explicit seekRef (a transcript ▶ tap)
    // wins; otherwise preserve the current line's position across the swap.
    const prevRef = seekRef ?? currentCue?.ref ?? null;

    // Guard: prevent the browser's pause/emptied events from clobbering resume.
    swapping = true;
    wasPlayingBeforeSwap = !audioEl.paused;

    // Swap the audio source.
    audioEl.src = sectionUrlFn(section.audio_id);

    // Swap the active cue list + rebuild derived state.
    const newCues = section.cues ?? null;
    activeCues = newCues;
    refGroups = newCues ? buildRefGroups(newCues) : [];
    activeSectionType = sectionType;

    // Rebuild sectionTitles so section-title narration cues stay correct.
    sectionTitles = audioSections.reduce(
      (acc, s) => {
        acc[s.section_index] = s.title;
        return acc;
      },
      {} as Record<number, string>,
    );

    // Find the matching cue by ref.kind + target_index for position preservation.
    if (prevRef && newCues && newCues.length > 0) {
      const match = newCues.find(
        (c) => c.ref && c.ref.kind === prevRef.kind && c.ref.target_index === prevRef.target_index,
      );
      if (match) {
        pendingSeek = match.start_ms / 1000;
      } else {
        pendingSeek = 0;
      }
    } else {
      pendingSeek = 0;
    }
    // The seek is applied inside the loadedmetadata handler.
  }

  // The canonical section a transcript ref plays from: key phrases from the
  // key_phrases track, dialogue lines from natural_speed. Stable regardless of
  // the phase/variant currently selected, so the ▶ buttons always show.
  function canonicalSection(ref: CueRef): string {
    return ref.kind === "key_phrase" ? "key_phrases" : "natural_speed";
  }

  function findCueInSection(sectionType: string, ref: CueRef): Cue | null {
    const section = audioSections.find((s) => s.section_type === sectionType);
    return (
      section?.cues?.find(
        (c) => c.ref && c.ref.kind === ref.kind && c.ref.target_index === ref.target_index,
      ) ?? null
    );
  }

  // For button visibility: is there audio for this transcript ref at all?
  function findPlayableCue(ref: CueRef): Cue | null {
    const section = audioSections.find((s) => s.section_type === canonicalSection(ref));
    if (!section?.cues) {
      // Legacy lesson (pre per-section cues): the section rows carry no
      // manifests, but the full-track manifest spans every section — resolve
      // ▶ against it. playRef then seeks in place via its activeCues branch,
      // so the player never switches to a cue-less section track.
      return (
        initialCues?.find(
          (c) => c.ref && c.ref.kind === ref.kind && c.ref.target_index === ref.target_index,
        ) ?? null
      );
    }
    return findCueInSection(canonicalSection(ref), ref);
  }

  // Play a transcript ref (a per-line ▶). If the ref lives in the current track
  // (e.g. tapping a dialogue line while a dialogue variant is active), just seek
  // there — no track change, preserving the chosen variant. Otherwise switch to
  // the ref's canonical section and seek to it.
  function playRef(ref: CueRef): void {
    const here =
      activeCues?.find(
        (c) => c.ref && c.ref.kind === ref.kind && c.ref.target_index === ref.target_index,
      ) ?? null;
    if (here) {
      doSeek(here.start_ms / 1000);
      audioEl.play();
      return;
    }
    if (findCueInSection(canonicalSection(ref), ref)) {
      selectTrack(canonicalSection(ref), ref);
      wasPlayingBeforeSwap = true;
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
    get activeSectionType() {
      return activeSectionType;
    },
    get activeCues() {
      return activeCues;
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
    selectTrack,
    findPlayableCue,
    playRef,
    setRate(newRate: number) {
      audioEl.playbackRate = newRate;
      rate = newRate;
      updatePositionState();
    },
    setEnunciationRate(newRate: number) {
      enunciationRate = newRate;
      applyEnunciationRate();
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
