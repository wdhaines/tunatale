<script lang="ts">
	import { onMount } from 'svelte';
	import type { QueueStats } from '$lib/api';
	import { api } from '$lib/api';
	import QueueStatsWidget from '$lib/components/QueueStatsWidget.svelte';
	import type { ReviewQueueItem } from '$lib/api';
	import DrillCard from '$lib/components/DrillCard.svelte';

	type QueueItem = { item: ReviewQueueItem; direction: 'recognition' | 'production' };

	let queue: QueueItem[] = $state([]);
	let loading = $state(true);
	let error = $state('');
	let reviewed = $state(0);
	let stats = $state<QueueStats | null>(null);

	// The server is the source of truth: every grade refetches /review-queue and
	// /queue-stats so the local view tracks the server's authoritative ordering
	// (cutoff-aware ready/pending split, sibling burying, newSpread). The user
	// always sees queue[0]; we never mutate the queue locally between fetches.
	let current = $derived(queue[0]);
	let done = $derived(!loading && !error && queue.length === 0);

	async function refreshFromServer() {
		try {
			const [queueStats, queueData] = await Promise.all([
				api.fetchQueueStats(),
				api.fetchReviewQueue(),
			]);
			stats = queueStats;
			queue = queueData.queue.map(item => ({ item, direction: item.direction }));
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	onMount(async () => {
		await refreshFromServer();
		loading = false;
	});

	async function rate(rating: 'again' | 'hard' | 'good' | 'easy', timeMs: number) {
		const { item, direction } = current;
		try {
			await api.submitDrill(item.id, direction, rating, timeMs);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			return;
		}
		reviewed += 1;
		await refreshFromServer();
	}
</script>

<main>
	<h1>Review</h1>

	{#if stats}
		<p class="stats">
			<QueueStatsWidget {stats} currentState={current?.item?.state} />
			{#if stats.cap_source !== 'cache'}
				<span class="source"> ({stats.cap_source})</span>
			{/if}
			{#if stats.fsrs_source !== 'cache'}
				<span class="source"> · FSRS: defaults</span>
			{/if}
		</p>
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
		<p class="badge">{current.direction === 'recognition' ? 'Recognition' : 'Production'}</p>
		<p class="badge state-{current.item.state}">{current.item.state}</p>
		<section class="card-section">
			{#key reviewed}
				<DrillCard item={current.item} direction={current.direction} onRate={rate} />
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
	.source {
		color: var(--color-muted);
		font-size: 0.8rem;
	}
	.badge.state-new { background: #e0f2fe; color: #0369a1; }
	.badge.state-learning { background: #fef3c7; color: #d97706; }
	.badge.state-review { background: #d1fae5; color: #059669; }
	.badge.state-relearning { background: #fce7f3; color: #db2777; }
	.badge.state-suspended { background: #f3f4f6; color: #6b7280; }
	.badge.state-buried { background: #f3f4f6; color: #6b7280; }
	.badge.state-known { background: #ede9fe; color: #7c3aed; }
</style>
