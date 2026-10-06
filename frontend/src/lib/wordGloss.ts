import type { WordToken } from "$lib/api";

export interface WordGloss {
  /** What the reader shows for the word: the lesson's gloss, else the card's translation. */
  shown: string | null;
  /** The card's own translation, only when the word has a card that disagrees with `shown`. */
  card: string | null;
}

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/**
 * A card's translation is ONE sense of its word, and a lesson may use another:
 * Norwegian `gang` is "hall" on the card and "time" in every line that uses it
 * (bd tunatale-ceuc). So the lesson's in-context gloss leads, and the card's
 * translation is named beside it only where the two disagree — which is also
 * what tells the learner a grade here lands on a card about something else.
 */
export function wordGloss(word: WordToken): WordGloss {
  const gloss = word.gloss ?? null;
  const translation = word.translation ?? null;
  if (!gloss) return { shown: translation, card: null };
  const disagrees = word.srs_item_id !== null && translation !== null && !same(gloss, translation);
  return { shown: gloss, card: disagrees ? translation : null };
}
