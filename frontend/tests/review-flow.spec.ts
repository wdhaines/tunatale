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
	// Queue stats show Anki-style widget: "6 + 0 + 0" (new + learning + review)
	// 3 words × 2 directions = 6 new directions
	await page.goto('/');
	await expect(page.getByText('6').first()).toBeVisible({ timeout: 10000 });
	await expect(page.getByText('0').first()).toBeVisible();

	// Click the Review link in the main content (not the nav)
	await page.getByRole('main').getByRole('link', { name: 'Review' }).click();
	await expect(page).toHaveURL('/review');
	// Check for Anki-style widget on review page: "6 + 0 + 0"
	await expect(page.getByText('6').first()).toBeVisible({ timeout: 10000 });

	// Queue has 6 items (3 words × 2 directions), but client-side sibling burying skips
	// the second direction of each word once one direction is rated. So 3 effective reviews.
	// With NEW+GOOD now going to LEARNING step 1 (due_at = now + 10min), those cards
	// won't reappear in the queue until due_at passes. To complete the review flow in this
	// test, we use EASY which graduates immediately to REVIEW.
	const expectedReviews = 3;
	await expect(page.getByText(/Recognition|Production/)).toBeVisible();

	for (let i = 0; i < expectedReviews; i++) {
		await expect(page.getByRole('button', { name: 'Show' })).toBeVisible();
		await page.getByRole('button', { name: 'Show' }).click();
		// Use EASY to graduate immediately (NEW+EASY → REVIEW, card won't reappear)
		await page.getByRole('button', { name: 'Easy' }).click();
	}

	await expect(page.getByText('Done for today')).toBeVisible({ timeout: 5000 });
	await expect(page.getByText(`Reviewed: ${expectedReviews}`)).toBeVisible();
});
