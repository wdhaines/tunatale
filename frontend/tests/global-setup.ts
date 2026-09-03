// Global setup runs AFTER webServer starts in Playwright's task order — which
// is what makes the account creation below work: the backend has already
// created (and the webServer command has already removed) the auth store, so
// the CLI writes into the same SQLite file the running server reads.
//
// DB cleanup lives at module scope in playwright.config.ts (rmSync before any
// server spawns — an rm in a webServer command would race the shared auth DB).
//
// What this does: creates the E2E account, signs in through the real login
// page, and saves the cookie for every spec to reuse (see `use.storageState`).
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { request } from '@playwright/test';
import type { FullConfig } from '@playwright/test';

/** Backend/frontend server pairs `playwright.config.ts` actually starts. */
const CONFIGURED_WORKERS = 2;

const EMAIL = 'e2e@example.com';
export const STORAGE_STATE = 'tests/.auth/state.json';
/** The generated credentials, for the one spec that signs in by hand. Gitignored. */
export const CREDENTIALS = 'tests/.auth/credentials.json';

export default async function globalSetup(config: FullConfig): Promise<void> {
	// ⚠️ The worker count is capped by CONSTRUCTION, not by CPU, and exceeding it
	// fails in a way that reads like a SPEED-UP. `tests/fixtures.ts::PORTS` gives
	// worker N backend `8001 + 2N` and frontend `5174 + N`, and playwright.config
	// starts exactly two of each (:8001/:8003, :5174/:5175). At `--workers=4`,
	// workers 2 and 3 therefore address :8005/:8007 and :5176/:5177, which nothing
	// is listening on — measured 2026-09-03: 34 x ECONNREFUSED, 15-30 specs failed,
	// and the run finished in 24s against a healthy 38s BECAUSE the failures are
	// instant.
	//
	// That is how this bit before. tunatale-1l26 recorded "More Playwright workers,
	// locally too: 2/4/6 = 42/41/47s" as evidence that raising workers is flat. The
	// 4 and 6 arms were never passing runs, so the number measured nothing, and the
	// epic's stated reason (oversubscribing vCPUs) is not the mechanism either.
	// Checking the exit code would have caught it; a loud failure here means nobody
	// has to remember to.
	//
	// Raising the cap is a real change: add webServer entries AND keep PORTS in
	// lockstep. `workers: 2` in playwright.config.ts has its own note on why 2.
	if (config.workers > CONFIGURED_WORKERS) {
		throw new Error(
			`--workers=${config.workers} but only ${CONFIGURED_WORKERS} backend/frontend ` +
				`pairs are started (playwright.config.ts webServer). Workers ` +
				`${CONFIGURED_WORKERS}+ would address unstarted ports and fail with ` +
				`ECONNREFUSED — fast, and easy to mistake for a speed-up. ` +
				`Add webServer entries and update tests/fixtures.ts::PORTS to raise this.`
		);
	}

	// ⚠️ Generated per run, never committed. A literal test password in this file
	// would be a real secret-scanner finding (GitGuardian scans every commit in a
	// PR, and a later commit removing it does not clear the earlier one) — and a
	// checked-in password is a pattern worth not modelling even in fixtures.
	// There is no `--password` flag on the CLI by design; TT_AUTH_PASSWORD is one
	// of its two supported sources.
	const password = randomBytes(24).toString('hex');

	execFileSync('uv', ['run', 'python', '-m', 'app.auth.cli', 'create-user', EMAIL], {
		cwd: '../backend',
		env: {
			...process.env,
			TT_AUTH_PASSWORD: password,
			AUTH_DATABASE_URL: 'sqlite:///./tunatale-test-auth.db'
		},
		stdio: 'pipe'
	});

	// Signed in over the API, NOT through the login page.
	//
	// The UI version was written first and flaked: `goto('/login')` returns as
	// soon as the document loads, but the Svelte form handler only exists after
	// hydration, so a fast fill-and-click submitted the form NATIVELY — a plain
	// GET back to `/login?`, no request to the API, and a 30s `waitForURL`
	// timeout that blamed the wrong thing. It passed on one run and failed on
	// the next with no code change between them.
	//
	// Setup should not be racing the dev server's module graph. The login PAGE
	// is covered properly by auth-login.spec.ts, where the navigation that gets
	// there is itself client-side and therefore proves hydration happened.
	// :5174 stays hardcoded on purpose. globalSetup runs ONCE, before any worker
	// exists, so there is no worker index to read. One login through worker 0's
	// frontend suffices because cookies ignore port: a cookie set for host
	// `localhost` is sent to `localhost:5175` too, and the shared auth DB means
	// either backend validates it.
	const ctx = await request.newContext({ baseURL: 'http://localhost:5174' });
	const response = await ctx.post('/api/auth/login', { data: { email: EMAIL, password } });
	if (!response.ok()) {
		throw new Error(`E2E login failed: ${response.status()} ${await response.text()}`);
	}
	await ctx.storageState({ path: STORAGE_STATE });
	await ctx.dispose();

	// auth-login.spec.ts signs in for real, so it needs the password this run
	// generated. Written beside the cookie, under the same gitignored directory.
	mkdirSync('tests/.auth', { recursive: true });
	writeFileSync(CREDENTIALS, JSON.stringify({ email: EMAIL, password: password }));
}
