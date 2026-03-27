/**
 * Component tests for the main +page.svelte route.
 * These catch Svelte compilation issues and verify UI behaviour.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import Page from './+page.svelte';

vi.mock('$lib/api', () => ({
	api: {
		generateCurriculum: vi.fn(),
		listCurricula: vi.fn(),
		getCurriculum: vi.fn(),
		generateStory: vi.fn(),
		renderAudio: vi.fn(),
		audioUrl: vi.fn(),
		getSRSDue: vi.fn(),
		getSRSStats: vi.fn(),
		postSRSFeedback: vi.fn()
	}
}));

import { api } from '$lib/api';
const mockGenerateCurriculum = vi.mocked(api.generateCurriculum);

beforeEach(() => {
	vi.clearAllMocks();
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

	it('shows error message when api.generateCurriculum rejects', async () => {
		mockGenerateCurriculum.mockRejectedValue(new Error('Network error'));

		const { getByRole, getByPlaceholderText, findByText } = render(Page);
		await fireEvent.input(getByPlaceholderText('e.g. ordering coffee in Ljubljana'), {
			target: { value: 'coffee' }
		});
		await fireEvent.click(getByRole('button', { name: 'Generate' }));

		expect(await findByText('Network error')).toBeTruthy();
	});
});
