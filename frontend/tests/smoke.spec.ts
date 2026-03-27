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
