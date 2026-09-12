import { untrack } from "svelte";
import type { Cue, CueRef, LessonAudio } from "$lib/api";
import { mediaTrace, mediaTraceSource } from "$lib/mediaTrace";

// The hands-free pass sequence, in order of playback. One const so a later
// change is a single edit. Matches LessonPlayer.svelte's pill model
// (resolveSectionType): key_phrases -> natural_speed -> slow_speed -> translated.
//
// key_phrases leads because a hands-free listen that STARTS in the Key Phrases
// phase should hand off to the dialogue on its own rather than stopping the
// stereo dead at the last phrase. It only ever leads for a listener already on
// that track: the advance is indexed from whatever is playing, so turning
// hands-free on mid-dialogue still starts at that pass and never rewinds into
// the key phrases.
export const HANDS_FREE_SEQUENCE = [
  "key_phrases",
  "natural_speed",
  "slow_speed",
  "translated",
] as const;

export interface PlaybackController {
  readonly currentCue: Cue | null;
  readonly currentSectionIndex: number | null;
  readonly currentSectionTitle: string;
  readonly playing: boolean;
  readonly currentTime: number;
  readonly duration: number;
  readonly playbackRate: number;
  readonly handsFree: boolean;
  readonly repeatLatched: boolean;
  readonly activeSectionType: string | null;
  readonly activeCues: Cue[] | null;
  // The section a saved resume offset belongs to, while that offset is still
  // waiting for loadedmetadata; null once applied or discarded, or when there
  // is no offset or the saved value predates per-section resume. Not reactive —
  // it is read once, at mount.
  readonly resumeSection: string | null;

  play(): void;
  pause(): void;
  togglePlay(): void;
  seekBy(delta: number): void;
  seekTo(time: number): void;
  seekToCue(cue: Cue): void;
  nextSection(): void;
  prevSection(): void;
  restartSection(): void;
  nextCue(): void;
  prevCue(): void;
  repeatCue(): void;
  toggleRepeatLatch(): void;
  selectTrack(sectionType: string, seekRef?: CueRef | null, fromStart?: boolean): void;
  findPlayableCue(ref: CueRef): Cue | null;
  playRef(ref: CueRef): void;
  setRate(rate: number): void;
  setEnunciationRate(rate: number): void;
  setHandsFree(v: boolean): void;
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
  // Fired when a hands-free run reaches the end of HANDS_FREE_SEQUENCE and has
  // no further pass to play. The controller is built around ONE lesson's audio
  // and deliberately does not learn to load another: what comes next (the next
  // day, the next review session) is the page's question, and only the page can
  // answer it. So this reports the fact and stops.
  onHandsFreeEnd?: () => void;
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
  let handsFree = $state(false);
  // Section type captured when hands-free turned ON, so turning it OFF can
  // restore the user's saved phase/enunciation/English preference. null until
  // first enabled. LessonPlayer's pill-mirror $effect follows
  // ctrl.activeSectionType and calls persistSelection(), so without this
  // restore one use of hands-free would silently rewrite the saved selection
  // to "English After" (translated).
  let handsFreeRestoreSection: string | null = null;
  let repeatLatched = $state(false);
  // The cue captured when the latch engaged — pinned, never re-read from
  // currentCue, so the loop cannot drift onto the next sentence.
  //
  // ⚠️ HONEST LIMIT, measured by sabotage drill 2026-08-28: with the loop check
  // placed BEFORE the reactive currentTime copy (see the timeupdate listener),
  // re-reading currentCue here instead of pinning is behaviourally IDENTICAL —
  // currentCue is derived from the reactive copy, which is still one tick
  // stale at that point, so it resolves to this same cue. No behavioural test
  // can therefore guard the pin on its own, and none does: the suite guards
  // the ORDERING, and it guards the two defects TOGETHER. The pin is
  // defence-in-depth against someone later moving that check. Do not delete it
  // on the grounds that no test fails.
  let latchedCue: Cue | null = null;
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
  const savedResumeRaw = storage.getItem(`tt-resume-${lessonId}`);
  let pendingResume: number | null = null;
  let pendingResumeSection: string | null = null;
  if (savedResumeRaw !== null) {
    try {
      const parsed = JSON.parse(savedResumeRaw);
      if (typeof parsed === "object" && parsed !== null) {
        pendingResumeSection = parsed.section ?? null;
        const pos = Number(parsed.position);
        if (Number.isFinite(pos) && pos > 0) pendingResume = pos;
      } else if (typeof parsed === "number" && Number.isFinite(parsed) && parsed > 0) {
        pendingResume = parsed;
        pendingResumeSection = null;
      }
    } catch {
      // Malformed value — discard
    }
  }
  let lastSavedPosition = pendingResume ?? 0;

