import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			'/api': `http://localhost:${process.env.API_PORT ?? 8000}`
		}
	},
	resolve: {
		conditions: ['browser']
	},
	test: {
		include: ['src/**/*.{test,spec}.{js,ts}', 'tests/**/*.test.ts'],
		environment: 'jsdom',
		setupFiles: ['@testing-library/svelte/vitest'],
		coverage: {
			provider: 'v8',
			// `text` for human-readable summary on local runs; `json` writes
			// `coverage/coverage-final.json` which `scripts/coverage-gate.ts`
			// consumes for the 100% gate (filters Svelte 5 phantom branches).
			reporter: ['text', 'json'],
			include: ['src/lib/**/*.ts', 'src/lib/**/*.svelte', 'src/routes/**/*.svelte'],
			exclude: [
				'src/**/*.d.ts',
				'src/lib/index.ts',
				'src/lib/stores/DerivedTest.svelte',
				'src/routes/+layout.svelte',
				'src/test/**'
			]
			// No `thresholds:` block — the gate moved to scripts/coverage-gate.ts.
			// Vitest's built-in gate can't distinguish Svelte 5 phantom branches
			// from real user branches; the custom script does.
		}
	}
});
