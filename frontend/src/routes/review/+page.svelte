	<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { ReviewQueueItem, QueueStats } from '$lib/api';
	import DrillCard from '$lib/components/DrillCard.svelte';

	type QueueItem = { item: ReviewQueueItem; direction: 'recognition' | 'production' };
	type DeferredCard = QueueItem & { dueAt: number };

	let queue: QueueItem[] = $state([]);
	let index = $state(0);
	let loading = $state(true);
	let error = $state('');
	let reviewed = $state(0);
	let stats = $state<QueueStats | null>(null);
	let buriedCollocationIds: Set<number> = $state(new Set());
	let deferred: DeferredCard[] = $state([]);

	let current = $derived(queue[index]);
	let done = $derived(!loading && !error && index >= queue.length && deferred.length === 0);
	let upcomingUnburiedCount = $derived(
		queue.slice(index + 1).filter(q => !buriedCollocationIds.has(q.item.id)).length
	);
	let progressCurrent = $derived(reviewed + 1);
	let progressTotal = $derived(progressCurrent + upcomingUnburiedCount);

	function nextNonBuriedIndex(start: number): number {
		let i = start;
		while (i < queue.length && buriedCollocationIds.has(queue[i].item.id)) {
			i++;
		}
		return i;
	}

	function reapDeferred() {
		const now = Date.now();
		const ready = deferred.filter(d => d.dueAt <= now);
		if (ready.length === 0) return;
		deferred = deferred.filter(d => d.dueAt > now);
		queue = [...queue, ...ready.map(d => ({ item: d.item, direction: d.direction }))];
	}

	onMount(async () => {
		try {
			const [queueStats, queueData] = await Promise.all([
				api.fetchQueueStats(),
				api.fetchReviewQueue(),
			]);
			stats = queueStats;
			queue = queueData.queue.map(item => ({ item, direction: item.direction }));
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	async function rate(rating: 'again' | 'hard' | 'good' | 'easy', timeMs: number) {
		const { item, direction } = current;
		let resp;
		try {
			resp = await api.submitDrill(item.id, direction, rating, timeMs);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			return;
		}
		reviewed += 1;

		if (resp.new_state === 'learning' && resp.due_at) {
			const dueAt = Date.parse(resp.due_at);
			if (dueAt > Date.now()) {
				deferred = [...deferred, { item, direction, dueAt }];
				index = nextNonBuriedIndex(index + 1);
				reapDeferred();
				return;
			}
		}

		buriedCollocationIds = new Set(buriedCollocationIds).add(item.id);
		index = nextNonBuriedIndex(index + 1);
		reapDeferred();
	}
</script>

<main>
	<h1>Review</h1>

	{#if stats}
		<p class="stats">New {stats.new} · Due {stats.due}{stats.cap_source !== 'cache' ? ` (${stats.cap_source})` : ''}{stats.fsrs_source !== 'cache' ? ' · FSRS: defaults' : ''}</p>
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
		<p class="progress">{progressCurrent} / {progressTotal}</p>
		<p class="badge">{current.direction === 'recognition' ? 'Recognition' : 'Production'}</p>
		<section class="card-section">
			{#key index}
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
