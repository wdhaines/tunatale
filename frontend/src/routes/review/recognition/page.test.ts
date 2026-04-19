/**
 * Tests for /review/recognition route.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import RecognitionPage from './+page.svelte';
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

const makeItem = (id: number, text: string, translation: string): SRSItemDetail => ({
	id,
	text,
	translation,
	word_count: 1,
	state: 'review',
	due_date: '2026-04-18',
	stability: 5.0,
	difficulty: 4.0,
	reps: 3,
	lapses: 0,
	last_review: '2026-04-10',
	language_code: 'sl',
	image_url: null,
	directions: {
		recognition: { state: 'review', due_date: '2026-04-18', stability: 5.0, difficulty: 4.0, reps: 3, lapses: 0, last_review: '2026-04-10', anki_card_id: null },
		production: { state: 'new', due_date: '2026-04-18', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, anki_card_id: null }
	}
});

beforeEach(() => {
	vi.clearAllMocks();
	mockSubmitDrill.mockResolvedValue({ new_due_date: '2026-04-25', new_state: 'review' });
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('review/recognition/+page.svelte', () => {
	it('shows loading state initially', () => {
		mockFetchDue.mockReturnValue(new Promise(() => {}));
		const { container } = render(RecognitionPage);
		expect(container.textContent).toContain('Loading');
	});

	it('calls fetchDue with recognition direction', async () => {
		mockFetchDue.mockResolvedValue([]);
		render(RecognitionPage);
		await waitFor(() => expect(mockFetchDue).toHaveBeenCalledWith('recognition'));
	});

	it('shows L2 text as prompt from queue', async () => {
		mockFetchDue.mockResolvedValue([makeItem(1, 'okno', 'window')]);
		const { findByText } = render(RecognitionPage);
		expect(await findByText('okno')).toBeTruthy();
	});

	it('shows empty-done state when queue is empty', async () => {
		mockFetchDue.mockResolvedValue([]);
		const { findByText } = render(RecognitionPage);
		expect(await findByText(/Done for today/)).toBeTruthy();
	});

	it('reveals L1 translation after Show clicked', async () => {
		mockFetchDue.mockResolvedValue([makeItem(1, 'okno', 'window')]);
		const { findByRole, findByText } = render(RecognitionPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		expect(await findByText('window')).toBeTruthy();
	});

	it('calls submitDrill with recognition direction on rating', async () => {
		mockFetchDue.mockResolvedValue([makeItem(5, 'voda', 'water')]);
		const { findByRole } = render(RecognitionPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(mockSubmitDrill).toHaveBeenCalledWith(5, 'recognition', 'good');
	});

	it('advances to next card after rating', async () => {
		mockFetchDue.mockResolvedValue([
			makeItem(1, 'okno', 'window'),
			makeItem(2, 'voda', 'water')
		]);
		const { findByRole, findByText } = render(RecognitionPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText('voda')).toBeTruthy();
	});

	it('shows done state after rating last card', async () => {
		mockFetchDue.mockResolvedValue([makeItem(1, 'okno', 'window')]);
		const { findByRole, findByText } = render(RecognitionPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText(/Done for today/)).toBeTruthy();
	});

	it('shows error when fetchDue rejects', async () => {
		mockFetchDue.mockRejectedValue(new Error('Network error'));
		const { findByText } = render(RecognitionPage);
		expect(await findByText('Network error')).toBeTruthy();
	});

	it('shows error and stays on card when submitDrill rejects', async () => {
		mockFetchDue.mockResolvedValue([makeItem(1, 'okno', 'window')]);
		mockSubmitDrill.mockRejectedValue(new Error('Submit failed'));
		const { findByRole, findByText } = render(RecognitionPage);
		await fireEvent.click(await findByRole('button', { name: 'Show' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));
		expect(await findByText('Submit failed')).toBeTruthy();
	});
});
