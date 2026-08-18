import adapter from '@sveltejs/adapter-static';
import { relative, sep } from 'node:path';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	compilerOptions: {
		// defaults to rune mode for the project, execept for `node_modules`. Can be removed in svelte 6.
		runes: ({ filename }) => {
			const relativePath = relative(import.meta.dirname, filename);
			const pathSegments = relativePath.toLowerCase().split(sep);
			const isExternalLibrary = pathSegments.includes('node_modules');

			return isExternalLibrary ? undefined : true;
		}
	},
	kit: {
		// Where `svelte-kit sync` writes its generated tree. Defaults to
		// `.svelte-kit`; the TEST scripts in package.json point it at
		// `.svelte-kit-test` instead.
		//
		// Why: `sync` runs at the start of BOTH `bun run check` and vitest, and
		// rewriting the dev server's `.svelte-kit` makes vite reload the page a
		// developer is looking at. Measured 2026-08-18 with a Playwright probe
		// (a `window` marker survives HMR, dies on a reload):
		//   touch src/routes/+page.svelte -> no reload (HMR swapped the module)
		//   touch vite.config.ts          -> RELOADED  (positive control)
		//   bunx svelte-kit sync          -> RELOADED
		// A write to a SIBLING directory does not reload, which is why redirecting
		// the output is enough and no `server.watch.ignored` entry is needed —
		// and ignoring `.svelte-kit/generated` would have been actively wrong,
		// since that tree is how a newly-added route reaches the running server.
		outDir: process.env.SVELTEKIT_OUT_DIR || '.svelte-kit',

		// adapter-static: production is a plain static bundle served by any file
		// server — no Node SSR process to run, supervise, or keep patched. See
		// `.beads-tasks/briefs/design-production-deployment-2026-08.md` § Phase 0.
		//
		// `fallback: 'index.html'` makes this an SPA rather than a prerendered
		// site: nothing is prerendered (SvelteKit's `prerender` default is false
		// and no route opts in), so every path is served the same shell and the
		// client router takes over. That is already how the app behaves — the
		// three data routes declare `export const ssr = false` and `$lib/api.ts`
		// speaks to relative `/api/...` paths — so no route needed rewriting.
		//
		// The host must serve `index.html` for unknown paths or deep links 404.
		adapter: adapter({ fallback: 'index.html' })
	}
};

export default config;
