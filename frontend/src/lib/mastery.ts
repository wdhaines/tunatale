/** Map a mastery fraction (0 = new, 1 = mastered) to a red→green hue.
 *  0 → red (hue 0), 0.5 → yellow (hue 60), 1 → green (hue 120). */
export function masteryColor(progress: number): string {
  const p = Math.max(0, Math.min(1, progress));
  const hue = p * 120;
  const lightness = 50 - p * 8;
  return `hsl(${hue}, 70%, ${lightness}%)`;
}

export interface MasteryBreakdown {
  new: number;
  learning: number;
  due: number;
  review: number;
  known: number;
}

export interface SideResult {
  pct: number | null;
  bands: Record<string, number>;
}

export interface MasteryResult {
  pct: number | null;
  counts: MasteryBreakdown;
  sides: {
    understand: SideResult;
    produce: SideResult;
  };
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
      well_known?: boolean;
      understand_progress?: number | null;
      produce_progress?: number | null;
      understand_band?: string | null;
      produce_band?: string | null;
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
    well_known: boolean | undefined;
    understand_progress: number | null | undefined;
    produce_progress: number | null | undefined;
    understand_band: string | null | undefined;
    produce_band: string | null | undefined;
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
        well_known: word.well_known,
        understand_progress: word.understand_progress,
        produce_progress: word.produce_progress,
        understand_band: word.understand_band,
        produce_band: word.produce_band,
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

  const sideValues = { understand: [] as number[], produce: [] as number[] };
  const sideBands = {
    understand: {} as Record<string, number>,
    produce: {} as Record<string, number>,
  };

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

    // Side bands: every deduped non-ignored word contributes
    const ub = e.understand_band ?? "none";
    const pb = e.produce_band ?? "none";
    sideBands.understand[ub] = (sideBands.understand[ub] ?? 0) + 1;
    sideBands.produce[pb] = (sideBands.produce[pb] ?? 0) + 1;

    // Side progress values
    if (e.state === "unknown") {
      sideValues.understand.push(0);
      sideValues.produce.push(0);
    } else {
      if (e.understand_progress != null) sideValues.understand.push(e.understand_progress);
      if (e.produce_progress != null) sideValues.produce.push(e.produce_progress);
    }

    // Recognition-based bucketing (excludes tracked clozes with null recognition_state)
    if (e.state === "unknown" || e.recognition_state === "new") {
      counts.new++;
      lemmas.new.push(e.lemma);
    } else if (e.recognition_state === "learning" || e.recognition_state === "relearning") {
      counts.learning++;
      lemmas.learning.push(e.lemma);
    } else if (e.well_known) {
      counts.known++;
      lemmas.known.push(e.lemma);
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
  }

  const sidePct = (vals: number[]): number | null =>
    vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;

  return {
    pct: counted > 0 ? sum / counted : null,
    counts,
    sides: {
      understand: { pct: sidePct(sideValues.understand), bands: sideBands.understand },
      produce: { pct: sidePct(sideValues.produce), bands: sideBands.produce },
    },
    lemmas,
  };
}
