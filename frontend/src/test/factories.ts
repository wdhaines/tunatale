import type { WordToken, ReviewQueueItem, SRSItemDetail } from "$lib/api";

export function makeWordToken(overrides: Partial<WordToken> = {}): WordToken {
  return {
    surface: "zdravo",
    lemma: "zdravo",
    srs_state: "new",
    srs_item_id: null,
    translation: null,
    collocation_span_id: null,
    collocation_start: false,
    collocation_srs_state: null,
    collocation_lemma: null,
    collocation_translation: null,
    collocation_progress: null,
    collocation_is_due: false,
    card_type: null,
    active_state: "new",
    active_direction: null,
    is_due: false,
    progress: null,
    inflectable: false,
    inflection_feature: null,
    known_marked: false,
    ...overrides,
  };
}

export function makeReviewQueueItem(overrides: Partial<ReviewQueueItem> = {}): ReviewQueueItem {
  return {
    id: 1,
    text: "banka",
    translation: "bank",
    word_count: 2,
    state: "review",
    due_at: "2026-04-18",
    stability: 5.0,
    difficulty: 4.0,
    reps: 3,
    lapses: 0,
    last_review: "2026-04-10",
    language_code: "sl",
    guid: "guid_1",
    anki_note_id: null,
    image_url: null,
    audio_url: null,
    grammar: "",
    note: "",
    directions: {
      recognition: {
        state: "review",
        due_at: "2026-04-18",
        stability: 5.0,
        difficulty: 4.0,
        reps: 3,
        lapses: 0,
        last_review: "2026-04-10",
        anki_card_id: null,
      },
      production: {
        state: "new",
        due_at: "2026-04-18",
        stability: 1.0,
        difficulty: 5.0,
        reps: 0,
        lapses: 0,
        last_review: null,
        anki_card_id: null,
      },
    },
    direction: "recognition",
    pending_rating: null,
    ...overrides,
  };
}

export function makeSRSItemDetail(overrides: Partial<SRSItemDetail> = {}): SRSItemDetail {
  return {
    id: 1,
    text: "banka",
    translation: "bank",
    word_count: 1,
    state: "review",
    due_at: "2026-04-18",
    stability: 5.0,
    difficulty: 4.0,
    reps: 3,
    lapses: 0,
    last_review: "2026-04-10",
    language_code: "sl",
    card_type: "vocab",
    source_sentence: "",
    image_url: "/api/media/banka.jpg",
    audio_url: null,
    grammar: "",
    note: "",
    directions: {
      recognition: {
        state: "review",
        due_at: "2026-04-18",
        stability: 5.0,
        difficulty: 4.0,
        reps: 3,
        lapses: 0,
        last_review: "2026-04-10",
        anki_card_id: null,
      },
      production: {
        state: "new",
        due_at: "2026-04-18",
        stability: 1.0,
        difficulty: 5.0,
        reps: 0,
        lapses: 0,
        last_review: null,
        anki_card_id: null,
      },
    },
    ...overrides,
  };
}

/** A distinct, deterministic collocation id per candidate text.
 *
 * Listen-preview fixtures used to hardcode one id (`item_id: 42`) on every word
 * row, which was harmless while the commit payload was keyed by TEXT. It is not
 * harmless now: tracked rows are keyed by COLLOCATION ID (bd tunatale-og4d), so
 * two rows sharing an id collapse onto one payload entry and the second silently
 * overwrites the first.
 *
 * A shared 42 was never realistic either — the backend guarantees one row per
 * card (`_lemmas_losing_a_shared_card`, pinned by
 * `test_api_listen_word_word_collision.py::test_no_candidate_id_is_duplicated`),
 * so a real preview cannot repeat an item_id.
 *
 * Stable within a file run, so an assertion can name a card the same way the
 * fixture did: `confirmedWords: [candidateId("prosim")]`.
 */
const _candidateIds = new Map<string, number>();
export function candidateId(text: string): number {
  let id = _candidateIds.get(text);
  if (id === undefined) {
    id = 1000 + _candidateIds.size;
    _candidateIds.set(text, id);
  }
  return id;
}

/** A `due_at` for the card that is due *days* study-days from now.
 *
 * ⚠️ The anchor is the STUDY DAY — the local date of the most recent 04:00
 * LOCAL rollover — not the browser's UTC date, and the difference is a whole
 * calendar day for a third of every day. `ListenPreviewModal.svelte::dueDays`
 * compares a UTC-dated `due_at` against exactly that local study day, so a seed
 * anchored on `new Date().toISOString()` reads one day HIGH from 20:00 to 04:00
 * local at UTC-4: "today" renders as "1d" and the suite goes red every evening
 * on a tree nobody touched.
 *
 * Measured 2026-09-15: five tests across two files failed at 22:11 EDT and the
 * same commit passed at 04:11 CEST. That control — same code, different wall
 * clock — is the one that tells a seeding bug from a product bug, and it is
 * cheaper than reading either file.
 *
 * The emitted timestamp keeps the 04:00-UTC convention `due_at_rollover_utc`
 * writes, because `dueDays` recovers the due date by flooring it to UTC
 * midnight.
 */
export function studyDayDueAt(days = 0): string {
  const now = new Date();
  const ROLLOVER_HOUR = 4;
  return new Date(
    Date.UTC(
      now.getFullYear(),
      now.getMonth(),
      now.getDate() - (now.getHours() < ROLLOVER_HOUR ? 1 : 0) + days,
      ROLLOVER_HOUR,
      0,
      0,
      0,
    ),
  ).toISOString();
}
