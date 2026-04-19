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
		generateCurriculum: vi.fn(),
		listCurricula: vi.fn()
	}
}));

// Mock $lib/storage (used by CurriculumForm)
vi.mock('$lib/storage', () => ({
	saveFormPreferences: vi.fn(),
	loadFormPreferences: vi.fn().mockReturnValue(null)
}));

import { api } from '$lib/api';
const mockGenerate = vi.mocked(api.generateCurriculum);
const mockListCurricula = vi.mocked(api.listCurricula);

beforeEach(() => {
	vi.clearAllMocks();
	mockListCurricula.mockResolvedValue([{ id: 'x', topic: 'test', created_at: '2026-01-01 00:00:00' }]);
});

describe('Home page', () => {
	it('renders the Generate Curriculum heading', () => {
		const { getByText } = render(Page);
		expect(getByText('Generate Curriculum')).toBeTruthy();
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

describe('Review links', () => {
	it('renders a link to /review/recognition', async () => {
		const { findByRole } = render(Page);
		const link = await findByRole('link', { name: /recognition/i });
		expect((link as HTMLAnchorElement).getAttribute('href')).toBe('/review/recognition');
	});

	it('renders a link to /review/production', async () => {
		const { findByRole } = render(Page);
		const link = await findByRole('link', { name: /production/i });
		expect((link as HTMLAnchorElement).getAttribute('href')).toBe('/review/production');
	});
});

describe('Recent Curricula section', () => {
	it('shows loading state initially', () => {
		mockListCurricula.mockReturnValue(new Promise(() => {})); // never resolves
		const { getByText } = render(Page);
		expect(getByText('Loading…')).toBeTruthy();
	});

	it('renders curricula as links after load', async () => {
		mockListCurricula.mockResolvedValue([
			{ id: 'slug-abc123', topic: 'Ordering Coffee', created_at: '2026-04-10 12:00:00' },
			{ id: 'slug-def456', topic: 'At the Airport', created_at: '2026-04-07 08:30:00' }
		]);
		const { findByText, getByRole } = render(Page);
		expect(await findByText('Ordering Coffee')).toBeTruthy();
		expect((getByRole('link', { name: 'Ordering Coffee' }) as HTMLAnchorElement).getAttribute('href')).toBe('/c/slug-abc123');
		expect((getByRole('link', { name: 'At the Airport' }) as HTMLAnchorElement).getAttribute('href')).toBe('/c/slug-def456');
		// Dates should be displayed
		expect(await findByText(/4\/10\/2026|Apr(il)? 10/i)).toBeTruthy();
	});

	it('shows empty state when no curricula', async () => {
		mockListCurricula.mockResolvedValue([]);
		const { findByText, queryByText } = render(Page);
		expect(await findByText(/no curricula yet/i)).toBeTruthy();
	});

	it('shows error when listCurricula fails', async () => {
		mockListCurricula.mockRejectedValue(new Error('fetch failed'));
		const { findByText } = render(Page);
		expect(await findByText('fetch failed')).toBeTruthy();
	});

	it('prepends new curriculum to list after generate', async () => {
		mockListCurricula.mockResolvedValue([{ id: 'old-id', topic: 'Old Topic', created_at: '2026-04-01 00:00:00' }]);
		mockGenerate.mockResolvedValue({
			id: 'new-id',
			topic: 'New Topic',
			language_code: 'sl',
			days: 7
		});
		const { getByPlaceholderText, getByRole, findByText } = render(Page);
		await findByText('Old Topic'); // wait for initial load
		await fireEvent.input(getByPlaceholderText(/ordering coffee/i), {
			target: { value: 'New Topic' }
		});
		await fireEvent.click(getByRole('button', { name: 'Generate' }));
		await waitFor(() => {
			expect((getByRole('link', { name: 'New Topic' }) as HTMLAnchorElement).getAttribute('href')).toBe('/c/new-id');
		});
	});
});
