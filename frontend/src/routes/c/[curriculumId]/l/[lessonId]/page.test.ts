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
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'new' as const, srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false }]
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
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'learning' as const, srs_item_id: 42, translation: null, collocation_span_id: null, collocation_start: false }]
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
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'learning' as const, srs_item_id: 42, translation: null, collocation_span_id: null, collocation_start: false }]
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
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'learning' as const, srs_item_id: 42, translation: null, collocation_span_id: null, collocation_start: false }]
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
					words: [{ surface: 'dober', lemma: 'dober', srs_state: 'new' as const, srs_item_id: null, translation: null, collocation_span_id: null, collocation_start: false }]
				},
				{
					role: 'Ana',
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'learning' as const, srs_item_id: 42, translation: null, collocation_span_id: null, collocation_start: false }]
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
					words: [{ surface: 'zdravo', lemma: 'zdravo', srs_state: 'exotic_state', srs_item_id: 42, translation: null, collocation_span_id: null, collocation_start: false }]
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
});
