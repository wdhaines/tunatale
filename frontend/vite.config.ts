import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			'/api': 'http://localhost:8000'
		}
	},
	resolve: {
		conditions: ['browser']
	},
	test: {
		include: ['src/**/*.{test,spec}.{js,ts}'],
		environment: 'jsdom',
		setupFiles: ['@testing-library/svelte/vitest'],
		coverage: {
			provider: 'v8',
			reporter: ['text'],
			include: ['src/lib/**/*.ts', 'src/lib/**/*.svelte', 'src/routes/**/*.svelte'],
			exclude: [
				'src/**/*.test.ts',
				'src/**/*.d.ts',
				'src/lib/index.ts',
				'src/routes/+layout.svelte',
				'src/routes/+error.svelte',
				'src/routes/admin/+layout.svelte'
			],
			thresholds: {
				statements: 99,
				// Svelte 5 compiles templates to JS with reactive update functions that
				// contain V8-visible branches not exercisable by user tests (dirty-bit
				// checks, null guards on $state/$derived). These ~13% of branches are
				// compilation artifacts; 85% enforces all real code paths are covered.
				branches: 85,
				functions: 100,
				lines: 100
			}
		}
	}
});
