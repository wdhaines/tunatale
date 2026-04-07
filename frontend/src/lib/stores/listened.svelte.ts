/**
 * Tracks which lesson IDs have been marked as listened.
 * Persisted in localStorage under 'tunatale:listened-lessons'.
 * Migrates from the old 'tunatale:home' listenedLessonIds key on first read.
 */

const STORAGE_KEY = 'tunatale:listened-lessons';
const LEGACY_HOME_KEY = 'tunatale:home';

function loadIds(): Set<string> {
	if (typeof window === 'undefined') return new Set();
	try {
		// Migrate from old key on first access
		const legacy = localStorage.getItem(LEGACY_HOME_KEY);
		if (legacy) {
			const parsed = JSON.parse(legacy);
			if (Array.isArray(parsed?.listenedLessonIds)) {
				const ids = new Set<string>(parsed.listenedLessonIds);
				localStorage.setItem(STORAGE_KEY, JSON.stringify([...ids]));
				return ids;
			}
		}
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return new Set();
		return new Set(JSON.parse(raw) as string[]);
	} catch {
		return new Set();
	}
}

function createListenedStore() {
	let ids = $state<Set<string>>(new Set());
	let hydrated = false;

	function hydrate() {
		if (hydrated || typeof window === 'undefined') return;
		ids = loadIds();
		hydrated = true;
	}

	return {
		has(lessonId: string): boolean {
			hydrate();
			return ids.has(lessonId);
		},
		add(lessonId: string): void {
			hydrate();
			ids = new Set([...ids, lessonId]);
			try {
				localStorage.setItem(STORAGE_KEY, JSON.stringify([...ids]));
			} catch {
				// quota exceeded — silently ignore
			}
		}
	};
}

export const listenedStore = createListenedStore();
