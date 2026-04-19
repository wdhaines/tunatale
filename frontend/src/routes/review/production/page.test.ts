/**
 * Tests for /review/production route.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import ProductionPage from './+page.svelte';
import type { SRSItemDetail } from '$lib/api';

vi.mock('$lib/api', () => ({
	api: {
		fetchDue: vi.fn(),
		submitDrill: vi.fn()
	}
}));

import { api } from '$lib/api';
const mockFetchDue = vi.mocked(api.fetchDue);
const mockSubmitDrill = vi.mocked(api.submitDrill);

const makeItem = (overrides: Partial<SRSItemDetail> = {}): SRSItemDetail => ({
	id: 1,
	text: 'banka',
	translation: 'bank',
	word_count: 1,
	state: 'review',
	due_date: '2026-04-18',
	stability: 5.0,
	difficulty: 4.0,
	reps: 3,
	lapses: 0,
	last_review: '2026-04-10',
	language_code: 'sl',
	image_url: '/api/media/banka.jpg',
	directions: {
		recognition: { state: 'review', due_date: '2026-04-18', stability: 5.0, difficulty: 4.0, reps: 3, lapses: 0, last_review: '2026-04-10', anki_card_id: null },
		production: { state: 'review', due_date: '2026-04-18', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, anki_card_id: null }
	},
	...overrides
});

beforeEach(() => {
	vi.clearAllMocks();
	mockSubmitDrill.mockResolvedValue({ new_due_date: '2026-04-25', new_state: 'review' });
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('review/production/+page.svelte', () => {
	it('calls fetchDue with production direction', async () => {
		mockFetchDue.mockResolvedValue([]);
		render(ProductionPage);
		await waitFor(() => expect(mockFetchDue).toHaveBeenCalledWith('production'));
	});

	it('shows image prompt for single-word item with image', async () => {
		mockFetchDue.mockResolvedValue([makeItem({ word_count: 1, image_url: '/api/media/banka.jpg' })]);
		const { container } = await waitFor(async () => {
			const result = render(ProductionPage);
			await result.findByRole('img');
			return result;
		});
		const img = container.querySelector('img');
		expect(img?.getAttribute('src')).toBe('/api/media/banka.jpg');
	});

	it('shows L1 gloss for multi-word item instead of image', async () => {
		mockFetchDue.mockResolvedValue([makeItem({ word_count: 2, image_url: null, text: 'dober dan', translation: 'good day' })]);
		const { findByText } = render(ProductionPage);
		expect(await findByText('good day')).toBeTruthy();
	});

	it('shows empty-done state when queue is empty', async () => {
		mockFetchDue.mockResolvedValue([]);
		const { findByText } = render(ProductionPage);
		expect(await findByText(/Done for today/)).toBeTruthy();
	});

	it('reveals L2 text after Show clicked', async () => {
		mockFetchDue.mockResolvedValue([makeItem({ text: 'banka', image_url: '/api/media/banka.jpg' })]);
		const { findByRole, findByText } = render(ProductionPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		expect(await findByText('banka')).toBeTruthy();
	});

	it('calls submitDrill with production direction on rating', async () => {
		mockFetchDue.mockResolvedValue([makeItem({ id: 7 })]);
		const { findByRole } = render(ProductionPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Easy' }));
		expect(mockSubmitDrill).toHaveBeenCalledWith(7, 'production', 'easy');
	});

	it('shows done state after rating last card', async () => {
		mockFetchDue.mockResolvedValue([makeItem()]);
		const { findByRole, findByText } = render(ProductionPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText(/Done for today/)).toBeTruthy();
	});

	it('shows error when fetchDue rejects', async () => {
		mockFetchDue.mockRejectedValue(new Error('Network failure'));
		const { findByText } = render(ProductionPage);
		expect(await findByText('Network failure')).toBeTruthy();
	});

	it('shows error and stays on card when submitDrill rejects', async () => {
		mockFetchDue.mockResolvedValue([makeItem({ id: 7 })]);
		mockSubmitDrill.mockRejectedValue(new Error('Rate failed'));
		const { findByRole, findByText } = render(ProductionPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText('Rate failed')).toBeTruthy();
	});
});
