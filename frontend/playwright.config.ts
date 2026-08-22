import { rmSync } from 'node:fs';

import { defineConfig, devices } from '@playwright/test';

// DB cleanup happens HERE, at module scope, before any server spawns — NOT in
// the webServer commands. Both backends share the auth store
// (tunatale-test-auth.db), so an `rm -f` inside either command is a boot race:
// if worker 1's backend opens the auth DB before worker 0's rm runs, it holds
// a deleted inode, the storageState cookie stops validating on that worker,
// and every spec there redirects to /login.
//
// ⚠️ This file is evaluated by MORE than the main process — each worker loads
// it too — so the loop below MUST be guarded: unguarded, a worker's copy fires
// mid-run and deletes the DBs out from under the RUNNING backends, which
// surfaces as `sqlite3.OperationalError: no such table: sessions /
// audio_files`, 503s from /api/health, and mass failures. Setting the flag in
// our own env marks the run clean; every child process inherits it and skips.
if (!process.env.TT_E2E_DBS_CLEANED) {
	process.env.TT_E2E_DBS_CLEANED = '1';
	for (const f of [
		'../backend/tunatale-test-auth.db',
		'../backend/tunatale-test-0.db',
		'../backend/tunatale-test-1.db',
		'../backend/tunatale-test-no-0.db',
		'../backend/tunatale-test-no-1.db'
	]) rmSync(f, { force: true });
}

