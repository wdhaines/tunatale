/**
 * Regression test: listenedStore.has() must not mutate $state during $derived evaluation.
 * A prior version used lazy hydration inside has(), which triggered Svelte 5's
 * state_unsafe_mutation error when has() was called inside a $derived.
 */
import { it, expect } from 'vitest';
import { render } from '@testing-library/svelte';
import DerivedTest from './DerivedTest.svelte';

it('listenedStore.has() does not throw state_unsafe_mutation when used in $derived', () => {
	expect(() => {
		render(DerivedTest, { props: { lessonId: 'test-id' } });
	}).not.toThrow();
});
