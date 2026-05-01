import { test, expect } from '@playwright/test';

// Depends on playwright.config.ts `rm -f tunatale-test.db` in webServer startup.
// If reuseExistingServer is ever flipped to true, this test will 409 on the
// second run because the seeded items already exist.

test('review flow: seed items, drill through queue, complete', async ({ page, request }) => {
	const health = await request.get('http://localhost:8001/api/health');
	test.skip(!health.ok(), 'Backend not available');

	const items = [
		{ text: 'zdravo', language_code: 'sl', word_count: 1, translation: 'hello' },
		{ text: 'hvala', language_code: 'sl', word_count: 1, translation: 'thank you' },
		{ text: 'prosim', language_code: 'sl', word_count: 1, translation: 'please' },
	];

	for (const item of items) {
		const res = await request.post('http://localhost:8001/api/srs/items', { data: item });
		expect(res.ok()).toBe(true);
	}

	// Wait for home page and queue stats to load
	// Queue stats show "new" as number of directions (3 words × 2 directions = 6)
	await page.goto('/');
	await expect(page.getByRole('link', { name: /Review · New 6 · Due 0/ })).toBeVisible({ timeout: 10000 });

	await page.getByRole('link', { name: /Review · New 6 · Due 0/ }).click();
	await expect(page).toHaveURL('/review');
	await expect(page.getByText(/New 6 · Due 0/)).toBeVisible({ timeout: 10000 });

	// Review queue has 6 items (3 words × 2 directions)
	const totalItems = 6;
	await expect(page.getByText(`1 / ${totalItems}`)).toBeVisible({ timeout: 5000 });
	await expect(page.getByText(/Recognition|Production/)).toBeVisible();

	for (let i = 0; i < totalItems; i++) {
		await expect(page.getByRole('button', { name: 'Show' })).toBeVisible();
		await page.getByRole('button', { name: 'Show' }).click();
		await page.getByRole('button', { name: 'Good' }).click();
		if (i < totalItems - 1) {
			await expect(page.getByText(`${i + 2} / ${totalItems}`)).toBeVisible({ timeout: 5000 });
		}
	}

	await expect(page.getByText('Done for today')).toBeVisible({ timeout: 5000 });
	await expect(page.getByText(`Reviewed: ${totalItems}`)).toBeVisible();
});
