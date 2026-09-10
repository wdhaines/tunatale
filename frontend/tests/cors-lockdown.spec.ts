import { expect, PORTS, test } from './fixtures';

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

/**
 * No service worker for this spec — it is a confound, and it WAS the prime
 * suspect in `tunatale-vnf.15`. ⚠️ Point 2 below is history, not a live
 * hypothesis: the SEGV has since recurred in a spec that does no cross-origin
 * fetch (tooltip-popover, 2026-08-18) and then on the FULL browser, not
 * `chrome-headless-shell` (transcript-overflow, 2026-09-08 — tunatale-1l26.6).
 * Blocking the worker stays for point 1 alone.
 *
 * SvelteKit auto-registers `src/service-worker.ts`, in dev too, and it registers
 * **per origin** — so this file's two origins each get their own registration in
 * the same browser profile. Measured 2026-08-17: a *first* visit to an origin
 * leaves the worker installing and not yet controlling (`controller: null`),
 * while a *revisit* comes back activated and controlling. Nearly every other
 * spec visits `localhost:5174` via `baseURL`, so whether this spec's fetch runs
 * through a controlling worker's `fetch` handler depends on what ran before it.
 *
 * That state-dependence is why blocking it matters twice over:
 *
 * 1. **It removes a confound the docstring above only controls for.** That
 *    paragraph argues the localhost/127.0.0.1 pairing protects the negative case
 *    against "a `Failed to fetch` that came from a service worker". True — but
 *    not having one is better than compensating for one.
 * 2. **It is the leading hypothesis for the SEGV.** `tunatale-vnf.15` is a
 *    `chrome-headless-shell` crash at a *fixed* fault address (`cr2: 0x1b0`)
 *    that surfaces as `browser.newContext: Target page, context or browser has
 *    been closed`. Five e2e failures in ~60 CI runs; this spec is the named
 *    failure in two of the three with a confirmed signature, and the last thing
 *    to execute before the crash in the third. A cross-origin **credentialed**
 *    fetch passing through a service worker's `fetch` handler is the one exotic
 *    thing this spec does that no other spec does.
 *
 * This costs no coverage: the worker's own behaviour is covered by
 * `offline-audio.spec.ts` and `$lib/sw/audio-cache`'s unit tests. If the SEGV
 * recurs *here* with workers blocked, the hypothesis is dead — say so in the
 * bead rather than adding a retry.
 */
test.use({ serviceWorkers: 'block' });

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

/**
 * The endpoint both cases probe. It must be one whose answer the page could
 * only have read if CORS let it, and whose status carries no OTHER meaning.
 *
 * ⚠️ It was `/api/health` until 2026-09-10, and that was a flake
 * (tunatale-yp7b). Health returns 503 whenever one of its four dependency
 * probes exceeds its 2 s budget — correctly, since the container healthcheck
 * reads the status code — and a loaded gate can make a cold SQLite read that
 * slow. The cross-origin read had still SUCCEEDED (`res.ok` true) while the
 * spec failed on `503`: a test of the CORS seam going red on a verdict about
 * database latency. Reproduced deterministically by pointing AUDIO_DIR at a
 * missing directory: `Expected: 200 / Received: 503` at the status line, with
 * the CORS assertion one line above it passing.
 *
 * `/api/auth/status` has neither problem. It is unauthenticated by design
 * (it is asked before a session exists), touches no database or disk, and its
 * body is pinned to one boolean by `test_status_leaks_nothing_beyond_the_flag`
 * — so asserting the exact body is stable, and it is a STRONGER claim than a
 * status code: it proves the browser handed the response body to the page,
 * which is precisely what CORS decides.
 */
const PROBE_PATH = '/api/auth/status';

test('CORS: an allowlisted origin may read the API cross-origin', async ({ page, backendURL }) => {
	// The page origin must be the worker's OWN frontend port, spelled
	// `localhost` — that spelling is what the worker's backend CORS_ORIGINS
	// allowlists. Relative goto('/') would land here too, but keeping the
	// absolute form makes the localhost/127.0.0.1 pairing below visible.
	await page.goto(`http://localhost:${PORTS.frontend()}/`);
	const res = await fetchFromPage(page, `${backendURL}${PROBE_PATH}`);
	expect(res.ok, `expected the allowlisted origin to succeed, got ${res.error}`).toBe(true);
	expect(res.status).toBe(200);
	// AUTH_ENABLED is 'true' for every e2e backend (playwright.config.ts).
	expect(JSON.parse(res.body ?? 'null')).toEqual({ auth_enabled: true });
});

test('CORS: an unlisted origin is refused by the browser', async ({ page, backendURL }) => {
	// Same server, same port, same path — only the host spelling differs, and
	// that alone puts the page on an origin the backend does not list.
	await page.goto(`http://127.0.0.1:${PORTS.frontend()}/`);
	const res = await fetchFromPage(page, `${backendURL}${PROBE_PATH}`);
	expect(res.ok, `unlisted origin read the API: ${res.status} ${res.body}`).toBe(false);
});
