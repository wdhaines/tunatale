import { test, expect } from '@playwright/test';

test('backend health check', async ({ request }) => {
	const res = await request.get('http://localhost:8000/api/health');
	expect(res.ok()).toBe(true);
	const body = await res.json();
	expect(body.status).toBe('ok');
});

test('home page loads', async ({ page }) => {
	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'TunaTale' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Practice (SRS)' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Generate' })).toBeDisabled();
});

test('practice page loads', async ({ page }) => {
	await page.goto('/practice');
	await expect(page.getByRole('link', { name: /TunaTale/ })).toBeVisible();
	// Either shows loading → empty state, or cards if any are due
	await expect(
		page.getByText(/No cards due|Loading cards…/)
	).toBeVisible({ timeout: 5000 });
});

test('frontend proxies /api to backend', async ({ request }) => {
	// Hits backend via Vite proxy — catches the "Not Found" gap
	const res = await request.get('http://localhost:5173/api/health');
	expect(res.ok()).toBe(true);
	const body = await res.json();
	expect(body.status).toBe('ok');
});

test('generate curriculum flow', async ({ page, request }) => {
	const health = await request.get('http://localhost:8000/api/health');
	test.skip(!health.ok(), 'Backend not available');

	await page.goto('/');
	await page.getByPlaceholder('e.g. ordering coffee in Ljubljana').fill('ordering coffee');
	await expect(page.getByRole('button', { name: 'Generate' })).toBeEnabled();
	await page.getByRole('button', { name: 'Generate' }).click();
	// Wait for curriculum to appear (calls backend LLM endpoint)
	await expect(page.getByText(/Curriculum:/)).toBeVisible({ timeout: 30000 });
});

test('practice page shows stats from backend', async ({ page, request }) => {
	const health = await request.get('http://localhost:8000/api/health');
	test.skip(!health.ok(), 'Backend not available');

	await page.goto('/practice');
	// Stats section is populated once backend responds
	await expect(
		page.getByText(/cards total/)
	).toBeVisible({ timeout: 5000 });
});
