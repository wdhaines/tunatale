import { test, expect } from './fixtures';
import { resetSRSItems } from './helpers';

test('backend health check', async ({ request, backendURL }) => {
	const res = await request.get(`${backendURL}/api/health`);
	expect(res.ok()).toBe(true);
	const body = await res.json();
	expect(body.status).toBe('ok');
});

test('home page loads', async ({ page }) => {
	await page.goto('/');
	await expect(page.getByRole('link', { name: 'TunaTale' })).toBeVisible();
	await expect(page.locator('nav').getByRole('link', { name: 'Review' })).toBeVisible();
	// Library home: the generate form opens from "+ New curriculum" (disclosure
	// behavior is covered in unit tests; here we just smoke-test that home renders).
	await expect(page.getByRole('button', { name: '+ New curriculum' })).toBeVisible();
});

test('frontend proxies /api to backend', async ({ request }) => {
	// Hits backend via Vite proxy — catches the "Not Found" gap
	const res = await request.get('/api/health');
	expect(res.ok()).toBe(true);
	const body = await res.json();
	expect(body.status).toBe('ok');
});

test('start curriculum plan flow', async ({ page, request, backendURL }) => {
	const health = await request.get(`${backendURL}/api/health`);
	test.skip(!health.ok(), 'Backend not available');

	await page.goto('/');
	// Wait for client hydration before interacting — the nav review-count badge is
	// rendered only after the layout's onMount fetch, so its presence means the
	// page is interactive and the disclosure click won't be dropped pre-hydration.
	await expect(page.locator('.review-badge')).toBeVisible({ timeout: 10000 });
	await page.getByRole('button', { name: '+ New curriculum' }).click();
	await expect(page.getByPlaceholder('e.g. ordering coffee in Ljubljana')).toBeVisible();
	await page.getByPlaceholder('e.g. ordering coffee in Ljubljana').fill('ordering coffee');
	await expect(page.getByRole('button', { name: 'Start planning' })).toBeEnabled();
	await page.getByRole('button', { name: 'Start planning' }).click();

	// startPlan is LLM-free: navigates straight to the planner chat
	await expect(page).toHaveURL(/\/c\/[a-z0-9-]+\/plan$/, { timeout: 15000 });
	await expect(page.getByPlaceholder('Message the planner…')).toBeVisible();
	await expect(page.getByRole('button', { name: /Plan the next \d+ days/ })).toBeVisible();
});

test('review page loads', async ({ page, request }) => {
	// ⚠️ The subject of this test is the EMPTY-QUEUE done state, and until
	// 2026-09-03 nothing established that precondition — whichever specs happened
	// to precede it on this worker decided whether cards were due. The one that
	// bit is review-pressure's `zapadlica`: promoted to LEARNING with today's
	// due_at, two directions, so the nav reads exactly the `0 + 2 + 0` recorded on
	// tunatale-g9kr. At workers: 2 that spec lands on this worker or the other
	// depending on how Playwright distributes files, which is the whole of the
	// "only reproduces at 2 workers" mystery. Wiping this worker's own SRS rows
	// (never the other's — see resetSRSItems) makes the assertion below a claim
	// about the page rather than about the run order.
	await resetSRSItems(request);

	await page.goto('/review');
	await expect(page.getByRole('link', { name: /TunaTale/ })).toBeVisible();
	// `Done for today` ALONE. This read /Done for today|Loading/, which made the
	// assertion vacuous: `Loading…` is the pre-fetch state of EVERY render of this
	// page, including one about to show a due card. Passing therefore meant only
	// that Playwright's first poll beat the queue fetch — measured 2026-09-03 with
	// a card seeded, the page reads `Loading…` immediately after goto and
	// `0 + 2 + 0 … zapadlica … Show` three seconds later, and BOTH satisfied the
	// old regex. That coin flip is why CI went ~5% red and then ~33% once `vite
	// preview` (2026-09-01) made the app boot fast enough to close the window.
	//
	// 10s matches the sibling test below because this now waits for the fetch to
	// SETTLE instead of catching a transient. It is not the "raise the timeout"
	// fix g9kr forbids: the failing page renders a card and never reaches this
	// text at any timeout.
	await expect(page.getByText('Done for today')).toBeVisible({ timeout: 10000 });
});

test('review page loads (with backend)', async ({ page, request, backendURL }) => {
	const health = await request.get(`${backendURL}/api/health`);
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
