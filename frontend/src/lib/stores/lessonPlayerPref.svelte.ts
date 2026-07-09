// Lesson-player phase/enunciation/English selection, persisted across lessons.
// Mirrors the lessonModePref $state + localStorage pattern: the default lives
// here, init() seeds from storage on mount (browser-only), set() writes the
// override. Default is Dialogue · Natural · English-off — the plain listening
// start point.

export type PlayerPhase = "key_phrases" | "dialogue";

export interface PlayerSelection {
  phase: PlayerPhase;
  // One of LessonPlayer's ENUNCIATION_OPTIONS levels ("natural",
  // "enunciated", "enunciated_0.9", "enunciated_0.8"). Stored as a bare string
  // so the option list can evolve without a migration; an unknown value
  // degrades gracefully (resolveRate falls back to 1.0).
  enunciation: string;
  english: boolean;
}

const STORAGE_KEY = "lessonPlayerSelection";

function defaultSelection(): PlayerSelection {
  return { phase: "dialogue", enunciation: "natural", english: false };
}

function isValid(v: unknown): v is PlayerSelection {
  if (typeof v !== "object" || v === null) return false;
  const s = v as Record<string, unknown>;
  return (
    (s.phase === "key_phrases" || s.phase === "dialogue") &&
    typeof s.enunciation === "string" &&
    typeof s.english === "boolean"
  );
}

function createLessonPlayerPref() {
  let selection = $state<PlayerSelection>(defaultSelection());

  // Called from LessonPlayer's onMount (browser-only), the same way the
  // theme/prefetch/mode prefs seed. Always establishes a clean state — a valid
  // stored value, else the default — so it also resets any in-memory carryover
  // when storage is empty (matters for test isolation and lesson re-mounts).
  function init(): void {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null) {
      try {
        const parsed: unknown = JSON.parse(stored);
        if (isValid(parsed)) {
          selection = parsed;
          return;
        }
      } catch {
        // Malformed JSON — fall through to the default.
      }
    }
    selection = defaultSelection();
  }

  function set(next: PlayerSelection): void {
    selection = next;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }

  return {
    get selection(): PlayerSelection {
      return selection;
    },
    init,
    set,
  };
}

export const lessonPlayerPref = createLessonPlayerPref();
