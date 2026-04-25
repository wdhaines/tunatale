/**
 * Tests for DrillCard shared flashcard component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import DrillCard from './DrillCard.svelte';
import type { SRSItemDetail } from '$lib/api';

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
		production: { state: 'new', due_date: '2026-04-18', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, anki_card_id: null }
	},
	...overrides
});

describe('DrillCard', () => {
	describe('recognition mode (promptSide=L2)', () => {
		it('shows L2 text as prompt', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByText } = render(DrillCard, { item: makeItem(), promptSide: 'L2', onRate });
			expect(await findByText('banka')).toBeTruthy();
		});

		it('shows Show button before reveal', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole } = render(DrillCard, { item: makeItem(), promptSide: 'L2', onRate });
			expect(await findByRole('button', { name: 'Show' })).toBeTruthy();
		});

		it('hides translation before reveal', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const item = makeItem({ text: 'okno', translation: 'window' });
			const { container } = render(DrillCard, { item, promptSide: 'L2', onRate });
			expect(container.textContent).not.toContain('window');
		});

		it('shows L1 translation after Show clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, findByText } = render(DrillCard, { item: makeItem(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(await findByText('bank')).toBeTruthy();
		});

		it('shows all four rating buttons after Show clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, getByText } = render(DrillCard, { item: makeItem(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(getByText('Again')).toBeTruthy();
			expect(getByText('Hard')).toBeTruthy();
			expect(getByText('Good')).toBeTruthy();
			expect(getByText('Easy')).toBeTruthy();
		});

		it('calls onRate with "good" when Good clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole } = render(DrillCard, { item: makeItem(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));
			expect(onRate).toHaveBeenCalledWith('good');
		});

		it('calls onRate with "again" when Again clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole } = render(DrillCard, { item: makeItem(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Again' }));
			expect(onRate).toHaveBeenCalledWith('again');
		});

		it('calls onRate with "hard" when Hard clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole } = render(DrillCard, { item: makeItem(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Hard' }));
			expect(onRate).toHaveBeenCalledWith('hard');
		});

		it('calls onRate with "easy" when Easy clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole } = render(DrillCard, { item: makeItem(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Easy' }));
			expect(onRate).toHaveBeenCalledWith('easy');
		});
	});

	describe('production mode (promptSide=image)', () => {
		it('shows image when item has image_url', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { container } = render(DrillCard, { item: makeItem(), promptSide: 'image', onRate });
			const img = container.querySelector('img');
			expect(img).not.toBeNull();
			expect(img?.getAttribute('src')).toBe('/api/media/banka.jpg');
		});

		it('shows L1 translation as gloss fallback when no image_url', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByText } = render(DrillCard, { item: makeItem({ image_url: null }), promptSide: 'image', onRate });
			expect(await findByText('bank')).toBeTruthy();
		});

		it('shows L2 text as answer after Show clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, findByText } = render(DrillCard, { item: makeItem(), promptSide: 'image', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(await findByText('banka')).toBeTruthy();
		});
	});

	describe('L1 gloss mode (promptSide=L1)', () => {
		it('shows L1 translation as prompt', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByText } = render(DrillCard, { item: makeItem({ text: 'okno', translation: 'window' }), promptSide: 'L1', onRate });
			expect(await findByText('window')).toBeTruthy();
		});

		it('shows L2 text as answer after Show clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, findByText } = render(DrillCard, { item: makeItem({ text: 'okno', translation: 'window' }), promptSide: 'L1', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(await findByText('okno')).toBeTruthy();
		});
	});
});
