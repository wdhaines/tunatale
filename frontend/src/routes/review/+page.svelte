<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { SRSItemDetail, QueueStats } from '$lib/api';
	import DrillCard from '$lib/components/DrillCard.svelte';

	type Direction = 'recognition' | 'production';
	type QueueItem = { item: SRSItemDetail; direction: Direction };

	let queue: QueueItem[] = $state([]);
	let index = $state(0);
	let loading = $state(true);
	let error = $state('');
	let reviewed = $state(0);
	let stats = $state<QueueStats | null>(null);

	let current = $derived(queue[index]);
	let done = $derived(!loading && !error && index >= queue.length);

	function interleave(rec: SRSItemDetail[], prod: SRSItemDetail[]): QueueItem[] {
		const result: QueueItem[] = [];
		const maxLen = Math.max(rec.length, prod.length);
		for (let i = 0; i < maxLen; i++) {
			if (i < rec.length) result.push({ item: rec[i], direction: 'recognition' });
			if (i < prod.length) result.push({ item: prod[i], direction: 'production' });
		}
		return result;
	}

	function getPromptSide(item: SRSItemDetail, direction: Direction): 'L2' | 'L1' | 'image' {
		if (direction === 'recognition') return 'L2';
		if ((item.word_count ?? 2) === 1 && item.image_url) return 'image';
		return 'L1';
	}

	onMount(async () => {
		try {
			const queueStats = await api.fetchQueueStats();
			stats = queueStats;
			const recCap = Math.ceil(queueStats.daily_new_cap / 2);
			const prodCap = Math.floor(queueStats.daily_new_cap / 2);

			const fetches: Promise<SRSItemDetail[]>[] = [
				api.fetchDue('recognition'),
				api.fetchDue('production'),
				api.fetchNew('recognition', recCap),
			];
			if (prodCap > 0) {
				fetches.push(api.fetchNew('production', prodCap));
			}
			const [dueRec, dueProd, newRec, newProd = []] = await Promise.all(fetches);
			queue = interleave([...dueRec, ...newRec], [...dueProd, ...newProd]);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	async function rate(rating: 'again' | 'hard' | 'good' | 'easy') {
		const { item, direction } = current;
		try {
			await api.submitDrill(item.id, direction, rating);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			return;
		}
		reviewed += 1;
		index += 1;
	}
</script>

<main>
	<h1>Review</h1>

	{#if stats}
		<p class="stats">New {stats.new} · Due {stats.due}{stats.cap_source !== 'cache' ? ` (${stats.cap_source})` : ''}</p>
	{/if}

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
		<p class="badge">{current.direction === 'recognition' ? 'Recognition' : 'Production'}</p>
		<section class="card-section">
			{#key index}
				<DrillCard item={current.item} promptSide={getPromptSide(current.item, current.direction)} onRate={rate} />
			{/key}
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
	.badge {
		display: inline-block;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		padding: 0.2rem 0.6rem;
		border-radius: 4px;
		background: var(--color-border);
		color: var(--color-muted);
		margin-bottom: 0.5rem;
	}
	.error {
		color: var(--color-danger);
	}
	.stats {
		color: var(--color-muted);
		font-size: 0.9rem;
		margin-bottom: 0.5rem;
	}
</style>
