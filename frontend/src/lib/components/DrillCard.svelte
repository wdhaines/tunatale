<script lang="ts">
	import type { SRSItemDetail } from '$lib/api';

	type Rating = 'again' | 'hard' | 'good' | 'easy';

	let {
		item,
		direction,
		onRate
	}: {
		item: SRSItemDetail;
		direction: 'recognition' | 'production';
		onRate: (rating: Rating, timeMs: number) => Promise<void>;
	} = $props();

	let revealed = $state(false);
	let audioEl: HTMLAudioElement | undefined = $state();
	const startedAt = performance.now();

	function show() {
		revealed = true;
	}

	async function rate(r: Rating) {
		const elapsed = Math.min(60000, Math.round(performance.now() - startedAt));
		await onRate(r, elapsed);
	}

	function playAudio() {
		audioEl?.play().catch(() => {});
	}
</script>

<div class="drill-card">
	<div class="prompt">
		{#if direction === 'recognition'}
			{#if item.audio_url}
				<audio bind:this={audioEl} src={item.audio_url} autoplay preload="auto"></audio>
				<button class="play-btn" onclick={playAudio} aria-label="Play audio">▶</button>
			{/if}
			<p class="main-text slovene">{item.text}</p>
		{:else if direction === 'production'}
			{#if item.image_url != null}
				<img src={item.image_url} alt={item.translation} class="prompt-image" />
			{:else}
				<p class="main-text">{item.translation}</p>
			{/if}
		{/if}
	</div>

	{#if revealed}
		<hr class="answer-divider" />
		<div class="answer">
			{#if direction === 'recognition'}
				{#if item.image_url != null}
					<img src={item.image_url} alt={item.translation} class="answer-image" />
				{/if}
				<p class="answer-text english">{item.translation}</p>
				{#if item.grammar}
					<div class="gram">{item.grammar}</div>
				{/if}
				{#if item.note}
					<div class="note">{item.note}</div>
				{/if}
			{:else if direction === 'production'}
				{#if item.audio_url}
					<audio bind:this={audioEl} src={item.audio_url} autoplay preload="auto"></audio>
					<button class="play-btn" onclick={playAudio} aria-label="Play audio">▶</button>
				{/if}
				<p class="answer-text slovene">{item.text}</p>
				<p class="answer-text english">{item.translation}</p>
				{#if item.grammar}
					<div class="gram">{item.grammar}</div>
				{/if}
				{#if item.note}
					<div class="note">{item.note}</div>
				{/if}
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
	.answer-divider {
		border: none;
		border-top: 1px solid var(--color-border, #ccc);
		margin: 1.5rem 0;
	}
	.answer-image {
		max-width: 240px;
		max-height: 240px;
		border-radius: 8px;
		margin-bottom: 1rem;
	}
	.answer-text {
		font-size: 1.25rem;
		color: var(--color-muted);
		margin-bottom: 0.75rem;
	}
	.slovene {
		/* Semantic hook for per-language typography — currently inherits .main-text */
		font: inherit;
	}
	.english {
		/* Semantic hook for per-language typography — currently inherits .answer-text */
		font: inherit;
	}
	.gram {
		font-size: 0.9rem;
		color: var(--color-muted);
		font-style: italic;
		margin-bottom: 0.5rem;
	}
	.note {
		font-size: 0.85rem;
		color: var(--color-muted);
		margin-bottom: 0.75rem;
	}
	.play-btn {
		background: var(--color-text, #333);
		border: 1px solid var(--color-text, #333);
		border-radius: 50%;
		width: 36px;
		height: 36px;
		cursor: pointer;
		font-size: 1rem;
		color: var(--color-bg, #fff);
		margin-bottom: 0.75rem;
		display: inline-flex;
		align-items: center;
		justify-content: center;
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
