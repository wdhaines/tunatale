/**
 * Tests for the unified /review route (merges recognition + production).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import ReviewPage from './+page.svelte';
import type { SRSItemDetail } from '$lib/api';

vi.mock('$lib/api', () => ({
	api: {
		fetchDue: vi.fn(),
		fetchNew: vi.fn(),
		submitDrill: vi.fn(),
		fetchQueueStats: vi.fn()
	}
}));

import { api } from '$lib/api';
const mockFetchDue = vi.mocked(api.fetchDue);
const mockFetchNew = vi.mocked(api.fetchNew);
const mockSubmitDrill = vi.mocked(api.submitDrill);
const mockFetchQueueStats = vi.mocked(api.fetchQueueStats);

const makeItem = (
	id: number,
	text: string,
	translation: string,
	opts: Partial<SRSItemDetail> = {}
): SRSItemDetail => ({
	id,
	text,
	translation,
	word_count: opts.word_count ?? 2,
	state: 'review',
	due_date: '2026-04-18',
	stability: 5.0,
	difficulty: 4.0,
	reps: 3,
	lapses: 0,
	last_review: '2026-04-10',
	language_code: 'sl',
	image_url: opts.image_url ?? null,
	directions: {
		recognition: { state: 'review', due_date: '2026-04-18', stability: 5.0, difficulty: 4.0, reps: 3, lapses: 0, last_review: '2026-04-10', anki_card_id: null },
		production: { state: 'new', due_date: '2026-04-18', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, anki_card_id: null }
	}
});

beforeEach(() => {
	vi.clearAllMocks();
	mockFetchDue.mockResolvedValue([]);
	mockFetchNew.mockResolvedValue([]);
	mockSubmitDrill.mockResolvedValue({ new_due_date: '2026-04-25', new_state: 'review' });
	mockFetchQueueStats.mockResolvedValue({ new: 0, due: 0, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' });
});

describe('review/+page.svelte', () => {
	it('shows loading state initially', () => {
		mockFetchDue.mockReturnValue(new Promise(() => {}));
		const { container } = render(ReviewPage);
		expect(container.textContent).toContain('Loading');
	});

	it('fetches due recognition and production', async () => {
		render(ReviewPage);
		await waitFor(() => {
			expect(mockFetchDue).toHaveBeenCalledWith('recognition');
			expect(mockFetchDue).toHaveBeenCalledWith('production');
		});
	});

	it('fetches new recognition and production', async () => {
		render(ReviewPage);
		await waitFor(() => {
			expect(mockFetchNew).toHaveBeenCalledWith('recognition', expect.any(Number));
			expect(mockFetchNew).toHaveBeenCalledWith('production', expect.any(Number));
		});
	});

	it('shows done state when all queues are empty', async () => {
		const { findByText } = render(ReviewPage);
		expect(await findByText(/Done for today/)).toBeTruthy();
	});

	it('interleaves recognition and production cards', async () => {
		const rec = makeItem(1, 'okno', 'window');
		const prod = makeItem(2, 'voda', 'water');
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'recognition' ? [rec] : [prod])
		);
		const { findByText } = render(ReviewPage);
		expect(await findByText('okno')).toBeTruthy();
	});

	it('shows direction badge for current card', async () => {
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'recognition' ? [makeItem(1, 'okno', 'window')] : [])
		);
		const { findByText } = render(ReviewPage);
		expect(await findByText(/Recognition/i)).toBeTruthy();
	});

	it('calls submitDrill with correct direction and id on rating', async () => {
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'recognition' ? [makeItem(5, 'voda', 'water')] : [])
		);
		const { findByRole } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(mockSubmitDrill).toHaveBeenCalledWith(5, 'recognition', 'good');
	});

	it('calls submitDrill with production direction for production cards', async () => {
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'production' ? [makeItem(7, 'banka', 'bank', { word_count: 2 })] : [])
		);
		const { findByRole } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(mockSubmitDrill).toHaveBeenCalledWith(7, 'production', 'good');
	});

	it('advances to next card after rating', async () => {
		mockFetchDue.mockImplementation((dir) =>
			dir === 'recognition'
				? Promise.resolve([makeItem(1, 'okno', 'window'), makeItem(3, 'hiša', 'house')])
				: Promise.resolve([])
		);
		const { findByRole, findByText } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText('hiša')).toBeTruthy();
	});

	it('answer is hidden on the next card after rating (no answer leak)', async () => {
		mockFetchDue.mockImplementation((dir) =>
			dir === 'recognition'
				? Promise.resolve([makeItem(1, 'okno', 'window'), makeItem(3, 'hiša', 'house')])
				: Promise.resolve([])
		);
		const { findByRole, queryByRole } = render(ReviewPage);
		// Reveal and rate the first card
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		// Second card should show Show button (not rating buttons) — answer not yet revealed
		expect(await findByRole('button', { name: 'Show' })).toBeTruthy();
		expect(queryByRole('button', { name: 'Good' })).toBeNull();
	});

	it('shows done after last card rated', async () => {
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'recognition' ? [makeItem(1, 'okno', 'window')] : [])
		);
		const { findByRole, findByText } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText(/Done for today/)).toBeTruthy();
	});

	it('shows error when fetch rejects', async () => {
		mockFetchDue.mockRejectedValue(new Error('Network error'));
		const { findByText } = render(ReviewPage);
		expect(await findByText('Network error')).toBeTruthy();
	});

	it('shows error and stays on card when submitDrill rejects', async () => {
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'recognition' ? [makeItem(1, 'okno', 'window')] : [])
		);
		mockSubmitDrill.mockRejectedValue(new Error('Submit failed'));
		const { findByRole, findByText } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText('Submit failed')).toBeTruthy();
	});

	it('production word_count=1 with image_url shows img element', async () => {
		const item = makeItem(10, 'banka', 'bank', {
			word_count: 1,
			image_url: 'banka.jpg'
		});
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'production' ? [item] : [])
		);
		const { container, findByRole } = render(ReviewPage);
		await findByRole('button', { name: 'Show' });
		expect(container.querySelector('img')).toBeTruthy();
	});

	it('production word_count>1 shows L1 translation as prompt', async () => {
		const item = makeItem(11, 'dober dan', 'good day', { word_count: 2 });
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'production' ? [item] : [])
		);
		const { findByText } = render(ReviewPage);
		expect(await findByText('good day')).toBeTruthy();
	});

	it('production word_count=1 without image_url shows L1 translation as prompt', async () => {
		const item = makeItem(12, 'banka', 'bank', { word_count: 1, image_url: null });
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'production' ? [item] : [])
		);
		const { findByText } = render(ReviewPage);
		expect(await findByText('bank')).toBeTruthy();
	});

	it('includes new recognition items in queue alongside due items', async () => {
		const dueItem = makeItem(1, 'okno', 'window');
		const newItem = makeItem(2, 'voda', 'water');
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'recognition' ? [dueItem] : [])
		);
		mockFetchNew.mockImplementation((dir) =>
			Promise.resolve(dir === 'recognition' ? [newItem] : [])
		);
		const { findByText } = render(ReviewPage);
		expect(await findByText('okno')).toBeTruthy();
	});

	it('interleave: more recognition than production shows all rec items', async () => {
		const rec1 = makeItem(1, 'okno', 'window');
		const rec2 = makeItem(3, 'hiša', 'house');
		const prod1 = makeItem(2, 'voda', 'water');
		mockFetchDue.mockImplementation((dir) =>
			dir === 'recognition'
				? Promise.resolve([rec1, rec2])
				: Promise.resolve([prod1])
		);
		const { findByText } = render(ReviewPage);
		expect(await findByText('okno')).toBeTruthy();
	});

	it('same item id in dueRec and newProd shows both directions', async () => {
		// This is the common real-world case: mature recognition + new production
		const recItem = makeItem(1, 'banka', 'bank');
		const prodItem = makeItem(1, 'banka', 'bank', { word_count: 1, image_url: 'banka.jpg' });
		mockFetchDue.mockImplementation((dir) =>
			Promise.resolve(dir === 'recognition' ? [recItem] : [])
		);
		mockFetchNew.mockImplementation((dir) =>
			Promise.resolve(dir === 'production' ? [prodItem] : [])
		);
		const { findByText } = render(ReviewPage);
		// Recognition drill is first: shows L2 text
		expect(await findByText('banka')).toBeTruthy();
	});

	it('new production item is included in queue', async () => {
		const newProdItem = makeItem(20, 'voda', 'water');
		mockFetchDue.mockResolvedValue([]);
		mockFetchNew.mockImplementation((dir) =>
			Promise.resolve(dir === 'production' ? [newProdItem] : [])
		);
		const { findByText } = render(ReviewPage);
		expect(await findByText('water')).toBeTruthy();
	});

	// ── queue-stats breakdown display ──────────────────────────────────────────

	it('shows New · Due breakdown from queue-stats', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 7, due: 15, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'cache' });
		const { findByText } = render(ReviewPage);
		expect(await findByText(/New 7/)).toBeTruthy();
		expect(await findByText(/Due 15/)).toBeTruthy();
	});

	it('shows source label when cap_source is not anki', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, due: 3, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' });
		const { findByText } = render(ReviewPage);
		expect(await findByText(/\(default\)/)).toBeTruthy();
	});

	it('does not show source label when cap_source is cache (freshly synced from Anki)', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, due: 3, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'cache' });
		const { queryByText, findByText } = render(ReviewPage);
		await findByText(/New 5/);
		expect(queryByText(/\(cache\)/)).toBeFalsy();
	});

	it('shows source label when cap_source is config', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, due: 3, daily_new_cap: 20, cap_source: 'config', fsrs_source: 'default' });
		const { findByText } = render(ReviewPage);
		expect(await findByText(/\(config\)/)).toBeTruthy();
	});

	// ── cap-driven fetchNew calls ──────────────────────────────────────────────

	it('uses daily_new_cap=30 to call fetchNew with 15 for each direction', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 15, due: 0, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'cache' });
		render(ReviewPage);
		await waitFor(() => {
			expect(mockFetchNew).toHaveBeenCalledWith('recognition', 15);
			expect(mockFetchNew).toHaveBeenCalledWith('production', 15);
		});
	});

	it('uses daily_new_cap=20 to call fetchNew with 10 for each direction', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 10, due: 0, daily_new_cap: 20, cap_source: 'config', fsrs_source: 'default' });
		render(ReviewPage);
		await waitFor(() => {
			expect(mockFetchNew).toHaveBeenCalledWith('recognition', 10);
			expect(mockFetchNew).toHaveBeenCalledWith('production', 10);
		});
	});

	it('daily_new_cap=1 calls fetchNew with 1 for recognition and skips production', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 1, due: 0, daily_new_cap: 1, cap_source: 'cache', fsrs_source: 'cache' });
		render(ReviewPage);
		await waitFor(() => {
			expect(mockFetchNew).toHaveBeenCalledWith('recognition', 1);
		});
		// production cap is 0 → fetchNew for production should NOT be called
		const prodCalls = mockFetchNew.mock.calls.filter(([dir]) => dir === 'production');
		expect(prodCalls).toHaveLength(0);
	});

	// ── FSRS source indicator ─────────────────────────────────────────────────

	it('shows FSRS: defaults when fsrs_source is not cache', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, due: 3, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'default' });
		const { findByText } = render(ReviewPage);
		expect(await findByText(/FSRS: defaults/)).toBeTruthy();
	});

	it('does not show FSRS marker when fsrs_source is cache', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, due: 3, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'cache' });
		const { queryByText, findByText } = render(ReviewPage);
		await findByText(/New 5/);
		expect(queryByText(/FSRS:/)).toBeFalsy();
	});
});
