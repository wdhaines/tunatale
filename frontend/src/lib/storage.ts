const PREFS_KEY = 'tunatale:prefs';

export interface FormPreferences {
	topic: string;
	cefrLevel: string;
	numDays: number;
}

export function saveFormPreferences(prefs: FormPreferences): void {
	if (typeof window === 'undefined') return;
	try {
		localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
	} catch {
		// quota exceeded or private mode — silently ignore
	}
}

export function loadFormPreferences(): FormPreferences | null {
	if (typeof window === 'undefined') return null;
	try {
		const raw = localStorage.getItem(PREFS_KEY);
		if (!raw) return null;
		return JSON.parse(raw) as FormPreferences;
	} catch {
		return null;
	}
}
