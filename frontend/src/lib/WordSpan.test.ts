/**
 * Tests for WordSpan.svelte — per-word SRS state widget.
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
		srs_item_id: null,
		translation: null,
		collocation_span_id: null,
		collocation_start: false,
		...overrides
	};
}

describe('WordSpan', () => {
	it('renders the word surface text', () => {
		const { getByRole } = render(WordSpan, { props: { word: makeWord({ surface: 'hvala' }) } });
		expect(getByRole('button').textContent).toBe('hvala');
	});

	it('calls onStateChange with lemma and srs_item_id on click', async () => {
		const onStateChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ lemma: 'zdravo', srs_item_id: 42 }), onStateChange }
		});
		await fireEvent.click(getByRole('button'));
		expect(onStateChange).toHaveBeenCalledWith('zdravo', 42);
	});

	it('passes null srs_item_id when word has no card', async () => {
		const onStateChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_item_id: null }), onStateChange }
		});
		await fireEvent.click(getByRole('button'));
		expect(onStateChange).toHaveBeenCalledWith('zdravo', null);
	});

	it('handles Enter key the same as click', async () => {
		const onStateChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), onStateChange }
		});
		await fireEvent.keyDown(getByRole('button'), { key: 'Enter' });
		expect(onStateChange).toHaveBeenCalled();
	});

	it('handles Space key the same as click', async () => {
		const onStateChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), onStateChange }
		});
		await fireEvent.keyDown(getByRole('button'), { key: ' ' });
		expect(onStateChange).toHaveBeenCalled();
	});

	it('ignores other keys', async () => {
		const onStateChange = vi.fn();
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord(), onStateChange }
		});
		await fireEvent.keyDown(getByRole('button'), { key: 'Tab' });
		expect(onStateChange).not.toHaveBeenCalled();
	});

	it('does not throw when onStateChange is not provided', async () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord() }
		});
		await expect(fireEvent.click(getByRole('button'))).resolves.not.toThrow();
	});

	it('shows word-new class for unknown srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'unknown' }) }
		});
		expect(getByRole('button').className).toContain('word-new');
	});

	it('shows word-new class for new srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'new' }) }
		});
		expect(getByRole('button').className).toContain('word-new');
	});

	it('shows word-learning class for learning srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'learning' }) }
		});
		expect(getByRole('button').className).toContain('word-learning');
	});

	it('shows word-learning class for relearning srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'relearning' }) }
		});
		expect(getByRole('button').className).toContain('word-learning');
	});

	it('shows word-review class for review srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'review' }) }
		});
		expect(getByRole('button').className).toContain('word-review');
	});

	it('shows word-known class for known srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'known' }) }
		});
		expect(getByRole('button').className).toContain('word-known');
	});

	it('shows word-ignored class for suspended srs_state', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'suspended' }) }
		});
		expect(getByRole('button').className).toContain('word-ignored');
	});

	it('shows srs_state as title', () => {
		const { getByRole } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'learning' }) }
		});
		expect(getByRole('button').getAttribute('title')).toBe('learning');
	});

	it('updates colorClass reactively when srs_state changes', async () => {
		const { getByRole, rerender } = render(WordSpan, {
			props: { word: makeWord({ srs_state: 'new' }) }
		});

		expect(getByRole('button').className).toContain('word-new');

		await rerender({ word: makeWord({ srs_state: 'learning' }) });

		await waitFor(() => {
			expect(getByRole('button').className).toContain('word-learning');
		});
	});
});
