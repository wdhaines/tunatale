import { execSync } from 'node:child_process';
import { rmSync } from 'node:fs';

import { defineConfig, devices } from '@playwright/test';

// Worker count, overridable so a spare-core machine isn't stuck at the
// measured-safe default. `workers: 2` used to be a literal below with the
// comment "measure at 2 before building for N" — this IS building for N.
// Every backend/frontend pair below is generated from this count, and
// tests/fixtures.ts::PORTS derives the SAME ports from Playwright's own
// TEST_PARALLEL_INDEX — change the formula in one place without the other and
// a worker silently talks to a backend that was never started (see PORTS'
// comment). tests/helpers.ts::BACKEND duplicates the backend half of the
// formula for the same reason and must also stay in lockstep.
//
// Default stays 2. Measured standalone 2026-09-03 on a 10-core (8P+2E) box,
// colima stopped, one run each — NOT more is better:
//
//   E2E_WORKERS   wall   test-reported   1-min load at start
//   2             41s    37.5s           1.82
//   4             38s    35.3s           4.84
//   6             46s    42.7s           10.14
//   8             46s    43.7s           54.24
//   10            53s    49.7s           47.68
//
// 4 is the fastest measured here, 2 close behind; 6/8/10 all get SLOWER as
// the load-average column shows why — each worker is its own uvicorn + vite
// preview + browser context, so past 4 this box is oversubscribed and
// thrashing outweighs the added parallelism. This did not also measure
// contention against ./test.sh's other concurrent groups (backend pytest,
// frontend vitest, peer-sync) the way the backend `-n` sweet spot in test.sh
// was — that is a different, unmeasured question, which is why the DEFAULT
// stays 2 rather than moving to the standalone-fastest 4. Re-measure with the
// same method (frontend/, `for n in …; do E2E_WORKERS=$n bun run test:e2e;
// done`) if the suite's shape or this machine changes; a 10-core answer is not
// a universal one. Override locally with `E2E_WORKERS=4 bun run test:e2e`;
// CI does not set this, so its behavior is unchanged.
// Exported so tests/global-setup.ts can guard against `--workers=N` (a CLI
// flag, which overrides this file's `workers:` below) exceeding how many
// server pairs actually got started — see its own comment for what that
// guard catches and why it is worth keeping even though this file now scales.
export const WORKER_COUNT = Number(process.env.E2E_WORKERS ?? 2);

// DB cleanup happens HERE, at module scope, before any server spawns — NOT in
// the webServer commands. All backends share the auth store
// (tunatale-test-auth.db), so an `rm -f` inside any command is a boot race:
// if one worker's backend opens the auth DB before another's rm runs, it
// holds a deleted inode, the storageState cookie stops validating on that
// worker, and every spec there redirects to /login.
//
// ⚠️ This file is evaluated by MORE than the main process — each worker loads
// it too — so the loop below MUST be guarded: unguarded, a worker's copy fires
// mid-run and deletes the DBs out from under the RUNNING backends, which
// surfaces as `sqlite3.OperationalError: no such table: sessions /
// audio_files`, 503s from /api/health, and mass failures. Setting the flag in
// our own env marks the run clean; every child process inherits it and skips.
if (!process.env.TT_E2E_DBS_CLEANED) {
	process.env.TT_E2E_DBS_CLEANED = '1';
	const dbFiles = ['../backend/tunatale-test-auth.db'];
	for (let i = 0; i < WORKER_COUNT; i++) {
		dbFiles.push(`../backend/tunatale-test-${i}.db`, `../backend/tunatale-test-no-${i}.db`);
	}
	for (const f of dbFiles) rmSync(f, { force: true });

	// Build ONCE, here, for every worker frontend to serve (see the webServer
	// entries below for why they are `vite preview` and not `vite dev`). This sits
	// inside the same guard as the rm above: the config is evaluated by every
	// worker too, and an unguarded build would have each worker rebuild into the
	// directory the running preview servers are serving.
	//
	// 2s for a 1.4 MB static SPA (adapter-static, SPA fallback), against the ~8s
	// two dev servers would cost the suite. `bun install` runs before Playwright
	// both locally and in CI's e2e job, so `bun run build` is available in both.
	execSync('bun run build', {
		stdio: 'inherit',
		env: { ...process.env, SVELTEKIT_OUT_DIR: '.svelte-kit-e2e' }
	});
}

