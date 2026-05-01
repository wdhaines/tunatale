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
				DATABASE_URL: 'sqlite:///./tunatale-test.db'
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
	testMatch: /(.+\.)?(test|spec)\.[jt]s/,
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
