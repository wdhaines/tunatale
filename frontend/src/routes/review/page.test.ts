/**
 * Tests for the unified /review route (single fetch from /review-queue).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor, screen } from '@testing-library/svelte';
import ReviewPage from './+page.svelte';
import type { ReviewQueueItem } from '$lib/api';

vi.mock('$lib/api', () => ({
	api: {
		fetchQueueStats: vi.fn(),
		fetchReviewQueue: vi.fn(),
		submitDrill: vi.fn(),
	}
}));

import { api } from '$lib/api';
const mockFetchQueueStats = vi.mocked(api.fetchQueueStats);
const mockFetchReviewQueue = vi.mocked(api.fetchReviewQueue);
const mockSubmitDrill = vi.mocked(api.submitDrill);
import { makeReviewQueueItem } from '../../test/factories';

beforeEach(() => {
	vi.clearAllMocks();
	mockFetchQueueStats.mockResolvedValue({ new: 0, due: 0, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' });
	mockFetchReviewQueue.mockResolvedValue({ queue: [] });
	mockSubmitDrill.mockResolvedValue({ new_due_date: '2026-04-25', new_state: 'review' });
});

describe('review/+page.svelte', () => {
	it('shows loading state initially', () => {
		mockFetchReviewQueue.mockReturnValue(new Promise(() => {}));
		const { container } = render(ReviewPage);
		expect(container.textContent).toContain('Loading');
	});

	it('shows done state when queue is empty', async () => {
		const { findByText } = render(ReviewPage);
		expect(await findByText(/Done for today/)).toBeTruthy();
	});

	it('renders queue items from single fetch', async () => {
		const item = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
		const { findByText } = render(ReviewPage);
		expect(await findByText('okno')).toBeTruthy();
	});

	it('shows direction badge for current card', async () => {
		const item = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
		const { findByText } = render(ReviewPage);
		expect(await findByText(/Recognition/i)).toBeTruthy();
	});

	it('calls submitDrill with correct direction and id on rating', async () => {
		const item = makeReviewQueueItem({ id: 5, text: 'voda', translation: 'water', direction: 'recognition' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
		const { findByRole } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(mockSubmitDrill).toHaveBeenCalledWith(5, 'recognition', 'good', expect.any(Number));
		// Verify timeMs is within reasonable range (0-60000)
		const timeMs = mockSubmitDrill.mock.calls[0][3];
		expect(timeMs).toBeGreaterThanOrEqual(0);
		expect(timeMs).toBeLessThanOrEqual(60000);
	});

	it('calls submitDrill with production direction for production cards', async () => {
		const item = makeReviewQueueItem({ id: 7, text: 'banka', translation: 'bank', direction: 'production', word_count: 2 });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
		const { findByRole } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(mockSubmitDrill).toHaveBeenCalledWith(7, 'production', 'good', expect.any(Number));
	});

	it('advances to next card after rating', async () => {
		const item1 = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
		const item2 = makeReviewQueueItem({ id: 3, text: 'hiša', translation: 'house', direction: 'recognition' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item1, item2] });
		const { findByRole, findByText } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText('hiša')).toBeTruthy();
	});

	it('answer is hidden on the next card after rating (no answer leak)', async () => {
		const item1 = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
		const item2 = makeReviewQueueItem({ id: 3, text: 'hiša', translation: 'house', direction: 'recognition' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item1, item2] });
		const { findByRole, queryByRole } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByRole('button', { name: 'Show' })).toBeTruthy();
		expect(queryByRole('button', { name: 'Good' })).toBeNull();
	});

	it('shows done after last card rated', async () => {
		const item = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
		const { findByRole, findByText } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText(/Done for today/)).toBeTruthy();
	});

	it('shows error when fetch rejects', async () => {
		mockFetchReviewQueue.mockRejectedValue(new Error('Network error'));
		const { findByText } = render(ReviewPage);
		expect(await findByText('Network error')).toBeTruthy();
	});

	it('shows error and stays on card when submitDrill rejects', async () => {
		const item = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
		mockSubmitDrill.mockRejectedValue(new Error('Submit failed'));
		const { findByRole, findByText } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText('Submit failed')).toBeTruthy();
	});

	it('production word_count=1 with image_url shows img element', async () => {
		const item = makeReviewQueueItem({ id: 10, text: 'banka', translation: 'bank', direction: 'production', word_count: 1, image_url: 'banka.jpg' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
		const { findByRole } = render(ReviewPage);
		await findByRole('button', { name: 'Show' });
		expect(screen.queryByRole('img')).not.toBeNull();
	});

	it('production word_count>1 shows L1 translation as prompt', async () => {
		const item = makeReviewQueueItem({ id: 11, text: 'dober dan', translation: 'good day', direction: 'production', word_count: 2 });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
		const { findByText } = render(ReviewPage);
		expect(await findByText('good day')).toBeTruthy();
	});

	it('production word_count=1 without image_url shows L1 translation as prompt', async () => {
		const item = makeReviewQueueItem({ id: 12, text: 'banka', translation: 'bank', direction: 'production', word_count: 1, image_url: null });
		mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
		const { findByText } = render(ReviewPage);
		expect(await findByText('bank')).toBeTruthy();
	});

	// ── queue-stats breakdown display ───────────────────────────────────────

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

	it('does not show source label when cap_source is cache (freshly synced from anki)', async () => {
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

	// ── FSRS source indicator ──────────────────────────────────────────────

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

	// ── client-side sibling burying ────────────────────────────────────────
	// Backend buries collocations whose last_review=today at queue-build time, but
	// the queue is fetched once on mount; without client-side burying the OTHER
	// direction of a just-reviewed collocation would appear next in the cached queue.

	it('skips sibling direction after rating: prašič rec then prašič prod', async () => {
		const prasicRec = makeReviewQueueItem({ id: 202, text: 'prašič', translation: 'pig', direction: 'recognition' });
		const prasicProd = makeReviewQueueItem({ id: 202, text: 'prašič', translation: 'pig', direction: 'production' });
		const vlakRec = makeReviewQueueItem({ id: 251, text: 'vlak', translation: 'train', direction: 'recognition' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [prasicRec, prasicProd, vlakRec] });
		const { findByRole, findByText } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		// Sibling prasicProd is buried; vlak (recognition) is shown next.
		expect(await findByText('vlak')).toBeTruthy();
	});

	it('shows done when all remaining queue items are siblings of just-reviewed card', async () => {
		const recA = makeReviewQueueItem({ id: 100, text: 'okno', translation: 'window', direction: 'recognition' });
		const prodA = makeReviewQueueItem({ id: 100, text: 'okno', translation: 'window', direction: 'production' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [recA, prodA] });
		const { findByRole, findByText } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText(/Done for today/)).toBeTruthy();
	});

	it('progress counter excludes buried siblings from total', async () => {
		const recA = makeReviewQueueItem({ id: 100, text: 'okno', translation: 'window', direction: 'recognition' });
		const prodA = makeReviewQueueItem({ id: 100, text: 'okno', translation: 'window', direction: 'production' });
		const recB = makeReviewQueueItem({ id: 200, text: 'vrata', translation: 'door', direction: 'recognition' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [recA, prodA, recB] });
		const { findByRole, findByText } = render(ReviewPage);
		// Before rating: 1 / 3 (full queue)
		expect(await findByText('1 / 3')).toBeTruthy();
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		// After rating recA (id=100), prodA is buried → effective total drops to 2
		expect(await findByText('2 / 2')).toBeTruthy();
	});

	it('does not bury non-siblings with different collocation ids', async () => {
		const itemA = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
		const itemB = makeReviewQueueItem({ id: 2, text: 'vrata', translation: 'door', direction: 'recognition' });
		mockFetchReviewQueue.mockResolvedValue({ queue: [itemA, itemB] });
		const { findByRole, findByText } = render(ReviewPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText('vrata')).toBeTruthy();
	});

	// ── deferred learning ──────────────────────────────────────

	describe('deferred learning', () => {
		it('defers learning card with future due_at to end of queue', async () => {
			const future = new Date(Date.now() + 600_000).toISOString(); // +10 min
			mockSubmitDrill.mockResolvedValue({
				new_due_date: '2026-04-25', new_state: 'learning', due_at: future, left: 1002,
			});
			const item1 = makeReviewQueueItem({ id: 1, text: 'okno', direction: 'recognition' });
			const item2 = makeReviewQueueItem({ id: 3, text: 'hiša', direction: 'recognition' });
			mockFetchReviewQueue.mockResolvedValue({ queue: [item1, item2] });
			const { findByRole, findByText } = render(ReviewPage);
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));
			// Card 2 shown next; card 1 deferred (not buried — should resurface later)
			expect(await findByText('hiša')).toBeTruthy();
		});

		it('graduated card (new_state=review) does NOT resurface', async () => {
			mockSubmitDrill.mockResolvedValue({ new_due_date: '2026-04-25', new_state: 'review' });
			const item = makeReviewQueueItem({ id: 1, text: 'okno', direction: 'recognition' });
			mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
			const { findByRole, findByText } = render(ReviewPage);
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));
			expect(await findByText(/Done for today/)).toBeTruthy();
		});

		it('resurfaces deferred card on next rating after due_at passes', async () => {
			const t0 = Date.parse('2026-05-04T10:00:00Z');
			const dueAt = new Date(t0 + 5 * 60_000); // +5 min
			const t1 = t0 + 10 * 60_000; // +10 min — past dueAt

			const dateNowSpy = vi.spyOn(Date, 'now').mockReturnValue(t0);

			mockSubmitDrill
				.mockResolvedValueOnce({
					new_due_date: '2026-04-25', new_state: 'learning',
					due_at: dueAt.toISOString(), left: 1002,
				})
				.mockResolvedValueOnce({ new_due_date: '2026-04-25', new_state: 'review' });

			const item1 = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
			const item2 = makeReviewQueueItem({ id: 3, text: 'hiša', translation: 'house', direction: 'recognition' });
			mockFetchReviewQueue.mockResolvedValue({ queue: [item1, item2] });

			const { findByRole, findByText } = render(ReviewPage);

			// Rate card 1 → deferred
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));
			expect(await findByText('hiša')).toBeTruthy();

			// Advance the clock past dueAt, then rate card 2
			dateNowSpy.mockReturnValue(t1);
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));

			// reapDeferred runs after card 2's rate; card 1 should resurface
			expect(await findByText('okno')).toBeTruthy();

			dateNowSpy.mockRestore();
		});
	});
});
