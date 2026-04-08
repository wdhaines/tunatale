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
			exclude: ['src/**/*.test.ts', 'src/**/*.d.ts'],
			thresholds: {
				statements: 90,
				branches: 80,
				functions: 90,
				lines: 90
			}
		}
	}
});
