/**
 * Component tests for the /admin/srs +page.svelte route.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import AdminSRSPage from './+page.svelte';

vi.mock('$lib/api', () => ({
	api: {
		listSRSItems: vi.fn(),
		updateSRSItem: vi.fn(),
		deleteSRSItem: vi.fn(),
		bulkDeleteSRSItems: vi.fn(),
		resetSRSItem: vi.fn(),
		suspendSRSItem: vi.fn()
	}
}));

import { api } from '$lib/api';
import type { SRSItemDetail } from '$lib/api';
const mockList = vi.mocked(api.listSRSItems);
const mockUpdate = vi.mocked(api.updateSRSItem);
const mockDelete = vi.mocked(api.deleteSRSItem);
const mockBulkDelete = vi.mocked(api.bulkDeleteSRSItems);
const mockReset = vi.mocked(api.resetSRSItem);
const mockSuspend = vi.mocked(api.suspendSRSItem);

function makeItem(id: number, text: string, state: SRSItemDetail['state'] = 'new'): SRSItemDetail {
	return {
		id,
		text,
		translation: `trans_${text}`,
		state,
		due_date: '2026-04-07',
		stability: 1.0,
		difficulty: 5.0,
		reps: 0,
		lapses: 0,
		last_review: null,
		language_code: 'sl'
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	vi.useFakeTimers();
	mockList.mockResolvedValue({ items: [], total: 0 });
});

describe('admin/srs/+page.svelte', () => {
	it('renders rows returned from listSRSItems', async () => {
		mockList.mockResolvedValue({
			items: [makeItem(1, 'zdravo'), makeItem(2, 'hvala')],
			total: 2
		});
		const { findByText } = render(AdminSRSPage);
		expect(await findByText('zdravo')).toBeTruthy();
		expect(await findByText('hvala')).toBeTruthy();
	});

	it('typing in search re-queries after debounce', async () => {
		mockList.mockResolvedValue({ items: [makeItem(1, 'zdravo')], total: 1 });
		const { getByPlaceholderText } = render(AdminSRSPage);
		const input = getByPlaceholderText(/Search/);

		await fireEvent.input(input, { target: { value: 'zdr' } });
		// Should not have re-queried yet
		const callCount = mockList.mock.calls.length;

		// Advance debounce timer
		vi.runAllTimers();
		await waitFor(() => {
			expect(mockList.mock.calls.length).toBeGreaterThan(callCount);
		});
	});

	it('clicking column header flips sort order', async () => {
		mockList.mockResolvedValue({ items: [makeItem(1, 'a')], total: 1 });
		const { findByText } = render(AdminSRSPage);

		// Wait for initial load
		await findByText('a');

		const callsBefore = mockList.mock.calls.length;
		const textHeader = (await findByText(/^text/));
		await fireEvent.click(textHeader);

		await waitFor(() => {
			expect(mockList.mock.calls.length).toBeGreaterThan(callsBefore);
		});
	});

	it('clicking Edit, changing inputs, clicking Save calls updateSRSItem', async () => {
		const item = makeItem(42, 'zdravo');
		mockList.mockResolvedValue({ items: [item], total: 1 });
		mockUpdate.mockResolvedValue({ ...item, text: 'Zdravo!', translation: 'Hello!' });

		const { findByText, findByRole, getAllByRole } = render(AdminSRSPage);
		await findByText('zdravo');

		const editBtn = (await findByText('Edit'));
		await fireEvent.click(editBtn);

		const inputs = getAllByRole('textbox') as HTMLInputElement[];
		const textInput = inputs.find((i) => i.value === 'zdravo')!;
		const transInput = inputs.find((i) => i.value === 'trans_zdravo')!;

		await fireEvent.input(textInput, { target: { value: 'Zdravo!' } });
		await fireEvent.input(transInput, { target: { value: 'Hello!' } });

		const saveBtn = await findByText('Save');
		await fireEvent.click(saveBtn);

		await waitFor(() => {
			expect(mockUpdate).toHaveBeenCalledWith(42, { text: 'Zdravo!', translation: 'Hello!' });
		});
	});

	it('selecting two rows and clicking Bulk delete calls bulkDeleteSRSItems', async () => {
		mockList.mockResolvedValue({
			items: [makeItem(1, 'a'), makeItem(2, 'b')],
			total: 2
		});
		mockBulkDelete.mockResolvedValue({ deleted: 2 });
		vi.stubGlobal('confirm', () => true);

		const { findAllByRole, findByText } = render(AdminSRSPage);
		await findByText('a');

		const checkboxes = (await findAllByRole('checkbox')) as HTMLInputElement[];
		// Skip the "select all" header checkbox (first), select item checkboxes
		const itemCheckboxes = checkboxes.slice(1);
		await fireEvent.click(itemCheckboxes[0]);
		await fireEvent.click(itemCheckboxes[1]);

		const bulkBtn = await findByText(/Delete selected/);
		await fireEvent.click(bulkBtn);

		await waitFor(() => {
			expect(mockBulkDelete).toHaveBeenCalledWith([1, 2]);
		});
	});

	it('clicking Delete with confirm stubbed calls deleteSRSItem', async () => {
		const item = makeItem(7, 'lep');
		mockList.mockResolvedValue({ items: [item], total: 1 });
		mockDelete.mockResolvedValue({ status: 'deleted' });
		vi.stubGlobal('confirm', () => true);

		const { findByText } = render(AdminSRSPage);
		await findByText('lep');

		const deleteBtn = await findByText('Delete');
		await fireEvent.click(deleteBtn);

		await waitFor(() => {
			expect(mockDelete).toHaveBeenCalledWith(7);
		});
	});

	it('clicking Suspend on a review-state row calls suspendSRSItem(id, true)', async () => {
		const item = makeItem(9, 'lep', 'review');
		mockList.mockResolvedValue({ items: [item], total: 1 });
		mockSuspend.mockResolvedValue({ ...item, state: 'suspended' });

		const { findByText } = render(AdminSRSPage);
		await findByText('lep');

		const suspendBtn = await findByText('Suspend');
		await fireEvent.click(suspendBtn);

		await waitFor(() => {
			expect(mockSuspend).toHaveBeenCalledWith(9, true);
		});
	});
});
