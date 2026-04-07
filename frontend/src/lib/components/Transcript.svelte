<script lang="ts">
	import WordSpan from '$lib/WordSpan.svelte';
	import type { TranscriptData, WordRating } from '$lib/api';

	interface Props {
		transcript: TranscriptData;
		pendingRatings: Record<string, WordRating | null>;
		isListened: boolean;
		listenLoading: boolean;
		listenResult: { registered: number } | null;
		error: string;
		onRatingChange: (lemma: string, rating: WordRating | null) => void;
		onMarkListened: () => void;
	}

	let {
		transcript,
		pendingRatings,
		isListened,
		listenLoading,
		listenResult,
		error,
		onRatingChange,
		onMarkListened
	}: Props = $props();
</script>

<div class="transcript-wrapper">
	<div class="listen-action">
		<button
			class="listen-btn"
			class:listened={isListened}
			onclick={onMarkListened}
			disabled={listenLoading}
		>
			{#if listenLoading}
				Registering…
			{:else if isListened}
				✓ Listened
			{:else}
				Mark as Listened
			{/if}
		</button>

		{#if listenResult && !error}
			<p class="listen-confirmation">
				{listenResult.registered}
				{listenResult.registered === 1 ? 'word' : 'words'} tracked in SRS
			</p>
		{/if}
	</div>

	{#if transcript.key_phrases.length > 0}
		<div class="transcript-section">
			<h3>Key Phrases</h3>
			<ul class="key-phrases-list">
				{#each transcript.key_phrases as kp}
					<li>
						<span class="kp-phrase">{kp.phrase}</span>
						<span class="kp-translation">{kp.translation}</span>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if transcript.dialogue_lines.length > 0}
		<div class="transcript-section">
			<h3>Dialogue <span class="transcript-hint">(click a word: orange=hard, purple=easy)</span></h3>
			{#each transcript.dialogue_lines as line}
				<div class="dialogue-line">
					<span class="dialogue-role">{line.role}</span>
					<span class="dialogue-words">
						{#each line.words as word}
							<WordSpan
								{word}
								rating={pendingRatings[word.lemma] ?? null}
								onRatingChange={onRatingChange}
							/>{' '}
						{/each}
					</span>
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.transcript-wrapper {
		margin-top: 1.25rem;
	}
	.listen-action {
		padding-bottom: 1rem;
		border-bottom: 1px solid var(--color-border);
		margin-bottom: 1.25rem;
	}
	.listen-btn {
		padding: 0.5rem 1.25rem;
		background: var(--color-primary);
		color: white;
		border: none;
		border-radius: 4px;
		cursor: pointer;
	}
	.listen-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.listen-btn.listened {
		background: var(--color-success);
	}
	.listen-confirmation {
		color: var(--color-success);
		font-size: 0.85rem;
		margin-top: 0.5rem;
	}
	.transcript-section {
		margin-bottom: 1.25rem;
	}
	.transcript-section h3 {
		font-size: 0.8rem;
		text-transform: uppercase;
		color: var(--color-muted);
		margin-bottom: 0.5rem;
	}
	.transcript-hint {
		font-style: italic;
		text-transform: none;
		font-size: 0.75rem;
	}
	.key-phrases-list {
		list-style: none;
		padding: 0;
		margin-top: 0.5rem;
	}
	.key-phrases-list li {
		display: flex;
		justify-content: space-between;
		padding: 0.25rem 0;
		border-bottom: 1px solid var(--color-border);
	}
	.kp-phrase {
		font-weight: 500;
	}
	.kp-translation {
		color: var(--color-muted);
		font-style: italic;
	}
	.dialogue-line {
		display: flex;
		gap: 0.75rem;
		padding: 0.3rem 0;
		border-bottom: 1px solid var(--color-border);
		font-size: 0.95rem;
		line-height: 1.5;
	}
	.dialogue-role {
		color: var(--color-primary);
		min-width: 6rem;
		font-size: 0.85rem;
		padding-top: 0.1rem;
		flex-shrink: 0;
	}
	.dialogue-words {
		flex: 1;
		line-height: 1.6;
	}
</style>
