/**
 * Tests for DayPicker.svelte.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import DayPicker from './DayPicker.svelte';
import type { CurriculumSummary } from '$lib/api';

const curriculum: CurriculumSummary = { id: 'c1', topic: 'Coffee', language_code: 'sl', days: 3 };

describe('DayPicker', () => {
	it('renders one button per day', () => {
		const { getAllByRole } = render(DayPicker, {
			props: { curriculum, onSelectDay: vi.fn() }
		});
		const buttons = getAllByRole('button');
		expect(buttons).toHaveLength(3);
		expect(buttons[0].textContent).toContain('Day 1');
		expect(buttons[2].textContent).toContain('Day 3');
	});

	it('calls onSelectDay with the correct day when clicked', async () => {
		const onSelectDay = vi.fn().mockResolvedValue(undefined);
		const { getByText } = render(DayPicker, { props: { curriculum, onSelectDay } });
		await fireEvent.click(getByText('Day 2'));
		expect(onSelectDay).toHaveBeenCalledWith(2);
	});

	it('blocks concurrent clicks when a day is already loading (line 14 guard)', async () => {
		let resolveClick!: () => void;
		const slowSelect = new Promise<void>((r) => { resolveClick = r; });
		const onSelectDay = vi.fn().mockReturnValue(slowSelect);

		const { getAllByRole } = render(DayPicker, { props: { curriculum, onSelectDay } });
		const buttons = getAllByRole('button') as HTMLButtonElement[];

		// Start loading Day 1
		await fireEvent.click(buttons[0]);
		// While Day 1 is loading, buttons should be disabled
		expect(buttons[1].disabled).toBe(true);

		// Clicking Day 2 while loading should be a no-op (covered by if guard)
		await fireEvent.click(buttons[1]);
		expect(onSelectDay).toHaveBeenCalledTimes(1); // Only called once

		resolveClick();
	});

	it('shows … on the loading button', async () => {
		let resolveClick!: () => void;
		const slowSelect = new Promise<void>((r) => { resolveClick = r; });
		const onSelectDay = vi.fn().mockReturnValue(slowSelect);

		const { getAllByRole } = render(DayPicker, { props: { curriculum, onSelectDay } });
		const buttons = getAllByRole('button') as HTMLButtonElement[];

		await fireEvent.click(buttons[0]);
		expect(buttons[0].textContent).toContain('…');
		resolveClick();
	});
});
