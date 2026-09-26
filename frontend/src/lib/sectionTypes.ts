// The section-type token set, spelled once.
//
// A rendered lesson's audio is cut into sections, and each one is typed by a
// token that crosses three boundaries: the renderer writes it, the API returns
// it as `section_type`, and the player turns it into a track choice
// (LessonPlayer::resolveSectionType), a pill state
// (lessonPlayerPref::pillsForSection) and a hands-free pass order
// (playbackController::HANDS_FREE_SEQUENCE). Those last three are inverses of
// each other, so a token spelled two ways in two of them is a silent
// disagreement — the pills stop mirroring the audio, and hands-free advances to
// a section that does not exist.
//
// The tokens themselves are the renderer's, not ours, and they are NOT
// enumerated by the API schema: `section_type` is typed `string` in
// `$lib/api-types.d.ts` (generated from the OpenAPI snapshot), so there is
// nothing to derive this union from. Hence the tuple — and hence the value of
// keeping it to one place.
//
// Spelling: the base name is the pass, `slow_` is the enunciated-rate version
// of it, and `_en_` (or the `en_` prefix) marks the English-first ordering.

export const SECTION_TYPES = [
  "key_phrases",
  "natural_speed",
  "translated",
  "en_translated",
  "slow_speed",
  "slow_translated",
  "slow_en_translated",
] as const;

export type SectionType = (typeof SECTION_TYPES)[number];

// Section types arrive from the API as plain `string`, so anything that keys on
// one has to narrow before it can switch exhaustively. This is that narrowing,
// and it is also what makes a misspelled token a compile error at the call
// site: inside the guard the value is a SectionType, so every `case` is checked
// against this set.
const KNOWN: ReadonlySet<string> = new Set(SECTION_TYPES);

export function isSectionType(value: string | null): value is SectionType {
  return value !== null && KNOWN.has(value);
}
