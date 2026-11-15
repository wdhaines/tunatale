/**
 * Pure helpers for the Grade page (tunatale-5i1p.3): the range steps, the due
 * tag, and where a production card sits in its lesson line.
 *
 * Kept out of the component so each rule is one function with its own test.
 */
import type { GradePlanCard, TranscriptData, WordToken } from "#lib/api.js";

/** A range of days, or null for every gradable row ("all"). */
export type GradeRange = number | null;

export const RANGE_PRESETS: readonly GradeRange[] = [0, 3, 7, null];
export const DEFAULT_RANGE: GradeRange = 3;
/** The − / + steps stay inside 0..MAX_RANGE; − from "all" lands here. */
export const MAX_RANGE = 30;
export const ALL_STEP_DOWN = 14;

export function stepRange(range: GradeRange, delta: -1 | 1): GradeRange {
  if (range === null) return delta < 0 ? ALL_STEP_DOWN : null;
  return Math.min(MAX_RANGE, Math.max(0, range + delta));
}

/** The stored string for the range preference, and back. Garbage in, the default out. */
export function parseRange(raw: string | null): GradeRange {
  if (raw === "all") return null;
  if (raw === null || !/^\d+$/.test(raw)) return DEFAULT_RANGE;
  return Math.min(MAX_RANGE, Number(raw));
}

export function serializeRange(range: GradeRange): string {
  return range === null ? "all" : String(range);
}

const DAY_MS = 86_400_000;

function localDay(d: Date): number {
  return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / DAY_MS;
}

/**
 * Whole calendar days from `now` to `dueAt`, in the viewer's own time zone:
 * 0 = today, negative = overdue. A naive ISO string (no offset) is read as UTC,
 * which is what the backend stores.
 */
export function daysUntil(dueAt: string, now: Date): number {
  const iso = /[zZ]|[+-]\d\d:\d\d$/.test(dueAt) ? dueAt : `${dueAt}Z`;
  return localDay(new Date(iso)) - localDay(now);
}

/** "2d late", "today", "in 3d". */
export function dueLabel(days: number): string {
  if (days < 0) return `${-days}d late`;
  if (days === 0) return "today";
  return `in ${days}d`;
}

export function gradeKey(card: Pick<GradePlanCard, "item_id" | "direction">): string {
  return `${card.item_id}:${card.direction}`;
}

/** A line split around what Say it hides: `hidden` is shown blurred until tapped. */
export interface SayLine {
  /** Who says it (the transcript's role), when the line came from the transcript. */
  role: string | null;
  before: string;
  hidden: string;
  after: string;
  /** The English under the line: the card's gloss. */
  english: string;
  /** A drill card's prompt above the line: the worked example (its cue and answer for a
   *  transformation, the sentence before for a substitution), then the new cue. */
  prompt: string[];
}

function surface(w: WordToken): string {
  return `${w.prefix_punct ?? ""}${w.surface}${w.suffix_punct ?? ""}`;
}

function joinWords(words: WordToken[]): string {
  return words.map(surface).join(" ");
}

/**
 * The line a word or phrase card sits in: the first transcript line carrying
 * its card id, with that word (or the phrase's whole span) hidden. Null when
 * no line carries it (a card made from elsewhere); the page then shows the
 * card's own text hidden under its gloss.
 */
function lineFor(card: GradePlanCard, transcript: TranscriptData | null): SayLine | null {
  for (const line of transcript?.dialogue_lines ?? []) {
    const idx = line.words.flatMap((w, i) =>
      w.srs_item_id === card.item_id || w.collocation_span_id === card.item_id ? [i] : [],
    );
    if (idx.length === 0) continue;
    const first = idx[0];
    const last = idx[idx.length - 1];
    const before = joinWords(line.words.slice(0, first));
    const after = joinWords(line.words.slice(last + 1));
    return {
      role: line.role,
      before: before ? `${before} ` : "",
      hidden: joinWords(line.words.slice(first, last + 1)),
      after: after ? ` ${after}` : "",
      english: card.gloss,
      prompt: [],
    };
  }
  return null;
}

/** How Say it shows one production card. */
export function sayLine(card: GradePlanCard, transcript: TranscriptData | null): SayLine {
  if (card.drill) {
    const d = card.drill;
    const prompt = [d.before_cue, d.before, d.cue].filter((p): p is string => Boolean(p));
    return { role: null, before: "", hidden: d.answer, after: "", english: d.english, prompt };
  }
  if (card.kind === "sentence") {
    const start = card.blank_start ?? 0;
    const end = card.blank_end ?? card.text.length;
    return {
      role: null,
      before: card.text.slice(0, start),
      hidden: card.text.slice(start, end),
      after: card.text.slice(end),
      english: card.gloss,
      prompt: [],
    };
  }
  return (
    lineFor(card, transcript) ?? {
      role: null,
      before: "",
      hidden: card.text,
      after: "",
      english: card.gloss,
      prompt: [],
    }
  );
}

/** The Grade page's link, from a lesson or a review session. */
export function gradeHref(contentId: string, back: string, title: string): string {
  return `/grade/${contentId}?${new URLSearchParams({ back, title })}`;
}

/** The `back` query value, only when it is a path on this site; else home. */
export function safeBack(raw: string | null): string {
  return raw !== null && /^\/(?![/\\])/.test(raw) ? raw : "/";
}
