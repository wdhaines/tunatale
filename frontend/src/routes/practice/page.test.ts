/**
 * Component tests for the /practice +page.svelte route.
 * These catch Svelte compilation issues and verify the flashcard UI behaviour.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import PracticePage from './+page.svelte';

vi.mock('$lib/api', () => ({
	api: {
		getSRSDue: vi.fn(),
		getSRSNew: vi.fn(),
		getSRSStats: vi.fn(),
		postSRSFeedback: vi.fn()
	}
}));

import { api } from '$lib/api';
const mockGetSRSDue = vi.mocked(api.getSRSDue);
const mockGetSRSNew = vi.mocked(api.getSRSNew);
const mockPostSRSFeedback = vi.mocked(api.postSRSFeedback);

beforeEach(() => {
	vi.clearAllMocks();
	mockGetSRSNew.mockResolvedValue({ new: [] });
});

describe('practice/+page.svelte', () => {
	it('shows loading state initially', () => {
		mockGetSRSDue.mockReturnValue(new Promise(() => {})); // never resolves
		const { container } = render(PracticePage);
		expect(container.textContent).toContain('Loading cards');
	});

	it('shows empty state when no cards are due', async () => {
		mockGetSRSDue.mockResolvedValue({ due: [] });
		const { findByText } = render(PracticePage);
		expect(await findByText(/No cards due/)).toBeTruthy();
	});

	it('renders the first card L2 text when cards are due', async () => {
		mockGetSRSDue.mockResolvedValue({
			due: [{ text: 'dober dan', translation: 'good day' }]
		});
		const { findByText } = render(PracticePage);
		expect(await findByText('dober dan')).toBeTruthy();
	});

	it('shows Reveal button before translation is shown', async () => {
		mockGetSRSDue.mockResolvedValue({
			due: [{ text: 'hvala', translation: 'thank you' }]
		});
		const { findByRole } = render(PracticePage);
		expect(await findByRole('button', { name: 'Reveal' })).toBeTruthy();
	});

	it('shows translation and rating buttons after Reveal is clicked', async () => {
		mockGetSRSDue.mockResolvedValue({
			due: [{ text: 'hvala', translation: 'thank you' }]
		});
		const { findByRole, getByText } = render(PracticePage);
		await fireEvent.click(await findByRole('button', { name: 'Reveal' }));

		expect(getByText('thank you')).toBeTruthy();
		expect(getByText('Again')).toBeTruthy();
		expect(getByText('Hard')).toBeTruthy();
		expect(getByText('Good')).toBeTruthy();
		expect(getByText('Easy')).toBeTruthy();
	});

	it('calls postSRSFeedback with correct signal when Good is clicked', async () => {
		mockGetSRSDue.mockResolvedValue({
			due: [{ text: 'hvala', translation: 'thank you' }]
		});
		mockPostSRSFeedback.mockResolvedValue({ status: 'ok' });

		const { findByRole } = render(PracticePage);
		await fireEvent.click(await findByRole('button', { name: 'Reveal' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));

		expect(mockPostSRSFeedback).toHaveBeenCalledWith('hvala', 'no_help');
	});

	it('shows completion screen after rating the last card', async () => {
		mockGetSRSDue.mockResolvedValue({
			due: [{ text: 'hvala', translation: 'thank you' }]
		});
		mockPostSRSFeedback.mockResolvedValue({ status: 'ok' });

		const { findByRole, findByText } = render(PracticePage);
		await fireEvent.click(await findByRole('button', { name: 'Reveal' }));
		await fireEvent.click(await findByRole('button', { name: 'Good' }));

		expect(await findByText(/Session complete/)).toBeTruthy();
	});

	it('shows error message when getSRSDue rejects', async () => {
		mockGetSRSDue.mockRejectedValue(new Error('Failed to load'));
		const { findByText } = render(PracticePage);
		expect(await findByText('Failed to load')).toBeTruthy();
	});

	it('renders a link back to the home page', async () => {
		mockGetSRSDue.mockResolvedValue({ due: [] });
		const { findByRole } = render(PracticePage);
		const link = (await findByRole('link', { name: /TunaTale/ })) as HTMLAnchorElement;
		expect(link.href).toContain('/');
	});

	it('shows new cards before due cards', async () => {
		mockGetSRSNew.mockResolvedValue({ new: [{ text: 'nov besedi', translation: 'new word' }] });
		mockGetSRSDue.mockResolvedValue({ due: [{ text: 'dober dan', translation: 'good day' }] });
		const { findByText } = render(PracticePage);
		expect(await findByText('nov besedi')).toBeTruthy();
	});

	it('shows empty state when both new and due are empty', async () => {
		mockGetSRSNew.mockResolvedValue({ new: [] });
		mockGetSRSDue.mockResolvedValue({ due: [] });
		const { findByText } = render(PracticePage);
		expect(await findByText(/No cards due/)).toBeTruthy();
	});

	it('shows correct total count with new and due combined', async () => {
		mockGetSRSNew.mockResolvedValue({ new: [{ text: 'nov besedi', translation: 'new word' }] });
		mockGetSRSDue.mockResolvedValue({ due: [{ text: 'dober dan', translation: 'good day' }] });
		const { findByText } = render(PracticePage);
		expect(await findByText('1 / 2')).toBeTruthy();
	});

	it('shows error when getSRSNew rejects', async () => {
		mockGetSRSNew.mockRejectedValue(new Error('Network error on new'));
		mockGetSRSDue.mockResolvedValue({ due: [] });
		const { findByText } = render(PracticePage);
		expect(await findByText('Network error on new')).toBeTruthy();
	});
});
