import { test, expect } from '@playwright/test';
import { backendAvailable, seedSRSItems } from './helpers';

test('review: Again rating puts card into learning queue, not immediate re-show', async ({
	page,
	request,
}) => {
	test.skip(!(await backendAvailable(request)), 'Backend not available');

	await seedSRSItems(request, [
		{ text: 'zdravo', translation: 'hello' },
		{ text: 'hvala', translation: 'thank you' },
	]);

	await page.goto('/review');
	const prompt = page.locator('.main-text').first();
	await expect(prompt).toBeVisible({ timeout: 10000 });

	// Capture the first card's prompt (shown before clicking Show)
	const firstPrompt = (await prompt.textContent()) ?? '';

	await page.getByRole('button', { name: 'Show' }).click();
	await page.getByRole('button', { name: 'Again' }).click();

	// After Again, the graded card enters learning (due_at = now + first_learning_step ≥ 1min).
	// It must NOT be served again immediately. A different card should appear next.
	// Wait on the prompt text actually changing — checking for the Show button
	// is racey because the next card's Show button mounts before the prompt
	// re-renders, and we'd capture the first card's text as `secondPrompt`.
	// If this assertion times out, that's a real queue-cutoff bug — escalate
	// to Opus. Do NOT add a sleep or extend the timeout to paper over it.
	await expect(prompt).not.toHaveText(firstPrompt, { timeout: 5000 });
});
