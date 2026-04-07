import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { saveFormPreferences, loadFormPreferences, type FormPreferences } from './storage';

beforeEach(() => {
	localStorage.clear();
	vi.restoreAllMocks();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('storage', () => {
	it('saveFormPreferences writes to tunatale:prefs key', () => {
		const prefs: FormPreferences = { topic: 'coffee', cefrLevel: 'A2', numDays: 7 };
		saveFormPreferences(prefs);
		expect(localStorage.getItem('tunatale:prefs')).toBe(JSON.stringify(prefs));
	});

	it('saveFormPreferences does not throw when localStorage.setItem throws', () => {
		vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
			throw new Error('QuotaExceededError');
		});
		expect(() => saveFormPreferences({ topic: 'x', cefrLevel: 'A2', numDays: 7 })).not.toThrow();
	});

	it('loadFormPreferences returns parsed prefs when data exists', () => {
		const prefs: FormPreferences = { topic: 'coffee', cefrLevel: 'B1', numDays: 5 };
		localStorage.setItem('tunatale:prefs', JSON.stringify(prefs));
		expect(loadFormPreferences()).toEqual(prefs);
	});

	it('loadFormPreferences returns null when no entry exists', () => {
		expect(loadFormPreferences()).toBeNull();
	});

	it('loadFormPreferences returns null on corrupted JSON', () => {
		localStorage.setItem('tunatale:prefs', 'not-json{{{');
		expect(loadFormPreferences()).toBeNull();
	});

	it('round-trips all fields correctly', () => {
		const prefs: FormPreferences = { topic: 'hiking', cefrLevel: 'A1', numDays: 3 };
		saveFormPreferences(prefs);
		expect(loadFormPreferences()).toEqual(prefs);
	});
});
