import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';
import { readFileSync } from 'node:fs';

// SSL (HTTPS) is opt-in via VITE_SSL_ENABLED=true. The dev script (start-dev.sh)
// sets this; E2E tests and CI don't, so they keep plain HTTP which avoids cert
// dependency and proxy-protocol mismatch with the E2E backend (HTTP-only).
const USE_SSL = process.env.VITE_SSL_ENABLED === 'true';
const API_PROTO = USE_SSL ? 'https' : 'http';

// Expose the dev server beyond localhost so a Tailscale-connected phone can reach
// it. `host: true` binds all interfaces (incl. the Tailscale address).
// `allowedHosts: ['.ts.net']` lets Vite's host check accept the Mac's MagicDNS
// hostname (it otherwise rejects non-IP/non-localhost Host headers); the suffix
// match is scoped to Tailscale MagicDNS only.
// Shared by `server` (vite dev) and `preview` (vite preview, used by the
// `start-dev.sh --prod` build-serve path). The service worker only activates
// against a production build — HMR and SWs conflict — so the phone-facing
// offline mode runs `vite preview`, which needs the same host/HTTPS/proxy wiring.
const serverOptions = {
	host: true,
	...(USE_SSL && {
		https: {
			key: readFileSync('../certs/localhost-key.pem'),
			cert: readFileSync('../certs/localhost.pem'),
		}
	}),
	allowedHosts: ['.ts.net'],
	proxy: USE_SSL
		? { '/api': { target: `${API_PROTO}://localhost:${process.env.API_PORT ?? 8000}`, secure: false } }
		: { '/api': `http://localhost:${process.env.API_PORT ?? 8000}` }
};

export default defineConfig({
	plugins: [sveltekit()],
	server: serverOptions,
	preview: serverOptions,
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
