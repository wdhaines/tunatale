import { test, expect } from './fixtures';
import { backendAvailable, resetSRSItems, seedSRSItems } from './helpers';

// This spec suspends 'eden' and does not restore it, and /cards paginates at 50
// sorted by text — so both the suspend state and 'eden' being on page 1 depend on
// what ran before. Wipe first to restore the precondition. Without this, a repeat
// run re-enters with 'eden' already suspended and the Suspend/Unsuspend assertions
// invert (verified: --repeat-each=3 fails 1 of 3 pre-fix).
test.beforeEach(async ({ request }) => {
	if (await backendAvailable(request)) await resetSRSItems(request);
});

test('cards: search filters list, suspend toggles state', async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), 'Backend not available');

	// Use words unique to this test — avoids overlapping with review-flow's zdravo/hvala/prosim
	await seedSRSItems(request, [
		{ text: 'eden', translation: 'one' },
		{ text: 'dva', translation: 'two' },
		{ text: 'tri', translation: 'three' },
		{ text: 'štiri', translation: 'four' },
		{ text: 'pet', translation: 'five' },
	]);

	await page.goto('/cards');
	await expect(page.getByText('eden').first()).toBeVisible({ timeout: 10000 });

	// Search (debounced 250ms) narrows the list to matching rows only
	await page.getByPlaceholder('Search cards').fill('dva');
	await expect(page.getByText('dva').first()).toBeVisible({ timeout: 5000 });
	await expect(page.getByText('eden')).not.toBeVisible();

	// Clear search — all items reload
	await page.getByPlaceholder('Search cards').fill('');
	await expect(page.getByText('eden').first()).toBeVisible({ timeout: 5000 });

	// Find the eden row, open its actions menu, and suspend it
	const edenRow = page.locator('.row').filter({ hasText: 'eden' });
	await edenRow.getByRole('button', { name: /^Actions for eden/ }).click();
	await edenRow.getByRole('menuitem', { name: 'Suspend' }).click();

	// After suspend: state badge shows 'suspended', and the menu now offers Unsuspend
	await expect(edenRow.locator('.state-suspended')).toBeVisible();
	await edenRow.getByRole('button', { name: /^Actions for eden/ }).click();
	await expect(edenRow.getByRole('menuitem', { name: 'Unsuspend' })).toBeVisible({ timeout: 5000 });
});
