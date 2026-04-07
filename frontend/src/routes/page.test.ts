/**
 * Component tests for the home +page.svelte route.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import Page from './+page.svelte';

// Mock $app/navigation
const mockGoto = vi.fn();
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

// Mock $lib/api (used by CurriculumForm inside the page)
vi.mock('$lib/api', () => ({
	api: {
		generateCurriculum: vi.fn()
	}
}));

// Mock $lib/storage (used by CurriculumForm)
vi.mock('$lib/storage', () => ({
	saveFormPreferences: vi.fn(),
	loadFormPreferences: vi.fn().mockReturnValue(null)
}));

import { api } from '$lib/api';
const mockGenerate = vi.mocked(api.generateCurriculum);

beforeEach(() => {
	vi.clearAllMocks();
});

describe('Home page', () => {
	it('renders the heading and practice link', () => {
		const { getByText } = render(Page);
		expect(getByText('TunaTale')).toBeTruthy();
		expect(getByText('Practice (SRS)')).toBeTruthy();
	});

	it('renders the Generate button disabled when topic is empty', () => {
		const { getByRole } = render(Page);
		const btn = getByRole('button', { name: 'Generate' });
		expect((btn as HTMLButtonElement).disabled).toBe(true);
	});

	it('enables Generate button when topic is typed', async () => {
		const { getByRole, getByPlaceholderText } = render(Page);
		const input = getByPlaceholderText(/ordering coffee/i);
		await fireEvent.input(input, { target: { value: 'coffee' } });
		const btn = getByRole('button', { name: 'Generate' });
		expect((btn as HTMLButtonElement).disabled).toBe(false);
	});

	it('calls api.generateCurriculum and navigates to /c/:id on submit', async () => {
		mockGenerate.mockResolvedValue({
			id: 'cid-1',
			topic: 'coffee',
			language_code: 'sl',
			days: 7
		});

		const { getByRole, getByPlaceholderText } = render(Page);
		await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
			target: { value: 'coffee' }
		});
		await fireEvent.click(getByRole('button', { name: 'Generate' }));

		await waitFor(() => {
			expect(mockGenerate).toHaveBeenCalledWith('coffee', 'A2', 7);
			expect(mockGoto).toHaveBeenCalledWith('/c/cid-1');
		});
	});

	it('shows error message when generateCurriculum fails', async () => {
		mockGenerate.mockRejectedValue(new Error('Network error'));

		const { getByRole, getByPlaceholderText, findByText } = render(Page);
		await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
			target: { value: 'coffee' }
		});
		await fireEvent.click(getByRole('button', { name: 'Generate' }));

		expect(await findByText('Network error')).toBeTruthy();
	});
});
