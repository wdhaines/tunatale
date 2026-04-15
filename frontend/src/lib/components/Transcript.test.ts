/**
 * Tests for Transcript.svelte component.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import Transcript from './Transcript.svelte';
import type { TranscriptData } from '$lib/api';

const baseTranscript: TranscriptData = {
	lesson_id: 'l1',
	key_phrases: [],
	dialogue_lines: []
};

const transcriptWithPhrases: TranscriptData = {
	lesson_id: 'l1',
	key_phrases: [
		{ phrase: 'dober dan', translation: 'good day' },
		{ phrase: 'hvala', translation: 'thank you' }
	],
	dialogue_lines: []
};

const transcriptWithDialogue: TranscriptData = {
	lesson_id: 'l1',
	key_phrases: [],
	dialogue_lines: [
		{
			role: 'Petra',
			words: [
				{
					surface: 'zdravo',
					lemma: 'zdravo',
					srs_state: 'new',
					srs_item_id: null,
					translation: null,
					collocation_span_id: null,
					collocation_start: false,
					collocation_srs_state: null,
					collocation_lemma: null
				}
			]
		}
	]
};

const transcriptWithCollocation: TranscriptData = {
	lesson_id: 'l1',
	key_phrases: [],
	dialogue_lines: [
		{
			role: 'Petra',
			words: [
				{
					surface: 'dober',
					lemma: 'dober',
					srs_state: 'new',
					srs_item_id: null,
					translation: 'good',
					collocation_span_id: 99,
					collocation_start: true,
					collocation_srs_state: 'learning',
					collocation_lemma: 'dober dan'
				},
				{
					surface: 'dan',
					lemma: 'dan',
					srs_state: 'new',
					srs_item_id: null,
					translation: 'day',
					collocation_span_id: 99,
					collocation_start: false,
					collocation_srs_state: 'learning',
					collocation_lemma: 'dober dan'
				},
				{
					surface: 'hvala',
					lemma: 'hvala',
					srs_state: 'unknown',
					srs_item_id: null,
					translation: null,
					collocation_span_id: null,
					collocation_start: false,
					collocation_srs_state: null,
					collocation_lemma: null
				}
			]
		}
	]
};

function defaultProps(overrides = {}) {
	return {
		transcript: baseTranscript,
		isListened: false,
		listenLoading: false,
		listenResult: null,
		error: '',
		onStateChange: vi.fn(),
		onMarkListened: vi.fn(),
		...overrides
	};
}

describe('Transcript', () => {
	it('renders Mark as Listened button', () => {
		const { getByText } = render(Transcript, { props: defaultProps() });
		expect(getByText('Mark as Listened')).toBeTruthy();
	});

	it('shows ✓ Listened when isListened is true', () => {
		const { getByText } = render(Transcript, { props: defaultProps({ isListened: true }) });
		expect(getByText('✓ Listened')).toBeTruthy();
	});

	it('shows Registering… when listenLoading is true', () => {
		const { getByText } = render(Transcript, { props: defaultProps({ listenLoading: true }) });
		expect(getByText('Registering…')).toBeTruthy();
	});

	it('calls onMarkListened when button is clicked', async () => {
		const onMarkListened = vi.fn();
		const { getByText } = render(Transcript, { props: defaultProps({ onMarkListened }) });
		await fireEvent.click(getByText('Mark as Listened'));
		expect(onMarkListened).toHaveBeenCalled();
	});

	it('renders key phrases when present', () => {
		const { getByText } = render(Transcript, {
			props: defaultProps({ transcript: transcriptWithPhrases })
		});
		expect(getByText('Key Phrases')).toBeTruthy();
		expect(getByText('dober dan')).toBeTruthy();
		expect(getByText('good day')).toBeTruthy();
	});

	it('does not render Key Phrases section when empty', () => {
		const { queryByText } = render(Transcript, { props: defaultProps() });
		expect(queryByText('Key Phrases')).toBeFalsy();
	});

	it('renders dialogue lines when present', () => {
		const { getByText } = render(Transcript, {
			props: defaultProps({ transcript: transcriptWithDialogue })
		});
		expect(getByText('Dialogue')).toBeTruthy();
		expect(getByText('Petra')).toBeTruthy();
	});

	it('does not render Dialogue section when empty', () => {
		const { queryByText } = render(Transcript, { props: defaultProps() });
		expect(queryByText('Dialogue')).toBeFalsy();
	});

	it('shows listen confirmation when listenResult is set and no error', () => {
		const { getByText } = render(Transcript, {
			props: defaultProps({ listenResult: { registered: 3 }, error: '' })
		});
		expect(getByText(/3.*words tracked/i)).toBeTruthy();
	});

	it('shows singular word when registered is 1', () => {
		const { getByText } = render(Transcript, {
			props: defaultProps({ listenResult: { registered: 1 }, error: '' })
		});
		expect(getByText(/1 word tracked/i)).toBeTruthy();
	});

	it('hides listen confirmation when error is set', () => {
		const { queryByText } = render(Transcript, {
			props: defaultProps({ listenResult: { registered: 3 }, error: 'something went wrong' })
		});
		expect(queryByText(/words tracked/i)).toBeFalsy();
	});

	it('shows listen confirmation after listenResult changes from null to non-null (reactive update)', async () => {
		const { rerender, findByText, queryByText } = render(Transcript, {
			props: defaultProps({ listenResult: null })
		});
		expect(queryByText(/words tracked/i)).toBeFalsy();

		await rerender(defaultProps({ listenResult: { registered: 2 }, error: '' }));

		await waitFor(() => {
			expect(queryByText(/2.*words tracked/i)).toBeTruthy();
		});
	});

	it('shows singular word after listenResult changes to registered=1', async () => {
		const { rerender, findByText } = render(Transcript, {
			props: defaultProps({ listenResult: null })
		});

		await rerender(defaultProps({ listenResult: { registered: 1 }, error: '' }));

		expect(await findByText(/1 word tracked/i)).toBeTruthy();
	});

	it('wraps collocation tokens in a collocation-span container', () => {
		const { container } = render(Transcript, {
			props: defaultProps({ transcript: transcriptWithCollocation })
		});
		const spans = container.querySelectorAll('.collocation-span');
		expect(spans.length).toBe(1);
	});

	it('collocation-span contains both tokens', () => {
		const { container } = render(Transcript, {
			props: defaultProps({ transcript: transcriptWithCollocation })
		});
		const span = container.querySelector('.collocation-span');
		expect(span).not.toBeNull();
		expect(span!.textContent).toContain('dober');
		expect(span!.textContent).toContain('dan');
	});

	it('word outside collocation is not inside a collocation-span', () => {
		const { container } = render(Transcript, {
			props: defaultProps({ transcript: transcriptWithCollocation })
		});
		// 'hvala' should not be inside .collocation-span
		const spans = container.querySelectorAll('.collocation-span');
		for (const span of spans) {
			expect(span.textContent).not.toContain('hvala');
		}
	});

	describe('collocation click behavior', () => {
		it('plain click on collocation wrapper fires onCollocationStateChange', async () => {
			const onCollocationStateChange = vi.fn();
			const { container } = render(Transcript, {
				props: defaultProps({
					transcript: transcriptWithCollocation,
					onCollocationStateChange
				})
			});
			const span = container.querySelector('.collocation-span') as HTMLElement;
			await fireEvent.click(span);
			expect(onCollocationStateChange).toHaveBeenCalledWith('dober dan', 99, 'learning');
		});

		it('Enter key on collocation wrapper fires onCollocationStateChange', async () => {
			const onCollocationStateChange = vi.fn();
			const { container } = render(Transcript, {
				props: defaultProps({
					transcript: transcriptWithCollocation,
					onCollocationStateChange
				})
			});
			const span = container.querySelector('.collocation-span') as HTMLElement;
			await fireEvent.keyDown(span, { key: 'Enter' });
			expect(onCollocationStateChange).toHaveBeenCalledWith('dober dan', 99, 'learning');
		});

		it('plain click inside collocation does not fire word-level onStateChange', async () => {
			const onStateChange = vi.fn();
			const onCollocationStateChange = vi.fn();
			const { getByText } = render(Transcript, {
				props: defaultProps({
					transcript: transcriptWithCollocation,
					onStateChange,
					onCollocationStateChange
				})
			});
			await fireEvent.click(getByText('dober'));
			expect(onStateChange).not.toHaveBeenCalled();
			expect(onCollocationStateChange).toHaveBeenCalled();
		});

		it('Alt+click inside collocation fires word-level onStateChange', async () => {
			const onStateChange = vi.fn();
			const onCollocationStateChange = vi.fn();
			const { getByText } = render(Transcript, {
				props: defaultProps({
					transcript: transcriptWithCollocation,
					onStateChange,
					onCollocationStateChange
				})
			});
			await fireEvent.click(getByText('dober'), { altKey: true });
			expect(onStateChange).toHaveBeenCalledWith('dober', null);
		});

		it('plain click on word outside collocation fires word-level onStateChange', async () => {
			const onStateChange = vi.fn();
			const onCollocationStateChange = vi.fn();
			const { getByText } = render(Transcript, {
				props: defaultProps({
					transcript: transcriptWithCollocation,
					onStateChange,
					onCollocationStateChange
				})
			});
			await fireEvent.click(getByText('hvala'));
			expect(onStateChange).toHaveBeenCalledWith('hvala', null);
			expect(onCollocationStateChange).not.toHaveBeenCalled();
		});

		it('collocation wrapper has state-based background class', () => {
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptWithCollocation })
			});
			const span = container.querySelector('.collocation-span') as HTMLElement;
			expect(span.className).toContain('coll-bg-learning');
		});

		it('collocation wrapper has role=button and is keyboard-reachable', () => {
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptWithCollocation })
			});
			const span = container.querySelector('.collocation-span') as HTMLElement;
			expect(span.getAttribute('role')).toBe('button');
			expect(span.getAttribute('tabindex')).toBe('0');
		});

		it('Space key on collocation wrapper fires onCollocationStateChange', async () => {
			const onCollocationStateChange = vi.fn();
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptWithCollocation, onCollocationStateChange })
			});
			await fireEvent.keyDown(container.querySelector('.collocation-span') as HTMLElement, { key: ' ' });
			expect(onCollocationStateChange).toHaveBeenCalled();
		});

		it('other keys on collocation wrapper do not fire', async () => {
			const onCollocationStateChange = vi.fn();
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptWithCollocation, onCollocationStateChange })
			});
			await fireEvent.keyDown(container.querySelector('.collocation-span') as HTMLElement, { key: 'Tab' });
			expect(onCollocationStateChange).not.toHaveBeenCalled();
		});
	});

	describe('collocation background colors', () => {
		function makeCollTranscript(state: string): TranscriptData {
			return {
				lesson_id: 'l1',
				key_phrases: [],
				dialogue_lines: [
					{
						role: 'Petra',
						words: [
							{
								surface: 'dober', lemma: 'dober', srs_state: 'new', srs_item_id: null,
								translation: null, collocation_span_id: 1, collocation_start: true,
								collocation_srs_state: state, collocation_lemma: 'dober dan'
							},
							{
								surface: 'dan', lemma: 'dan', srs_state: 'new', srs_item_id: null,
								translation: null, collocation_span_id: 1, collocation_start: false,
								collocation_srs_state: state, collocation_lemma: 'dober dan'
							}
						]
					}
				]
			};
		}

		it('review state → coll-bg-review', () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: makeCollTranscript('review') }) });
			expect(container.querySelector('.collocation-span')!.className).toContain('coll-bg-review');
		});

		it('known state → coll-bg-known', () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: makeCollTranscript('known') }) });
			expect(container.querySelector('.collocation-span')!.className).toContain('coll-bg-known');
		});

		it('suspended state → coll-bg-ignored', () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: makeCollTranscript('suspended') }) });
			expect(container.querySelector('.collocation-span')!.className).toContain('coll-bg-ignored');
		});

		it('ignored state → coll-bg-ignored', () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: makeCollTranscript('ignored') }) });
			expect(container.querySelector('.collocation-span')!.className).toContain('coll-bg-ignored');
		});

		it('relearning state → coll-bg-learning', () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: makeCollTranscript('relearning') }) });
			expect(container.querySelector('.collocation-span')!.className).toContain('coll-bg-learning');
		});

		it('unknown state → coll-bg-new (default)', () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: makeCollTranscript('exotic') }) });
			expect(container.querySelector('.collocation-span')!.className).toContain('coll-bg-new');
		});
	});
});
