// Lesson-player phase/enunciation/English selection, persisted across lessons.
// Mirrors the lessonModePref $state + localStorage pattern: the default lives
// here, init() seeds from storage on mount (browser-only), set() writes the
// override. Default is Dialogue · Natural · English-off — the plain listening
// start point.

export type PlayerPhase = "key_phrases" | "dialogue";

// The English control is a three-way cycle:
//   "off"      — target language only (natural_speed / slow_speed)
//   "l2_first" — target line then its English gloss (translated / slow_translated)
//   "en_first" — English gloss then the target line (en_translated / slow_en_translated)
export type EnglishMode = "off" | "l2_first" | "en_first";

export interface PlayerSelection {
  phase: PlayerPhase;
  // One of LessonPlayer's ENUNCIATION_OPTIONS levels ("natural",
  // "enunciated", "enunciated_0.9", "enunciated_0.8"). Stored as a bare string
  // so the option list can evolve without a migration; an unknown value
  // degrades gracefully (resolveRate falls back to 1.0).
  enunciation: string;
  english: EnglishMode;
}

const STORAGE_KEY = "lessonPlayerSelection";

function defaultSelection(): PlayerSelection {
  return { phase: "dialogue", enunciation: "natural", english: "off" };
}

// Reverse-map the section type actually playing back onto the player pills, so
// the controls mirror the audio even when something outside the player (a
// transcript ▶ tap) switches the track. Fields left undefined are not forced:
// key_phrases leaves enunciation/English (hidden in that phase) untouched, and
// the slow sections leave the enunciation *level* (natural vs the three
// enunciated rates isn't recoverable from the section type alone — the pill
// already holds it).
export function pillsForSection(sectionType: string | null): {
  phase?: PlayerPhase;
  enunciation?: string;
  english?: EnglishMode;
} {
  switch (sectionType) {
    case "key_phrases":
      return { phase: "key_phrases" };
    case "natural_speed":
      return { phase: "dialogue", enunciation: "natural", english: "off" };
    case "translated":
      return { phase: "dialogue", enunciation: "natural", english: "l2_first" };
    case "en_translated":
      return { phase: "dialogue", enunciation: "natural", english: "en_first" };
    case "slow_speed":
      return { phase: "dialogue", english: "off" };
    case "slow_translated":
      return { phase: "dialogue", english: "l2_first" };
    case "slow_en_translated":
      return { phase: "dialogue", english: "en_first" };
    default:
      return {};
  }
}

// Coerce a parsed stored value into a valid PlayerSelection, or null if it's
// unrecognisable. Also migrates the legacy boolean `english` field
// (true → "l2_first", false → "off") from before the three-way cycle existed.
function coerce(v: unknown): PlayerSelection | null {
  if (typeof v !== "object" || v === null) return null;
  const s = v as Record<string, unknown>;
  if (s.phase !== "key_phrases" && s.phase !== "dialogue") return null;
  if (typeof s.enunciation !== "string") return null;

  let english: EnglishMode;
  if (typeof s.english === "boolean") {
    english = s.english ? "l2_first" : "off";
  } else if (s.english === "off" || s.english === "l2_first" || s.english === "en_first") {
    english = s.english;
  } else {
    return null;
  }
  return { phase: s.phase, enunciation: s.enunciation, english };
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
        const coerced = coerce(JSON.parse(stored));
        if (coerced !== null) {
          selection = coerced;
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
