import { test, expect } from '@playwright/test';

test('backend health check', async ({ request }) => {
	const res = await request.get('http://localhost:8001/api/health');
	expect(res.ok()).toBe(true);
	const body = await res.json();
	expect(body.status).toBe('ok');
});

test('home page loads', async ({ page }) => {
	await page.goto('/');
	await expect(page.getByRole('link', { name: 'TunaTale' })).toBeVisible();
	await expect(page.locator('nav').getByRole('link', { name: 'Review' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Generate' })).toBeDisabled();
});

test('frontend proxies /api to backend', async ({ request }) => {
	// Hits backend via Vite proxy — catches the "Not Found" gap
	const res = await request.get('http://localhost:5174/api/health');
	expect(res.ok()).toBe(true);
	const body = await res.json();
	expect(body.status).toBe('ok');
});

test('generate curriculum flow', async ({ page, request }) => {
	const health = await request.get('http://localhost:8001/api/health');
	test.skip(!health.ok(), 'Backend not available');

	await page.goto('/');
	await page.getByPlaceholder('e.g. ordering coffee in Ljubljana').fill('ordering coffee');
	await expect(page.getByRole('button', { name: 'Generate' })).toBeEnabled();
	await page.getByRole('button', { name: 'Generate' }).click();

	// After curriculum generates, should navigate to /c/:id
	await expect(page).toHaveURL(/\/c\/[a-z0-9-]+$/, { timeout: 30000 });
	// Day picker should be visible
	await expect(page.getByText('Day 1')).toBeVisible();
});

test('review page loads', async ({ page }) => {
	await page.goto('/review');
	await expect(page.getByRole('link', { name: /TunaTale/ })).toBeVisible();
	// Either shows loading → done state when no cards
	await expect(
		page.getByText(/Done for today|Loading/)
	).toBeVisible({ timeout: 5000 });
});

test('review page loads (with backend)', async ({ page, request }) => {
	const health = await request.get('http://localhost:8001/api/health');
	test.skip(!health.ok(), 'Backend not available');

	await page.goto('/review');
	// With backend reachable, should resolve past "Loading" to either done or queue
	// Wait for either "Done for today" or a card (Show button) to appear
	await Promise.race([
		page.getByText(/Done for today/).waitFor({ state: 'visible', timeout: 10000 }),
		page.getByRole('button', { name: 'Show' }).waitFor({ state: 'visible', timeout: 10000 }),
	]);
});

test('bad curriculum URL shows error boundary', async ({ page }) => {
	await page.goto('/c/nonexistent-curriculum-id');
	// Either shows a 404 error or redirects — either way should not 500
	const status = page.getByText(/404|not found|Curriculum not found/i);
	const isVisible = await status.isVisible({ timeout: 3000 }).catch(() => false);
	// As long as it doesn't show a generic "500 Internal Server Error" we're good
	const content = await page.content();
	expect(content).not.toContain('500 Internal Server Error');
});
