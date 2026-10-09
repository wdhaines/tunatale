/**
 * The invite journey, end to end against the real stack (tunatale-1mh / P4.3).
 *
 * E2E by the **core-journey** door, not the seam door: everything asserted here
 * is app-computed (a URL, a field value, an alert), and the claim is that a
 * chain nothing below can join actually joins. It spans the CLI that mints the
 * token, a URL **fragment** only a real browser carries, the layout's session
 * guard deciding NOT to bounce a logged-out visitor, the redeem endpoint, and
 * then the ordinary sign-in with the account that now exists. Vitest mocks the
 * fetch and jsdom has no router; pytest has no browser to hold a fragment in.
 *
 * ⚠️ Starts logged out, like auth-login.spec.ts — an invitee has no session.
 */
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { expect, test } from './fixtures';

test.use({ storageState: { cookies: [], origins: [] } });

/**
 * Mint an invite the way an operator does, into the auth store the running
 * backends share (same `AUTH_DATABASE_URL` and cwd as global-setup.ts).
 * Returns the printed path, `/invite#<token>`.
 */
function mintInvite(): string {
	const out = execFileSync('uv', ['run', 'python', '-m', 'app.auth.cli', 'create-invite'], {
		cwd: '../backend',
		env: { ...process.env, AUTH_DATABASE_URL: 'sqlite:///./tunatale-test-auth.db' },
		encoding: 'utf8'
	});
	const link = /\/invite#[A-Za-z0-9_-]+/.exec(out);
	if (!link) throw new Error(`create-invite printed no redeem link: ${out}`);
	return link[0];
}

test('an invite link becomes an account that can sign in, exactly once', async ({ page }) => {
	// Generated per run: nothing here is a credential worth committing, and a
	// fixed address would collide with itself on a second run against a kept DB.
	const email = `invitee-${randomBytes(6).toString('hex')}@example.com`;
	const password = randomBytes(18).toString('hex');
	const link = mintInvite();
	const token = link.slice('/invite#'.length);

	await page.goto(link);
	// HYDRATION, as in auth-login.spec.ts: the submit handler and the onMount
	// that reads the fragment only exist after it.
	await page.waitForLoadState('networkidle');

	// The guard left a logged-out visitor where they were. A redirect to
	// `/login?next=%2Finvite` would have dropped the fragment — the token.
	expect(new URL(page.url()).pathname).toBe('/invite');
	await expect(page.getByLabel('Invite token')).toHaveValue(token);
	await expect(page.getByRole('link', { name: 'TunaTale' })).toHaveCount(0);

	await page.getByLabel('Email').fill(email);
	await page.getByLabel('Password').fill(password);
	await page.getByRole('button', { name: 'Create account' }).click();

	// Redeeming does not sign you in: there is one way in, and it is the login
	// page. Reached by a client-side navigation, so the form is already live.
	await page.waitForURL('**/login');
	await page.getByLabel('Email').fill(email);
	await page.getByLabel('Password').fill(password);
	await page.getByRole('button', { name: 'Sign in' }).click();

	await page.waitForURL('/');
	// Signed in AS THE NEW ACCOUNT. It has no deck yet, and "who am I" must
	// still answer — this is the request the SPA's guard reads on every load.
	const me = await page.request.get('/api/auth/me');
	expect(me.status()).toBe(200);
	expect(await me.json()).toEqual({ email });

	// Single-use, through the same page: the link that just worked is dead.
	await page.goto(link);
	await page.waitForLoadState('networkidle');
	await page.getByLabel('Email').fill(`second-${email}`);
	await page.getByLabel('Password').fill(password);
	await page.getByRole('button', { name: 'Create account' }).click();

	await expect(page.getByRole('alert')).toHaveText('Invalid or expired invite');
	expect(new URL(page.url()).pathname).toBe('/invite');
});
