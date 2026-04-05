import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { saveHomeState, loadHomeState, clearHomeState } from './storage';

beforeEach(() => {
	localStorage.clear();
	vi.restoreAllMocks();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('storage utilities', () => {
	it('saveHomeState writes to localStorage under tunatale:home key', () => {
		const state = { topic: 'coffee', cefrLevel: 'A2', numDays: 7 };
		saveHomeState(state);
		expect(localStorage.getItem('tunatale:home')).toBe(JSON.stringify(state));
	});

	it('saveHomeState does not throw when localStorage.setItem throws', () => {
		vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
			throw new Error('QuotaExceededError');
		});
		expect(() => saveHomeState({ topic: 'x', cefrLevel: 'A2', numDays: 7 })).not.toThrow();
	});

	it('loadHomeState returns parsed state when data exists', () => {
		const state = { topic: 'coffee', cefrLevel: 'B1', numDays: 5, curriculumId: 'c1' };
		localStorage.setItem('tunatale:home', JSON.stringify(state));
		expect(loadHomeState()).toEqual(state);
	});

	it('loadHomeState returns null when no entry exists', () => {
		expect(loadHomeState()).toBeNull();
	});

	it('loadHomeState returns null on corrupted JSON', () => {
		localStorage.setItem('tunatale:home', 'not-json{{{');
		expect(loadHomeState()).toBeNull();
	});

	it('clearHomeState removes the entry', () => {
		localStorage.setItem('tunatale:home', '{}');
		clearHomeState();
		expect(localStorage.getItem('tunatale:home')).toBeNull();
	});

	it('save then load round-trips correctly', () => {
		const state = {
			topic: 'hiking',
			cefrLevel: 'A1',
			numDays: 3,
			curriculumId: 'c2',
			lessonId: 'l2',
			audioUrl: '/api/audio/a1'
		};
		saveHomeState(state);
		expect(loadHomeState()).toEqual(state);
	});
});
