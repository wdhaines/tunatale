<script lang="ts">
	import type { LessonDetail } from '$lib/api';

	interface Props {
		lesson: LessonDetail;
	}
	let { lesson }: Props = $props();

	// The natural-speed section holds the L2 dialogue. The enriched transcript
	// (extract_transcript) filters to phrases in the lesson's language; mirror that
	// so the placeholder shows the same lines the real transcript will — just as
	// plain, uncolored, non-interactive text while word states are computed.
	const dialogue = $derived(
		(lesson.sections.find((s) => s.type === 'natural_speed')?.phrases ?? []).filter(
			(p) => p.language_code === lesson.language_code
		)
	);
</script>

<div class="placeholder" aria-busy="true">
	<p class="placeholder-hint">
		<span class="spinner" aria-hidden="true"></span>
		Preparing word states… showing the dialogue meanwhile.
	</p>

	{#if lesson.key_phrases.length > 0}
		<div class="placeholder-section">
			<h3>Key Phrases</h3>
			<ul class="key-phrases-list">
				{#each lesson.key_phrases as kp (kp.phrase)}
					<li>
						<span class="kp-phrase">{kp.phrase}</span>
						<span class="kp-translation">{kp.translation}</span>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if dialogue.length > 0}
		<div class="placeholder-section">
			<h3>Dialogue</h3>
			{#each dialogue as phrase, i (i)}
				<div class="dialogue-line">
					<span class="dialogue-role">{phrase.role}</span>
					<span class="dialogue-words">{phrase.text}</span>
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.placeholder {
		margin-top: 1.25rem;
		/* Dimmed to signal this is a preview being enhanced; word coloring and
		   click-to-grade appear once the enriched transcript loads. */
		opacity: 0.65;
	}
	.placeholder-hint {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		color: var(--color-muted);
		font-size: 0.85rem;
		font-style: italic;
		margin: 0 0 1rem;
	}
	.spinner {
		display: inline-block;
		width: 0.9rem;
		height: 0.9rem;
		border: 2px solid var(--color-border);
		border-top-color: var(--color-muted);
		border-radius: 50%;
		animation: spin 0.7s linear infinite;
		flex-shrink: 0;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.placeholder-section {
		margin-bottom: 1.25rem;
	}
	.placeholder-section h3 {
		font-size: 0.8rem;
		text-transform: uppercase;
		color: var(--color-muted);
		margin-bottom: 0.5rem;
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
		min-width: 0;
		line-height: 1.6;
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
