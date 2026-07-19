/** Map a mastery fraction (0 = new, 1 = mastered) to a red→green hue.
 *  0 → red (hue 0), 0.5 → yellow (hue 60), 1 → green (hue 120). */
export function masteryColor(progress: number): string {
  const p = Math.max(0, Math.min(1, progress));
  const hue = p * 120;
  const lightness = 50 - p * 8;
  return `hsl(${hue}, 70%, ${lightness}%)`;
}

/** Same red→green hue ramp as {@link masteryColor}, but a low-alpha tint for use
 *  as a background behind text (e.g. a collocation span). 0 → faint red,
 *  1 → faint green. */
export function masteryBackgroundColor(progress: number): string {
  const p = Math.max(0, Math.min(1, progress));
  const hue = p * 120;
  return `hsla(${hue}, 70%, 45%, 0.15)`;
}

export interface MasteryBreakdown {
  new: number;
  learning: number;
  due: number;
  review: number;
  known: number;
}

export interface MasteryResult {
  pct: number | null;
  counts: MasteryBreakdown;
  lemmas?: {
    new: string[];
    learning: string[];
    due: string[];
    review: string[];
    known: string[];
  };
}

/** Compute lesson-level mastery from a transcript's word tokens.
 *  Dedupes by lemma (first occurrence wins). Ignores "ignored" words
 *  entirely (excluded from numerator and denominator).
 *  Buckets words by recognition-side state for the mastery line.
 *  Returns null for an empty/word-less transcript. */
export function lessonMastery(transcript: {
  dialogue_lines: Array<{
    words: Array<{
      lemma: string;
      active_state: string;
      progress: number | null;
      recognition_state?: string | null;
      recognition_is_due?: boolean;
    }>;
  }>;
}): MasteryResult | null {
  const seen = new Set<string>();
  const entries: Array<{
    lemma: string;
    state: string;
    progress: number | null;
    recognition_state: string | null | undefined;
    recognition_is_due: boolean | undefined;
  }> = [];

  for (const line of transcript.dialogue_lines) {
    for (const word of line.words) {
      if (seen.has(word.lemma)) continue;
      seen.add(word.lemma);
      entries.push({
        lemma: word.lemma,
        state: word.active_state,
        progress: word.progress,
        recognition_state: word.recognition_state,
        recognition_is_due: word.recognition_is_due,
      });
    }
  }

  if (entries.length === 0) return null;

  let sum = 0;
  let counted = 0;
  const counts: MasteryBreakdown = { new: 0, learning: 0, due: 0, review: 0, known: 0 };
  const lemmas: {
    new: string[];
    learning: string[];
    due: string[];
    review: string[];
    known: string[];
  } = { new: [], learning: [], due: [], review: [], known: [] };

  for (const e of entries) {
    if (e.state === "ignored") continue;

    // pct computation unchanged: same inputs, same weights, same denominator
    let value: number;
    if (e.state === "unknown") {
      value = 0;
    } else if (e.state === "known") {
      value = 1.0;
    } else {
      value = e.progress ?? 0;
    }

    sum += value;
    counted++;

    // Recognition-based bucketing (excludes tracked clozes with null recognition_state)
    if (e.state === "unknown" || e.recognition_state === "new") {
      counts.new++;
      lemmas.new.push(e.lemma);
    } else if (e.recognition_state === "learning" || e.recognition_state === "relearning") {
      counts.learning++;
      lemmas.learning.push(e.lemma);
    } else if (e.recognition_state === "review" && e.recognition_is_due) {
      counts.due++;
      lemmas.due.push(e.lemma);
    } else if (e.recognition_state === "review" && !e.recognition_is_due) {
      counts.review++;
      lemmas.review.push(e.lemma);
    } else if (e.recognition_state === "known") {
      counts.known++;
      lemmas.known.push(e.lemma);
    }
    // Tracked word with recognition_state === null (cloze) → excluded from buckets
  }

  return {
    pct: counted > 0 ? sum / counted : null,
    counts,
    lemmas,
  };
}
