<script lang="ts">
	import { page } from '$app/stores';
	import { t } from '$lib/i18n/i18n.svelte';
</script>

<main>
	<h1>{$page.status}</h1>
	<p>{$page.error?.message ?? t('error.genericFallback')}</p>
	<a href="/">← {t('error.backToTunaTale')}</a>
</main>

<style>
	main {
		max-width: 700px;
		margin: 4rem auto;
		padding: 0 1rem;
		text-align: center;
	}
	h1 {
		font-size: 2.5rem;
		color: var(--color-muted);
	}
	a {
		color: var(--color-primary);
	}

	@media (min-width: 641px) {
		h1 {
			font-size: 4rem;
		}
	}
</style>
