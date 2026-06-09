import { test, expect } from '@playwright/test';
import { backendAvailable, seedSRSItems } from './helpers';

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
	await page.getByPlaceholder('Search text or translation…').fill('dva');
	await expect(page.getByText('dva').first()).toBeVisible({ timeout: 5000 });
	await expect(page.getByText('eden')).not.toBeVisible();

	// Clear search — all items reload
	await page.getByPlaceholder('Search text or translation…').fill('');
	await expect(page.getByText('eden').first()).toBeVisible({ timeout: 5000 });

	// Find the eden row and suspend it
	const edenRow = page.locator('.row').filter({ hasText: 'eden' });
	await edenRow.getByRole('button', { name: 'Suspend' }).click();

	// After suspend: button flips to Unsuspend and state badge shows 'suspended'
	await expect(edenRow.getByRole('button', { name: 'Unsuspend' })).toBeVisible({ timeout: 5000 });
	await expect(edenRow.locator('.state-suspended')).toBeVisible();
});
