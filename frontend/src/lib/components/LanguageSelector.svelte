<script lang="ts">
	import { languageStore } from '$lib/stores/language.svelte';

	// Changing the active language re-points every API request (X-TT-Language) at
	// the other language's connection. A full reload is the simplest correct way to
	// refetch all the per-page data + nav badges under the new language.
	function onChange(event: Event): void {
		const code = (event.currentTarget as HTMLSelectElement).value;
		if (code === languageStore.code) return;
		languageStore.set(code);
		window.location.reload();
	}
</script>

{#if languageStore.options.length > 1}
	<select
		class="language-selector"
		aria-label="Active language"
		title="Active language"
		value={languageStore.code}
		onchange={onChange}
	>
		{#each languageStore.options as option (option.code)}
			<option value={option.code}>{option.name}</option>
		{/each}
	</select>
{/if}

<style>
	.language-selector {
		/* Match the sibling nav controls (theme toggle): same height, pill shape,
		   design tokens. appearance:none + a token-aware caret so the box and the
		   popup follow the app's [data-theme] rather than the OS color-scheme. */
		appearance: none;
		-webkit-appearance: none;
		color-scheme: light;
		font: inherit;
		font-size: 0.88rem;
		font-weight: 600;
		height: 34px;
		padding: 0 1.7rem 0 0.7rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background-color: var(--color-surface);
		color: var(--color-text);
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M3 4.5 6 7.5 9 4.5' fill='none' stroke='%235c6672' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
		background-repeat: no-repeat;
		background-position: right 0.6rem center;
		background-size: 0.7rem;
		cursor: pointer;
		transition:
			border-color 0.15s ease,
			background-color 0.15s ease;
	}
	.language-selector:hover {
		border-color: var(--color-primary);
		background-color: var(--color-surface-2);
	}
	.language-selector:focus-visible {
		outline: 2px solid var(--color-primary);
		outline-offset: 1px;
	}
	:global(:root[data-theme='dark']) .language-selector {
		color-scheme: dark;
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M3 4.5 6 7.5 9 4.5' fill='none' stroke='%239faaa9' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
	}
</style>
