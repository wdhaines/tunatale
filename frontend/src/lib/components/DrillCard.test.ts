/**
 * Tests for DrillCard shared flashcard component.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/svelte';
import DrillCard from './DrillCard.svelte';
import type { SRSItemDetail } from '$lib/api';
import { makeSRSItemDetail } from '../../test/factories';

describe('DrillCard', () => {
	describe('recognition direction', () => {
		const item = makeSRSItemDetail({
			text: 'dober dan',
			translation: 'good day',
			audio_url: '/api/media/sl_dober_dan.mp3',
			image_url: '/api/media/dober_dan.jpg',
			grammar: 'phrase, masc',
			note: 'common greeting',
		});

		it('front renders audio element with autoplay and Slovene text', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { container } = render(DrillCard, { item, direction: 'recognition', onRate });
			const audio = container.querySelector('audio');
			expect(audio).toBeTruthy();
			expect(audio?.getAttribute('autoplay')).toBe('');
			expect(audio?.getAttribute('src')).toBe('/api/media/sl_dober_dan.mp3');
			expect(container.textContent).toContain('dober dan');
		});

		it('front shows play button for manual replay', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { getByRole } = render(DrillCard, { item, direction: 'recognition', onRate });
			expect(getByRole('button', { name: 'Play audio' })).toBeTruthy();
		});

		it('front does NOT show image, English, grammar, note before reveal', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { container } = render(DrillCard, { item, direction: 'recognition', onRate });
			expect(container.textContent).not.toContain('good day');
			expect(container.textContent).not.toContain('common greeting');
			expect(container.querySelector('img')).toBeNull();
		});

		it('back stacks: Slovene still in DOM, <hr>, image, English, grammar, note', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, container } = render(DrillCard, { item, direction: 'recognition', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));

			// Slovene still visible
			expect(container.textContent).toContain('dober dan');
			// HR divider exists
			expect(container.querySelector('hr')).toBeTruthy();
			// Image shown
			expect(container.querySelector('img')).toBeTruthy();
			// English translation
			expect(container.textContent).toContain('good day');
			// Grammar shown
			expect(container.textContent).toContain('phrase, masc');
			// Note shown
			expect(container.textContent).toContain('common greeting');
		});

		it('back hides empty grammar/note divs', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const noGramNote = makeSRSItemDetail({
				text: 'hvala',
				translation: 'thank you',
				grammar: '',
				note: ''
			});
			const { findByRole, container } = render(DrillCard, { item: noGramNote, direction: 'recognition', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(container.querySelector('.gram')).toBeNull();
			expect(container.querySelector('.note')).toBeNull();
		});

		it('shows all four rating buttons after reveal', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, getByText } = render(DrillCard, { item, direction: 'recognition', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			expect(getByText('Again')).toBeTruthy();
			expect(getByText('Hard')).toBeTruthy();
			expect(getByText('Good')).toBeTruthy();
			expect(getByText('Easy')).toBeTruthy();
		});
	});

	describe('production direction', () => {
		const item = makeSRSItemDetail({
			text: 'dober dan',
			translation: 'good day',
			audio_url: '/api/media/sl_dober_dan.mp3',
			image_url: '/api/media/dober-dan.jpg',
			grammar: 'phrase',
			note: 'greeting',
		});

		it('front: image only (no text)', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { queryByText, container } = render(DrillCard, { item, direction: 'production', onRate });
			expect(container.querySelector('img')).toBeTruthy();
			expect(queryByText('dober dan')).toBeFalsy();
			expect(queryByText('good day')).toBeFalsy();
		});

		it('back: image stays on top after reveal, <hr>, then audio + Slovene + English + grammar + note', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { findByRole, container, findByText } = render(DrillCard, { item, direction: 'production', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));

			// Wait for back content to render
			await findByText('dober dan'); // Slovene text indicates back is shown

			// Image still visible AFTER reveal
			const img = container.querySelector('img');
			expect(img).toBeTruthy();
			expect(img?.getAttribute('src')).toBe('/api/media/dober-dan.jpg');

			// HR divider between front and back
			expect(container.querySelector('hr')).toBeTruthy();

			// Audio element on back
			const audios = container.querySelectorAll('audio');
			expect(audios.length).toBe(1);

			// Slovene and English visible
			expect(container.textContent).toContain('dober dan');
			expect(container.textContent).toContain('good day');

			// Grammar and note
			expect(container.textContent).toContain('phrase');
			expect(container.textContent).toContain('greeting');
		});

		it('front renders image only (no audio), falls back to translation when no image', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const { container } = render(DrillCard, { item, direction: 'production', onRate });
			expect(container.querySelector('audio')).toBeNull();
			expect(container.querySelector('img')).toBeTruthy();
		});

		it('front falls back to translation text when image_url is null', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const noImg = makeSRSItemDetail({ text: 'hvala', translation: 'thank you', image_url: null });
			const { findByText } = render(DrillCard, { item: noImg, direction: 'production', onRate });
			expect(await findByText('thank you')).toBeTruthy();
		});
	});

	describe('rating callbacks', () => {
		it('calls onRate("good") when Good clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const item = makeSRSItemDetail({});
			const { findByRole } = render(DrillCard, { item, direction: 'recognition', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Good' }));
			expect(onRate).toHaveBeenCalledWith('good', expect.any(Number));
		});

		it('calls onRate("again") when Again clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const item = makeSRSItemDetail({});
			const { findByRole } = render(DrillCard, { item, direction: 'recognition', onRate });
			await fireEvent.click(await findByRole('button', { name: 'Show' }));
			await fireEvent.click(await findByRole('button', { name: 'Again' }));
			expect(onRate).toHaveBeenCalledWith('again', expect.any(Number));
		});
	});

	describe('audio play button', () => {
		it('calls audioEl.play() when play button clicked', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const item = makeSRSItemDetail({
				audio_url: '/api/media/test.mp3',
			});
			const { getByRole, container } = render(DrillCard, { item, direction: 'recognition', onRate });
			const audio = container.querySelector('audio');
			expect(audio).toBeTruthy();
			const playMock = vi.fn().mockResolvedValue(undefined);
			if (audio) {
				audio.play = playMock;
			}
			await fireEvent.click(getByRole('button', { name: 'Play audio' }));
			expect(playMock).toHaveBeenCalled();
		});
	});

	describe('card with null audio_url', () => {
		it('renders cleanly without audio element or play button', async () => {
			const onRate = vi.fn().mockResolvedValue(undefined);
			const item = makeSRSItemDetail({
				audio_url: null,
			});
			const { container } = render(DrillCard, { item, direction: 'recognition', onRate });
			expect(container.querySelector('audio')).toBeNull();
			expect(container.querySelector('button[aria-label="Play audio"]')).toBeNull();
		});
	});
});
