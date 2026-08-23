/**
 * The sign-in journey, end to end against the real stack (tunatale-boy / P1.4).
 *
 * E2E by the **core-journey** door, not the seam door: everything asserted here
 * is app-computed (a URL, a visible heading), and that is fine — the claim is
 * that a chain nothing below can join actually joins. It spans the login form,
 * a `Secure` HttpOnly cookie the browser stores and replays, the backend's
 * session lookup, and the SPA's client-side router. No lower tier can hold all
 * four at once: vitest mocks the fetch, and pytest has no browser to store a
 * cookie in.
 *
 * ⚠️ This file is the ONE spec that starts logged out. Every other spec inherits
 * the signed-in cookie from `use.storageState` in playwright.config.ts.
 */
import { readFileSync } from 'node:fs';
import { expect, test } from './fixtures';
import { CREDENTIALS } from './global-setup';

test.use({ storageState: { cookies: [], origins: [] } });

const { email, password } = JSON.parse(readFileSync(CREDENTIALS, 'utf8')) as {
	email: string;
	password: string;
};

async function signIn(page: import('@playwright/test').Page): Promise<void> {
	// ⚠️ HYDRATION, NOT POLITENESS. `goto` resolves on document load, but the
	// form's submit handler only exists after hydration. Filling and clicking an
	// unhydrated form submits it NATIVELY — a GET to `/login?` with no request to
	// the API — so the caller's `waitForURL('/')` then waits 30s for a navigation
	// that will never happen. That bare trailing `?` is the signature.
	//
	// This lives in the helper rather than at each call site because every caller
	// needs it and only one of the three had it (tunatale-vnf.16: 5 CI failures
	// in 99 suite runs, 5.05%, all with the `/login?` signature).
	await page.waitForLoadState('networkidle');
	await page.getByLabel('Email').fill(email);
	await page.getByLabel('Password').fill(password);
	await page.getByRole('button', { name: 'Sign in' }).click();
}

test('a deep link while logged out returns to the requested route after signing in', async ({
	page
}) => {
	// /cards rather than a lesson route on purpose: it is a real data route that
	// needs no seeding, so the spec measures the redirect round-trip and not
	// somebody else's fixture.
	await page.goto('/cards');

	// The destination is carried, not discarded — landing on the root after
	// signing in is the bug this asserts against.
	await page.waitForURL('**/login?next=%2Fcards');
	await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
	// No chrome on the login page: every nav destination behind it needs a session.
	await expect(page.getByRole('link', { name: 'TunaTale' })).toHaveCount(0);

	await signIn(page);

	await page.waitForURL('**/cards');
	await expect(page.getByRole('link', { name: 'TunaTale' })).toBeVisible();
});

test('wrong credentials keep you on the login page with the reason', async ({ page }) => {
	await page.goto('/login');
	// `goto` resolves on document load; the form's submit handler only exists
	// after hydration. Typing into an unhydrated form and clicking submits it
	// NATIVELY — a GET to `/login?` with no request to the API. This wait is
	// what makes the assertion below about the app rather than about timing.
	await page.waitForLoadState('networkidle');
	await page.getByLabel('Email').fill(email);
	await page.getByLabel('Password').fill('not the password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page.getByRole('alert')).toHaveText('Invalid credentials');
	expect(new URL(page.url()).pathname).toBe('/login');
});

test('a session that dies mid-visit lands on the login page, not a broken one', async ({
	page,
	context
}) => {
	await page.goto('/login');
	await signIn(page);
	await page.waitForURL('/');

	// The cookie expiring is indistinguishable, from the browser's side, from
	// this. The layout guard cannot catch it — it already ran and passed — so
	// what is being tested here is the 401 interceptor in api.ts.
	await context.clearCookies();

	await page.getByRole('link', { name: 'Review' }).click();

	await page.waitForURL('**/login?next=%2Freview');
	await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
});
