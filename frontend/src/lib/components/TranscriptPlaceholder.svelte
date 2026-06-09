<script lang="ts">
	import type { LessonDetail } from '$lib/api';
	import { buildScenes } from '$lib/transcriptScenes';

	interface Props {
		lesson: LessonDetail;
	}
	let { lesson }: Props = $props();

	// Build scene structure from the lesson (with empty dialogueLines — no word
	// tokens yet) so the placeholder shows scene headers + per-line dialogue
	// matching the real transcript layout, just dimmed and non-interactive.
	const scenes = $derived(buildScenes(lesson, []));
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

	{#if scenes.length > 0}
		<div class="placeholder-section">
			<h3>Dialogue</h3>
			{#each scenes as scene, sceneIdx (sceneIdx)}
				{#if scene.title}
					<h4 class="scene-header">{scene.title}</h4>
				{/if}
				{#each scene.lines as line (line.transcriptIndex)}
					<div class="dialogue-line">
						<span class="dialogue-role">{line.role}</span>
						<span class="dialogue-words">{line.naturalText}</span>
					</div>
				{/each}
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
		flex-direction: column;
		gap: 0.1rem;
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
	.scene-header {
		margin: 1.25rem 0 0.5rem;
		padding: 0.45rem 0.75rem;
		font-size: 0.82rem;
		font-weight: 600;
		letter-spacing: 0.02em;
		color: var(--color-primary, #2563eb);
		background: rgba(37, 99, 235, 0.08);
		border-left: 3px solid var(--color-primary, #2563eb);
		border-radius: 0 4px 4px 0;
		text-transform: uppercase;
	}
	.scene-header:first-of-type {
		margin-top: 0.5rem;
	}
	.dialogue-line {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		padding: 0.3rem 0;
		border-bottom: 1px solid var(--color-border);
		font-size: 0.95rem;
		line-height: 1.5;
	}
	.dialogue-role {
		color: var(--color-primary);
		min-width: unset;
		font-weight: 600;
		font-size: 0.85rem;
		padding-top: 0.1rem;
		flex-shrink: 0;
	}
	.dialogue-words {
		flex: 1;
		min-width: 0;
		line-height: 1.6;
	}

	@media (min-width: 641px) {
		.dialogue-line {
			flex-direction: row;
			gap: 0.75rem;
		}
		.dialogue-role {
			min-width: 6rem;
			font-weight: 400;
		}
		.key-phrases-list li {
			flex-direction: row;
			justify-content: space-between;
			gap: 0;
		}
	}
</style>
