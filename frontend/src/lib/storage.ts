const STORAGE_KEY = 'tunatale:home';

export interface PersistedHomeState {
	topic: string;
	cefrLevel: string;
	numDays: number;
	curriculumId?: string;
	lessonId?: string;
	audioUrl?: string;
	listenedLessonIds?: string[];
}

export function saveHomeState(state: PersistedHomeState): void {
	if (typeof window === 'undefined') return;
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
	} catch {
		// quota exceeded or private mode — silently ignore
	}
}

export function loadHomeState(): PersistedHomeState | null {
	if (typeof window === 'undefined') return null;
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return null;
		return JSON.parse(raw) as PersistedHomeState;
	} catch {
		return null;
	}
}

export function clearHomeState(): void {
	if (typeof window === 'undefined') return;
	try {
		localStorage.removeItem(STORAGE_KEY);
	} catch {
		// ignore
	}
}
