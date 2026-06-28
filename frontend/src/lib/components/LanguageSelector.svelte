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
		font: inherit;
		padding: 0.2rem 0.4rem;
		border-radius: 6px;
		border: 1px solid var(--border, #ccc);
		background: var(--surface, #fff);
		color: inherit;
		cursor: pointer;
	}
</style>
