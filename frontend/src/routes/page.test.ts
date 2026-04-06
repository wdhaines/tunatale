/**
 * Component tests for the main +page.svelte route.
 * These catch Svelte compilation issues and verify UI behaviour.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import Page from './+page.svelte';

vi.mock('$lib/api', () => ({
	api: {
		generateCurriculum: vi.fn(),
		listCurricula: vi.fn(),
		getCurriculum: vi.fn(),
		generateStory: vi.fn(),
		getLesson: vi.fn(),
		renderAudio: vi.fn(),
		audioUrl: vi.fn(),
		getSRSDue: vi.fn(),
		getSRSStats: vi.fn(),
		postSRSFeedback: vi.fn(),
		markAsListened: vi.fn()
	}
}));

vi.mock('$lib/storage', () => ({
	saveHomeState: vi.fn(),
	loadHomeState: vi.fn(),
	clearHomeState: vi.fn()
}));

import { api } from '$lib/api';
import { saveHomeState, loadHomeState, clearHomeState } from '$lib/storage';
const mockGenerateCurriculum = vi.mocked(api.generateCurriculum);
const mockGetLesson = vi.mocked(api.getLesson);
const mockMarkAsListened = vi.mocked(api.markAsListened);
const mockSaveHomeState = vi.mocked(saveHomeState);
const mockLoadHomeState = vi.mocked(loadHomeState);
const mockClearHomeState = vi.mocked(clearHomeState);

beforeEach(() => {
	vi.clearAllMocks();
	mockLoadHomeState.mockReturnValue(null);
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('+page.svelte', () => {
	it('renders the TunaTale heading', () => {
		const { getByRole } = render(Page);
		expect(getByRole('heading', { name: 'TunaTale' })).toBeTruthy();
	});

	it('renders a nav link to /practice', () => {
		const { getByRole } = render(Page);
		const link = getByRole('link', { name: 'Practice (SRS)' }) as HTMLAnchorElement;
		expect(link.href).toContain('/practice');
	});

	it('renders topic input and Generate button', () => {
		const { getByRole, getByPlaceholderText } = render(Page);
		expect(getByPlaceholderText('e.g. ordering coffee in Ljubljana')).toBeTruthy();
		expect(getByRole('button', { name: 'Generate' })).toBeTruthy();
	});

	it('Generate button is disabled when topic is empty', () => {
		const { getByRole } = render(Page);
		const btn = getByRole('button', { name: 'Generate' }) as HTMLButtonElement;
		expect(btn.disabled).toBe(true);
	});

	it('calls api.generateCurriculum when Generate is clicked with a topic', async () => {
		mockGenerateCurriculum.mockResolvedValue({
			id: 'c1',
			topic: 'ordering coffee',
			language_code: 'sl',
			days: 3
		});

		const { getByRole, getByPlaceholderText } = render(Page);
		await fireEvent.input(getByPlaceholderText('e.g. ordering coffee in Ljubljana'), {
			target: { value: 'ordering coffee' }
		});
		await fireEvent.click(getByRole('button', { name: 'Generate' }));

		expect(mockGenerateCurriculum).toHaveBeenCalledWith('ordering coffee', 'A2', 7);
	});

	it('shows lesson script after generating curriculum and clicking a day button', async () => {
		mockGenerateCurriculum.mockResolvedValue({
			id: 'c1',
			topic: 'ordering coffee',
			language_code: 'sl',
			days: 1
		});
		vi.mocked(api.generateStory).mockResolvedValue({ id: 'l1', title: 'Day 1', sections: [] });
		mockGetLesson.mockResolvedValue({
			id: 'l1',
			title: 'Day 1',
			language_code: 'sl',
			key_phrases: [],
			sections: [
				{
					type: 'key_phrases',
					phrases: [{ text: 'dober dan', role: 'female-1', language_code: 'sl', voice_id: 'sl-SI-PetraNeural' }]
				}
			]
		});

		const { getByRole, getByPlaceholderText, findByText, findByRole } = render(Page);
		await fireEvent.input(getByPlaceholderText('e.g. ordering coffee in Ljubljana'), {
			target: { value: 'ordering coffee' }
		});
		await fireEvent.click(getByRole('button', { name: 'Generate' }));
		await fireEvent.click(await findByRole('button', { name: 'Day 1' }));

		expect(await findByText('Lesson Script')).toBeTruthy();
		expect(await findByText('dober dan')).toBeTruthy();
		expect(await findByText('female-1')).toBeTruthy();
	});

	it('shows error message when api.generateCurriculum rejects', async () => {
		mockGenerateCurriculum.mockRejectedValue(new Error('Network error'));

		const { getByRole, getByPlaceholderText, findByText } = render(Page);
		await fireEvent.input(getByPlaceholderText('e.g. ordering coffee in Ljubljana'), {
			target: { value: 'coffee' }
		});
		await fireEvent.click(getByRole('button', { name: 'Generate' }));

		expect(await findByText('Network error')).toBeTruthy();
	});

	describe('auto-save', () => {
		it('saves state to localStorage after curriculum is generated', async () => {
			mockGenerateCurriculum.mockResolvedValue({
				id: 'c1',
				topic: 'ordering coffee',
				language_code: 'sl',
				days: 3
			});

			const { getByRole, getByPlaceholderText } = render(Page);
			await fireEvent.input(getByPlaceholderText('e.g. ordering coffee in Ljubljana'), {
				target: { value: 'ordering coffee' }
			});
			await fireEvent.click(getByRole('button', { name: 'Generate' }));

			await waitFor(() => {
				expect(mockSaveHomeState).toHaveBeenCalledWith(
					expect.objectContaining({ curriculumId: 'c1' })
				);
			});
		});

		it('saves lessonId after lesson is generated', async () => {
			mockGenerateCurriculum.mockResolvedValue({
				id: 'c1',
				topic: 'ordering coffee',
				language_code: 'sl',
				days: 1
			});
			vi.mocked(api.generateStory).mockResolvedValue({ id: 'l1', title: 'Day 1', sections: [] });
			mockGetLesson.mockResolvedValue({
				id: 'l1',
				title: 'Day 1',
				language_code: 'sl',
				key_phrases: [],
				sections: []
			});

			const { getByRole, getByPlaceholderText, findByRole } = render(Page);
			await fireEvent.input(getByPlaceholderText('e.g. ordering coffee in Ljubljana'), {
				target: { value: 'ordering coffee' }
			});
			await fireEvent.click(getByRole('button', { name: 'Generate' }));
			await fireEvent.click(await findByRole('button', { name: 'Day 1' }));

			await waitFor(() => {
				expect(mockSaveHomeState).toHaveBeenCalledWith(
					expect.objectContaining({ curriculumId: 'c1', lessonId: 'l1' })
				);
			});
		});
	});

	describe('restore on mount', () => {
		it('restores topic, cefrLevel, numDays from localStorage on mount', async () => {
			mockLoadHomeState.mockReturnValue({ topic: 'hiking', cefrLevel: 'B1', numDays: 5 });

			const { getByPlaceholderText, getByDisplayValue } = render(Page);

			await waitFor(() => {
				expect(
					(getByPlaceholderText('e.g. ordering coffee in Ljubljana') as HTMLInputElement).value
				).toBe('hiking');
			});
			expect((getByDisplayValue('B1') as HTMLSelectElement).value).toBe('B1');
		});

		it('fetches and displays curriculum when curriculumId in localStorage', async () => {
			mockLoadHomeState.mockReturnValue({
				topic: 'coffee',
				cefrLevel: 'A2',
				numDays: 3,
				curriculumId: 'c1'
			});
			vi.mocked(api.getCurriculum).mockResolvedValue({
				id: 'c1',
				topic: 'coffee',
				language_code: 'sl',
				days: 3
			});

			const { findByText, findByRole } = render(Page);

			expect(await findByText('Curriculum: coffee')).toBeTruthy();
			expect(await findByRole('button', { name: 'Day 1' })).toBeTruthy();
		});

		it('fetches and displays lesson script when lessonId in localStorage', async () => {
			mockLoadHomeState.mockReturnValue({
				topic: 'coffee',
				cefrLevel: 'A2',
				numDays: 3,
				curriculumId: 'c1',
				lessonId: 'l1'
			});
			vi.mocked(api.getCurriculum).mockResolvedValue({
				id: 'c1',
				topic: 'coffee',
				language_code: 'sl',
				days: 3
			});
			mockGetLesson.mockResolvedValue({
				id: 'l1',
				title: 'Day 1 Coffee',
				language_code: 'sl',
				key_phrases: [],
				sections: [
					{
						type: 'key_phrases',
						phrases: [
							{ text: 'dober dan', role: 'female-1', language_code: 'sl', voice_id: 'sl-SI-PetraNeural' }
						]
					}
				]
			});

			const { findByText } = render(Page);

			expect(await findByText('Lesson Script')).toBeTruthy();
			expect(await findByText('dober dan')).toBeTruthy();
		});

		it('displays audio player when audioUrl in localStorage', async () => {
			mockLoadHomeState.mockReturnValue({
				topic: 'coffee',
				cefrLevel: 'A2',
				numDays: 3,
				curriculumId: 'c1',
				lessonId: 'l1',
				audioUrl: '/api/audio/a1'
			});
			vi.mocked(api.getCurriculum).mockResolvedValue({
				id: 'c1',
				topic: 'coffee',
				language_code: 'sl',
				days: 3
			});
			mockGetLesson.mockResolvedValue({
				id: 'l1',
				title: 'Day 1',
				language_code: 'sl',
				key_phrases: [],
				sections: []
			});

			const { findByText } = render(Page);

			expect(await findByText('Audio Player')).toBeTruthy();
		});

		it('clears localStorage when curriculum fetch fails', async () => {
			mockLoadHomeState.mockReturnValue({
				topic: 'coffee',
				cefrLevel: 'A2',
				numDays: 3,
				curriculumId: 'c1'
			});
			vi.mocked(api.getCurriculum).mockRejectedValue(new Error('Not found'));

			const { queryByText } = render(Page);

			await waitFor(() => {
				expect(mockClearHomeState).toHaveBeenCalled();
			});
			expect(queryByText('Curriculum: coffee')).toBeNull();
		});

		it('clears localStorage when lesson fetch fails but keeps curriculum', async () => {
			mockLoadHomeState.mockReturnValue({
				topic: 'coffee',
				cefrLevel: 'A2',
				numDays: 3,
				curriculumId: 'c1',
				lessonId: 'l1'
			});
			vi.mocked(api.getCurriculum).mockResolvedValue({
				id: 'c1',
				topic: 'coffee',
				language_code: 'sl',
				days: 3
			});
			mockGetLesson.mockRejectedValue(new Error('Not found'));

			const { findByText, queryByText } = render(Page);

			expect(await findByText('Curriculum: coffee')).toBeTruthy();
			await waitFor(() => {
				expect(mockClearHomeState).toHaveBeenCalled();
			});
			expect(queryByText('Lesson Script')).toBeNull();
		});

		it('does not call API on mount when localStorage is empty', async () => {
			render(Page);

			await waitFor(() => {});
			expect(vi.mocked(api.getCurriculum)).not.toHaveBeenCalled();
			expect(mockGetLesson).not.toHaveBeenCalled();
		});
	});

	describe('mark as listened', () => {
		function setupWithAudioAndPhrases(keyPhrases = [{ phrase: 'dober dan', translation: 'good day' }]) {
			mockLoadHomeState.mockReturnValue({
				topic: 'coffee',
				cefrLevel: 'A2',
				numDays: 3,
				curriculumId: 'c1',
				lessonId: 'l1',
				audioUrl: '/api/audio/a1'
			});
			vi.mocked(api.getCurriculum).mockResolvedValue({
				id: 'c1',
				topic: 'coffee',
				language_code: 'sl',
				days: 3
			});
			mockGetLesson.mockResolvedValue({
				id: 'l1',
				title: 'Day 1',
				language_code: 'sl',
				key_phrases: keyPhrases,
				sections: []
			});
		}

		it('shows "Mark as Listened" button when audio is rendered and key_phrases exist', async () => {
			setupWithAudioAndPhrases();
			const { findByRole } = render(Page);
			expect(await findByRole('button', { name: 'Mark as Listened' })).toBeTruthy();
		});

		it('does not show "Mark as Listened" when key_phrases is empty', async () => {
			setupWithAudioAndPhrases([]);
			const { findByText, queryByRole } = render(Page);
			await findByText('Audio Player');
			expect(queryByRole('button', { name: 'Mark as Listened' })).toBeNull();
		});

		it('calls markAsListened and shows confirmation', async () => {
			setupWithAudioAndPhrases([
				{ phrase: 'dober dan', translation: 'good day' },
				{ phrase: 'prosim', translation: 'please' }
			]);
			mockMarkAsListened.mockResolvedValue({ status: 'ok', registered: 2 });

			const { findByRole, findByText } = render(Page);
			await fireEvent.click(await findByRole('button', { name: 'Mark as Listened' }));

			expect(mockMarkAsListened).toHaveBeenCalledWith('l1');
			expect(await findByText('2 phrases added to SRS')).toBeTruthy();
		});

		it('shows "✓ Listened" when lesson is in listenedLessonIds', async () => {
			mockLoadHomeState.mockReturnValue({
				topic: 'coffee',
				cefrLevel: 'A2',
				numDays: 3,
				curriculumId: 'c1',
				lessonId: 'l1',
				audioUrl: '/api/audio/a1',
				listenedLessonIds: ['l1']
			});
			vi.mocked(api.getCurriculum).mockResolvedValue({
				id: 'c1',
				topic: 'coffee',
				language_code: 'sl',
				days: 3
			});
			mockGetLesson.mockResolvedValue({
				id: 'l1',
				title: 'Day 1',
				language_code: 'sl',
				key_phrases: [{ phrase: 'dober dan', translation: 'good day' }],
				sections: []
			});

			const { findByRole } = render(Page);
			expect(await findByRole('button', { name: '✓ Listened' })).toBeTruthy();
		});

		it('shows key phrases summary in expandable details', async () => {
			setupWithAudioAndPhrases([{ phrase: 'dober dan', translation: 'good day' }]);
			const { findByText } = render(Page);
			expect(await findByText('1 key phrase in this lesson')).toBeTruthy();
		});

		it('shows error when markAsListened fails', async () => {
			setupWithAudioAndPhrases();
			mockMarkAsListened.mockRejectedValue(new Error('Network error'));

			const { findByRole, findByText } = render(Page);
			await fireEvent.click(await findByRole('button', { name: 'Mark as Listened' }));

			expect(await findByText('Network error')).toBeTruthy();
		});
	});
});
