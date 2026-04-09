/**
 * Tests for WordSpan.svelte — word rating widget.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import WordSpan from './WordSpan.svelte';
import type { WordToken } from './api';

function makeWord(overrides: Partial<WordToken> = {}): WordToken {
	return {
		surface: 'zdravo',
		lemma: 'zdravo',
		srs_state: 'new',
		...overrides
	};
}

describe('WordSpan', () => {
	it('renders the word surface text', () => {
		const { getByRole } = render(WordSpan, { props: { word: makeWord({ surface: 'hvala' }) } });
		expect(getByRole('button').textContent).toBe('hvala');
	});

	it('cycles rating from null → hard on first click', async () => {
		const onRatingChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), rating: null, onRatingChange }
		});
		await fireEvent.click(getByRole('button'));
		expect(onRatingChange).toHaveBeenCalledWith('zdravo', 'hard');
	});

	it('cycles rating hard → easy on second click', async () => {
		const onRatingChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), rating: 'hard', onRatingChange }
		});
		await fireEvent.click(getByRole('button'));
		expect(onRatingChange).toHaveBeenCalledWith('zdravo', 'easy');
	});

	it('cycles rating easy → null on third click', async () => {
		const onRatingChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), rating: 'easy', onRatingChange }
		});
		await fireEvent.click(getByRole('button'));
		expect(onRatingChange).toHaveBeenCalledWith('zdravo', null);
	});

	it('handles Enter key the same as click', async () => {
		const onRatingChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), rating: null, onRatingChange }
		});
		await fireEvent.keyDown(getByRole('button'), { key: 'Enter' });
		expect(onRatingChange).toHaveBeenCalledWith('zdravo', 'hard');
	});

	it('handles Space key the same as click', async () => {
		const onRatingChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), rating: null, onRatingChange }
		});
		await fireEvent.keyDown(getByRole('button'), { key: ' ' });
		expect(onRatingChange).toHaveBeenCalledWith('zdravo', 'hard');
	});

	it('ignores other keys', async () => {
		const onRatingChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), rating: null, onRatingChange }
		});
		await fireEvent.keyDown(getByRole('button'), { key: 'Tab' });
		expect(onRatingChange).not.toHaveBeenCalled();
	});

	it('does not throw when onRatingChange is not provided', async () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord() }
		});
		await expect(fireEvent.click(getByRole('button'))).resolves.not.toThrow();
	});

	it('shows word-new class for unknown srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'unknown' }), rating: null }
		});
		expect(getByRole('button').className).toContain('word-new');
	});

	it('shows word-new class for new srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'new' }), rating: null }
		});
		expect(getByRole('button').className).toContain('word-new');
	});

	it('shows word-learning class for learning srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'learning' }), rating: null }
		});
		expect(getByRole('button').className).toContain('word-learning');
	});

	it('shows word-learning class for relearning srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'relearning' }), rating: null }
		});
		expect(getByRole('button').className).toContain('word-learning');
	});

	it('shows word-review class for review srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'review' }), rating: null }
		});
		expect(getByRole('button').className).toContain('word-review');
	});

	it('shows word-hard class when rating is hard', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), rating: 'hard' }
		});
		expect(getByRole('button').className).toContain('word-hard');
	});

	it('shows word-easy class when rating is easy', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), rating: 'easy' }
		});
		expect(getByRole('button').className).toContain('word-easy');
	});

	it('shows flagged title when rating is set', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), rating: 'hard' }
		});
		expect(getByRole('button').getAttribute('title')).toContain('hard');
	});

	it('shows srs_state as title when no rating', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'learning' }), rating: null }
		});
		expect(getByRole('button').getAttribute('title')).toBe('learning');
	});

	it('updates colorClass and title reactively when rating prop changes', async () => {
		const { getByRole, rerender } = render(WordSpan, {
			props: { word: makeWord(), rating: null }
		});

		expect(getByRole('button').className).toContain('word-new');

		await rerender({ word: makeWord(), rating: 'hard' });

		await waitFor(() => {
			expect(getByRole('button').className).toContain('word-hard');
			expect(getByRole('button').getAttribute('title')).toContain('hard');
		});
	});
});
