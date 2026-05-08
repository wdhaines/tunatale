<script lang="ts">
	import { onMount } from 'svelte';
	import type { QueueStats } from '$lib/api';
	import { api } from '$lib/api';
	import QueueStatsWidget from '$lib/components/QueueStatsWidget.svelte';
	import type { ReviewQueueItem } from '$lib/api';
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

	function nextNonBuriedIndex(start: number): number {
		let i = start;
		while (i < queue.length && buriedCollocationIds.has(queue[i].item.id)) {
			i++;
		}
		return i;
	}

	function localLearningCount(): number {
		const remaining = queue.slice(index).filter(q => q.item.state === 'learning' || q.item.state === 'relearning');
		return remaining.length + deferred.length;
	}

	function reapDeferred() {
		// Anki parity: called only after `index` has advanced past the just-rated
		// card. Ready deferred cards splice at `index` so they become the next
		// current — mirroring Anki's intraday_now bucket (served before main).
		const now = Date.now();
		const ready = deferred.filter(d => d.dueAt <= now);
		if (ready.length === 0) return;
		deferred = deferred.filter(d => d.dueAt > now);
		queue = [
			...queue.slice(0, index),
			...ready.map(d => ({ item: d.item, direction: d.direction })),
			...queue.slice(index),
		];
		refreshStats();
	}

	function drainDeferredAtTail() {
		// Anki parity: when main is empty, intraday_ahead cards are served at the
		// tail of the iter even before their step elapses. Append all remaining
		// deferred so the user is never stuck staring at a blank done state.
		if (index >= queue.length && deferred.length > 0) {
			queue = [...queue, ...deferred.map(d => ({ item: d.item, direction: d.direction }))];
			deferred = [];
		}
	}

	async function refreshStats() {
		try {
			stats = await api.fetchQueueStats();
			// If server has more learning cards than we have locally, top up
			if (stats.learning > localLearningCount()) {
				await topUpQueue();
			}
		} catch {
			// Silently ignore - widget will show stale data
		}
	}

	async function topUpQueue() {
		try {
			const data = await api.fetchReviewQueue();
			const existingKeys = new Set(queue.map(q => `${q.item.id}:${q.direction}`));
			const newItems = data.queue
				.map(item => ({ item, direction: item.direction as 'recognition' | 'production' }))
				.filter(q => !existingKeys.has(`${q.item.id}:${q.direction}`));
			if (newItems.length > 0) {
				// Preserve learning-first ordering: splice learning cards right after current index
				const newLearning = newItems.filter(q => q.item.state === 'learning' || q.item.state === 'relearning');
				const newOther = newItems.filter(q => !newLearning.includes(q));
				queue = [...queue.slice(0, index + 1), ...newLearning, ...queue.slice(index + 1), ...newOther];
			}
		} catch {
			// Silently ignore - queue fetch failed, will retry on next refresh
		}
	}

	onMount(async () => {
		try {
			const [queueStats, queueData] = await Promise.all([
				api.fetchQueueStats(),
				api.fetchReviewQueue(),
			]);
			stats = queueStats;
			queue = queueData.queue.map(item => ({ item, direction: item.direction }));
			// Top up if server has more learning cards than we have locally
			if (stats.learning > localLearningCount()) {
				await topUpQueue();
			}
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
				drainDeferredAtTail();
				// Refresh stats on deferred branch too
				await refreshStats();
				return;
			}
		}

		buriedCollocationIds = new Set(buriedCollocationIds).add(item.id);
		index = nextNonBuriedIndex(index + 1);
		reapDeferred();
		drainDeferredAtTail();
		// Refetch queue stats to update the widget in real-time
		await refreshStats();
	}
</script>

<main>
	<h1>Review</h1>

	{#if stats}
		<p class="stats">
			<QueueStatsWidget {stats} />
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
