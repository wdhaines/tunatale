<script lang="ts">
	import type { SRSItemDetail } from '$lib/api';

	type Rating = 'again' | 'hard' | 'good' | 'easy';

	let {
		item,
		promptSide,
		onRate
	}: {
		item: SRSItemDetail;
		promptSide: 'L2' | 'L1' | 'image';
		onRate: (rating: Rating) => Promise<void>;
	} = $props();

	let revealed = $state(false);

	function show() {
		revealed = true;
	}

	async function rate(r: Rating) {
		await onRate(r);
	}
</script>

<div class="drill-card">
	<div class="prompt">
		{#if promptSide === 'L2'}
			<p class="main-text">{item.text}</p>
		{:else if promptSide === 'image'}
			{#if item.image_url != null}
				<img src={item.image_url} alt={item.translation} class="prompt-image" />
			{:else}
				<p class="main-text">{item.translation}</p>
			{/if}
		{:else}
			<p class="main-text">{item.translation}</p>
		{/if}
	</div>

	{#if revealed}
		<div class="answer">
			{#if promptSide === 'L2'}
				<p class="answer-text">{item.translation}</p>
			{:else}
				<p class="answer-text">{item.text}</p>
			{/if}
		</div>
		<div class="ratings">
			<button class="btn-again" onclick={() => rate('again')}>Again</button>
			<button class="btn-hard" onclick={() => rate('hard')}>Hard</button>
			<button class="btn-good" onclick={() => rate('good')}>Good</button>
			<button class="btn-easy" onclick={() => rate('easy')}>Easy</button>
		</div>
	{:else}
		<button onclick={show}>Show</button>
	{/if}
</div>

<style>
	.drill-card {
		text-align: center;
		padding: 2rem 1rem;
	}
	.main-text {
		font-size: 2rem;
		font-weight: bold;
		margin-bottom: 1rem;
	}
	.prompt-image {
		max-width: 240px;
		max-height: 240px;
		border-radius: 8px;
		margin-bottom: 1rem;
	}
	.answer-text {
		font-size: 1.25rem;
		color: var(--color-muted);
		margin-bottom: 1.5rem;
	}
	.ratings {
		display: flex;
		justify-content: center;
		gap: 0.75rem;
		flex-wrap: wrap;
		margin-top: 1rem;
	}
	button {
		margin-top: 0.75rem;
		padding: 0.5rem 1.25rem;
		background: var(--color-primary);
		color: white;
		border: none;
		border-radius: 4px;
		cursor: pointer;
	}
	.ratings button {
		margin-top: 0;
	}
	.btn-again { background: var(--color-danger); }
	.btn-hard  { background: var(--color-warning); }
	.btn-good  { background: var(--color-success); }
	.btn-easy  { background: var(--color-primary); }

	@media (max-width: 640px) {
		.main-text { font-size: 1.5rem; }
		.ratings { gap: 0.5rem; }
		.ratings button {
			flex: 1 1 calc(50% - 0.5rem);
			min-height: 44px;
			font-size: 1rem;
		}
	}
</style>