  // On-device Media Session trace (mediaTrace.ts). Every interesting event in
  // this controller funnels through here, so the recorded line always carries
  // the same context: hands-free mode, active section, and playhead.
  function trace(event: string, extra = ""): void {
    mediaTrace(
      `${event} hf=${handsFree ? 1 : 0} section=${activeSectionType ?? "-"} t=${audioEl.currentTime.toFixed(1)}${extra ? " " + extra : ""}`,
    );
  }

  // Audio event listeners
  audioEl.addEventListener("timeupdate", () => {
    // The loop check runs BEFORE the playhead is copied into the reactive
    // currentTime: a latch-seek must land in the reactive, or a derived read
    // (currentCue) would resolve from the pre-loop position and, past the
    // pinned cue's end, report the NEXT sentence.
    latchLoopCheck();
    currentTime = audioEl.currentTime;
    applyEnunciationRate();
    if (pendingResume === null && Math.abs(audioEl.currentTime - lastSavedPosition) >= 5) {
      saveResume();
    }
  });
  audioEl.addEventListener("loadedmetadata", () => {
    duration = audioEl.duration;
    if (pendingResume !== null) {
      const applies = pendingResumeSection === null || pendingResumeSection === activeSectionType;
      if (applies && pendingResume < audioEl.duration) {
        audioEl.currentTime = pendingResume;
        currentTime = pendingResume;
        if (pendingSeek !== null) pendingSeek = pendingResume;
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
    trace("el:play");
    playing = true;
    if (mediaSession) mediaSession.playbackState = "playing";
  });
  audioEl.addEventListener("pause", () => {
    trace("el:pause", `swapping=${swapping ? 1 : 0}`);
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
    trace("el:ended");
    // A latched loop over the LAST sentence of a track never sees a timeupdate
    // past the end, so "ended" is its only signal — and seeking alone would
    // leave the element paused at the cue start. The latch would go silent
    // exactly where drilling one line matters most, so resume playback.
    if (latchLoopCheck()) {
      void audioEl.play();
      return;
    }
    // Hands-free: advance to the next pass of the sequence, starting from the
    // beginning of that track. MUST run BEFORE `playing = false` below —
    // selectTrack captures wasPlayingBeforeSwap = playing to decide whether to
    // resume after the swap; if it ran after that assignment the resume would
    // be lost and the next pass would load and sit silent (looking exactly
    // like "hands-free doesn't work").
    if (handsFree && activeSectionType !== null) {
      const idx = (HANDS_FREE_SEQUENCE as readonly string[]).indexOf(activeSectionType);
      // Skip to the next pass this lesson actually HAS. selectTrack no-ops on a
      // missing section, so advancing blindly to idx + 1 would leave the same
      // track selected and then play() it — an ended element restarts, so the
      // pass would repeat forever with no escape but the transport.
      const next =
        idx === -1
          ? undefined
          : HANDS_FREE_SEQUENCE.slice(idx + 1).find((t) =>
              audioSections.some((s) => s.section_type === t),
            );
      if (next !== undefined) {
        selectTrack(next, null, true);
        void audioEl.play();
        return;
      }
      // In the sequence with nothing left to play: the run is COMPLETE, as
      // distinct from a track that merely ended. Reported after the state below
      // is settled, so a handler that navigates cannot observe a half-updated
      // controller. idx === -1 (a section outside the sequence) is not a
      // completion and stays silent.
      if (idx !== -1) {
        playing = false;
        if (mediaSession) mediaSession.playbackState = "none";
        updatePositionState();
        deps.onHandsFreeEnd?.();
        return;
      }
    }
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

    // Each registration is its own try/catch: browsers throw for actions they
    // do not support, and one unsupported action must not abort the ones after
    // it. A refusal is worth a trace line — that fact is exactly the data this
    // log exists to capture.
    try {
      ms.setActionHandler("play", () => {
        trace("action:play");
        audioEl.play();
      });
    } catch {
      trace("refused:play");
    }
    try {
      ms.setActionHandler("pause", () => {
        trace("action:pause");
        audioEl.pause();
      });
    } catch {
      trace("refused:pause");
    }
    try {
      ms.setActionHandler("seekbackward", () => {
        trace("action:seekbackward");
        doSeek(audioEl.currentTime - 10);
      });
    } catch {
      trace("refused:seekbackward");
    }
    try {
      ms.setActionHandler("seekforward", () => {
        trace("action:seekforward");
        doSeek(audioEl.currentTime + 10);
      });
    } catch {
      trace("refused:seekforward");
    }
    try {
      ms.setActionHandler("previoustrack", () => {
        trace("action:previoustrack");
        if (handsFree) {
          prevCueAction();
        } else {
          prevSection();
        }
      });
    } catch {
      trace("refused:previoustrack");
    }
    try {
      ms.setActionHandler("nexttrack", () => {
        trace("action:nexttrack");
        if (handsFree) {
          nextCueAction();
        } else {
          nextSection();
        }
      });
    } catch {
      trace("refused:nexttrack");
    }
    try {
      ms.setActionHandler("seekto", (details) => {
        trace("action:seekto", `seekTime=${details.seekTime}`);
        if (details.seekTime != null) {
          doSeek(details.seekTime);
        }
      });
    } catch {
      trace("refused:seekto");
    }

    updatePositionState();
    // String(), not a bare .slice: this line runs on EVERY mount, trace on or
    // off, so a navigator without userAgent (a test stub built by spread — see
    // withMediaSessionNavigator) must not be able to throw out of player setup.
    // `src=` FIRST, before the unbounded user agent, so it survives any later
    // truncation of the line. "unknown" rather than a guessed default: a car
    // head unit, a headset and the notification shade are indistinguishable
    // here, and on 2026-09-12 that ambiguity got a car log read as phone
    // testing. Declared via `?mediatrace=on&src=car`.
    trace(
      `mediasession-ready src=${mediaTraceSource() || "unknown"} ua=${String(navigator.userAgent).slice(0, 120)}`,
    );
  }

  const RESUME_KEY = `tt-resume-${lessonId}`;

  function saveResume() {
    // While a restore is still pending (metadata never arrived), position 0
    // is meaningless — writing it would clobber the real saved spot.
    if (pendingResume !== null) return;
    storage.setItem(
      RESUME_KEY,
      JSON.stringify({ section: activeSectionType, position: audioEl.currentTime }),
    );
    lastSavedPosition = audioEl.currentTime;
  }

  // Saving without a pause: a hard refresh never fires pause, so store the
  // spot on tab-hide (mobile refresh / app switch) and pagehide (desktop).
  // beforeunload is deliberately NOT used — unreliable on mobile Safari.
  function onVisibilityChange() {
    if (document.visibilityState === "hidden") {
      saveResume();
    }
  }
  function onPageHide() {
    saveResume();
  }
  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("pagehide", onPageHide);

  // --- Section navigation ---

  function nextSection(): void {
    cancelRepeatLatch();
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
    cancelRepeatLatch();
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

  function restartSection(): void {
    cancelRepeatLatch();
    if (!activeCues) return;
    if (currentSectionIndex === null) {
      doSeek(0);
      return;
    }
    const firstCueInSection = activeCues.find((c) => c.section_index === currentSectionIndex);
    if (firstCueInSection) {
      doSeek(firstCueInSection.start_ms / 1000);
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
    cancelRepeatLatch();
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
    cancelRepeatLatch();
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

  function selectTrack(
    sectionType: string,
    seekRef: CueRef | null = null,
    fromStart = false,
  ): void {
    cancelRepeatLatch();
    const section = audioSections.find((s) => s.section_type === sectionType);
    if (!section) return;

    // Where to land in the new track: an explicit seekRef (a transcript ▶ tap)
    // wins; otherwise preserve the current line's position across the swap.
    // fromStart bypasses both: advancing a hands-free pass must start at the
    // BEGINNING of the next track (at `ended` the current cue is the last
    // line, so position-preservation would land at the END of the next pass).
    let prevRef: CueRef | null = null;
    if (!fromStart) {
      prevRef = seekRef ?? currentCue?.ref ?? null;
    }

    // Guard: prevent the browser's pause/emptied events from clobbering resume.
    // Capture the *intent* to resume from `playing`, not `!audioEl.paused`: a
    // rapid second swap (quickly toggling setting chips) re-enters here after
    // the first `src` assignment has already paused the element, so reading the
    // element would see paused=true and lose the resume — stranding the player
    // paused with `playing` stuck true and no way to recover but a refresh.
    // `playing` holds the pre-swap state because the swapping guard swallows the
    // intervening pause events.
    swapping = true;
    wasPlayingBeforeSwap = playing;

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
    cancelRepeatLatch();
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

  // --- Latching repeat (tunatale-b23x) ---

  function cancelRepeatLatch(): void {
    repeatLatched = false;
    latchedCue = null;
  }

  // Shared by the timeupdate and ended listeners — the condition is the same
  // and seek is the whole payload. The ended path is a real case: a latched
  // LAST sentence of a track may never see another timeupdate past its end.
  // Returns whether it actually looped, which is what lets the ended listener
  // resume playback WITHOUT making every track ending restart itself.
  function latchLoopCheck(): boolean {
    if (!repeatLatched || !latchedCue) return false;
    if (audioEl.currentTime * 1000 < latchedCue.end_ms) return false;
    doSeek(latchedCue.start_ms / 1000);
    return true;
  }

  function toggleRepeatLatch(): void {
    if (repeatLatched) {
      cancelRepeatLatch();
      return;
    }
    // Engaging pins the CURRENT cue — captured once, never re-read on a tick,
    // or the loop would start drilling the NEXT sentence the moment playback
    // crosses into it.
    const cue = currentCue;
    if (!cue) return;
    latchedCue = cue;
    repeatLatched = true;
    // Engaging is itself the first repetition: rewind the sentence.
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
    get handsFree() {
      return handsFree;
    },
    get repeatLatched() {
      return repeatLatched;
    },
    get activeSectionType() {
      return activeSectionType;
    },
    get activeCues() {
      return activeCues;
    },
    get resumeSection() {
      return pendingResume !== null ? pendingResumeSection : null;
    },

    play() {
      trace("call:play");
      // The hand-off between lessons calls this without a user gesture directly
      // behind it, and a browser that blocks autoplay REJECTS rather than
      // throwing. Unhandled, that surfaces as a console error on a path the
      // user experiences simply as "it didn't start" — the transport is right
      // there, so swallow it rather than making a blocked autoplay look like a
      // crash.
      const started = audioEl.play();
      if (started && typeof started.catch === "function") started.catch(() => {});
    },
    pause() {
      trace("call:pause");
      audioEl.pause();
    },
    togglePlay() {
      trace("call:togglePlay");
      // Branch on the element's real state, not the `playing` flag: if the two
      // ever desync (e.g. a swallowed pause event during a track swap), acting
      // on `playing` could call pause() on an already-paused element — no event
      // fires, so the button stays stuck. Ground truth always recovers.
      if (audioEl.paused) {
        audioEl.play();
      } else {
        audioEl.pause();
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
    restartSection,
    nextCue: nextCueAction,
    prevCue: prevCueAction,
    repeatCue,
    toggleRepeatLatch,
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
    setHandsFree(v: boolean) {
      // Capture the active section when turning ON so turning OFF restores it.
      // Restore only if a capture exists. See the handsFreeRestoreSection note.
      if (v !== handsFree) {
        // The transition needs its OWN event. Every trace line already carries
        // `hf=`, but that context field is stale right after a mount: on the
        // real 2026-09-12 car log hands-free was on throughout, and every
        // mediasession-ready line plus the first call:togglePlay still read
        // hf=0. Without this, no reader can tell when the mode actually
        // changed — only what it had settled to by the next event.
        //
        // Inside the `v !== handsFree` guard on purpose: a write that changes
        // nothing must not manufacture a transition.
        trace(v ? "handsfree:on" : "handsfree:off");
        if (v) {
          handsFreeRestoreSection = activeSectionType;
        } else if (handsFreeRestoreSection !== null) {
          selectTrack(handsFreeRestoreSection);
          handsFreeRestoreSection = null;
        }
      }
      handsFree = v;
    },
    destroy() {
      destroyed = true;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("pagehide", onPageHide);
      audioEl.pause();
      saveResume();
      audioEl.src = "";
      if (mediaSession) {
        // The same unsupported-action throw applies at teardown, and nulling a
        // handler that was never registered throws too. Each is wrapped
        // silently — the controller is being destroyed, there is no one left to
        // read a trace line.
        const clearHandler = (name: MediaSessionAction) => {
          try {
            mediaSession.setActionHandler(name, null);
          } catch {
            /* teardown continues past an unsupported action */
          }
        };
        clearHandler("play");
        clearHandler("pause");
        clearHandler("seekbackward");
        clearHandler("seekforward");
        clearHandler("previoustrack");
        clearHandler("nexttrack");
        clearHandler("seekto");
        mediaSession.metadata = null;
      }
    },
  };
}