// One backend + frontend pair per worker index, so WORKER_COUNT can be any N
// without hand-adding entries. Formulas MUST match PORTS in tests/fixtures.ts
// and BACKEND in tests/helpers.ts:
//   backend port  = 8001 + 2*i
//   frontend port = 5174 + i
// The backend stride of 2 (not 1) is a leftover, kept for exactly this reason
// — a second per-worker backend at 8002+2*i (TARGET_LANGUAGE=no) used to live
// here and was deleted 2026-08-15 (tunatale-vnf.10) along with its only
// consumer, generate-norwegian.spec.ts. Renumbering to a stride of 1 would
// save nothing (ports are free either way) and would just be gratuitous
// formula churn across three files for zero benefit.
function backendServer(i: number) {
	const port = 8001 + 2 * i;
	const frontendPort = 5174 + i;
	return {
		// Isolated DBs, dedicated port — never reuses the dev server. DB cleanup
		// lives at module scope above (see the boot-race note).
		//
		// BOTH language DBs are removed every run, and that is load-bearing: this
		// one backend serves both (see DATABASE_URLS below), so
		// language-switch.spec.ts reaches the Norwegian DB with a header.
		// tunatale-test-no.db used to be cleaned by a SECOND uvicorn on :8002,
		// deleted 2026-08-15 with the spec that was its only consumer
		// (tunatale-vnf.10) — leaving the rm behind would have made the Norwegian
		// seed accumulate one row per run, and the spec's strict text locator go
		// non-idempotent on the second one.
		command: `cd ../backend && uv run uvicorn app.main:app --host 0.0.0.0 --port ${port} --log-level error`,
		port,
		reuseExistingServer: false,
		timeout: 30000,
		env: {
			LLM_MODE: 'mock',
			PIPELINE_AUTOSTART: 'false',
			DATABASE_URL: `sqlite:///./tunatale-test-${i}.db`,
			// Redirect the Phase-5 multi-language map at test DBs. _language_db_map()
			// returns settings.database_urls verbatim when non-empty and IGNORES
			// database_url — so a developer whose .env sets DATABASE_URLS (real
			// per-language DBs) would make this "isolated" backend open the REAL
			// tunatale_sl.db/_no.db, and e2e specs (topic "ordering coffee") would
			// pollute live data. The KEY MUST BE UPPERCASE to match .env's
			// DATABASE_URLS: on case-sensitive Unix a lowercase `database_urls` is a
			// DIFFERENT os.environ key, so load_dotenv(override=False) still injects
			// the .env value and pydantic's case-insensitive read resolves to the
			// real DBs (this silently wiped tunatale_sl.db twice — 2026-06-30,
			// 2026-07-13). Every language key must be listed here to fully isolate.
			DATABASE_URLS: `{"sl":"sqlite:///./tunatale-test-${i}.db","no":"sqlite:///./tunatale-test-no-${i}.db"}`,
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
			// This backend's own frontend is on a port outside the default
			// allowlist (:5173), so cors-lockdown.spec.ts has no ALLOWED case to
			// pair its refusal against — and a lone refusal proves nothing (a dead
			// port refuses too). Listing exactly one origin also makes the spec's
			// control real: 127.0.0.1 on the same port is the same server under a
			// spelling this list does not cover.
			CORS_ORIGINS: `["http://localhost:${frontendPort}"]`,
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
			// Shared across every worker (see the boot-race comment above): each
			// worker's own DB pair holds no accounts, only this one does. Never
			// the real ./auth.db.
			AUTH_DATABASE_URL: 'sqlite:///./tunatale-test-auth.db'
		}
	};
}

function frontendServer(i: number) {
	const port = 5174 + i;
	const backendPort = 8001 + 2 * i;
	return {
		// Serves the PRODUCTION BUILD (see the shared `bun run build` above),
		// proxying /api to this worker's own backend.
		//
		// This was `npm run dev` until 2026-09-01. Vite dev servers transform the
		// whole app on demand, once each, and that work landed on an already-
		// saturated box: the gate is CPU-throughput-bound, not schedule-bound
		// (measured — backend pytest 32s alone / 56s in-gate, vitest 16s / 34s,
		// peer-sync 18s / 39s). Swapping both for `vite preview` over one shared
		// build took e2e 50s -> 42s and the whole gate 93s -> 86s, with all 52
		// specs passing unchanged.
		//
		// ⚠️ WHAT THIS TRADES AWAY, stated rather than hidden: the gate no
		// longer exercises `vite dev`. A dev-server-only regression — Vite
		// pre-bundling deps at boot is the known class — is now invisible to
		// it. Accepted because the deployed shape is what this suite is FOR:
		// AUTH_ENABLED is on for the same reason, and the service-worker spec
		// now runs against a build where service workers actually activate
		// (vite.config.ts notes HMR and SWs conflict), which is where it
		// belongs.
		//
		// `--strictPort` so a port already in use FAILS instead of silently
		// serving a different worker's app to this one.
		command: `SVELTEKIT_OUT_DIR=.svelte-kit-e2e bun run preview -- --port ${port} --strictPort`,
		url: `http://localhost:${port}`,
		reuseExistingServer: false,
		timeout: 30000,
		env: { API_PORT: String(backendPort) }
	};
}

export default defineConfig({
	globalSetup: './tests/global-setup.ts',
	webServer: [
		...Array.from({ length: WORKER_COUNT }, (_, i) => backendServer(i)),
		...Array.from({ length: WORKER_COUNT }, (_, i) => frontendServer(i))
	],
	testDir: 'tests',
	// E2E specs use `.spec.ts`. Vitest unit tests under `tests/` (e.g.,
	// `coverage-gate.test.ts`) use `.test.ts` and must NOT be collected here.
	testMatch: /\.spec\.[jt]s/,
	timeout: 30000,
	// Each worker owns a backend (backendServer above) with its own app DB pair
	// (tunatale-test-{i}.db, tunatale-test-no-{i}.db) and its own frontend port
	// — tests/fixtures.ts::PORTS and tests/helpers.ts::BACKEND must stay in
	// lockstep, per the WORKER_COUNT comment at the top of this file. The auth
	// DB is SHARED (AUTH_DATABASE_URL in backendServer): sessions are
	// token_hash → user_id rows, so one storageState cookie validates against
	// any worker's backend.
	workers: WORKER_COUNT,
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
