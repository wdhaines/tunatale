/**
 * Tests for SyncButton component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';

vi.mock('$lib/api', () => ({
	api: {
		syncWithAnki: vi.fn()
	}
}));

import { api } from '$lib/api';
import SyncButton from '$lib/components/SyncButton.svelte';

const mockSyncWithAnki = vi.mocked(api.syncWithAnki);

describe('SyncButton', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('renders the sync button', () => {
		const { getByText, queryByText } = render(SyncButton);
		expect(getByText('Sync with Anki')).toBeTruthy();
		// syncResult is null initially, so result display should not be present
		expect(queryByText(/Mode:/)).toBeNull();
	});

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
			revlog_drained: 5,
			dry_run: false
		});
		const { getByText } = render(SyncButton);

		const btn = getByText('Sync with Anki');
		await fireEvent.click(btn);

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
			revlog_drained: 5,
			dry_run: false
		});
		const { getByText } = render(SyncButton);

		const btn = getByText('Sync with Anki');
		await fireEvent.click(btn);

		await waitFor(() => {
			expect(getByText(/Mode: full/)).toBeTruthy();
			expect(getByText(/Created: 5/)).toBeTruthy();
			expect(getByText(/Linked: 2/)).toBeTruthy();
			expect(getByText(/Skipped: 1/)).toBeTruthy();
			expect(getByText(/Pulled: 3 notes/)).toBeTruthy();
			expect(getByText(/Pushed: 2 notes/)).toBeTruthy();
			expect(getByText(/Conflicts: 0/)).toBeTruthy();
			expect(getByText(/Revlog drained: 5/)).toBeTruthy();
		});
	});

	it('sets error when syncWithAnki fails with Error instance', async () => {
		mockSyncWithAnki.mockRejectedValue(new Error('Sync failed'));
		const { getByText, findByText } = render(SyncButton);

		const btn = getByText('Sync with Anki');
		await fireEvent.click(btn);

		expect(await findByText('Sync failed')).toBeTruthy();
	});

	it('sets error when syncWithAnki fails with non-Error value', async () => {
		mockSyncWithAnki.mockRejectedValue('string error');
		const { getByText, findByText } = render(SyncButton);

		const btn = getByText('Sync with Anki');
		await fireEvent.click(btn);

		expect(await findByText('string error')).toBeTruthy();
	});

	it('shows loading state while syncing', async () => {
		let resolveSync: ((value: any) => void) | undefined;
		const syncPromise = new Promise<any>((resolve) => {
			resolveSync = resolve;
		});
		mockSyncWithAnki.mockReturnValue(syncPromise);

		const { getByText } = render(SyncButton);
		const btn = getByText('Sync with Anki');
		await fireEvent.click(btn);

		expect(getByText('Syncing…')).toBeTruthy();

		resolveSync!({
			mode: 'full',
			created: 1,
			linked: 0,
			skipped: 0,
			notes_pulled: 0,
			directions_pulled: 0,
			conflicts: 0,
			notes_pushed: 0,
			directions_pushed: 0,
			revlog_drained: 0,
			dry_run: false
		});
	});
});
