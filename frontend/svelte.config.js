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
