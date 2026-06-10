import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

// Expose the dev server beyond localhost so a Tailscale-connected phone can reach
// it. `host: true` binds all interfaces (incl. the Tailscale address).
// `allowedHosts: ['.ts.net']` lets Vite's host check accept the Mac's MagicDNS
// hostname (it otherwise rejects non-IP/non-localhost Host headers); the suffix
// match is scoped to Tailscale MagicDNS only.
export default defineConfig({
	plugins: [sveltekit()],
	server: {
		host: true,
		allowedHosts: ['.ts.net'],
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
			// `json` writes `coverage/coverage-final.json` which
			// `scripts/coverage-gate.ts` consumes for the 100% gate (filters
			// Svelte 5 phantom branches). No `'text'` reporter — its per-file
			// table reports raw, unfiltered v8 numbers that disagree with the
			// gate's filtered verdict. For ad-hoc debugging, add it on the CLI:
			//   bun run test:coverage --coverage.reporter=text
			reporter: ['json'],
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
