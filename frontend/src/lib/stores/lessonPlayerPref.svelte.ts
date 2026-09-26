// Lesson-player phase/enunciation/English selection, persisted across lessons.
// The storage half is the shared createLocalPref; what is stored and what the
// stored bytes mean (the legacy english-boolean migration) stays here, because
// that is this preference's business and no other store's.
// Default is Dialogue · Natural · English-off — the plain listening start point.

import { createLocalPref } from "./localPref.svelte";
import { isSectionType, type SectionType } from "$lib/sectionTypes";

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

function defaultSelection(): PlayerSelection {
  return { phase: "dialogue", enunciation: "natural", english: "off" };
}

// The enunciated level a slow_* section implies, given whatever the pill holds
// now. Which of the three enunciated rates is playing is genuinely not
// recoverable from the section type — slow_speed is slow_speed at 1.0x, 0.9x
// and 0.8x — so an enunciated level already selected is kept.
//
// ⚠️ What is recoverable is that it is NOT natural, and failing to say so was a
// bug. The old version returned no enunciation at all and left the pill alone,
// which is right only when the user cycled the pill to GET here. Every other
// route in — a hands-free advance, a transcript ▶ tap — selects the track
// directly, so the pill sat on "Natural", unhighlighted, while an enunciated
// track played. It also desynced resolveSectionType, which then computed
// natural_speed for a player on slow_speed.
function enunciatedLevel(current: string | undefined): string {
  return current !== undefined && current !== "natural" ? current : "enunciated";
}

// Reverse-map the section type actually playing back onto the player pills, so
// the controls mirror the audio even when something outside the player (a
// transcript ▶ tap, a hands-free advance) switches the track. Fields left
// undefined are not forced: key_phrases leaves enunciation/English (hidden in
// that phase) untouched.
//
// *currentEnunciation* is the level the pill holds now, and is consulted only
// by the slow_* rows — see enunciatedLevel.
//
// Keyed by the shared SectionType union rather than switched on a string, so
// this is the declared INVERSE of LessonPlayer::resolveSectionType and stays
// exhaustive in both directions: a token added to `$lib/sectionTypes` without a
// row here, or a row keyed on a token that does not exist, is a compile error.
// (A switch could not say that — an unlisted token would fall through to
// returning nothing at all.)
type Pills = {
  phase?: PlayerPhase;
  enunciation?: string;
  english?: EnglishMode;
};

const PILLS_FOR_SECTION: Record<SectionType, (current: string | undefined) => Pills> = {
  key_phrases: () => ({ phase: "key_phrases" }),
  natural_speed: () => ({ phase: "dialogue", enunciation: "natural", english: "off" }),
  translated: () => ({ phase: "dialogue", enunciation: "natural", english: "l2_first" }),
  en_translated: () => ({ phase: "dialogue", enunciation: "natural", english: "en_first" }),
  slow_speed: (current) => ({
    phase: "dialogue",
    enunciation: enunciatedLevel(current),
    english: "off",
  }),
  slow_translated: (current) => ({
    phase: "dialogue",
    enunciation: enunciatedLevel(current),
    english: "l2_first",
  }),
  slow_en_translated: (current) => ({
    phase: "dialogue",
    enunciation: enunciatedLevel(current),
    english: "en_first",
  }),
};

// The tokens come off the wire, so an unrecognised type (or none) is still
// possible; it forces nothing, exactly as before.
export function pillsForSection(sectionType: string | null, currentEnunciation?: string): Pills {
  if (!isSectionType(sectionType)) return {};
  return PILLS_FOR_SECTION[sectionType](currentEnunciation);
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

// Always yields a clean selection — a coerced stored value, else the default —
// so it also resets any in-memory carryover when storage is empty (matters for
// test isolation and lesson re-mounts).
function parseSelection(raw: string | null): PlayerSelection {
  if (raw !== null) {
    try {
      const coerced = coerce(JSON.parse(raw));
      if (coerced !== null) return coerced;
    } catch {
      // Malformed JSON — fall through to the default.
    }
  }
  return defaultSelection();
}

const pref = createLocalPref<PlayerSelection>("lessonPlayerSelection", {
  parse: parseSelection,
  serialize: (next) => JSON.stringify(next),
});

export const lessonPlayerPref = {
  get selection(): PlayerSelection {
    return pref.value;
  },
  init: pref.init,
  set: pref.set,
};
