import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	globalSetup: './tests/global-setup.ts',
	webServer: [
		{
			// Test backend: isolated DB, dedicated port — never reuses dev server
			// rm -f ensures a clean DB every run (globalSetup runs AFTER webServer starts)
			command: 'cd ../backend && rm -f tunatale-test.db && uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --log-level error',
			port: 8001,
			reuseExistingServer: false,
			timeout: 30000,
			env: {
				LLM_MODE: 'mock',
				PIPELINE_AUTOSTART: 'false',
				DATABASE_URL: 'sqlite:///./tunatale-test.db',
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
				DATABASE_URLS: '{"sl":"sqlite:///./tunatale-test.db","no":"sqlite:///./tunatale-test-no.db"}',
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
				// Pin the target language to Slovene: the e2e curriculum/story flows are
				// backed by Slovene LLM cassettes. A developer's .env with TARGET_LANGUAGE=no
				// (running TT as Norwegian) would otherwise generate a Norwegian prompt with
				// no cassette → 500. Uppercase matches the .env key so load_dotenv keeps it.
				TARGET_LANGUAGE: 'sl'
			}
		},
		{
			// Norwegian test backend: same image, TARGET_LANGUAGE=no, isolated DB +
			// port. Exercises the Phase-2 Norwegian generation path (story prompt +
			// nb-NO voices + syllabifier) against the Norwegian cassettes recorded in
			// e2e.json. API-level only — the frontend isn't language-switchable yet
			// (Phase 5), so the Norwegian spec hits port 8002 directly via `request`.
			command: 'cd ../backend && rm -f tunatale-test-no.db && uv run uvicorn app.main:app --host 0.0.0.0 --port 8002 --log-level error',
			port: 8002,
			reuseExistingServer: false,
			timeout: 30000,
			env: {
				LLM_MODE: 'mock',
				PIPELINE_AUTOSTART: 'false',
				DATABASE_URL: 'sqlite:///./tunatale-test-no.db',
				// See the 8001 block: UPPERCASE key (matches .env's DATABASE_URLS) and
				// every language key listed, or e2e leaks into the real per-language DBs.
				DATABASE_URLS: '{"sl":"sqlite:///./tunatale-test.db","no":"sqlite:///./tunatale-test-no.db"}',
				PIXABAY_API_KEY: '',
				lemmatizer_type: 'lowercase',
				TARGET_LANGUAGE: 'no'
			}
		},
		{
			// Test frontend: proxies /api to port 8001, dedicated port
			command: 'npm run dev -- --port 5174',
			url: 'http://localhost:5174',
			reuseExistingServer: false,
			timeout: 30000,
			env: { API_PORT: '8001' }
		}
	],
	testDir: 'tests',
	// E2E specs use `.spec.ts`. Vitest unit tests under `tests/` (e.g.,
	// `coverage-gate.test.ts`) use `.test.ts` and must NOT be collected here.
	testMatch: /\.spec\.[jt]s/,
	timeout: 30000,
	// workers: 1 — all specs share one backend DB (tunatale-test.db), so
	// parallel runs cause seed-data bleeding between specs.
	workers: 1,
	retries: process.env.CI ? 2 : 0,
	use: {
		baseURL: 'http://localhost:5174',
		trace: 'on-first-retry'
	},
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'] }
		}
	]
});
