<script lang="ts">
	import ReviewSessionCards from '$lib/components/ReviewSessionCards.svelte';
	import { orderSessions } from '$lib/reading/nextReviewSession';
	import { t } from '$lib/i18n/i18n.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Newest first, read BACKWARDS from the order the pager walks: the row above a
	// session is the one its "next →" link opens. Reversing the one ordering is
	// not the same as sorting by date again — a second comparator would be a
	// second answer to "what comes next" (bd tunatale-e6fq).
	const ordered = $derived(orderSessions(data.sessions).reverse());
</script>

<main>
	<a class="back" href="/">← {t('curriculum.backToLessons')}</a>
	<h1>{t('reviewSessionsIndex.title')}</h1>
	{#if ordered.length === 0}
		<p class="muted">{t('home.noReviewSessions')}</p>
	{:else}
		<ReviewSessionCards sessions={ordered} />
	{/if}
</main>

<style>
	main {
		max-width: 700px;
		margin: 1.5rem auto;
		padding: 0 1rem;
	}
	.back {
		display: inline-block;
		margin-bottom: 1rem;
		color: var(--color-muted);
		text-decoration: none;
		font-size: 0.9rem;
		font-weight: 600;
	}
	.back:hover {
		color: var(--color-primary);
	}
	h1 {
		/* The lesson page's h1 with a bottom margin: there the header grid spaces
		   it; here the list follows directly and sat flush against it. */
		margin: 0 0 1rem;
		font-size: 1.4rem;
		font-weight: 800;
		letter-spacing: -0.01em;
	}
	.muted {
		color: var(--color-muted);
	}
</style>