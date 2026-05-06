import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import QueueStatsWidget from './QueueStatsWidget.svelte';
import type { QueueStats } from '$lib/api';

describe('QueueStatsWidget', () => {
	it('renders three numbers with correct color classes', () => {
		const stats: QueueStats = {
			new: 30,
			learning: 16,
			review: 164,
			daily_new_cap: 30,
			cap_source: 'default',
			fsrs_source: 'default'
		};

		const { container } = render(QueueStatsWidget, { stats });

		// Check that all three numbers are visible
		expect(screen.getByText('30')).toBeTruthy();
		expect(screen.getByText('16')).toBeTruthy();
		expect(screen.getByText('164')).toBeTruthy();

		// Check separators - there should be exactly 2 separator spans
		const separators = container.querySelectorAll('.separator');
		expect(separators.length).toBe(2);
		expect(separators[0].textContent).toBe('+');
		expect(separators[1].textContent).toBe('+');

		// Check color classes
		const newSpan = container.querySelector('.new');
		const learningSpan = container.querySelector('.learning');
		const reviewSpan = container.querySelector('.review');

		expect(newSpan?.classList.contains('new')).toBe(true);
		expect(learningSpan?.classList.contains('learning')).toBe(true);
		expect(reviewSpan?.classList.contains('review')).toBe(true);
	});

	it('applies correct Anki colors via CSS classes', () => {
		const stats: QueueStats = {
			new: 5,
			learning: 3,
			review: 10,
			daily_new_cap: 30,
			cap_source: 'default',
			fsrs_source: 'default'
		};

		render(QueueStatsWidget, { stats });

		const newSpan = document.querySelector('.new') as HTMLElement;
		const learningSpan = document.querySelector('.learning') as HTMLElement;
		const reviewSpan = document.querySelector('.review') as HTMLElement;

		// Check that elements have the correct classes (styles are applied via CSS)
		expect(newSpan?.classList.contains('new')).toBe(true);
		expect(learningSpan?.classList.contains('learning')).toBe(true);
		expect(reviewSpan?.classList.contains('review')).toBe(true);
	});

	it('handles zero values', () => {
		const stats: QueueStats = {
			new: 0,
			learning: 0,
			review: 0,
			daily_new_cap: 30,
			cap_source: 'default',
			fsrs_source: 'default'
		};

		render(QueueStatsWidget, { stats });

		// All zeros should be displayed
		const zeros = document.querySelectorAll('span');
		let zeroCount = 0;
		zeros.forEach((span) => {
			if (span.textContent === '0') zeroCount++;
		});
		expect(zeroCount).toBe(3);
	});
});
