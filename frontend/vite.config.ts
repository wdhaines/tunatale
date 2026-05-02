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
		include: ['src/**/*.{test,spec}.{js,ts}'],
		environment: 'jsdom',
		setupFiles: ['@testing-library/svelte/vitest'],
		coverage: {
			provider: 'v8',
			reporter: ['text'],
			include: ['src/lib/**/*.ts', 'src/lib/**/*.svelte', 'src/routes/**/*.svelte'],
			exclude: [
				'src/**/*.d.ts',
				'src/lib/index.ts',
				'src/lib/stores/DerivedTest.svelte',
				'src/routes/+layout.svelte',
				'src/routes/+error.svelte',
				'src/routes/admin/+layout.svelte',
				'src/test/**'
			],
			thresholds: {
				perFile: true,
				// Base thresholds absorb Svelte 5 codegen artifacts: every {#if} without
				// an explicit {:else} compiles to a render-nothing alternate function;
				// templates compile to multi-statement reactive update wrappers. v8 counts
				// these but they're unreachable from user code. lines:100 + branches:75
				// catches all real code paths while tolerating ~25% codegen artifacts.
				// (vitest applies global thresholds to ALL files; glob keys can only ADD
				// stricter checks for subsets, not relax them — so .ts gets a glob.)
				statements: 98,
				branches: 75,
				functions: 0,
				lines: 100,
				// .ts files have no codegen — hold them to the strict bar.
				'src/**/*.ts': {
					statements: 99,
					branches: 85,
					lines: 100
				}
			}
		}
	}
});
