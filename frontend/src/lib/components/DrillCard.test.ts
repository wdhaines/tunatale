/**
 * Tests for DrillCard shared flashcard component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/svelte';
import DrillCard from './DrillCard.svelte';
import type { SRSItemDetail } from '$lib/api';
import { makeSRSItemDetail } from '../../test/factories';

describe('DrillCard', () => {
	describe('recognition mode (promptSide=L2)', () => {
		it('shows L2 text as prompt', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByText } = render(DrillCard, { item: makeSRSItemDetail(), promptSide: 'L2', onRate });
			expect(await findByText('banka')).toBeTruthy();
		});

		it('shows Show button before reveal', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole } = render(DrillCard, { item: makeSRSItemDetail(), promptSide: 'L2', onRate });
			expect(await findByRole('button', { name: 'Show' })).toBeTruthy();
		});

		it('hides translation before reveal', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const item = makeSRSItemDetail({ text: 'okno', translation: 'window' });
			const { container } = render(DrillCard, { item, promptSide: 'L2', onRate });
			expect(container.textContent).not.toContain('window');
		});

		it('shows L1 translation after Show clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, findByText } = render(DrillCard, { item: makeSRSItemDetail(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(await findByText('bank')).toBeTruthy();
		});

		it('shows all four rating buttons after Show clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, getByText } = render(DrillCard, { item: makeSRSItemDetail(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(getByText('Again')).toBeTruthy();
			expect(getByText('Hard')).toBeTruthy();
			expect(getByText('Good')).toBeTruthy();
			expect(getByText('Easy')).toBeTruthy();
		});

		it('calls onRate with "good" when Good clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole } = render(DrillCard, { item: makeSRSItemDetail(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));
			expect(onRate).toHaveBeenCalledWith('good');
		});

		it('calls onRate with "again" when Again clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole } = render(DrillCard, { item: makeSRSItemDetail(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Again' }));
			expect(onRate).toHaveBeenCalledWith('again');
		});

		it('calls onRate with "hard" when Hard clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole } = render(DrillCard, { item: makeSRSItemDetail(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Hard' }));
			expect(onRate).toHaveBeenCalledWith('hard');
		});

		it('calls onRate with "easy" when Easy clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole } = render(DrillCard, { item: makeSRSItemDetail(), promptSide: 'L2', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Easy' }));
			expect(onRate).toHaveBeenCalledWith('easy');
		});
	});

	describe('production mode (promptSide=image)', () => {
		it('shows image when item has image_url', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { container } = render(DrillCard, { item: makeSRSItemDetail(), promptSide: 'image', onRate });
			const img = screen.getByRole('img');
			expect(img).not.toBeNull();
			expect(img?.getAttribute('src')).toBe('/api/media/banka.jpg');
		});

		it('shows L1 translation as gloss fallback when no image_url', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByText } = render(DrillCard, { item: makeSRSItemDetail({ image_url: null }), promptSide: 'image', onRate });
			expect(await findByText('bank')).toBeTruthy();
		});

		it('shows L2 text as answer after Show clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, findByText } = render(DrillCard, { item: makeSRSItemDetail(), promptSide: 'image', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(await findByText('banka')).toBeTruthy();
		});
	});

	describe('L1 gloss mode (promptSide=L1)', () => {
		it('shows L1 translation as prompt', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByText } = render(DrillCard, { item: makeSRSItemDetail({ text: 'okno', translation: 'window' }), promptSide: 'L1', onRate });
			expect(await findByText('window')).toBeTruthy();
		});

		it('shows L2 text as answer after Show clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, findByText } = render(DrillCard, { item: makeSRSItemDetail({ text: 'okno', translation: 'window' }), promptSide: 'L1', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(await findByText('okno')).toBeTruthy();
		});
	});
});
