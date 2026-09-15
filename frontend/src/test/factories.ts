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
