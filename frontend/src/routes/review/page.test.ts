/**
 * Tests for the unified /review route (single fetch from /review-queue).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor, screen } from '@testing-library/svelte';
import ReviewPage from './+page.svelte';
import type { ReviewQueueItem } from '$lib/api';

// Mock onMount from svelte - must be before component import
vi.mock('svelte', () => {
	return {
		onMount: vi.fn((fn: () => void) => fn())
	};
});

vi.mock('$lib/api', () => ({
	api: {
		fetchQueueStats: vi.fn(),
		fetchReviewQueue: vi.fn(),
		submitDrill: vi.fn()
	}
}));

import { api } from '$lib/api';
const mockFetchQueueStats = vi.mocked(api.fetchQueueStats);
const mockFetchReviewQueue = vi.mocked(api.fetchReviewQueue);
const mockSubmitDrill = vi.mocked(api.submitDrill);
import { makeReviewQueueItem } from '../../test/factories';

beforeEach(() => {
	vi.clearAllMocks();
	mockFetchQueueStats.mockResolvedValue({ new: 0, learning: 0, review: 0, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' });
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

	// ── queue-stats breakdown display (Anki-style widget) ──────────────

	it('shows Anki-style widget with three counts', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 7, learning: 5, review: 10, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'cache' });
		const { findByText } = render(ReviewPage);
		// Widget shows: 7 + 5 + 10
		expect(await findByText('7')).toBeTruthy();
		expect(await findByText('5')).toBeTruthy();
		expect(await findByText('10')).toBeTruthy();
	});

	it('shows source label when cap_source is not anki', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, learning: 2, review: 3, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' });
		const { findByText } = render(ReviewPage);
		expect(await findByText(/\(default\)/)).toBeTruthy();
	});

	it('does not show source label when cap_source is cache (freshly synced from anki)', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, learning: 2, review: 3, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'cache' });
		const { queryByText, findByText } = render(ReviewPage);
		await findByText('5');
		expect(queryByText(/\(cache\)/)).toBeFalsy();
	});

	it('shows source label when cap_source is config', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, learning: 2, review: 3, daily_new_cap: 20, cap_source: 'config', fsrs_source: 'default' });
		const { findByText } = render(ReviewPage);
		expect(await findByText(/\(config\)/)).toBeTruthy();
	});

	// ── FSRS source indicator ───────────────────────────────────────────

	it('shows FSRS: defaults when fsrs_source is not cache', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, learning: 2, review: 3, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'default' });
		const { findByText } = render(ReviewPage);
		expect(await findByText(/FSRS: defaults/)).toBeTruthy();
	});

	it('does not show FSRS marker when fsrs_source is cache', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, learning: 2, review: 3, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'cache' });
		const { queryByText, findByText } = render(ReviewPage);
		await findByText('5');
		expect(queryByText(/FSRS:/)).toBeFalsy();
	});

	// ── client-side sibling burying ────────────────────────────────────────────────

	it('shows FSRS: defaults when fsrs_source is not cache', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, learning: 2, review: 3, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'default' });
		const { findByText } = render(ReviewPage);
		expect(await findByText(/FSRS: defaults/)).toBeTruthy();
	});

	it('does not show FSRS marker when fsrs_source is cache', async () => {
		mockFetchQueueStats.mockResolvedValue({ new: 5, learning: 2, review: 3, daily_new_cap: 30, cap_source: 'cache', fsrs_source: 'cache' });
		const { queryByText, findByText } = render(ReviewPage);
		await findByText('5');
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

		it('surfaces deferred card when queue is exhausted, regardless of dueAt', async () => {
			// Anki parity: when the main queue is empty, intraday_ahead cards are
			// served at the tail of the iter even before their step elapses. So a
			// deferred card with dueAt in the future surfaces immediately when
			// nothing else is left to rate — no wall-clock timer needed.
			vi.useFakeTimers();
			const t0 = Date.parse('2026-05-04T10:00:00Z');
			vi.setSystemTime(t0);

			const dueAt = new Date(t0 + 60_000).toISOString(); // +60s, in the future
			mockSubmitDrill.mockResolvedValue({
				new_due_date: '2026-05-06', new_state: 'learning',
				due_at: dueAt, left: 1002,
			});
			const item = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
			mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
			const { findByRole, findByText } = render(ReviewPage);

			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Again' }));

			// No timer advance — card surfaces because queue exhausted.
			expect(await findByText('okno')).toBeTruthy();

			vi.useRealTimers();
		});

		it('does not surface deferred card before next rating, even after dueAt passes', async () => {
			// Anki parity: current_learning_cutoff only advances at answer time.
			// While the user idles, the deferred card stays in intraday_ahead and
			// does not interrupt the current card.
			vi.useFakeTimers();
			const t0 = Date.parse('2026-05-04T10:00:00Z');
			vi.setSystemTime(t0);

			const dueAt = new Date(t0 + 60_000).toISOString();
			mockSubmitDrill.mockResolvedValue({
				new_due_date: '2026-05-06', new_state: 'learning',
				due_at: dueAt, left: 1002,
			});
			const item1 = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
			const item2 = makeReviewQueueItem({ id: 3, text: 'hiša', translation: 'house', direction: 'recognition' });
			const item3 = makeReviewQueueItem({ id: 4, text: 'miza', translation: 'table', direction: 'recognition' });
			mockFetchReviewQueue.mockResolvedValue({ queue: [item1, item2, item3] });

			const { findByRole, findByText, queryByText } = render(ReviewPage);

			// Rate okno AGAIN → defer; hiša becomes current.
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Again' }));
			expect(await findByText('hiša')).toBeTruthy();

			// Advance the wall clock past dueAt without rating anything.
			vi.advanceTimersByTime(60_000 + 100);

			// Idle time alone must not surface the deferred card. hiša still current.
			expect(await findByText('hiša')).toBeTruthy();
			expect(queryByText('okno')).toBeNull();

			vi.useRealTimers();
		});

		it('refreshes stats on deferred branch (AGAIN on new card)', async () => {
			const future = new Date(Date.now() + 60_000).toISOString();
			mockSubmitDrill.mockResolvedValue({
				new_due_date: '2026-05-06', new_state: 'learning', due_at: future, left: 1002,
			});
			mockFetchQueueStats
				.mockResolvedValueOnce({ new: 1, learning: 0, review: 0, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' })
				.mockResolvedValueOnce({ new: 0, learning: 1, review: 0, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' });
			const item = makeReviewQueueItem({ id: 1, text: 'okno', direction: 'recognition' });
			mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
			const { findByRole, findByText } = render(ReviewPage);

			// Wait for initial stats to load
			await findByText('1'); // new:1

			// Rate AGAIN → deferred
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Again' }));

			// Stats should have been refetched with updated counts
			await waitFor(() => expect(findByText('0')).toBeTruthy()); // new:0
			await waitFor(() => expect(findByText('1')).toBeTruthy()); // learning:1
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

		it('resurfaces deferred card immediately after current, not at end of queue', async () => {
			vi.useFakeTimers();
			const t0 = Date.parse('2026-05-04T10:00:00Z');
			vi.setSystemTime(t0);

			// Card 1 will be deferred; cards 2,3,4 are review cards that follow
			const dueAt = new Date(t0 + 60_000).toISOString(); // +60s
			mockSubmitDrill.mockResolvedValue({
				new_due_date: '2026-05-06', new_state: 'learning',
				due_at: dueAt, left: 1002,
			});

			const item1 = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition' });
			const item2 = makeReviewQueueItem({ id: 3, text: 'hiša', translation: 'house', direction: 'recognition' });
			const item3 = makeReviewQueueItem({ id: 4, text: 'miza', translation: 'table', direction: 'recognition' });
			const item4 = makeReviewQueueItem({ id: 5, text: 'stol', translation: 'chair', direction: 'recognition' });
			mockFetchReviewQueue.mockResolvedValue({ queue: [item1, item2, item3, item4] });

			const { findByRole, findByText } = render(ReviewPage);

			// Rate card 1 (okno) → AGAIN → deferred with future dueAt
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Again' }));

			// Card 2 (hiša) should now be current
			expect(await findByText('hiša')).toBeTruthy();

			// Advance past dueAt so deferred card is ready
			vi.advanceTimersByTime(60_000 + 100);

			// Rate card 2 (hiša) → this triggers reapDeferred which should
			// insert the deferred okno right after current (hiša), NOT at end
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));

			// After rating hiša, the next card should be okno (deferred resurfaced),
			// NOT miza (which would be next if deferred was appended to end)
			expect(await findByText('okno')).toBeTruthy();
			expect(screen.queryByText('miza')).toBeNull();

			vi.useRealTimers();
		}, 10000);

		it('displays state badge with correct text and class', async () => {
			const item = makeReviewQueueItem({ id: 1, text: 'okno', state: 'learning', direction: 'recognition' });
			mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
			const { findByText } = render(ReviewPage);
			const badge = await findByText('learning');
			expect(badge).toBeTruthy();
			expect(badge.className).toContain('state-learning');
		});

		it('refetches queue stats after rating a card', async () => {
			const item = makeReviewQueueItem({ id: 1, text: 'okno', direction: 'recognition' });
			mockFetchReviewQueue.mockResolvedValue({ queue: [item] });
			const callsBefore = mockFetchQueueStats.mock.calls.length;
			const { findByRole } = render(ReviewPage);
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));
			// Should have refetched stats at least once after rating
			expect(mockFetchQueueStats.mock.calls.length).toBeGreaterThan(callsBefore);
		});

		// ── server-side learning card refetch ──────────────────────────────
		// When a server-side learning card's due_at elapses during the session,
		// and the local queue is exhausted, we should refetch the queue.

		it('tops up queue on mount when stats.learning > 0 and initial queue empty', async () => {
			// Initial state: empty queue, but stats show 1 learning card on server
			mockFetchQueueStats.mockResolvedValue({ new: 0, learning: 1, review: 0, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' });
			mockFetchReviewQueue
				.mockResolvedValueOnce({ queue: [] }) // initial fetch: empty
				.mockResolvedValueOnce({ queue: [makeReviewQueueItem({ id: 999, text: 'umor', translation: 'mood', direction: 'production', state: 'learning' })] }); // refetch

			const { findByText, findByRole } = render(ReviewPage);

			// topUpQueue() should have been called during onMount
			// The learning card should be in the queue now
			// Click "Show" to reveal the card text
			await findByRole('button', { name: 'Show' });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(await findByText('umor')).toBeTruthy();
			expect(mockFetchReviewQueue).toHaveBeenCalledTimes(2);
		});

		it('does not refetch queue when stats.learning = 0 even if queue exhausted', async () => {
			// Initial state: empty queue, stats show 0 learning
			mockFetchQueueStats.mockResolvedValue({ new: 0, learning: 0, review: 0, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' });
			mockFetchReviewQueue.mockResolvedValue({ queue: [] });

			const { findByText } = render(ReviewPage);

			// Wait for initial load
			expect(await findByText(/Done for today/)).toBeTruthy();

			// Should NOT have refetched the queue
			expect(mockFetchReviewQueue).toHaveBeenCalledTimes(1);
		});

		// ── learning-first ordering after topUpQueue ──────────────────────

		it('reapDeferred respects due_at order vs earlier-due learning cards in queue', async () => {
			// Anki parity: when a deferred card's timer elapses, it surfaces in
			// due_at order with other learning cards already in the queue. A
			// freshly-discovered earlier-due learning card (e.g. teden, due 02:36)
			// must come before a recently-deferred later-due card (e.g. žlica,
			// due 13:43) — not the other way round.
			vi.useFakeTimers();
			const t0 = Date.parse('2026-05-08T13:42:00Z');
			vi.setSystemTime(t0);

			// Initial queue: žlica (a learning card with stale earlier-than-now
			// due_at) plus a dummy review card to advance through.
			const zlica = makeReviewQueueItem({
				id: 1, text: 'žlica', state: 'learning', direction: 'production',
				directions: {
					recognition: { state: 'new', due_date: '2026-05-08', stability: 1, difficulty: 5, reps: 0, lapses: 0, last_review: null, anki_card_id: null },
					production: { state: 'learning', due_date: '2026-05-08', stability: 0.3, difficulty: 5, reps: 1, lapses: 0, last_review: '2026-05-08T13:00:00Z', anki_card_id: 100, due_at: '2026-05-08T13:00:00Z' },
				},
			});
			const dummy = makeReviewQueueItem({ id: 2, text: 'dummy', state: 'review', direction: 'recognition' });
			// teden — a learning card with an EARLIER due_at than žlica's deferred dueAt
			const teden = makeReviewQueueItem({
				id: 3, text: 'teden', state: 'relearning', direction: 'recognition',
				directions: {
					recognition: { state: 'relearning', due_date: '2026-05-07', stability: 0.1, difficulty: 5, reps: 1, lapses: 1, last_review: '2026-05-08T02:36:14Z', anki_card_id: 200, due_at: '2026-05-08T02:36:14Z' },
					production: { state: 'new', due_date: '2026-05-08', stability: 1, difficulty: 5, reps: 0, lapses: 0, last_review: null, anki_card_id: null },
				},
			});

			mockFetchReviewQueue
				.mockResolvedValueOnce({ queue: [zlica, dummy] })
				.mockResolvedValueOnce({ queue: [zlica, dummy, teden] }); // topUp brings teden

			mockFetchQueueStats
				.mockResolvedValueOnce({ new: 0, learning: 1, review: 1, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' })
				.mockResolvedValueOnce({ new: 0, learning: 2, review: 1, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' })
				.mockResolvedValueOnce({ new: 0, learning: 2, review: 0, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' });

			// Rate žlica AGAIN → due_at = t0+60s (deferred); dummy GOOD (review)
			mockSubmitDrill
				.mockResolvedValueOnce({
					new_due_date: '2026-05-08', new_state: 'learning',
					due_at: new Date(t0 + 60_000).toISOString(), left: 1002,
				})
				.mockResolvedValueOnce({ new_due_date: '2026-05-09', new_state: 'review' });

			const { findByRole, findByText } = render(ReviewPage);

			// Rate žlica AGAIN → goes to deferred
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Again' }));

			// Now on dummy
			expect(await findByText('dummy')).toBeTruthy();

			// Advance past žlica's 60s deferred timer
			vi.advanceTimersByTime(60_000 + 100);

			// Rate dummy GOOD → triggers refreshStats (learning=2 > local=1)
			// → topUpQueue brings teden into queue
			// → reapDeferred fires for žlica
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));

			// teden's due_at (02:36) is earlier than žlica's deferred dueAt (13:43).
			// Anki would serve teden first. Buggy behavior: žlica appears (because
			// reapDeferred splices at queue[index] without considering due_at order).
			expect(await findByText('teden')).toBeTruthy();

			vi.useRealTimers();
		}, 10000);

		it('inserts server learning cards before remaining review cards after topUpQueue', async () => {
			// Initial queue: two review cards, no learning
			const reviewA = makeReviewQueueItem({ id: 1, text: 'okno', translation: 'window', direction: 'recognition', state: 'review' });
			const reviewB = makeReviewQueueItem({ id: 3, text: 'hiša', translation: 'house', direction: 'recognition', state: 'review' });
			const learningCard = makeReviewQueueItem({ id: 999, text: 'umor', translation: 'mood', direction: 'production', state: 'learning' });
			mockFetchQueueStats
				.mockResolvedValueOnce({ new: 0, learning: 0, review: 2, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' })
				.mockResolvedValueOnce({ new: 0, learning: 1, review: 1, daily_new_cap: 20, cap_source: 'default', fsrs_source: 'default' });
			mockFetchReviewQueue
				.mockResolvedValueOnce({ queue: [reviewA, reviewB] })
				.mockResolvedValueOnce({ queue: [learningCard, reviewB] });

			const { findByRole, findByText } = render(ReviewPage);

			// Rate reviewA → triggers refreshStats → sees learning:1 > localLearningCount() → topUpQueue
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));

			// Now on reviewB. Rate it to advance to the learning card.
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));

			// Learning card should be next (inserted at index 2, after reviewB)
			await findByRole('button', { name: 'Show' });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(await findByText('umor')).toBeTruthy();
		});
	});
});
