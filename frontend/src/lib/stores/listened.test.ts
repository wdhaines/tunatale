/**
 * Tests for the listenedStore (localStorage-backed set of listened lesson IDs).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

const STORAGE_KEY = 'tunatale:listened-lessons';
const LEGACY_HOME_KEY = 'tunatale:home';

beforeEach(() => {
	localStorage.clear();
	vi.resetModules();
});

async function freshStore() {
	// Each test gets a fresh module instance
	const mod = await import('./listened.svelte');
	return mod.listenedStore;
}

describe('listenedStore', () => {
	it('has() returns false when no lesson has been added', async () => {
		const store = await freshStore();
		expect(store.has('lesson-1')).toBe(false);
	});

	it('add() persists a lesson ID and has() returns true', async () => {
		const store = await freshStore();
		store.add('lesson-1');
		expect(store.has('lesson-1')).toBe(true);
	});

	it('add() writes to localStorage', async () => {
		const store = await freshStore();
		store.add('lesson-abc');
		const raw = localStorage.getItem(STORAGE_KEY);
		expect(raw).not.toBeNull();
		expect(JSON.parse(raw!)).toContain('lesson-abc');
	});

	it('has() hydrates from localStorage on first access', async () => {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(['lesson-pre-existing']));
		const store = await freshStore();
		expect(store.has('lesson-pre-existing')).toBe(true);
	});

	it('has() returns false for unknown IDs even after hydration', async () => {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(['lesson-x']));
		const store = await freshStore();
		expect(store.has('lesson-unknown')).toBe(false);
	});

	it('migrates from legacy tunatale:home key on first hydration', async () => {
		const legacyData = { listenedLessonIds: ['old-lesson-1', 'old-lesson-2'] };
		localStorage.setItem(LEGACY_HOME_KEY, JSON.stringify(legacyData));

		const store = await freshStore();
		expect(store.has('old-lesson-1')).toBe(true);
		expect(store.has('old-lesson-2')).toBe(true);

		// New key should now exist
		const raw = localStorage.getItem(STORAGE_KEY);
		expect(raw).not.toBeNull();
		expect(JSON.parse(raw!)).toContain('old-lesson-1');
	});

	it('ignores legacy key if listenedLessonIds is missing', async () => {
		localStorage.setItem(LEGACY_HOME_KEY, JSON.stringify({ other: [] }));
		const store = await freshStore();
		expect(store.has('any')).toBe(false);
	});

	it('handles corrupted localStorage JSON gracefully', async () => {
		localStorage.setItem(STORAGE_KEY, 'not-valid-json{{{');
		const store = await freshStore();
		expect(store.has('anything')).toBe(false);
	});

	it('add() does not throw when localStorage.setItem throws (quota)', async () => {
		const store = await freshStore();
		vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
			throw new Error('QuotaExceededError');
		});
		expect(() => store.add('lesson-quota')).not.toThrow();
	});

	it('add() accumulates multiple IDs', async () => {
		const store = await freshStore();
		store.add('lesson-a');
		store.add('lesson-b');
		expect(store.has('lesson-a')).toBe(true);
		expect(store.has('lesson-b')).toBe(true);
	});
});
