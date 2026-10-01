// The words a hand-written review session was asked to use, held between copying
// the prompt and pasting the story (bd tunatale-jmwb).
//
// Nothing server-side remembers a draft prompt — deliberately, see the stateless
// rationale on GET /api/review-sessions/prompt — so the client carries the list
// and hands it back with the paste. That makes this the only copy, and it has to
// outlive whatever happens in between: the learner leaves for another app to
// write the story, and the page they return to has usually been remounted or
// reloaded. Held as component state it was lost on the first navigation
// (2026-10-01), and the paste was refused for an empty list.
//
// Two copies, on purpose:
//   - localStorage survives a reload and a discarded phone tab.
//   - `held` survives blocked storage (private mode throws on get AND set), for
//     as long as the app stays loaded.
//
// Scoped to the language it was pinned under: a prompt copied for one deck must
// not become the denominator of a session in another.

import { LANGUAGE_STORAGE_KEY } from "$lib/api";

const KEY = "tt-review-draft";

interface Draft {
  language: string;
  words: string[];
}

let held: Draft | null = null;

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

// The same value the request header is built from, read at call time: the
// language store's own `code` is still empty while its first fetch is in flight.
function activeLanguage(): string {
  return stored(LANGUAGE_STORAGE_KEY) ?? "";
}

function parse(raw: string | null): Draft | null {
  if (raw === null) return null;
  try {
    const draft = JSON.parse(raw);
    return typeof draft?.language === "string" && Array.isArray(draft.words) ? draft : null;
  } catch {
    return null;
  }
}

export function pinReviewWords(words: string[]): void {
  held = { language: activeLanguage(), words };
  try {
    localStorage.setItem(KEY, JSON.stringify(held));
  } catch {
    // `held` still serves this visit.
  }
}

/** Empty when nothing is pinned for the active language. */
export function pinnedReviewWords(): string[] {
  const draft = parse(stored(KEY)) ?? held;
  return draft !== null && draft.language === activeLanguage() ? draft.words : [];
}

export function unpinReviewWords(): void {
  held = null;
  try {
    localStorage.removeItem(KEY);
  } catch {
    // Nothing was stored if storage is blocked.
  }
}
