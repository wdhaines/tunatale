<script lang="ts">
	import type { Cue } from '#lib/api.js';
	import type { PlaybackController } from '#lib/playback/playbackController.svelte.js';
	import type { StoryDrill } from '#lib/playback/sectionLine.js';
	import { captionBlurPref } from '#lib/stores/captionBlurPref.svelte.js';
	import { t } from '#lib/i18n/i18n.svelte.js';

	/**
	 * One story drill written out (tunatale-cs3p slice 3): the instruction, the
	 * model, and every step, each playable. The answers are what the audio asks
	 * for, so they follow the Captions setting like DrillForms' forms: blurred
	 * while captions are blurred, each shown by a tap.
	 */
	interface Props {
		drill: StoryDrill;
		controller: PlaybackController | null;
	}

	let { drill, controller }: Props = $props();

	let revealed: Set<number> = $state(new Set());

	// Its ▶ buttons are drawn only with a player.
	function play(cue: Cue) {
		if (cue.ref?.target_index != null) controller!.playRef(cue.ref);
		else controller!.seekToCue(cue);
	}

	// The step playing now, by its prompt's ref: a prompt and its answer share one.
	const playing = $derived(
		controller?.currentCue?.section_type === 'affix_drill' ? (controller.currentCue.ref?.target_index ?? null) : null
	);
</script>

<p class="instruction">{drill.instruction}</p>
{#if drill.model}
	<div class="model">
		<span class="label">{t('readDrill.model')}</span>
		<span class="ceb">{drill.model.text}</span>
		{#if controller}
			<button type="button" class="play" aria-label={t('readDrill.playModel')} onclick={() => play(drill.model!)}>▶</button>
		{/if}
	</div>
{/if}
<ol class="steps">
	{#each drill.steps as step (step.prompt.index)}
		{@const hidden = captionBlurPref.enabled && !revealed.has(step.prompt.index)}
		<li class:current={playing !== null && step.prompt.ref?.target_index === playing}>
			<span class="cue">{step.prompt.text}</span>
			{#if controller}
				<button type="button" class="play" aria-label={t('readDrill.play')} onclick={() => play(step.prompt)}>▶</button>
			{/if}
			{#if step.answer}
				{#if hidden}
					<button
						type="button"
						class="answer blurred"
						aria-label={t('readDrill.reveal')}
						onclick={() => (revealed = new Set([...revealed, step.prompt.index]))}
					>{step.answer.text}</button>
				{:else}
					<span class="answer ceb">{step.answer.text}</span>
				{/if}
			{/if}
		</li>
	{/each}
</ol>

<style>
	.instruction {
		margin: 0 0 0.6rem;
		color: var(--color-muted);
	}
	.model {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		margin-bottom: 0.6rem;
		flex-wrap: wrap;
	}
	.label {
		font-size: 0.75rem;
		font-weight: 600;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--color-muted);
	}
	.ceb {
		font-size: 1.05rem;
	}
	.steps {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-md, 10px);
		overflow: hidden;
	}
	.steps li {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 0.15rem 0.6rem;
		align-items: center;
		padding: 0.55rem 0.8rem;
		border-top: 1px solid var(--color-border);
	}
	.steps li:first-child {
		border-top: 0;
	}
	.steps li.current {
		background: var(--color-surface-2);
	}
	.cue {
		color: var(--color-muted);
		font-size: 0.9rem;
		min-width: 0;
	}
	.answer {
		grid-column: 1 / -1;
		min-width: 0;
	}
	.answer.blurred {
		filter: blur(5px);
		user-select: none;
		margin: 0;
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-text);
		font: inherit;
		font-size: 1.05rem;
		text-align: left;
		cursor: pointer;
	}
	.play {
		margin: 0;
		padding: 0.15rem 0.45rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--color-muted);
		cursor: pointer;
		font-size: 0.8rem;
	}
	.play:hover {
		color: var(--color-text);
	}
</style>
