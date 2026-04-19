<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { SRSItemDetail } from '$lib/api';
	import DrillCard from '$lib/components/DrillCard.svelte';

	let queue: SRSItemDetail[] = $state([]);
	let index = $state(0);
	let loading = $state(true);
	let error = $state('');
	let reviewed = $state(0);

	let current = $derived(queue[index]);
	let done = $derived(!loading && !error && index >= queue.length);

	function promptSide(item: SRSItemDetail): 'image' | 'L1' {
		return (item.word_count ?? 2) === 1 ? 'image' : 'L1';
	}

	onMount(async () => {
		try {
			queue = await api.fetchDue('production');
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	async function rate(rating: 'again' | 'hard' | 'good' | 'easy') {
		try {
			await api.submitDrill(current.id, 'production', rating);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			return;
		}
		reviewed += 1;
		index += 1;
	}
</script>

<main>
	<h1>Production Review</h1>

	{#if loading}
		<p>Loading…</p>
	{:else if error}
		<p class="error">{error}</p>
	{:else if done}
		<section class="done">
			<h2>Done for today</h2>
			<p>Reviewed: {reviewed}</p>
			<a href="/">← Home</a>
		</section>
	{:else if current}
		<p class="progress">{index + 1} / {queue.length}</p>
		<section class="card-section">
			<DrillCard item={current} promptSide={promptSide(current)} onRate={rate} />
		</section>
	{/if}
</main>

<style>
	main {
		max-width: 700px;
		margin: 2rem auto;
		font-family: system-ui, sans-serif;
		padding: 0 1rem;
	}
	.card-section {
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
	}
	.done {
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		padding: 2rem;
		text-align: center;
	}
	.progress {
		color: var(--color-muted);
		font-size: 0.9rem;
	}
	.error {
		color: var(--color-danger);
	}
</style>
