import { test, expect } from '@playwright/test';
import { backendAvailable, resetSRSItems, seedSRSItems } from './helpers';

// This spec asserts on exact queue counts ("3 + 0 + 0" → 3 reviews → "Done").
// Other specs in this suite (admin-srs, review-again-rating) seed into the
// same test DB earlier in the run, so we wipe SRS items here before seeding
// to restore the clean precondition this spec needs.

test.beforeEach(async ({ request }) => {
	if (await backendAvailable(request)) await resetSRSItems(request);
});

test('review flow: seed items, drill through queue, complete', async ({ page, request }) => {
	test.skip(!(await backendAvailable(request)), 'Backend not available');

	await seedSRSItems(request, [
		{ text: 'zdravo', translation: 'hello' },
		{ text: 'hvala', translation: 'thank you' },
		{ text: 'prosim', translation: 'please' },
	]);

	// Wait for home page and queue stats to load
	// Queue stats show Anki-style widget: "3 + 0 + 0" (new + learning + review).
	// 3 words × 2 directions = 6 new directions, but Anki's bury_new buries the
	// second new sibling of each note, so the new badge mirrors Anki at 3 (one
	// per word), matching what the queue actually serves (Layer 64).
	await page.goto('/');
	await expect(page.getByText('3').first()).toBeVisible({ timeout: 10000 });
	await expect(page.getByText('0').first()).toBeVisible();

	// Click the Review link in the main content (not the nav)
	await page.getByRole('main').getByRole('link', { name: 'Review' }).click();
	await expect(page).toHaveURL('/review');
	// Check for Anki-style widget on review page: "3 + 0 + 0"
	await expect(page.getByText('3').first()).toBeVisible({ timeout: 10000 });

	// The queue serves one direction per word (sibling burying), so 3 reviews.
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
