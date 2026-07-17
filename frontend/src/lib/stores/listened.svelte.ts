/**
 * Server-backed tracking of which lesson IDs have been listened to.
 * Hydrates from the backend API on mount; one-time migrates legacy
 * localStorage keys to the server on first hydrate.
 *
 * API surface:
 *  - has(lessonId)   — boolean, backward-compatible read
 *  - count(lessonId) — listen count (0 = never listened)
 *  - markListened(lessonId, wordRatings?) — async: calls API + returns response
 *  - hydrate()       — async: server fetch + one-time localStorage migration
 *  - refresh()       — async: resets hydration latch + re-runs hydrate()
 */
import { api, type ListenResponse, type WordRating } from "$lib/api";

const LEGACY_LISTENED_KEY = "tunatale:listened-lessons";
const LEGACY_HOME_KEY = "tunatale:home";

interface LessonEntry {
  listenCount: number;
  lastListenedAt: string;
}

function createListenedStore() {
  let entries = $state<Record<string, LessonEntry>>({});
  let hydrated = false;

  async function hydrate(): Promise<void> {
    if (hydrated) return;
    hydrated = true;

    await migrateFromLocalStorage();

    try {
      const { lessons } = await api.getListens();
      const next: Record<string, LessonEntry> = {};
      for (const l of lessons) {
        next[l.lesson_id] = {
          listenCount: l.listen_count,
          lastListenedAt: l.last_listened_at,
        };
      }
      entries = next;
    } catch {
      // Server unavailable — keep whatever local state we have.
    }
  }

  return {
    has(lessonId: string): boolean {
      return (entries[lessonId]?.listenCount ?? 0) > 0;
    },

    count(lessonId: string): number {
      return entries[lessonId]?.listenCount ?? 0;
    },

    /** Async: calls the listen API, updates local state, returns full response. */
    async markListened(
      lessonId: string,
      wordRatings: Record<string, WordRating> = {},
    ): Promise<ListenResponse> {
      const result = await api.markAsListened(lessonId, wordRatings);
      entries = {
        ...entries,
        [lessonId]: {
          listenCount: result.listen_count,
          lastListenedAt: new Date().toISOString(),
        },
      };
      return result;
    },

    /** Public entry-point for layout onMount. */
    hydrate,

    /** Reset hydration latch and re-hydrate — call when language switches. */
    async refresh(): Promise<void> {
      hydrated = false;
      await hydrate();
    },
  };
}

/**
 * Migration: read legacy localStorage keys, import the full ID list once per
 * configured language via api.importListens(ids, code), then remove both keys
 * ONLY after every language import resolved successfully. If any import
 * rejects, both keys survive so the next hydrate() retries.
 */
async function migrateFromLocalStorage(): Promise<void> {
  let ids: string[] = [];

  try {
    const raw = localStorage.getItem(LEGACY_LISTENED_KEY);
    if (raw !== null) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        ids = parsed;
      }
    }

    if (ids.length === 0) {
      const legacy = localStorage.getItem(LEGACY_HOME_KEY);
      if (legacy) {
        const parsed = JSON.parse(legacy);
        if (Array.isArray(parsed?.listenedLessonIds)) {
          ids = parsed.listenedLessonIds;
        }
      }
    }
  } catch {
    // Corrupted localStorage — nothing to migrate.
    return;
  }

  if (ids.length === 0) return;

  // Import the full id list once per configured language. The backend routes
  // each lesson to the right language DB — we do NOT partition client-side.
  const { languages } = await api.getLanguages();
  let allSucceeded = true;
  for (const lang of languages) {
    try {
      await api.importListens(ids, lang.code);
    } catch {
      allSucceeded = false;
    }
  }

  // Only remove the keys after every language's import resolved.
  if (allSucceeded) {
    localStorage.removeItem(LEGACY_LISTENED_KEY);
    localStorage.removeItem(LEGACY_HOME_KEY);
  }
}

export const listenedStore = createListenedStore();
