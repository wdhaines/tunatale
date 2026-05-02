/**
 * Tests for SyncButton component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';

vi.mock('$lib/api', () => ({
	api: {
		syncCreateNew: vi.fn()
	}
}));

import { api } from '$lib/api';
import SyncButton from '$lib/components/SyncButton.svelte';

const mockSyncCreateNew = vi.mocked(api.syncCreateNew);

describe('SyncButton', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('renders the sync button', () => {
		const { getByText } = render(SyncButton);
		expect(getByText('Sync New Cards to Anki')).toBeTruthy();
	});

	it('calls syncCreateNew with default deck and model names', async () => {
		mockSyncCreateNew.mockResolvedValue({ created: 3, updated: 0, skipped: 1 });
		const { getByText } = render(SyncButton);
		
		const btn = getByText('Sync New Cards to Anki');
		await fireEvent.click(btn);

		await waitFor(() => {
			expect(mockSyncCreateNew).toHaveBeenCalledWith('0. Slovene', 'Slovene Vocabulary');
		});
	});

	it('calls syncCreateNew with custom deck and model names', async () => {
		mockSyncCreateNew.mockResolvedValue({ created: 2, updated: 0, skipped: 0 });
		const { getByText } = render(SyncButton, {
			props: { deckName: 'Custom Deck', modelName: 'Custom Model' }
		});
		
		const btn = getByText('Sync New Cards to Anki');
		await fireEvent.click(btn);

		await waitFor(() => {
			expect(mockSyncCreateNew).toHaveBeenCalledWith('Custom Deck', 'Custom Model');
		});
	});

	it('displays sync result after successful sync', async () => {
		mockSyncCreateNew.mockResolvedValue({ created: 5, updated: 0, skipped: 2 });
		const { getByText } = render(SyncButton);
		
		const btn = getByText('Sync New Cards to Anki');
		await fireEvent.click(btn);

		await waitFor(() => {
			expect(getByText(/Created 5 new cards/)).toBeTruthy();
		});
	});

	it('displays singular "card" when created is 1', async () => {
		mockSyncCreateNew.mockResolvedValue({ created: 1, updated: 0, skipped: 0 });
		const { getByText } = render(SyncButton);
		
		const btn = getByText('Sync New Cards to Anki');
		await fireEvent.click(btn);

		await waitFor(() => {
			expect(getByText('Created 1 new card')).toBeTruthy();
		});
	});

	it('sets error when syncCreateNew fails', async () => {
		mockSyncCreateNew.mockRejectedValue(new Error('Sync failed'));
		const { getByText, findByText } = render(SyncButton);
		
		const btn = getByText('Sync New Cards to Anki');
		await fireEvent.click(btn);

		expect(await findByText('Sync failed')).toBeTruthy();
	});

	it('shows loading state while syncing', async () => {
		// Create a promise that we can control
		let resolveSync: ((value: { created: number; updated: number; skipped: number }) => void) | undefined;
		const syncPromise = new Promise<{ created: number; updated: number; skipped: number }>((resolve) => {
			resolveSync = resolve;
		});
		mockSyncCreateNew.mockReturnValue(syncPromise);
		
		const { getByText } = render(SyncButton);
		const btn = getByText('Sync New Cards to Anki');
		await fireEvent.click(btn);

		expect(getByText('Syncing…')).toBeTruthy();
		
		// Resolve the promise
		resolveSync!({ created: 1, updated: 0, skipped: 0 });
	});
});
