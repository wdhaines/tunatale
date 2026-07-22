<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import type { QueueStats } from '$lib/api';
	import { api } from '$lib/api';
	import QueueStatsWidget from '$lib/components/QueueStatsWidget.svelte';
	import type { ReviewQueueItem } from '$lib/api';
	import DrillCard from '$lib/components/DrillCard.svelte';
	import { queueStatsStore } from '$lib/stores/queueStats.svelte';
	import { syncStore } from '$lib/stores/sync.svelte';

	type QueueItem = { item: ReviewQueueItem; direction: 'recognition' | 'production' };

	// C1: lesson mode — read from URL search params.
	let lessonId = $derived($page.url.searchParams.get('lesson'));
	let curriculumId = $derived($page.url.searchParams.get('c'));
	let lessonMode = $derived(lessonId !== null);

	let queue: QueueItem[] = $state([]);
	let loading = $state(true);
	let error = $state('');
	let reviewed = $state(0);
	let stats = $state<QueueStats | null>(null);
	let reviewedPosted = $state(false);

	// The server is the source of truth: every grade refetches /review-queue and
	// /queue-stats so the local view tracks the server's authoritative ordering
	// (cutoff-aware ready/pending split, sibling burying, newSpread). The user
	// always sees queue[0]; we never mutate the queue locally between fetches.
	let current = $derived(queue[0]);
	let done = $derived(!loading && !error && queue.length === 0);

	// In lesson mode, derive scoped counts from the lesson queue so the header
	// widget underlines match the cards the user is actually studying.
	let displayStats = $derived(
		lessonMode && queue.length > 0 && stats
			? {
				...stats,
				new: queue.filter((q) => q.item.state === 'new').length,
				learning: queue.filter((q) => q.item.state === 'learning' || q.item.state === 'relearning').length,
				review: queue.filter((q) => q.item.state === 'review').length,
			}
			: stats
	);

	async function refreshFromServer(sessionStart = false) {
		try {
			const [queueStats, queueData] = await Promise.all([
				api.fetchQueueStats(),
				lessonMode
					? api.fetchLessonReviewQueue(lessonId!)
					: api.fetchReviewQueue({ sessionStart }),
			]);
			stats = queueStats;
			// Share with the nav badge so it tracks every grade live, not just on focus.
			queueStatsStore.set(queueStats);
			queue = queueData.queue.map(item => ({ item, direction: item.direction }));
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	onMount(async () => {
		// Page mount = "deck open" in Anki terms — advance the server's learning
		// cutoff so any learning card past-due since the last grade lands in the
		// ready bucket. Per-grade refetches keep the cutoff frozen.
		await refreshFromServer(true);
		loading = false;
	});

	$effect(() => {
		// /queue-stats reads Anki's collection.anki2 live, so the badge stays
		// fresh as the user grades in Anki — but only if we actually refetch.
		// Mid-session tab refocus is not a "deck open" event, so leave the
		// learning cutoff frozen (sessionStart=false).
		const onVisibility = () => {
			if (document.visibilityState === 'visible') {
				refreshFromServer(false);
			}
		};
		document.addEventListener('visibilitychange', onVisibility);
		return () => document.removeEventListener('visibilitychange', onVisibility);
	});

	$effect(() => {
		// A peer-sync rebuilds the server's frozen queue at sync time (sync_pull
		// clears + rebuilds session_main_queue). The header badge already refetches
		// via the layout's syncStore subscription; the review *body* must too, or
		// the queue keeps showing pre-sync cards until the page is re-mounted.
		// sessionStart=false: sync already advanced the cutoff server-side, so just
		// pull the freshly-built queue without forcing a second rebuild.
		if (syncStore.lastResult) {
			refreshFromServer(false);
		}
	});

	async function rate(rating: 'again' | 'hard' | 'good' | 'easy', timeMs: number) {
		const { item, direction } = current;
		try {
			await api.submitDrill(item.id, direction, rating, timeMs);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			return;
		}
		// Refetch BEFORE re-keying the DrillCard. `reviewed` drives the {#key}, so
		// bumping it first would tear down and rebuild the card with the *old* item
		// in its unrevealed state (prompt image jumps back to full size) for the
		// whole network round-trip, then swap to the next card — a visible flash.
		// Refetching first means the just-graded card stays put until the next card
		// is ready, then a single clean swap.
		await refreshFromServer();
		reviewed += 1;
		// One-shot "Check your work" completion: only once a lesson-scoped session
		// has ACTUALLY drained (the post-grade refetch returned an empty queue) do
		// we record the review, disarming the lesson page's link until the next
		// listen re-arms it. Keyed on the refetched queue, not a pre-grade "last
		// card" guess: grading the final card does not necessarily drain it — an
		// Again re-queues it as relearning and a multi-step learning card stays —
		// both of which `_classify` keeps in the lesson queue, so a pre-grade proxy
		// would fire prematurely and hide the link while a card is still due.
		if (lessonMode && queue.length === 0 && !reviewedPosted) {
			reviewedPosted = true;
			api.markLessonReviewed(lessonId!);
		}
	}
</script>

<main>
	<h1>Review</h1>

	{#if stats && displayStats}
		<p class="stats">
			<QueueStatsWidget stats={displayStats} currentState={current?.item?.state} />
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
			{#if lessonMode && curriculumId}
				<a href="/c/{curriculumId}/l/{lessonId}">← Back to lesson</a>
			{:else}
				<a href="/">← Home</a>
			{/if}
		</section>
	{:else if current}
		<div class="card-meta">
			<p class="badge">{current.direction === 'recognition' ? 'Recognition' : 'Production'}</p>
			<p class="badge state-{current.item.state}">{current.item.state}</p>
			<!-- Deep-link to this card's row in the Cards viewer: the id highlights the
			     exact row; the text seeds the search box so the row lands on page 1. -->
			<a
				class="cards-link"
				href="/cards?focus={current.item.id}&q={encodeURIComponent(current.item.text)}"
				target="_blank"
				rel="noopener"
				title="Open this card in the Cards viewer (new tab)">Card details ↗</a
			>
		</div>
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
		margin: 0.5rem auto;
		padding: 0 1rem;
	}
	h1 {
		font-size: 1.3rem;
		font-weight: 800;
		letter-spacing: -0.01em;
		margin: 0.3rem 0 0.5rem;
	}
	.card-section {
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-sm);
	}
	.done {
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-sm);
		padding: 2rem;
		text-align: center;
	}
	.card-meta {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-bottom: 0.35rem;
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
	}
	/* Unobtrusive: muted, pushed to the far edge, only gains emphasis on hover. */
	.cards-link {
		margin-left: auto;
		font-size: 0.8rem;
		color: var(--color-muted);
		text-decoration: none;
		white-space: nowrap;
	}
	.cards-link:hover {
		color: var(--color-primary);
		text-decoration: underline;
	}
	.error {
		color: var(--color-danger);
	}
	.stats {
		color: var(--color-muted);
		font-size: 0.9rem;
		margin-bottom: 0.35rem;
	}
	.source {
		color: var(--color-muted);
		font-size: 0.8rem;
	}
	/* State chips: tinted with the palette tokens so they cohere and adapt to
	   light/dark (was a set of fixed pastels). */
	.badge.state-new { background: color-mix(in srgb, var(--color-info) 16%, transparent); color: var(--color-info); }
	.badge.state-learning { background: color-mix(in srgb, var(--color-warning) 18%, transparent); color: var(--color-warning); }
	.badge.state-review { background: color-mix(in srgb, var(--color-success) 16%, transparent); color: var(--color-success); }
	.badge.state-relearning { background: color-mix(in srgb, var(--color-danger) 16%, transparent); color: var(--color-danger); }
	.badge.state-suspended { background: var(--color-surface-2); color: var(--color-muted); }
	.badge.state-buried { background: var(--color-surface-2); color: var(--color-muted); }
	.badge.state-known { background: color-mix(in srgb, var(--color-accent) 22%, transparent); color: var(--color-accent); }

	/* Mobile keeps the card compact so Good/Easy fit without scrolling; desktop
	   has room to breathe. */
	@media (min-width: 641px) {
		main { margin: 2rem auto; }
		h1 { font-size: 1.9rem; margin: 0.5rem 0 0.75rem; }
		.stats { margin-bottom: 0.5rem; }
		.card-meta { margin-bottom: 0.5rem; }
	}
</style>