export default defineConfig({
	globalSetup: './tests/global-setup.ts',
	webServer: [
		{
			// Worker-0 test backend: isolated DBs, dedicated port — never reuses dev
			// server. DB cleanup lives at module scope above (see the boot-race note).
			//
			// BOTH language DBs are removed every run, and that is load-bearing: this one
			// backend serves both (see DATABASE_URLS below), so language-switch.spec.ts
			// reaches the Norwegian DB with a header. tunatale-test-no.db used to be
			// cleaned by a SECOND uvicorn on :8002, deleted 2026-08-15 with the spec
			// that was its only consumer (tunatale-vnf.10) — leaving the rm behind
			// would have made the Norwegian seed accumulate one row per run, and the
			// spec's strict text locator go non-idempotent on the second one.
			command: 'cd ../backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --log-level error',
			port: 8001,
			reuseExistingServer: false,
			timeout: 30000,
			env: {
				LLM_MODE: 'mock',
				PIPELINE_AUTOSTART: 'false',
				DATABASE_URL: 'sqlite:///./tunatale-test-0.db',
				// Redirect the Phase-5 multi-language map at test DBs. _language_db_map()
				// returns settings.database_urls verbatim when non-empty and IGNORES
				// database_url — so a developer whose .env sets DATABASE_URLS (real
				// per-language DBs) would make this "isolated" backend open the REAL
				// tunatale_sl.db/_no.db, and e2e specs (topic "ordering coffee") would
				// pollute live data. The KEY MUST BE UPPERCASE to match .env's
				// DATABASE_URLS: on case-sensitive Unix a lowercase `database_urls` is a
				// DIFFERENT os.environ key, so load_dotenv(override=False) still injects
				// the .env value and pydantic's case-insensitive read resolves to the real
				// DBs (this silently wiped tunatale_sl.db twice — 2026-06-30, 2026-07-13).
				// Every language key must be listed here to fully isolate.
				DATABASE_URLS: '{"sl":"sqlite:///./tunatale-test-0.db","no":"sqlite:///./tunatale-test-no-0.db"}',
				// Startup DB-backup rotation would otherwise snapshot the throwaway
				// test DB into the real ~/.tunatale/db-backups. 0 disables it for E2E.
				DB_BACKUP_KEEP_DAYS: '0',
				// Add-time vocab media (POST /items, /listen) fetches image+audio when
				// a Pixabay key is set. E2E seeds cards via those endpoints, so a real
				// key in .env makes the suite hit Pixabay/Forvo live (slow, flaky).
				// Empty it so seeding stays offline. load_dotenv(override=False) keeps
				// this preset value; key is uppercase to match the .env's PIXABAY_API_KEY.
				PIXABAY_API_KEY: '',
				// E2E doesn't test lemmatization; force the fast lowercase lemmatizer
				// so a local `lemmatizer_type=classla` in .env doesn't make the
				// backend pay classla's ~26s model load and blow the webServer timeout.
				// Key MUST be lowercase to match the .env key: main.py's load_dotenv()
				// loads the lowercase `lemmatizer_type` from .env, and on case-sensitive
				// Unix an uppercase `LEMMATIZER_TYPE` is a *different* key that .env wins over.
				lemmatizer_type: 'lowercase',
				// The e2e frontend is on :5174, not the :5173 the default allowlist
				// names, so cors-lockdown.spec.ts would otherwise have no ALLOWED
				// case to pair its refusal against — and a lone refusal proves
				// nothing (a dead port refuses too). Listing exactly one origin also
				// makes the spec's control real: 127.0.0.1:5174 is the same server
				// under a spelling this list does not cover.
				CORS_ORIGINS: '["http://localhost:5174"]',
				// Pin the target language to Slovene: the e2e curriculum/story flows are
				// backed by Slovene LLM cassettes. A developer's .env with TARGET_LANGUAGE=no
				// (running TT as Norwegian) would otherwise generate a Norwegian prompt with
				// no cassette → 500. Uppercase matches the .env key so load_dotenv keeps it.
				TARGET_LANGUAGE: 'sl',
				// ⚠️ AUTH ON, deliberately — this suite runs the deployed shape.
				//
				// With the gate off, every spec here would prove the app works in a
				// configuration production never uses, and the login journey would have
				// no real stack to walk. So the backend requires a session, globalSetup
				// creates the account and signs in once, and `use.storageState` below
				// hands that cookie to every spec. Only auth-login.spec.ts opts out.
				//
				// The session cookie is `Secure` and this suite is plain http. That
				// works because browsers treat `localhost` as a trustworthy origin —
				// measured 2026-08-18 in the pinned chromium: cookie set over
				// http://localhost and returned on the next request, `me` → 200. Do
				// NOT generalise that to a deployment: over http on any other host the
				// cookie is stored and never sent back, and it reads exactly like a
				// broken server (see docs/deployment.md § Signing in).
				AUTH_ENABLED: 'true',
				// Its own store, removed by the rm above so each run starts with no
				// accounts. Never the real ./auth.db.
				AUTH_DATABASE_URL: 'sqlite:///./tunatale-test-auth.db'
			}
		},
		// A SECOND BACKEND ON :8002 (TARGET_LANGUAGE=no) USED TO LIVE HERE. It was
		// deleted 2026-08-15 (tunatale-vnf.10) along with generate-norwegian.spec.ts,
		// its only consumer — a spec that never opened a browser and whose
		// assertions were all backend claims, now made in pytest.
		//
		// ⚠️ ONE CLAIM WENT UNOWNED WITH IT, stated rather than hidden: Playwright
		// waits for a webServer's port to listen, so booting that process was an
		// implicit assertion that a uvicorn with TARGET_LANGUAGE=no starts at all.
		// Nothing asserts that now — pytest's ASGITransport builds the app in-process
		// and cannot make a claim about a configured OS process. It is a deployment
		// topology claim and belongs with the deploy work (tunatale-kbb), not here.
		// language-switch.spec.ts does NOT recover it: the frontend proxies /api to
		// :8001 only, so that journey reaches the Norwegian DB through :8001's
		// per-language connection map, never through a Norwegian-configured process.
		{
			// Worker-1 test backend: same shape as worker-0's, own app DB pair and
			// CORS origin. Differences from worker 0 are ONLY: port, DATABASE_URL,
			// DATABASE_URLS, CORS_ORIGINS — every env var below must stay in
			// lockstep with worker 0's, comments included. AUTH_DATABASE_URL is
			// deliberately SHARED (see its comment in worker 0's env).
			command: 'cd ../backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8003 --log-level error',
			port: 8003,
			reuseExistingServer: false,
			timeout: 30000,
			env: {
				LLM_MODE: 'mock',
				PIPELINE_AUTOSTART: 'false',
				DATABASE_URL: 'sqlite:///./tunatale-test-1.db',
				DATABASE_URLS: '{"sl":"sqlite:///./tunatale-test-1.db","no":"sqlite:///./tunatale-test-no-1.db"}',
				DB_BACKUP_KEEP_DAYS: '0',
				PIXABAY_API_KEY: '',
				lemmatizer_type: 'lowercase',
				CORS_ORIGINS: '["http://localhost:5175"]',
				TARGET_LANGUAGE: 'sl',
				AUTH_ENABLED: 'true',
				AUTH_DATABASE_URL: 'sqlite:///./tunatale-test-auth.db'
			}
		},
		{
			// Test frontend: proxies /api to port 8001, dedicated port
			command: 'SVELTEKIT_OUT_DIR=.svelte-kit-e2e npm run dev -- --port 5174',
			url: 'http://localhost:5174',
			reuseExistingServer: false,
			timeout: 30000,
			env: { API_PORT: '8001' }
		},
		{
			// Worker-1 test frontend: proxies /api to :8003. Distinct SVELTEKIT_OUT_DIR
			// or the two dev servers clobber each other's build output.
			command: 'SVELTEKIT_OUT_DIR=.svelte-kit-e2e-1 npm run dev -- --port 5175',
			url: 'http://localhost:5175',
			reuseExistingServer: false,
			timeout: 30000,
			env: { API_PORT: '8003' }
		}
	],
	testDir: 'tests',
	// E2E specs use `.spec.ts`. Vitest unit tests under `tests/` (e.g.,
	// `coverage-gate.test.ts`) use `.test.ts` and must NOT be collected here.
	testMatch: /\.spec\.[jt]s/,
	timeout: 30000,
	// workers: 2 — each worker owns a backend (:8001 / :8003) with its own app
	// DB pair (tunatale-test-{0,1}.db, tunatale-test-no-{0,1}.db) and its own
	// frontend port (:5174 / :5175); tests/fixtures.ts::PORTS must stay in
	// lockstep. The auth DB is SHARED (AUTH_DATABASE_URL below): sessions are
	// token_hash → user_id rows, so one storageState cookie validates against
	// either backend. Two is deliberate — measure at 2 before building for N.
	workers: 2,
	// retries: 0 EVERYWHERE, deliberately. This was `process.env.CI ? 2 : 0`,
	// which had never once executed its CI branch — Playwright did not run in CI
	// at all until the `e2e` job was added (tunatale-as5), so the retry policy was
	// dead config that would have silently switched on the day E2E went remote.
	//
	// Turning it on was the wrong move at exactly the wrong moment: this suite has
	// a KNOWN open flake (`card-image.spec.ts` thumbnail assertion, tunatale-vnf.3)
	// which may not be a flake at all — the upload round-trip may be genuinely
	// racy, in which case a user sees a missing thumbnail after upload. Two retries
	// would have converted that signal into a silent green on its first CI run.
	//
	// The cost is accepted and named: CI can go intermittently red on vnf.3 until
	// it is diagnosed. That is the correct failure mode. A retry does not make the
	// race go away, it makes it someone else's problem later.
	retries: 0,
	use: {
		baseURL: 'http://localhost:5174',
		// The signed-in cookie globalSetup produced. Every spec starts logged in,
		// which is what the app's own specs are about; auth-login.spec.ts overrides
		// this with an empty state to test the logged-out path.
		storageState: 'tests/.auth/state.json',
		// Was 'on-first-retry', which with retries:0 would capture NOTHING — the
		// two settings are coupled and changing one without the other silently
		// disables tracing. A CI failure is not locally reproducible by definition
		// (different machine, timing, shared runner), so the trace is the only
		// artifact that makes a remote red debuggable; the `e2e` job uploads it.
		trace: 'retain-on-failure'
	},
	projects: [
		{
			name: 'chromium',
			// channel: 'chromium' selects the FULL browser. Without it Playwright
			// resolves headless runs to `chrome-headless-shell`, which took
			// `Received signal 11 SEGV_MAPERR` on the runner during the coarse-pointer
			// project's browser.newContext — killing the browser and taking the rest
			// of the run with it (4 failed, 30 skipped, 1 did not run).
			//
			// Set unconditionally, NOT behind `process.env.CI`. An env-forked browser
			// would mean local and CI test different binaries, which is precisely the
			// divergence this change exists to remove — and the retries field next
			// door is what a CI-only fork decays into: config nobody has ever seen run.
			// `playwright install chromium` provides both binaries, so this costs no
			// extra download.
			//
			// ⚠️⚠️ IT MUST LIVE INSIDE `use`, AND IT DID NOT UNTIL 2026-08-18.
			// `channel` is a TestOptions field, so as a sibling of `use` — where it
			// sat from 18f3a89 until now — Playwright silently ignored it and every
			// run, local and CI, kept using chrome-headless-shell. Nothing said so:
			// the config parsed, the comment above described a mitigation that was
			// never in effect, and the suite went on segfaulting (tunatale-vnf.15,
			// three sightings in three different specs).
			//
			// Measured both ways before and after, because "it parses" proved
			// nothing the first time:
			//   raw chromium.launch({channel:'chromium'}) → chromium-1234           (full)
			//   the RUNNER, channel beside `use`         → chromium_headless_shell-1234
			//   the RUNNER, channel inside `use`         → chromium-1234           (full)
			// The probe is `ps -ax -o command= | grep ms-playwright` while a spec
			// runs. Re-run it if you touch this block — an inert option here looks
			// exactly like a working one.
			//
			// The spread comes FIRST so a device descriptor cannot clobber it.
			use: { ...devices['Desktop Chrome'], channel: 'chromium' }
		}
	]
});
