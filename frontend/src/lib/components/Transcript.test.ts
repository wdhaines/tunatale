/**
 * Tests for Transcript.svelte component.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import Transcript from './Transcript.svelte';
import type { LessonDetail, TranscriptData } from '$lib/api';

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
					collocation_lemma: null,
					collocation_translation: null
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
					collocation_lemma: 'dober dan',
					collocation_translation: 'good day'
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
					collocation_lemma: 'dober dan',
					collocation_translation: 'good day'
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
					collocation_lemma: null,
					collocation_translation: null
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

		it('collocation wrapper has no title attribute', () => {
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptWithCollocation })
			});
			const span = container.querySelector('.collocation-span') as HTMLElement;
			expect(span.getAttribute('title')).toBeNull();
		});

		it('collocation tooltip shows collocation_translation in DOM', () => {
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptWithCollocation })
			});
			const tooltip = container.querySelector('.collocation-span')!.closest('.tt-wrap')!.querySelector('[role="tooltip"]');
			expect(tooltip).not.toBeNull();
			expect(tooltip!.textContent).toContain('good day');
		});

		it('collocation tooltip shows state label when collocation_translation is null', () => {
			const noTranslationColl: TranscriptData = {
				lesson_id: 'l1',
				key_phrases: [],
				dialogue_lines: [{
					role: 'Petra',
					words: [
						{ surface: 'dober', lemma: 'dober', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: 1, collocation_start: true, collocation_srs_state: 'learning', collocation_lemma: 'dober dan', collocation_translation: null },
						{ surface: 'dan', lemma: 'dan', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: 1, collocation_start: false, collocation_srs_state: 'learning', collocation_lemma: 'dober dan', collocation_translation: null }
					]
				}]
			};
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: noTranslationColl })
			});
			const tooltip = container.querySelector('.collocation-span')!.closest('.tt-wrap')!.querySelector('[role="tooltip"]');
			expect(tooltip).not.toBeNull();
			expect(tooltip!.textContent).toContain('Learning');
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

	describe('collocation alt-key behavior (svelte:window listeners)', () => {
		// When altHeld=false: collocation Tooltip shows "good day"; word-level Tooltips are hidden.
		// When altHeld=true: collocation Tooltip is gone; word-level Tooltips appear inside the wrapper.
		// So we check for the collocation tooltip by its specific content ("good day").
		function hasCollocationTooltip(container: HTMLElement) {
			return Array.from(container.querySelectorAll('[role="tooltip"]')).some((el) =>
				el.textContent?.includes('good day')
			);
		}

		it('alt keydown hides collocation tooltip', async () => {
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptWithCollocation })
			});

			expect(hasCollocationTooltip(container)).toBe(true);

			await fireEvent.keyDown(window, { key: 'Alt', altKey: true });

			expect(hasCollocationTooltip(container)).toBe(false);
		});

		it('non-alt keydown does not hide collocation tooltip', async () => {
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptWithCollocation })
			});

			await fireEvent.keyDown(window, { key: 'Control' });

			expect(hasCollocationTooltip(container)).toBe(true);
		});

		it('alt keyup restores collocation tooltip', async () => {
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptWithCollocation })
			});

			await fireEvent.keyDown(window, { key: 'Alt', altKey: true });
			expect(hasCollocationTooltip(container)).toBe(false);

			await fireEvent.keyUp(window, { key: 'Alt' });
			expect(hasCollocationTooltip(container)).toBe(true);
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
								collocation_srs_state: state, collocation_lemma: 'dober dan',
								collocation_translation: null
							},
							{
								surface: 'dan', lemma: 'dan', srs_state: 'new', srs_item_id: null,
								translation: null, collocation_span_id: 1, collocation_start: false,
								collocation_srs_state: state, collocation_lemma: 'dober dan',
								collocation_translation: null
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

	describe('phrase creation — "+ New phrase" toggle and drag', () => {
		const transcriptForDrag: TranscriptData = {
			lesson_id: 'l1',
			key_phrases: [],
			dialogue_lines: [
				{
					role: 'Petra',
					words: [
						{ surface: 'centru', lemma: 'centru', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null },
						{ surface: 'mesta', lemma: 'mesto', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null },
						{ surface: 'hvala', lemma: 'hvala', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
					]
				}
			]
		};

		const transcriptTwoLines: TranscriptData = {
			lesson_id: 'l1',
			key_phrases: [],
			dialogue_lines: [
				{
					role: 'Petra',
					words: [
						{ surface: 'centru', lemma: 'centru', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null },
						{ surface: 'mesta', lemma: 'mesto', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
					]
				},
				{
					role: 'Ana',
					words: [
						{ surface: 'hvala', lemma: 'hvala', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
					]
				}
			]
		};

		it('renders a "+ New phrase" button', () => {
			const { getByText } = render(Transcript, { props: defaultProps({ transcript: transcriptForDrag }) });
			expect(getByText('+ New phrase')).toBeTruthy();
		});

		it('clicking "+ New phrase" button enables selection mode', async () => {
			const { getByText } = render(Transcript, { props: defaultProps({ transcript: transcriptForDrag }) });
			const btn = getByText('+ New phrase');
			await fireEvent.click(btn);
			// Once in selection mode the button should show a cancel label or the button is active
			expect(getByText('Cancel')).toBeTruthy();
		});

		it('pointerup without prior pointerdown does not show confirm bar', async () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: transcriptForDrag }) });
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
			// pointerUp fires without isDragging being set
			await fireEvent.pointerUp(mestaSpan);
			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();
		});

		it('pointermove without prior pointerdown does not show confirm bar', async () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: transcriptForDrag }) });
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
			await fireEvent.pointerMove(mestaSpan);
			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();
		});

		it('pointerdown on container (not on a word) does not start drag', async () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: transcriptForDrag }) });
			const wordsContainer = container.querySelector('.dialogue-words') as HTMLElement;
			// Fire directly on the container — resolveWordTarget returns null
			await fireEvent.pointerDown(wordsContainer);
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
			await fireEvent.pointerMove(mestaSpan);
			await fireEvent.pointerUp(mestaSpan);
			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();
		});

		it('drag: pointerdown + pointermove + pointerup over 2 words shows confirm bar', async () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: transcriptForDrag }) });
			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

			// Fire events directly on word spans so e.target resolves correctly
			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerMove(mestaSpan);
			await fireEvent.pointerUp(mestaSpan);

			expect(container.querySelector('.phrase-confirm-bar')).toBeTruthy();
			expect(container.querySelector('.phrase-confirm-bar')!.textContent).toContain('centru mesta');
		});

		it('drag with anchor == endpoint (single word) does not show confirm bar', async () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: transcriptForDrag }) });
			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;

			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerUp(centruSpan);

			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();
		});

		it('cross-line drag resets and shows no confirm bar', async () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: transcriptTwoLines }) });
			const centruSpan = container.querySelector('[data-line-index="0"][data-word-index="0"]') as HTMLElement;
			const hvalaSpan = container.querySelector('[data-line-index="1"][data-word-index="0"]') as HTMLElement;

			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerUp(hvalaSpan);

			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();
		});

		it('drag over a word with collocation_span_id aborts — no confirm bar', async () => {
			const transcriptWithExistingColl: TranscriptData = {
				lesson_id: 'l1',
				key_phrases: [],
				dialogue_lines: [
					{
						role: 'Petra',
						words: [
							{ surface: 'centru', lemma: 'centru', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: 5, collocation_start: true, collocation_srs_state: 'new', collocation_lemma: 'centru mesta', collocation_translation: null },
							{ surface: 'mesta', lemma: 'mesto', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: 5, collocation_start: false, collocation_srs_state: 'new', collocation_lemma: 'centru mesta', collocation_translation: null },
							{ surface: 'hvala', lemma: 'hvala', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
						]
					}
				]
			};
			const { container } = render(Transcript, { props: defaultProps({ transcript: transcriptWithExistingColl }) });
			// words 0 and 1 are inside a collocation-span wrapper, words rendered inside collocation
			// Try to drag from hvala (index 2) — but it can't overlap with collocation 5 unless we
			// start from inside the collocation. Start at word 0 (collocation), end at word 2.
			// Use the word-index data attributes on the inner WordSpan elements
			const word0Span = container.querySelector('[data-word-index="0"]') as HTMLElement;
			const hvalaSpan = container.querySelector('[data-word-index="2"]') as HTMLElement;

			await fireEvent.pointerDown(word0Span);
			await fireEvent.pointerMove(hvalaSpan);
			await fireEvent.pointerUp(hvalaSpan);

			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();
		});

		it('clicking Create fires onCreatePhrase with correct args', async () => {
			const onCreatePhrase = vi.fn();
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptForDrag, onCreatePhrase })
			});
			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerMove(mestaSpan);
			await fireEvent.pointerUp(mestaSpan);

			const createBtn = container.querySelector('.phrase-confirm-bar button.confirm-create') as HTMLElement;
			await fireEvent.click(createBtn);

			expect(onCreatePhrase).toHaveBeenCalledWith(
				expect.objectContaining({
					text: 'centru mesta',
					word_count: 2,
					translation: '',
					lineIndex: 0,
					startIdx: 0,
					endIdx: 1
				})
			);
		});

		it('clicking Cancel clears selection and fires no callback', async () => {
			const onCreatePhrase = vi.fn();
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptForDrag, onCreatePhrase })
			});
			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerMove(mestaSpan);
			await fireEvent.pointerUp(mestaSpan);

			const cancelBtn = container.querySelector('.phrase-confirm-bar button.confirm-cancel') as HTMLElement;
			await fireEvent.click(cancelBtn);

			expect(onCreatePhrase).not.toHaveBeenCalled();
			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();
		});

		it('selected words carry a word-selected highlight class during drag', async () => {
			const { container } = render(Transcript, { props: defaultProps({ transcript: transcriptForDrag }) });
			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerMove(mestaSpan);

			// Both centru (0) and mesta (1) should have word-selected
			expect(container.querySelector('[data-word-index="0"]')!.className).toContain('word-selected');
			expect(container.querySelector('[data-word-index="1"]')!.className).toContain('word-selected');
			// hvala (2) should not
			expect(container.querySelector('[data-word-index="2"]')!.className).not.toContain('word-selected');
		});

		it('selectionMode: first tap sets anchor, second tap shows confirm bar', async () => {
			const { container, getByText } = render(Transcript, { props: defaultProps({ transcript: transcriptForDrag }) });

			await fireEvent.click(getByText('+ New phrase'));

			// First tap: click word 0
			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
			await fireEvent.click(centruSpan);
			// No confirm bar yet after first tap
			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();

			// Second tap: click word 1
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
			await fireEvent.click(mestaSpan);
			// Now confirm bar should appear
			expect(container.querySelector('.phrase-confirm-bar')).toBeTruthy();
		});

		it('selectionMode: cross-line second tap resets anchor to new line, no confirm bar', async () => {
			const { container, getByText } = render(Transcript, { props: defaultProps({ transcript: transcriptTwoLines }) });

			await fireEvent.click(getByText('+ New phrase'));

			// First tap: line 0, word 0
			const centruSpan = container.querySelector('[data-line-index="0"][data-word-index="0"]') as HTMLElement;
			await fireEvent.click(centruSpan);
			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();

			// Second tap: line 1, word 0 (different line) — anchor resets to this word
			const hvalaSpan = container.querySelector('[data-line-index="1"][data-word-index="0"]') as HTMLElement;
			await fireEvent.click(hvalaSpan);
			// No confirm bar (anchor was reset, this is now the new first tap)
			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();
		});

		it('selectionMode: tapping same word twice (start===end) shows no confirm bar', async () => {
			const { container, getByText } = render(Transcript, { props: defaultProps({ transcript: transcriptForDrag }) });

			await fireEvent.click(getByText('+ New phrase'));

			// First tap: word 0
			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
			await fireEvent.click(centruSpan);
			// Second tap: same word 0
			await fireEvent.click(centruSpan);
			expect(container.querySelector('.phrase-confirm-bar')).toBeFalsy();
		});

		it('scene grouping: renders scene header from lesson natural_speed narrator+en phrases', () => {
			const lesson: LessonDetail = {
				id: 'l1',
				title: 'test',
				language_code: 'sl',
				key_phrases: [],
				sections: [
					{
						type: 'natural_speed',
						phrases: [
							{ text: 'Natural Speed', role: 'narrator', language_code: 'en', voice_id: 'v' },
							{ text: 'At the City Information Office', role: 'narrator', language_code: 'en', voice_id: 'v' },
							{ text: 'zdravo', role: 'female-1', language_code: 'sl', voice_id: 'v' }
						]
					},
					{ type: 'slow_speed', phrases: [] },
					{ type: 'translated', phrases: [] }
				]
			};
			const transcriptData: TranscriptData = {
				lesson_id: 'l1',
				key_phrases: [],
				dialogue_lines: [
					{
						role: 'female-1',
						words: [
							{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
						]
					}
				]
			};
			const { container, getByText } = render(Transcript, {
				props: defaultProps({ transcript: transcriptData, lesson })
			});
			const sceneHeader = container.querySelector('.scene-header');
			expect(sceneHeader).not.toBeNull();
			expect(sceneHeader!.textContent).toContain('At the City Information Office');
			// Scene header text should NOT be rendered as a dialogue line
			expect(container.querySelectorAll('.dialogue-line').length).toBe(1);
			expect(getByText('zdravo')).toBeTruthy();
		});

		it('scene grouping: multiple scenes each produce a scene header', () => {
			const lesson: LessonDetail = {
				id: 'l1',
				title: 'test',
				language_code: 'sl',
				key_phrases: [],
				sections: [
					{
						type: 'natural_speed',
						phrases: [
							{ text: 'Natural Speed', role: 'narrator', language_code: 'en', voice_id: 'v' },
							{ text: 'At the Airport', role: 'narrator', language_code: 'en', voice_id: 'v' },
							{ text: 'zdravo', role: 'female-1', language_code: 'sl', voice_id: 'v' },
							{ text: 'At the Hotel', role: 'narrator', language_code: 'en', voice_id: 'v' },
							{ text: 'hvala', role: 'female-1', language_code: 'sl', voice_id: 'v' }
						]
					},
					{ type: 'slow_speed', phrases: [] },
					{ type: 'translated', phrases: [] }
				]
			};
			const transcriptData: TranscriptData = {
				lesson_id: 'l1',
				key_phrases: [],
				dialogue_lines: [
					{
						role: 'female-1',
						words: [
							{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
						]
					},
					{
						role: 'female-1',
						words: [
							{ surface: 'hvala', lemma: 'hvala', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
						]
					}
				]
			};
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptData, lesson })
			});
			const sceneHeaders = container.querySelectorAll('.scene-header');
			expect(sceneHeaders.length).toBe(2);
			expect(sceneHeaders[0].textContent).toContain('At the Airport');
			expect(sceneHeaders[1].textContent).toContain('At the Hotel');
		});

		it('scene grouping: does not show the section title (Natural Speed) as a scene header', () => {
			const lesson: LessonDetail = {
				id: 'l1',
				title: 'test',
				language_code: 'sl',
				key_phrases: [],
				sections: [
					{
						type: 'natural_speed',
						phrases: [
							{ text: 'Natural Speed', role: 'narrator', language_code: 'en', voice_id: 'v' },
							{ text: 'zdravo', role: 'female-1', language_code: 'sl', voice_id: 'v' }
						]
					},
					{ type: 'slow_speed', phrases: [] },
					{ type: 'translated', phrases: [] }
				]
			};
			const transcriptData: TranscriptData = {
				lesson_id: 'l1',
				key_phrases: [],
				dialogue_lines: [
					{
						role: 'female-1',
						words: [
							{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
						]
					}
				]
			};
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptData, lesson })
			});
			const sceneHeaders = container.querySelectorAll('.scene-header');
			expect(sceneHeaders.length).toBe(0);
		});

		it('progressive disclosure: slow text hidden by default, shown when Slow toggle is enabled', async () => {
			const lesson: LessonDetail = {
				id: 'l1',
				title: 'test',
				language_code: 'sl',
				key_phrases: [],
				sections: [
					{
						type: 'natural_speed',
						phrases: [
							{ text: 'zdravo', role: 'female-1', language_code: 'sl', voice_id: 'v' }
						]
					},
					{
						type: 'slow_speed',
						phrases: [
							{ text: 'zdra...vo', role: 'female-1', language_code: 'sl', voice_id: 'v' }
						]
					},
					{ type: 'translated', phrases: [] }
				]
			};
			const transcriptData: TranscriptData = {
				lesson_id: 'l1',
				key_phrases: [],
				dialogue_lines: [
					{
						role: 'female-1',
						words: [
							{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
						]
					}
				]
			};
			const { container, getByText, queryByText } = render(Transcript, {
				props: defaultProps({ transcript: transcriptData, lesson })
			});
			// Slow text not shown by default
			expect(queryByText('zdra...vo')).toBeFalsy();
			// Toggle Slow
			await fireEvent.click(getByText('Slow'));
			expect(container.querySelector('.line-slow')).not.toBeNull();
			expect(container.querySelector('.line-slow')!.textContent).toContain('zdra...vo');
		});

		it('progressive disclosure: translation text hidden by default, shown when Translation toggle is enabled', async () => {
			const lesson: LessonDetail = {
				id: 'l1',
				title: 'test',
				language_code: 'sl',
				key_phrases: [],
				sections: [
					{
						type: 'natural_speed',
						phrases: [
							{ text: 'zdravo', role: 'female-1', language_code: 'sl', voice_id: 'v' }
						]
					},
					{ type: 'slow_speed', phrases: [] },
					{
						type: 'translated',
						phrases: [
							{ text: 'zdravo', role: 'female-1', language_code: 'sl', voice_id: 'v' },
							{ text: 'Hello', role: 'narrator', language_code: 'en', voice_id: 'v' }
						]
					}
				]
			};
			const transcriptData: TranscriptData = {
				lesson_id: 'l1',
				key_phrases: [],
				dialogue_lines: [
					{
						role: 'female-1',
						words: [
							{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'new', srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
						]
					}
				]
			};
			const { container, getByText, queryByText } = render(Transcript, {
				props: defaultProps({ transcript: transcriptData, lesson })
			});
			expect(queryByText('Hello')).toBeFalsy();
			await fireEvent.click(getByText('Translation'));
			expect(container.querySelector('.line-translation')).not.toBeNull();
			expect(container.querySelector('.line-translation')!.textContent).toContain('Hello');
		});

		it('mark-listened bar is rendered inside a sticky footer', () => {
			const { container } = render(Transcript, { props: defaultProps() });
			const footer = container.querySelector('.listen-footer');
			expect(footer).not.toBeNull();
			expect(footer!.querySelector('.listen-btn')).not.toBeNull();
		});

		it('translation input can be updated and is included in onCreatePhrase call', async () => {
			const onCreatePhrase = vi.fn();
			const { container } = render(Transcript, {
				props: defaultProps({ transcript: transcriptForDrag, onCreatePhrase })
			});
			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerMove(mestaSpan);
			await fireEvent.pointerUp(mestaSpan);

			const translationInput = container.querySelector('.phrase-translation-input') as HTMLInputElement;
			await fireEvent.input(translationInput, { target: { value: 'city centre' } });

			await fireEvent.click(container.querySelector('.phrase-confirm-bar button.confirm-create') as HTMLElement);

			expect(onCreatePhrase).toHaveBeenCalledWith(
				expect.objectContaining({ translation: 'city centre' })
			);
		});
	});
});
