<script lang="ts">
	/** The Reference tab's index: the active language's supplemental pages. */
	import { referenceStore } from '#lib/stores/reference.svelte.js';
	import { t } from '#lib/i18n/i18n.svelte.js';
</script>

<svelte:head><title>{t('reference.title')} · TunaTale</title></svelte:head>

<main class="reference">
	<h1>{t('reference.title')}</h1>
	{#if referenceStore.pages.length === 0}
		<p class="empty">{t('reference.none')}</p>
	{:else}
		<ul class="pages">
			{#each referenceStore.pages as p (p.id)}
				<li><a href="/reference/{p.id}">{p.title}</a></li>
			{/each}
		</ul>
	{/if}
</main>

<style>
	.reference {
		max-width: 60rem;
		margin: 0 auto;
		padding: 1.5rem 1rem 4rem;
	}
	h1 {
		margin: 0 0 1rem;
	}
	.pages {
		margin: 0;
		padding: 0;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.pages a {
		display: block;
		padding: 0.9rem 1rem;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		font-weight: 700;
		text-decoration: none;
	}
	.empty {
		color: var(--color-muted);
	}
</style>
