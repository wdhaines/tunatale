import { expect, test } from '@playwright/test';

/**
 * CORS is enforced by the BROWSER, not by the server.
 *
 * The backend unit tests (`backend/tests/test_prod_profile.py`) can only assert
 * what Starlette *grants* — which headers come back. httpx and FastAPI's
 * TestClient enforce nothing, so a green suite there is compatible with a
 * wide-open API. This spec is the other half: a real Chromium deciding whether
 * to hand the response body to the calling page.
 *
 * It guards a hole that was live on the Tailscale setup until 2026-08-12. The
 * app shipped `allow_origins=["*"]` with `allow_credentials=True` and no
 * authentication; Starlette echoes the caller's Origin back in that
 * combination, so any page the browser loaded could read and write TunaTale
 * data over localhost or the tailnet. Measured before the fix: the unlisted
 * origin below got `200` and a full body.
 *
 * **The pairing is the control, and it is why the negative test means
 * anything.** Both cases run identical code against the same backend, the same
 * port, and the same Vite server — the ONLY difference is the spelling of the
 * page's host, and `localhost:5174` / `127.0.0.1:5174` are different origins to
 * a browser. So a `Failed to fetch` that came from a service worker, a dead
 * port, or mixed content would take the positive case down with it. A negative
 * result on its own would prove nothing; that is the trap this pairing closes.
 */

const API = 'http://localhost:8001/api/health';

type Probe = { ok: boolean; status?: number; body?: string; error?: string };

/** Cross-origin fetch executed inside the page, credentials attached. */
async function fetchFromPage(page: import('@playwright/test').Page, url: string): Promise<Probe> {
	return page.evaluate(async (api) => {
		try {
			const res = await fetch(api, { credentials: 'include' });
			return { ok: true, status: res.status, body: await res.text() };
		} catch (e) {
			return { ok: false, error: String(e) };
		}
	}, url);
}

test('CORS: an allowlisted origin may read the API cross-origin', async ({ page }) => {
	await page.goto('http://localhost:5174/');
	const res = await fetchFromPage(page, API);
	expect(res.ok, `expected the allowlisted origin to succeed, got ${res.error}`).toBe(true);
	expect(res.status).toBe(200);
});

test('CORS: an unlisted origin is refused by the browser', async ({ page }) => {
	// Same server, same port — only the host spelling differs, and that alone
	// puts the page on an origin the backend does not list.
	await page.goto('http://127.0.0.1:5174/');
	const res = await fetchFromPage(page, API);
	expect(res.ok, `unlisted origin read the API: ${res.status} ${res.body}`).toBe(false);
});
