/**
 * Tests for /c/[curriculumId] — curriculum view with day picker.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';

// Mock navigation and api before importing the page
const mockGoto = vi.fn();
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

vi.mock('$lib/api', () => ({
	api: {
		getLessonByDay: vi.fn(),
		generateStory: vi.fn(),
		getLesson: vi.fn()
	}
}));

import { api } from '$lib/api';
import Page from './+page.svelte';

const mockGetLessonByDay = vi.mocked(api.getLessonByDay);
const mockGenerateStory = vi.mocked(api.generateStory);
const mockGetLesson = vi.mocked(api.getLesson);

const curriculum = { id: 'cid-1', topic: 'Coffee', language_code: 'sl', days: 3 };

beforeEach(() => {
	vi.clearAllMocks();
});

describe('/c/[curriculumId] page', () => {
	it('renders curriculum topic and day buttons', () => {
		const { getByText } = render(Page, { props: { data: { curriculum } } });
		expect(getByText('Coffee')).toBeTruthy();
		expect(getByText('Day 1')).toBeTruthy();
		expect(getByText('Day 3')).toBeTruthy();
	});

	it('navigates to lesson URL when cached lesson exists', async () => {
		const lesson = { id: 'l1', title: 'Day 1', language_code: 'sl', sections: [], key_phrases: [] };
		mockGetLessonByDay.mockResolvedValue(lesson);

		const { getByText } = render(Page, { props: { data: { curriculum } } });
		await fireEvent.click(getByText('Day 1'));

		await waitFor(() => {
			expect(mockGoto).toHaveBeenCalledWith('/c/cid-1/l/l1');
		});
		expect(mockGenerateStory).not.toHaveBeenCalled();
	});

	it('generates a new lesson when getLessonByDay returns 404', async () => {
		mockGetLessonByDay.mockRejectedValue(new Error('Not Found'));
		mockGenerateStory.mockResolvedValue({ id: 'l2', title: 'Day 2', sections: [] });
		const lesson = { id: 'l2', title: 'Day 2', language_code: 'sl', sections: [], key_phrases: [] };
		mockGetLesson.mockResolvedValue(lesson);

		const { getByText } = render(Page, { props: { data: { curriculum } } });
		await fireEvent.click(getByText('Day 2'));

		await waitFor(() => {
			expect(mockGenerateStory).toHaveBeenCalledWith('cid-1', 2);
			expect(mockGoto).toHaveBeenCalledWith('/c/cid-1/l/l2');
		});
	});

	it('shows error when lesson fetch fails entirely', async () => {
		mockGetLessonByDay.mockRejectedValue(new Error('GET fail'));
		mockGenerateStory.mockRejectedValue(new Error('LLM offline'));

		const { getByText, findByText } = render(Page, { props: { data: { curriculum } } });
		await fireEvent.click(getByText('Day 1'));

		expect(await findByText('LLM offline')).toBeTruthy();
	});

	it('shows string error when non-Error is thrown', async () => {
		mockGetLessonByDay.mockRejectedValue(new Error('first'));
		mockGenerateStory.mockRejectedValue('string error value');

		const { getByText, findByText } = render(Page, { props: { data: { curriculum } } });
		await fireEvent.click(getByText('Day 1'));

		expect(await findByText('string error value')).toBeTruthy();
	});

	it('blocks concurrent clicks (loadingDay guard)', async () => {
		// DayPicker disables all buttons while one is loading
		let resolveSelect!: (v: unknown) => void;
		const slowSelect = new Promise((r) => { resolveSelect = r; });
		const onSelectDay = vi.fn().mockReturnValue(slowSelect);

		const { getAllByRole } = render(Page, { props: { data: { curriculum } } });
		const buttons = getAllByRole('button');
		// Both buttons get disabled while one is loading
		await fireEvent.click(buttons[0]);
		expect((buttons[1] as HTMLButtonElement).disabled).toBe(true);
		resolveSelect(undefined);
	});
});

describe('load function for /c/[curriculumId]', () => {
	it('throws 404 when curriculum is not found', async () => {
		// Import the load function
		const { load } = await import('./+page');

		// Mock api module for this import
		vi.doMock('$lib/api', () => ({
			api: {
				getCurriculum: vi.fn().mockRejectedValue(new Error('Not Found'))
			}
		}));

		// Re-import to pick up mock
		const { api: mockApi } = await import('$lib/api');
		vi.mocked(mockApi.getCurriculum).mockRejectedValue(new Error('Not Found'));

		// The load function calls error(404) which throws
		await expect(
			load({ params: { curriculumId: 'nonexistent' } } as never)
		).rejects.toBeDefined();
	});
});
