<script lang="ts">
	import WordSpan from '$lib/WordSpan.svelte';
	import type { TranscriptData, WordToken } from '$lib/api';

	interface Props {
		transcript: TranscriptData;
		isListened: boolean;
		listenLoading: boolean;
		listenResult: { registered: number } | null;
		error: string;
		onStateChange?: (lemma: string, srs_item_id: number | null) => void;
		onCollocationStateChange?: (lemma: string, span_id: number, current_state: string) => void;
		onMarkListened: () => void;
	}

	let {
		transcript,
		isListened,
		listenLoading,
		listenResult,
		error,
		onStateChange,
		onCollocationStateChange,
		onMarkListened
	}: Props = $props();

	type WordSegment = { type: 'word'; word: WordToken } | { type: 'collocation'; words: WordToken[]; span_id: number };

	function collocationClassFor(state: string): string {
		if (state === 'learning' || state === 'relearning') return 'coll-bg-learning';
		if (state === 'review') return 'coll-bg-review';
		if (state === 'known') return 'coll-bg-known';
		if (state === 'suspended' || state === 'ignored') return 'coll-bg-ignored';
		return 'coll-bg-new';
	}

	function handleCollocationClick(segment: { words: WordToken[]; span_id: number }) {
		const first = segment.words[0];
		onCollocationStateChange?.(
			first.collocation_lemma!,
			segment.span_id,
			first.collocation_srs_state!
		);
	}

	function handleCollocationKeydown(
		e: KeyboardEvent,
		segment: { words: WordToken[]; span_id: number }
	) {
		if (e.key !== 'Enter' && e.key !== ' ') return;
		e.preventDefault();
		handleCollocationClick(segment);
	}

	function groupIntoSegments(words: WordToken[]): WordSegment[] {
		const segments: WordSegment[] = [];
		let i = 0;
		while (i < words.length) {
			const w = words[i];
			if (w.collocation_span_id !== null) {
				const id = w.collocation_span_id;
				const group: WordToken[] = [];
				while (i < words.length && words[i].collocation_span_id === id) {
					group.push(words[i]);
					i++;
				}
				segments.push({ type: 'collocation', words: group, span_id: id });
			} else {
				segments.push({ type: 'word', word: w });
				i++;
			}
		}
		return segments;
	}
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
			<h3>Dialogue <span class="transcript-hint">(click phrases/words to change SRS state; Alt+click a word inside a phrase for word-only)</span></h3>
			{#each transcript.dialogue_lines as line}
				<div class="dialogue-line">
					<span class="dialogue-role">{line.role}</span>
					<span class="dialogue-words">
						{#each groupIntoSegments(line.words) as segment}
							{#if segment.type === 'collocation'}
								<span
									class="collocation-span {collocationClassFor(segment.words[0].collocation_srs_state!)}"
									role="button"
									tabindex="0"
									title={segment.words[0].collocation_srs_state!}
									onclick={() => handleCollocationClick(segment)}
									onkeydown={(e) => handleCollocationKeydown(e, segment)}
								>
									{#each segment.words as cw}
										<WordSpan
											word={cw}
											{onStateChange}
											requireModifier={true}
										/>{' '}
									{/each}
								</span>
							{:else}
								<WordSpan
									word={segment.word}
									{onStateChange}
								/>{' '}
							{/if}
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
	.collocation-span {
		display: inline;
		border-bottom: 2px solid var(--color-primary, #2563eb);
		padding-bottom: 1px;
		cursor: pointer;
		border-radius: 2px;
		transition: background-color 0.1s;
	}
	.collocation-span:hover {
		filter: brightness(0.95);
	}
	.collocation-span:focus-visible {
		outline: 2px solid var(--color-primary, #2563eb);
		outline-offset: 2px;
	}
	.coll-bg-new {
		background-color: rgba(37, 99, 235, 0.1);
	}
	.coll-bg-learning {
		background-color: rgba(202, 138, 4, 0.15);
	}
	.coll-bg-review {
		background-color: rgba(22, 163, 74, 0.12);
	}
	.coll-bg-known {
		background-color: rgba(156, 163, 175, 0.15);
	}
	.coll-bg-ignored {
		background-color: rgba(156, 163, 175, 0.15);
		text-decoration: line-through;
	}

	@media (max-width: 640px) {
		.dialogue-line {
			flex-direction: column;
			gap: 0.15rem;
		}
		.dialogue-role {
			min-width: unset;
			font-weight: 600;
		}
		.key-phrases-list li {
			flex-direction: column;
			gap: 0.1rem;
		}
	}
</style>
