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
  review: number;
  known: number;
}

export interface MasteryResult {
  pct: number | null;
  counts: MasteryBreakdown;
}

/** Compute lesson-level mastery from a transcript's word tokens.
 *  Dedupes by lemma (first occurrence wins). Ignores "ignored" words
 *  entirely (excluded from numerator and denominator).
 *  Returns null for an empty/word-less transcript. */
export function lessonMastery(transcript: {
  dialogue_lines: Array<{
    words: Array<{ lemma: string; active_state: string; progress: number | null }>;
  }>;
}): MasteryResult | null {
  const seen = new Set<string>();
  const entries: Array<{ state: string; progress: number | null }> = [];

  for (const line of transcript.dialogue_lines) {
    for (const word of line.words) {
      if (seen.has(word.lemma)) continue;
      seen.add(word.lemma);
      entries.push({ state: word.active_state, progress: word.progress });
    }
  }

  if (entries.length === 0) return null;

  let sum = 0;
  let counted = 0;
  const counts: MasteryBreakdown = { new: 0, learning: 0, review: 0, known: 0 };

  for (const e of entries) {
    if (e.state === "ignored") continue;

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

    if (e.state === "known") {
      counts.known++;
    } else if (e.state === "new") {
      counts.new++;
    } else if (e.state === "learning" || e.state === "relearning") {
      counts.learning++;
    } else if (e.state === "review") {
      counts.review++;
    }
    // unknown, ignored, suspended → not in breakdown
  }

  return {
    pct: counted > 0 ? sum / counted : null,
    counts,
  };
}
