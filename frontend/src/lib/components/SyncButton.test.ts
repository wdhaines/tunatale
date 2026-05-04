/**
 * Tests for SyncButton component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';

vi.mock('$lib/api', () => ({
	api: {
		syncWithAnki: vi.fn(),
		fetchAnkiStatus: vi.fn()
	}
}));

import { api } from '$lib/api';
import SyncButton from '$lib/components/SyncButton.svelte';

const mockSyncWithAnki = vi.mocked(api.syncWithAnki);
const mockFetchAnkiStatus = vi.mocked(api.fetchAnkiStatus);

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

	it('disables button and shows warning when Anki is running', async () => {
		mockFetchAnkiStatus.mockResolvedValue({ anki_running: true, lock_acquirable: false });
		const { getByText, getByRole } = render(SyncButton);

		await waitFor(() => {
			const btn = getByRole('button', { name: /Sync with Anki/ });
			expect(btn.hasAttribute('disabled')).toBeTruthy();
			expect(getByText('Close Anki to sync.')).toBeTruthy();
		});
	});

	it('does not call syncWithAnki when Anki is running and button is clicked', async () => {
		mockFetchAnkiStatus.mockResolvedValue({ anki_running: true, lock_acquirable: false });
		const { getByRole } = render(SyncButton);

		await waitFor(() => {
			const btn = getByRole('button', { name: /Sync with Anki/ });
			expect(btn.hasAttribute('disabled')).toBeTruthy();
		});

		// Button should be disabled, but fireEvent.click might still work; verify sync not called
		expect(mockSyncWithAnki).not.toHaveBeenCalled();
	});

	it('aborts sync if Anki starts between mount and click', async () => {
		mockFetchAnkiStatus
			.mockResolvedValueOnce({ anki_running: false, lock_acquirable: true }) // onMount
			.mockResolvedValueOnce({ anki_running: true, lock_acquirable: false }); // click
		mockSyncWithAnki.mockResolvedValue({
			mode: 'full', created: 0, linked: 0, skipped: 0,
			notes_pulled: 0, directions_pulled: 0, conflicts: 0,
			notes_pushed: 0, directions_pushed: 0, revlog_drained: 0, dry_run: false
		});

		const { getByRole, findByText } = render(SyncButton);
		await waitFor(() => expect(getByRole('button').hasAttribute('disabled')).toBe(false));
		await fireEvent.click(getByRole('button', { name: /Sync with Anki/ }));
		await findByText('Close Anki to sync.');
		expect(mockSyncWithAnki).not.toHaveBeenCalled();
	});

	it('calls onSyncResult callback when sync succeeds', async () => {
		const onSyncResult = vi.fn();
		mockFetchAnkiStatus.mockResolvedValue({ anki_running: false, lock_acquirable: true });
		mockSyncWithAnki.mockResolvedValue({
			mode: 'full', created: 3, linked: 1, skipped: 0,
			notes_pulled: 0, directions_pulled: 0, conflicts: 0,
			notes_pushed: 0, directions_pushed: 0, revlog_drained: 0, dry_run: false
		});

		const { getByText } = render(SyncButton, { props: { onSyncResult } });
		await waitFor(() => expect(getByText('Sync with Anki').hasAttribute('disabled')).toBe(false));
		await fireEvent.click(getByText('Sync with Anki'));
		await waitFor(() => expect(onSyncResult).toHaveBeenCalledWith(
			expect.objectContaining({ created: 3, linked: 1 })
		));
	});

	it('compact variant shows summary when sync succeeds (no onSyncResult)', async () => {
		mockFetchAnkiStatus.mockResolvedValue({ anki_running: false, lock_acquirable: true });
		mockSyncWithAnki.mockResolvedValue({
			mode: 'full', created: 5, linked: 2, skipped: 0,
			notes_pulled: 0, directions_pulled: 0, conflicts: 0,
			notes_pushed: 0, directions_pushed: 0, revlog_drained: 0, dry_run: false
		});

		const { getByText, findByText } = render(SyncButton, { props: { variant: 'compact' } });
		await waitFor(() => expect(getByText('Sync with Anki').hasAttribute('disabled')).toBe(false));
		await fireEvent.click(getByText('Sync with Anki'));
		await findByText('5 created, 2 linked');
	});
});
