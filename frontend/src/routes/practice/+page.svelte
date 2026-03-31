<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { FeedbackSignal, SRSStats } from '$lib/api';

	type Card = { text: string; translation: string };

	let cards: Card[] = $state([]);
	let index = $state(0);
	let revealed = $state(false);
	let loading = $state(true);
	let error = $state('');
	let stats: SRSStats | null = $state(null);
	let done = $derived(index >= cards.length && cards.length > 0);

	onMount(async () => {
		try {
			const [newData, dueData] = await Promise.all([
				api.getSRSNew(),
				api.getSRSDue()
			]);
			cards = [...newData.new, ...dueData.due];
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
		api.getSRSStats().then((s) => { stats = s; }).catch(() => {});
	});

	function reveal() {
		revealed = true;
	}

	async function rate(signal: FeedbackSignal) {
		const card = cards[index];
		try {
			await api.postSRSFeedback(card.text, signal);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
		index += 1;
		revealed = false;
	}
</script>

<main>
	<h1><a href="/">TunaTale</a> — Practice</h1>
	{#if stats}
		<p class="stats">{stats.total} cards total · {stats.due_today} due today</p>
	{/if}

	{#if loading}
		<p>Loading cards…</p>
	{:else if error}
		<p class="error">{error}</p>
	{:else if cards.length === 0}
		<p>No cards due. Come back after generating a lesson.</p>
	{:else if done}
		<section>
			<h2>Session complete!</h2>
			<p>Reviewed: {index}</p>
		</section>
	{:else}
		<section class="card-section">
			<p class="progress">{index + 1} / {cards.length}</p>
			<div class="card">
				<p class="l2-text">{cards[index].text}</p>
				{#if revealed}
					<p class="translation">{cards[index].translation}</p>
					<div class="ratings">
						<button class="btn-again" onclick={() => rate('translation_request')}>Again</button>
						<button class="btn-hard" onclick={() => rate('slowdown')}>Hard</button>
						<button class="btn-good" onclick={() => rate('no_help')}>Good</button>
						<button class="btn-easy" onclick={() => rate('fast_forward')}>Easy</button>
					</div>
				{:else}
					<button onclick={reveal}>Reveal</button>
				{/if}
			</div>
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
	section {
		margin-top: 2rem;
		border: 1px solid #ddd;
		border-radius: 8px;
		padding: 1rem;
	}
	.card-section {
		text-align: center;
	}
	.progress {
		color: #666;
		font-size: 0.9rem;
	}
	.card {
		padding: 2rem 1rem;
	}
	.l2-text {
		font-size: 2rem;
		font-weight: bold;
		margin-bottom: 1rem;
	}
	.translation {
		font-size: 1.25rem;
		color: #555;
		margin-bottom: 1.5rem;
	}
	button {
		margin-top: 0.75rem;
		padding: 0.5rem 1.25rem;
		background: #2563eb;
		color: white;
		border: none;
		border-radius: 4px;
		cursor: pointer;
	}
	.ratings {
		display: flex;
		justify-content: center;
		gap: 0.75rem;
		flex-wrap: wrap;
		margin-top: 1rem;
	}
	.ratings button {
		margin-top: 0;
	}
	.btn-again { background: #dc2626; }
	.btn-hard  { background: #ea580c; }
	.btn-good  { background: #16a34a; }
	.btn-easy  { background: #2563eb; }
	.error {
		color: #dc2626;
		margin-top: 0.5rem;
	}
	.stats {
		color: #666;
		font-size: 0.85rem;
		margin-top: 0.25rem;
	}
</style>
