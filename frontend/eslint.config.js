import svelte from 'eslint-plugin-svelte';
import ts from 'typescript-eslint';
import globals from 'globals';
import oxlint from 'eslint-plugin-oxlint';

export default ts.config(
	// Svelte template rules — scoped to .svelte files by the plugin
	...svelte.configs['flat/recommended'],
	{
		files: ['**/*.svelte'],
		languageOptions: {
			globals: { ...globals.browser },
			parserOptions: {
				// Delegates <script lang="ts"> parsing to typescript-eslint
				parser: ts.parser,
				extraFileExtensions: ['.svelte'],
			},
		},
		rules: {
			// {@html} is used intentionally for Anki card HTML content
			'svelte/no-at-html-tags': 'off',
			// Standard SvelteKit <a href> and goto() don't need view-transition resolve()
			'svelte/no-navigation-without-resolve': 'off',
		},
	},
	// Suppress rules Oxlint already handles on .ts/.js files
	oxlint.configs['flat/recommended'],
	{
		ignores: [
			'.svelte-kit/',
			'build/',
			'node_modules/',
			// .svelte.ts files use Svelte 5 rune syntax in .ts context — handled by svelte-check
			'**/*.svelte.ts',
		],
	},
);
