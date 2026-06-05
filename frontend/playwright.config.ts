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
				DATABASE_URL: 'sqlite:///./tunatale-test.db',
				// E2E doesn't test lemmatization; force the fast lowercase lemmatizer
				// so a local `lemmatizer_type=classla` in .env doesn't make the
				// backend pay classla's ~26s model load and blow the webServer timeout.
				// Key MUST be lowercase to match the .env key: main.py's load_dotenv()
				// loads the lowercase `lemmatizer_type` from .env, and on case-sensitive
				// Unix an uppercase `LEMMATIZER_TYPE` is a *different* key that .env wins over.
				lemmatizer_type: 'lowercase'
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
