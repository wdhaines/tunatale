import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	webServer: {
		command: 'cd .. && ./start-dev.sh',
		url: 'http://localhost:5173',
		reuseExistingServer: true,
		timeout: 120000
	},
	testDir: 'tests',
	testMatch: /(.+\.)?(test|spec)\.[jt]s/,
	timeout: 30000,
	retries: process.env.CI ? 2 : 0,
	use: {
		baseURL: 'http://localhost:5173',
		trace: 'on-first-retry'
	},
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'] }
		}
	]
});
