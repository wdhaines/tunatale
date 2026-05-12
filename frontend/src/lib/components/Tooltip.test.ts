/**
 * Tests for Tooltip.svelte — CSS-only hover/focus tooltip.
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/svelte';
import TooltipTest from './TooltipTest.svelte';

describe('Tooltip', () => {
	it('renders the child content', () => {
		const { getByText } = render(TooltipTest, {
			props: { translation: null, state: null, childText: 'zdravo' }
		});
		expect(getByText('zdravo')).toBeTruthy();
	});

	it('renders translation text when provided', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: 'hello', state: null, childText: 'zdravo' }
		});
		const tooltip = getByRole('tooltip');
		expect(tooltip.textContent).toContain('hello');
	});

	it('renders readable state label for "learning"', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: null, state: 'learning', childText: 'zdravo' }
		});
		const tooltip = getByRole('tooltip');
		expect(tooltip.textContent).toContain('Learning');
	});

	it('renders readable state label for "new"', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: null, state: 'new', childText: 'zdravo' }
		});
		expect(getByRole('tooltip').textContent).toContain('New');
	});

	it('renders readable state label for "review"', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: null, state: 'review', childText: 'zdravo' }
		});
		expect(getByRole('tooltip').textContent).toContain('Review');
	});

	it('renders readable state label for "known"', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: null, state: 'known', childText: 'zdravo' }
		});
		expect(getByRole('tooltip').textContent).toContain('Known');
	});

	it('renders readable state label for "suspended" as "Suspended"', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: null, state: 'suspended', childText: 'zdravo' }
		});
		expect(getByRole('tooltip').textContent).toContain('Suspended');
	});

	it('shows "click to untrack" hint for known state', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: null, state: 'known', childText: 'zdravo' }
		});
		expect(getByRole('tooltip').textContent).toContain('click to untrack');
	});

	it('shows "click to restore" hint for suspended state', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: null, state: 'suspended', childText: 'zdravo' }
		});
		expect(getByRole('tooltip').textContent).toContain('click to restore');
	});

	it('shows "click to start learning" hint for new state', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: null, state: 'new', childText: 'zdravo' }
		});
		expect(getByRole('tooltip').textContent).toContain('click to start learning');
	});

	it('shows "click to mark known" hint for learning state', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: null, state: 'learning', childText: 'zdravo' }
		});
		expect(getByRole('tooltip').textContent).toContain('click to mark known');
	});

	it('renders no tooltip when both translation and state are null', () => {
		const { queryByRole } = render(TooltipTest, {
			props: { translation: null, state: null, childText: 'zdravo' }
		});
		expect(queryByRole('tooltip')).toBeNull();
	});

	it('renders both translation and state label when both provided', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: 'hello', state: 'learning', childText: 'zdravo' }
		});
		const tooltip = getByRole('tooltip');
		expect(tooltip.textContent).toContain('hello');
		expect(tooltip.textContent).toContain('Learning');
	});

	it('has role="tooltip" on the tooltip element', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: 'hello', state: null, childText: 'zdravo' }
		});
		expect(getByRole('tooltip')).toBeTruthy();
	});

	it('falls back to raw state value when state is not in STATE_LABELS', () => {
		const { getByRole } = render(TooltipTest, {
			props: { translation: null, state: 'exotic_state', childText: 'zdravo' }
		});
		expect(getByRole('tooltip').textContent).toContain('exotic_state');
	});
});
