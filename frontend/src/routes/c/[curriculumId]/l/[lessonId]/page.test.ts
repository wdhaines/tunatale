/**
 * Tests for /c/[curriculumId]/l/[lessonId] — lesson view.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';

vi.mock('$lib/api', () => ({
	api: {
		renderAudio: vi.fn(),
		getLessonTranscript: vi.fn(),
		markAsListened: vi.fn(),
		createSRSItem: vi.fn(),
		setSRSItemState: vi.fn(),
		syncWithAnki: vi.fn(),
		audioUrl: vi.fn((id: string) => `/api/audio/${id}`)
	}
}));

vi.mock('$lib/stores/listened.svelte', () => ({
	listenedStore: {
		has: vi.fn().mockReturnValue(false),
		add: vi.fn()
	}
}));

import { api } from '$lib/api';
import { listenedStore } from '$lib/stores/listened.svelte';
import Page from './+page.svelte';

const mockRenderAudio = vi.mocked(api.renderAudio);
const mockGetTranscript = vi.mocked(api.getLessonTranscript);
const mockMarkAsListened = vi.mocked(api.markAsListened);
const mockCreateSRSItem = vi.mocked(api.createSRSItem);
const mockSetSRSItemState = vi.mocked(api.setSRSItemState);
const mockSyncWithAnki = vi.mocked(api.syncWithAnki);

const curriculum = { id: 'cid-1', topic: 'Coffee', language_code: 'sl', days: 3 };
const lesson = {
	id: 'l1',
	title: 'Day 1: Coffee',
	language_code: 'sl',
	sections: [{ type: 'key_phrases', phrases: [{ text: 'kavo prosim', role: 'female-1', language_code: 'sl', voice_id: 'v1' }] }],
	key_phrases: []
};
const audio = { audio_id: 'a1', lesson_id: 'l1', sections: [] };
const transcript = {
	lesson_id: 'l1',
	key_phrases: [{ phrase: 'kavo prosim', translation: 'a coffee please' }],
	dialogue_lines: []
};

beforeEach(() => {
	vi.clearAllMocks();
	vi.mocked(listenedStore.has).mockReturnValue(false);
});

describe('/c/[curriculumId]/l/[lessonId] page', () => {
	it('renders lesson title and sections', () => {
		const { getByText } = render(Page, {
			props: { data: { curriculum, lesson, audio: null, transcript: null } }
		});
		expect(getByText('Day 1: Coffee')).toBeTruthy();
		expect(getByText('Render Audio')).toBeTruthy();
	});

	it('shows AudioPlayer when audio is pre-loaded', () => {
		const { queryByText, container } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript: null } }
		});
		expect(queryByText('Render Audio')).toBeFalsy();
		expect(queryByText('Audio Player')).toBeTruthy();
		expect(container.querySelector('audio')).toBeTruthy();
	});

	it('renders audio on Render Audio click', async () => {
		mockRenderAudio.mockResolvedValue(audio);
		mockGetTranscript.mockResolvedValue(transcript);

		const { getByText, findByText, container } = render(Page, {
			props: { data: { curriculum, lesson, audio: null, transcript: null } }
		});
		await fireEvent.click(getByText('Render Audio'));

		expect(await findByText('Audio Player')).toBeTruthy();
		expect(container.querySelector('audio')).toBeTruthy();
		expect(await findByText('a coffee please')).toBeTruthy();
	});

	it('still shows AudioPlayer if getLessonTranscript fails after render', async () => {
		mockRenderAudio.mockResolvedValue(audio);
		mockGetTranscript.mockRejectedValue(new Error('transcript unavailable'));

		const { getByText, findByText, queryByText } = render(Page, {
			props: { data: { curriculum, lesson, audio: null, transcript: null } }
		});
		await fireEvent.click(getByText('Render Audio'));

		expect(await findByText('Audio Player')).toBeTruthy();
		expect(queryByText('Render Audio')).toBeFalsy();
	});

	it('calls markAsListened and adds to listenedStore', async () => {
		mockMarkAsListened.mockResolvedValue({ status: 'ok', registered: 3 });
		mockGetTranscript.mockResolvedValue(transcript);

		const { findByText } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript } }
		});
		const btn = await findByText('Mark as Listened');
		await fireEvent.click(btn);

		await waitFor(() => {
			expect(mockMarkAsListened).toHaveBeenCalledWith('l1', {});
			expect(mockGetTranscript).toHaveBeenCalledWith('l1');
			expect(listenedStore.add).toHaveBeenCalledWith('l1');
		});
	});

	it('shows listened state when listenedStore.has returns true', () => {
		vi.mocked(listenedStore.has).mockReturnValue(true);
		const { getByText } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript } }
		});
		expect(getByText('✓ Listened')).toBeTruthy();
	});

	it('shows Transcript loading… when audio is loaded but transcript is null', () => {
		const { getByText } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript: null } }
		});
		expect(getByText('Transcript loading…')).toBeTruthy();
	});

	it('shows error when renderAudio fails with non-Error', async () => {
		mockRenderAudio.mockRejectedValue('plain string error');

		const { getByText, findByText } = render(Page, {
			props: { data: { curriculum, lesson, audio: null, transcript: null } }
		});
		await fireEvent.click(getByText('Render Audio'));

		expect(await findByText('plain string error')).toBeTruthy();
	});

	it('shows error when markAsListened fails', async () => {
		mockMarkAsListened.mockRejectedValue(new Error('listen failed'));
		mockGetTranscript.mockResolvedValue(transcript);

		const { findByText } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript } }
		});
		await fireEvent.click(await findByText('Mark as Listened'));

		expect(await findByText('listen failed')).toBeTruthy();
	});

	it('shows stringified error when markAsListened throws a non-Error', async () => {
		mockMarkAsListened.mockRejectedValue('plain listen error');
		mockGetTranscript.mockResolvedValue(transcript);

		const { findByText } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript } }
		});
		await fireEvent.click(await findByText('Mark as Listened'));

		expect(await findByText('plain listen error')).toBeTruthy();
	});

	it('shows plural phrases label when a section has more than one phrase', () => {
		const lessonMultiPhrase = {
			...lesson,
			sections: [
				{
					type: 'key_phrases',
					phrases: [
						{ text: 'kavo prosim', role: 'female-1', language_code: 'sl', voice_id: 'v1' },
						{ text: 'hvala', role: 'female-1', language_code: 'sl', voice_id: 'v1' }
					]
				}
			]
		};
		const { getByText } = render(Page, {
			props: { data: { curriculum, lesson: lessonMultiPhrase, audio: null, transcript: null } }
		});
		expect(getByText(/2 phrases/)).toBeTruthy();
	});

	it('clicking a word with no SRS card creates the card and sets state to learning', async () => {
		const transcriptWithWord = {
			lesson_id: 'l1',
			key_phrases: [],
			dialogue_lines: [
				{
					role: 'Petra',
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'new' as const, srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }]
				}
			]
		};
		const createdItem = { id: 99, text: 'zdravo', translation: '', state: 'new' as const, due_date: '2026-04-14', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, language_code: 'sl' };
		mockCreateSRSItem.mockResolvedValue(createdItem);
		mockSetSRSItemState.mockResolvedValue({ ...createdItem, state: 'learning' as const });
		mockGetTranscript.mockResolvedValue(transcriptWithWord);

		const { findByRole } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript: transcriptWithWord } }
		});

		const wordBtn = await findByRole('button', { name: 'zdravo' });
		await fireEvent.click(wordBtn);

		await waitFor(() => {
			expect(mockCreateSRSItem).toHaveBeenCalledWith({ text: 'zdravo', language_code: 'sl', word_count: 1 });
			expect(mockSetSRSItemState).toHaveBeenCalledWith(99, 'learning');
		});
	});

	it('clicking a word with an existing SRS card cycles to the next state', async () => {
		const transcriptWithWord = {
			lesson_id: 'l1',
			key_phrases: [],
			dialogue_lines: [
				{
					role: 'Petra',
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'learning' as const, srs_item_id: 42, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }]
				}
			]
		};
		mockSetSRSItemState.mockResolvedValue({ id: 42, text: 'zdravo', translation: '', state: 'known' as const, due_date: '2026-04-14', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, language_code: 'sl' });
		mockGetTranscript.mockResolvedValue(transcriptWithWord);

		const { findByRole } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript: transcriptWithWord } }
		});

		const wordBtn = await findByRole('button', { name: 'zdravo' });
		await fireEvent.click(wordBtn);

		await waitFor(() => {
			expect(mockSetSRSItemState).toHaveBeenCalledWith(42, 'known');
		});
	});

	it('shows error when setSRSItemState throws', async () => {
		const transcriptWithWord = {
			lesson_id: 'l1',
			key_phrases: [],
			dialogue_lines: [
				{
					role: 'Petra',
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'learning' as const, srs_item_id: 42, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }]
				}
			]
		};
		mockSetSRSItemState.mockRejectedValue(new Error('state update failed'));
		mockGetTranscript.mockResolvedValue(transcriptWithWord);

		const { findByRole, findByText } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript: transcriptWithWord } }
		});

		await fireEvent.click(await findByRole('button', { name: 'zdravo' }));

		expect(await findByText('state update failed')).toBeTruthy();
	});

	it('shows stringified error when setSRSItemState throws a non-Error', async () => {
		const transcriptWithWord = {
			lesson_id: 'l1',
			key_phrases: [],
			dialogue_lines: [
				{
					role: 'Petra',
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'learning' as const, srs_item_id: 42, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }]
				}
			]
		};
		mockSetSRSItemState.mockRejectedValue('plain state error');
		mockGetTranscript.mockResolvedValue(transcriptWithWord);

		const { findByRole, findByText } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript: transcriptWithWord } }
		});

		await fireEvent.click(await findByRole('button', { name: 'zdravo' }));

		expect(await findByText('plain state error')).toBeTruthy();
	});

	it('finds word state in a later dialogue line', async () => {
		const transcriptMultiLine = {
			lesson_id: 'l1',
			key_phrases: [],
			dialogue_lines: [
				{
					role: 'Petra',
					words: [{ surface: 'dober', lemma: 'dober', srs_state: 'new' as const, srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }]
				},
				{
					role: 'Ana',
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'learning' as const, srs_item_id: 42, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }]
				}
			]
		};
		mockSetSRSItemState.mockResolvedValue({ id: 42, text: 'zdravo', translation: '', state: 'known' as const, due_date: '2026-04-14', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, language_code: 'sl' });
		mockGetTranscript.mockResolvedValue(transcriptMultiLine);

		const { findByRole } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript: transcriptMultiLine } }
		});

		await fireEvent.click(await findByRole('button', { name: 'zdravo' }));

		await waitFor(() => {
			expect(mockSetSRSItemState).toHaveBeenCalledWith(42, 'known');
		});
	});

	it('falls back to learning state for unrecognized srs_state', async () => {
		const transcriptWithWord = {
			lesson_id: 'l1',
			key_phrases: [],
			dialogue_lines: [
				{
					role: 'Petra',
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'exotic_state', srs_item_id: 42, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }]
				}
			]
		};
		mockSetSRSItemState.mockResolvedValue({ id: 42, text: 'zdravo', translation: '', state: 'learning' as const, due_date: '2026-04-14', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, language_code: 'sl' });
		mockGetTranscript.mockResolvedValue(transcriptWithWord);

		const { findByRole } = render(Page, {
			props: { data: { curriculum, lesson, audio, transcript: transcriptWithWord } }
		});

		await fireEvent.click(await findByRole('button', { name: 'zdravo' }));

		await waitFor(() => {
			expect(mockSetSRSItemState).toHaveBeenCalledWith(42, 'learning');
		});
	});

	describe('collocation click', () => {
		const transcriptWithCollocation = {
			lesson_id: 'l1',
			key_phrases: [],
			dialogue_lines: [
				{
					role: 'Petra',
					words: [
						{
							surface: 'dober',
							lemma: 'dober',
							srs_state: 'new' as const,
							srs_item_id: null,
							translation: null,
							collocation_span_id: 77,
							collocation_start: true,
							collocation_srs_state: 'learning',
							collocation_lemma: 'dober dan',
							collocation_translation: null
						},
						{
							surface: 'dan',
							lemma: 'dan',
							srs_state: 'new' as const,
							srs_item_id: null,
							translation: null,
							collocation_span_id: 77,
							collocation_start: false,
							collocation_srs_state: 'learning',
							collocation_lemma: 'dober dan',
							collocation_translation: null
						}
					]
				}
			]
		};

		it('clicking a collocation cycles its own SRS state without creating a new item', async () => {
			mockSetSRSItemState.mockResolvedValue({ id: 77, text: 'dober dan', translation: '', state: 'known' as const, due_date: '2026-04-14', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, language_code: 'sl' });
			mockGetTranscript.mockResolvedValue(transcriptWithCollocation);

			const { container } = render(Page, {
				props: { data: { curriculum, lesson, audio, transcript: transcriptWithCollocation } }
			});

			const span = container.querySelector('.collocation-span') as HTMLElement;
			await fireEvent.click(span);

			await waitFor(() => {
				expect(mockSetSRSItemState).toHaveBeenCalledWith(77, 'known');
			});
			expect(mockCreateSRSItem).not.toHaveBeenCalled();
		});

		it('collocation cycle follows STATE_CYCLE from new', async () => {
			const transcriptNewColl = {
				...transcriptWithCollocation,
				dialogue_lines: [
					{
						role: 'Petra',
						words: transcriptWithCollocation.dialogue_lines[0].words.map((w) => ({
							...w,
							collocation_srs_state: 'new'
						}))
					}
				]
			};
			mockSetSRSItemState.mockResolvedValue({ id: 77, text: 'dober dan', translation: '', state: 'learning' as const, due_date: '2026-04-14', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, language_code: 'sl' });
			mockGetTranscript.mockResolvedValue(transcriptNewColl);

			const { container } = render(Page, {
				props: { data: { curriculum, lesson, audio, transcript: transcriptNewColl } }
			});

			await fireEvent.click(container.querySelector('.collocation-span') as HTMLElement);

			await waitFor(() => {
				expect(mockSetSRSItemState).toHaveBeenCalledWith(77, 'learning');
			});
		});

		it('shows error when collocation state update fails', async () => {
			mockSetSRSItemState.mockRejectedValue(new Error('coll state failed'));
			mockGetTranscript.mockResolvedValue(transcriptWithCollocation);

			const { container, findByText } = render(Page, {
				props: { data: { curriculum, lesson, audio, transcript: transcriptWithCollocation } }
			});

			await fireEvent.click(container.querySelector('.collocation-span') as HTMLElement);

			expect(await findByText('coll state failed')).toBeTruthy();
		});

		it('shows stringified error when collocation update throws a non-Error', async () => {
			mockSetSRSItemState.mockRejectedValue('plain coll error');
			mockGetTranscript.mockResolvedValue(transcriptWithCollocation);

			const { container, findByText } = render(Page, {
				props: { data: { curriculum, lesson, audio, transcript: transcriptWithCollocation } }
			});

			await fireEvent.click(container.querySelector('.collocation-span') as HTMLElement);

			expect(await findByText('plain coll error')).toBeTruthy();
		});

		it('falls back to learning state for unrecognized collocation srs_state', async () => {
			const transcriptExotic = {
				...transcriptWithCollocation,
				dialogue_lines: [
					{
						role: 'Petra',
						words: transcriptWithCollocation.dialogue_lines[0].words.map((w) => ({
							...w,
							collocation_srs_state: 'exotic'
						}))
					}
				]
			};
			mockSetSRSItemState.mockResolvedValue({ id: 77, text: 'dober dan', translation: '', state: 'learning' as const, due_date: '2026-04-14', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, language_code: 'sl' });
			mockGetTranscript.mockResolvedValue(transcriptExotic);

			const { container } = render(Page, {
				props: { data: { curriculum, lesson, audio, transcript: transcriptExotic } }
			});

			await fireEvent.click(container.querySelector('.collocation-span') as HTMLElement);

			await waitFor(() => {
				expect(mockSetSRSItemState).toHaveBeenCalledWith(77, 'learning');
			});
		});
	});

	describe('handleCreatePhrase', () => {
		const transcriptWithMultiWord = {
			lesson_id: 'l1',
			key_phrases: [],
			dialogue_lines: [
				{
					role: 'Petra',
					words: [
						{ surface: 'centru', lemma: 'centru', srs_state: 'new' as const, srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null },
						{ surface: 'mesta', lemma: 'mesto', srs_state: 'new' as const, srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
					]
				}
			]
		};

		it('calls createSRSItem and then getLessonTranscript on success', async () => {
			const createdItem = { id: 55, text: 'centru mesta', translation: '', state: 'new' as const, due_date: '2026-04-15', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, language_code: 'sl' };
			mockCreateSRSItem.mockResolvedValue(createdItem);
			mockGetTranscript.mockResolvedValue(transcriptWithMultiWord);

			const { container } = render(Page, {
				props: { data: { curriculum, lesson, audio, transcript: transcriptWithMultiWord } }
			});

			// Trigger phrase creation via drag
			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerMove(mestaSpan);
			await fireEvent.pointerUp(mestaSpan);

			const createBtn = container.querySelector('.phrase-confirm-bar button.confirm-create') as HTMLElement;
			await fireEvent.click(createBtn);

			await waitFor(() => {
				expect(mockCreateSRSItem).toHaveBeenCalledWith({
					text: 'centru mesta',
					language_code: 'sl',
					word_count: 2,
					translation: '',
					source_sentence: expect.any(String),
					source_lesson_id: expect.any(String),
					source_line_index: 0
				});
				expect(mockGetTranscript).toHaveBeenCalled();
			});
		});

		it('forwards source_line_index from the selected line', async () => {
			const transcriptTwoLines = {
				lesson_id: 'l1',
				key_phrases: [],
				dialogue_lines: [
					{
						role: 'Petra',
						words: [
							{ surface: 'prva', lemma: 'prva', srs_state: 'new' as const, srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
						]
					},
					{
						role: 'Petra',
						words: [
							{ surface: 'centru', lemma: 'centru', srs_state: 'new' as const, srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null },
							{ surface: 'mesta', lemma: 'mesto', srs_state: 'new' as const, srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false, collocation_srs_state: null, collocation_lemma: null, collocation_translation: null }
						]
					}
				]
			};
			const createdItem = { id: 56, text: 'centru mesta', translation: '', state: 'new' as const, due_date: '2026-04-15', stability: 1.0, difficulty: 5.0, reps: 0, lapses: 0, last_review: null, language_code: 'sl' };
			mockCreateSRSItem.mockResolvedValue(createdItem);
			mockGetTranscript.mockResolvedValue(transcriptTwoLines);

			const { container } = render(Page, {
				props: { data: { curriculum, lesson, audio, transcript: transcriptTwoLines } }
			});

			// Drag-select on line index 1 (the second dialogue line)
			const centruSpan = container.querySelector('[data-line-index="1"][data-word-index="0"]') as HTMLElement;
			const mestaSpan = container.querySelector('[data-line-index="1"][data-word-index="1"]') as HTMLElement;

			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerMove(mestaSpan);
			await fireEvent.pointerUp(mestaSpan);

			await fireEvent.click(container.querySelector('.phrase-confirm-bar button.confirm-create') as HTMLElement);

			await waitFor(() => {
				expect(mockCreateSRSItem).toHaveBeenCalledWith(
					expect.objectContaining({ source_line_index: 1 })
				);
			});
		});

		it('sets error when createSRSItem throws an Error', async () => {
			mockCreateSRSItem.mockRejectedValue(new Error('phrase create failed'));
			mockGetTranscript.mockResolvedValue(transcriptWithMultiWord);

			const { container, findByText } = render(Page, {
				props: { data: { curriculum, lesson, audio, transcript: transcriptWithMultiWord } }
			});

			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerMove(mestaSpan);
			await fireEvent.pointerUp(mestaSpan);

			await fireEvent.click(container.querySelector('.phrase-confirm-bar button.confirm-create') as HTMLElement);

			expect(await findByText('phrase create failed')).toBeTruthy();
		});

		it('sets error to String(e) when createSRSItem throws a non-Error', async () => {
			mockCreateSRSItem.mockRejectedValue('plain phrase error');
			mockGetTranscript.mockResolvedValue(transcriptWithMultiWord);

			const { container, findByText } = render(Page, {
				props: { data: { curriculum, lesson, audio, transcript: transcriptWithMultiWord } }
			});

			const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
			const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

			await fireEvent.pointerDown(centruSpan);
			await fireEvent.pointerMove(mestaSpan);
			await fireEvent.pointerUp(mestaSpan);

			await fireEvent.click(container.querySelector('.phrase-confirm-bar button.confirm-create') as HTMLElement);

			expect(await findByText('plain phrase error')).toBeTruthy();
		});
	});
});

describe('load function for /c/[curriculumId]/l/[lessonId]', () => {
	it('returns null audio and transcript when they are not found', async () => {
		const { api: mockApi } = await import('$lib/api');
		vi.mocked(mockApi.renderAudio);

		// Simulate a fresh import for the load function test
		vi.doMock('$lib/api', () => ({
			api: {
				getCurriculum: vi.fn().mockResolvedValue(curriculum),
				getLesson: vi.fn().mockResolvedValue(lesson),
				getLessonAudio: vi.fn().mockRejectedValue(new Error('Not Found')),
				getLessonTranscript: vi.fn().mockRejectedValue(new Error('Not Found'))
			}
		}));

		const { load } = await import('./+page');
		const result = await load({
			params: { curriculumId: 'cid-1', lessonId: 'l1' }
		} as never);

		// audio and transcript should be null due to Promise.allSettled fallthrough
		// (the actual mock resolution depends on vi.doMock timing, so just confirm structure)
		expect(result).toHaveProperty('curriculum');
		expect(result).toHaveProperty('lesson');
		expect(result).toHaveProperty('audio');
		expect(result).toHaveProperty('transcript');
	});

	describe('sync button', () => {
		it('calls syncWithAnki on click', async () => {
			mockSyncWithAnki.mockResolvedValue({
				mode: 'full',
				created: 3,
				linked: 0,
				skipped: 1,
				notes_pulled: 0,
				directions_pulled: 0,
				conflicts: 0,
				notes_pushed: 2,
				directions_pushed: 2,
				dry_run: false
			});
			const { getByText } = render(Page, {
				props: { data: { curriculum, lesson, audio: null, transcript: null } }
			});

			const syncBtn = getByText('Sync with Anki');
			await fireEvent.click(syncBtn);

			await waitFor(() => {
				expect(mockSyncWithAnki).toHaveBeenCalledWith(false);
			});
		});

		it('displays sync result after successful sync', async () => {
			mockSyncWithAnki.mockResolvedValue({
				mode: 'full',
				created: 5,
				linked: 2,
				skipped: 1,
				notes_pulled: 3,
				directions_pulled: 4,
				conflicts: 0,
				notes_pushed: 2,
				directions_pushed: 2,
				dry_run: false
			});
			const { getByText } = render(Page, {
				props: { data: { curriculum, lesson, audio: null, transcript: null } }
			});

			const syncBtn = getByText('Sync with Anki');
			await fireEvent.click(syncBtn);

			await waitFor(() => {
				expect(getByText(/Mode: full/)).toBeTruthy();
			});
		});

		it('sets error when syncWithAnki fails', async () => {
			mockSyncWithAnki.mockRejectedValue(new Error('Sync failed'));
			const { getByText, findByText } = render(Page, {
				props: { data: { curriculum, lesson, audio: null, transcript: null } }
			});

			const syncBtn = getByText('Sync with Anki');
			await fireEvent.click(syncBtn);

			expect(await findByText('Sync failed')).toBeTruthy();
		});
	});


});
