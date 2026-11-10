<script lang="ts">
	/**
	 * The verb chart page. `?row=<row id>` (e.g. `patient-on`) highlights that row, opens
	 * its phone card, and scrolls to whichever of the two is on screen: the way a card or
	 * the reader opens the chart at a form (tunatale-h0q3.47, links in a later slice).
	 */
	import { tick } from 'svelte';
	import { page } from '$app/state';
	import { api, type VerbChart as Chart } from '#lib/api.js';
	import VerbChart from '#lib/components/VerbChart.svelte';
	import { languageStore } from '#lib/stores/language.svelte.js';
	import { t } from '#lib/i18n/i18n.svelte.js';

	let chart = $state<Chart | null>(null);
	let failed = $state(false);
	const targetRow = $derived(page.url.searchParams.get('row'));

	async function load(): Promise<void> {
		failed = false;
		try {
			chart = await api.getVerbChart();
		} catch {
			chart = null;
			failed = true;
			return;
		}
		await tick();
		if (targetRow === null) return;
		// Both layouts carry the row; scroll to the one that is laid out.
		const shown = [...document.querySelectorAll(`[data-row="${CSS.escape(targetRow)}"]`)].find(
			(el) => el.getClientRects().length > 0,
		);
		shown?.scrollIntoView({ block: 'center' });
	}

	// Reload on a language switch: another language may have no chart.
	$effect(() => {
		void languageStore.code;
		load();
	});
</script>

<svelte:head><title>{chart?.title ?? t('reference.title')} · TunaTale</title></svelte:head>

<main class="chart-page">
	<div class="crumbs"><a href="/reference">{t('reference.title')}</a> ›</div>
	{#if chart}
		<h1>{chart.title}</h1>
		<VerbChart {chart} {targetRow} />
	{:else if failed}
		<p class="empty">{t('reference.noChart')}</p>
	{:else}
		<p class="empty">{t('reference.loading')}</p>
	{/if}
</main>

<style>
	.chart-page {
		max-width: 84rem;
		margin: 0 auto;
		padding: 1.5rem 1rem 4rem;
	}
	.crumbs {
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.crumbs a {
		text-decoration: none;
	}
	h1 {
		margin: 0.2rem 0 1rem;
	}
	.empty {
		color: var(--color-muted);
	}
</style>
